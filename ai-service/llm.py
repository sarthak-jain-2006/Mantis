"""Everything that talks to a language model.

The rest of the service only sees `LLMProvider` and `generate_structured`, so the
model behind them (Gemini today, maybe Ollama later) can change without touching
the agents.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Literal, Protocol, TypeVar

import httpx
from langchain_core.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from config import settings

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant"]
    content: str


# --- Errors -------------------------------------------------------------------
# Each message is safe to show a user: it never contains the API key or the prompt.


class LLMError(Exception):
    """A model call failed. The message says why in plain words."""


class LLMRateLimitError(LLMError):
    pass


class LLMTimeoutError(LLMError):
    pass


class LLMUnavailableError(LLMError):
    pass


class LLMOutputError(LLMError):
    """The model kept returning output that does not match the schema."""


class InvalidOutput(Exception):
    """Raised by a provider when the reply doesn't fit the schema. Carries what came back,
    so the caller can show it to the model and ask for a corrected reply."""

    def __init__(self, raw_text: str, problem: str):
        super().__init__(problem)
        self.raw_text = raw_text
        self.problem = problem


# --- Provider interface ---------------------------------------------------------


class LLMProvider(Protocol):
    model_name: str

    def generate(self, messages: list[Message], schema: type[T]) -> T:
        """Return the model's reply parsed into `schema`.

        Raises InvalidOutput for a reply that doesn't match, or an LLMError subclass
        when the call itself fails.
        """
        ...


class GeminiProvider:
    def __init__(self, model_name: str, api_key: str, timeout_seconds: float):
        # Imported here so tests that never build a real provider don't pay for loading the SDK.
        from langchain_google_genai import ChatGoogleGenerativeAI

        self.model_name = model_name
        self._chat = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            timeout=timeout_seconds,
            # 1 = a single attempt. Retries happen in generate_structured, where we can see
            # and log them. (0 would mean "use the SDK's default", not "no retries".)
            max_retries=1,
        )

    def generate(self, messages: list[Message], schema: type[T]) -> T:
        structured = self._chat.with_structured_output(schema, method="json_schema", include_raw=True)
        try:
            result = structured.invoke(_to_langchain(messages))
        except ModelRateLimitError as e:
            raise LLMRateLimitError("Gemini rate limit reached (HTTP 429)") from e
        except (ModelTimeoutError, httpx.TimeoutException) as e:
            raise LLMTimeoutError(f"Gemini did not answer within {settings.llm_timeout_seconds:g}s") from e
        except (ModelConnectionError, httpx.TransportError) as e:
            raise LLMUnavailableError("Could not reach Gemini") from e
        except ModelAPIError as e:
            raise LLMUnavailableError("Gemini had a server error") from e
        except ModelError as e:
            # Bad key, unknown model name, rejected request: retrying won't help.
            raise LLMError(f"Gemini rejected the request: {_google_reason(e)}") from e

        parsed = result.get("parsed")
        if isinstance(parsed, schema):
            return parsed
        raw = result.get("raw")
        raw_text = raw.text if isinstance(raw, AIMessage) else ""
        problem = str(result.get("parsing_error") or "The reply was empty")
        raise InvalidOutput(raw_text, problem)


def _google_reason(error: Exception) -> str:
    # LangChain wraps Google's error, whose text is a long JSON dump. The original error
    # (its __cause__) carries Google's one-line message, e.g. "API key not valid. ...".
    message = getattr(error.__cause__, "message", None)
    return message if isinstance(message, str) and message else str(error)


def _to_langchain(messages: list[Message]) -> list[BaseMessage]:
    kinds = {"system": SystemMessage, "user": HumanMessage, "assistant": AIMessage}
    return [kinds[m.role](content=m.content) for m in messages]


@lru_cache
def get_llm() -> LLMProvider:
    """The provider the service uses. Tests replace it with a fake via FastAPI's dependency_overrides."""
    match settings.llm_provider:
        case "gemini":
            assert settings.gemini_api_key is not None  # checked when settings load
            return GeminiProvider(
                settings.gemini_model,
                settings.gemini_api_key.get_secret_value(),
                settings.llm_timeout_seconds,
            )


# --- Calling a model safely -------------------------------------------------------


def generate_structured(
    llm: LLMProvider,
    messages: list[Message],
    schema: type[T],
    *,
    sleep: Callable[[float], None] | None = None,
) -> T:
    """Ask the model for output matching `schema`, with the two retry rules from the design doc:

    - Output that doesn't match the schema gets ONE repair attempt: the model is shown its
      reply and the validation error and asked to fix it. A second bad reply fails the step.
    - A rate-limited call (429) is retried with exponential back-off (2s, then 4s, ...).

    `sleep` is a parameter so tests can run the back-off instantly.
    """
    # Looked up at call time (not as a default argument) so tests can patch time.sleep.
    sleep = sleep or time.sleep
    try:
        return _with_backoff(llm, messages, schema, sleep)
    except InvalidOutput as first:
        repair = [
            *messages,
            Message("assistant", first.raw_text or "(empty reply)"),
            Message(
                "user",
                "Your reply did not match the required JSON schema.\n"
                f"Problem: {first.problem}\n"
                "Reply again with only the corrected JSON object.",
            ),
        ]
        try:
            return _with_backoff(llm, repair, schema, sleep)
        except InvalidOutput as second:
            raise LLMOutputError(
                f"The model's reply did not match the expected format, even after a retry: {second.problem}"
            ) from second


def _with_backoff(llm: LLMProvider, messages: list[Message], schema: type[T], sleep: Callable[[float], None]) -> T:
    attempts = settings.llm_rate_limit_attempts
    for attempt in range(1, attempts + 1):
        try:
            return llm.generate(messages, schema)
        except LLMRateLimitError:
            if attempt == attempts:
                raise LLMRateLimitError(f"Gemini rate limit reached; gave up after {attempts} attempts") from None
            sleep(2**attempt)
    raise AssertionError("unreachable")

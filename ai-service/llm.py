"""Everything that talks to a language model.

The rest of the service only sees `LLMProvider`, `generate_structured` and `call_tools`, so
the model behind them (Gemini today, maybe Ollama later) can change without touching the agents.
"""

import re
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Literal, Protocol, TypeVar

import httpx
from langchain_core.exceptions import (
    ModelAPIError,
    ModelConnectionError,
    ModelError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel

from config import settings

T = TypeVar("T", bound=BaseModel)
R = TypeVar("R")


@dataclass(frozen=True)
class ToolRequest:
    """The model asking for one tool call."""

    id: str
    name: str
    args: dict


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    # Assistant turns: the tool calls the model asked for.
    tool_calls: tuple[ToolRequest, ...] = ()
    # Tool results: which call this answers.
    tool_call_id: str | None = None
    name: str | None = None
    # The provider's own message object for an assistant turn, sent back unchanged.
    # Gemini attaches "thought signatures" to tool calls and rejects a conversation that drops them.
    raw: Any = field(default=None, compare=False, repr=False)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    args_schema: type[BaseModel]


@dataclass(frozen=True)
class AssistantTurn:
    tool_calls: tuple[ToolRequest, ...]
    text: str
    raw: Any = field(default=None, compare=False, repr=False)


# --- Errors -------------------------------------------------------------------
# Each message is safe to show a user: it never contains the API key or the prompt.


class LLMError(Exception):
    """A model call failed. The message says why in plain words."""


class LLMRateLimitError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None, daily: bool = False):
        super().__init__(message)
        # Seconds Google asked us to wait, when it said.
        self.retry_after = retry_after
        # The per-day quota is used up: waiting a few seconds won't help.
        self.daily = daily


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

    def call_tools(self, messages: list[Message], tools: list[ToolSpec]) -> AssistantTurn:
        """Return the model's next turn, which must request one or more of `tools`.

        Raises an LLMError subclass when the call fails.
        """
        ...


class GeminiProvider:
    def __init__(self, model_name: str, api_key: str, timeout_seconds: float, reasoning_effort: str = "low"):
        # Imported here so tests that never build a real provider don't pay for loading the SDK.
        from langchain_google_genai import ChatGoogleGenerativeAI

        self.model_name = model_name
        self._chat = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=api_key,
            timeout=timeout_seconds,
            reasoning_effort=reasoning_effort,
            # 1 = a single attempt. Retries happen in this module, where we can see
            # and log them. (0 would mean "use the SDK's default", not "no retries".)
            max_retries=1,
        )

    def generate(self, messages: list[Message], schema: type[T]) -> T:
        structured = self._chat.with_structured_output(schema, method="json_schema", include_raw=True)
        with _translate_errors(self.model_name):
            result = structured.invoke(_to_langchain(messages))

        parsed = result.get("parsed")
        if isinstance(parsed, schema):
            return parsed
        raw = result.get("raw")
        raw_text = raw.text if isinstance(raw, AIMessage) else ""
        problem = str(result.get("parsing_error") or "The reply was empty")
        raise InvalidOutput(raw_text, problem)

    def call_tools(self, messages: list[Message], tools: list[ToolSpec]) -> AssistantTurn:
        specs = [
            {
                "type": "function",
                "function": {"name": t.name, "description": t.description, "parameters": _gemini_schema(t.args_schema)},
            }
            for t in tools
        ]
        # "any" = the model must call a tool; it cannot reply with free text instead.
        bound = self._chat.bind_tools(specs, tool_choice="any")
        with _translate_errors(self.model_name):
            reply = bound.invoke(_to_langchain(messages))

        calls = tuple(
            ToolRequest(id=call.get("id") or f"call-{i}", name=call["name"], args=call.get("args") or {})
            for i, call in enumerate(reply.tool_calls)
        )
        return AssistantTurn(tool_calls=calls, text=reply.text, raw=reply)


def _gemini_schema(model: type[BaseModel]) -> dict:
    """The model's JSON schema in the form Gemini's tool declarations accept.

    Gemini ignores "$defs"/"$ref" (so a nested model such as a list of files would lose its shape)
    and "additionalProperties". References are inlined; unknown arguments are still refused,
    because the tool registry and the submit check validate with Pydantic.
    """
    schema = model.model_json_schema()
    definitions = schema.get("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return inline(definitions[node["$ref"].rsplit("/", 1)[-1]])
            return {k: inline(v) for k, v in node.items() if k not in ("$defs", "additionalProperties")}
        if isinstance(node, list):
            return [inline(v) for v in node]
        return node

    return inline(schema)


@contextmanager
def _translate_errors(model_name: str) -> Iterator[None]:
    """Turn the SDK's exceptions into ours, with messages fit to show on a task."""
    try:
        yield
    except ModelRateLimitError as e:
        quota, retry_after = _quota_details(e)
        if quota and "PerDay" in quota:
            hours = f"about {retry_after / 3600:.0f} h" if retry_after else "a few hours"
            raise LLMRateLimitError(
                f"Gemini's free daily quota for {model_name} is used up; it resets in {hours}. "
                "Set GEMINI_MODEL to another model, or try again later",
                retry_after=retry_after,
                daily=True,
            ) from e
        raise LLMRateLimitError("Gemini rate limit reached (HTTP 429)", retry_after=retry_after) from e
    except (ModelTimeoutError, httpx.TimeoutException) as e:
        raise LLMTimeoutError(f"Gemini did not answer within {settings.llm_timeout_seconds:g}s") from e
    except (ModelConnectionError, httpx.TransportError) as e:
        raise LLMUnavailableError("Could not reach Gemini") from e
    except ModelAPIError as e:
        raise LLMUnavailableError(f"Gemini had a server error: {_google_reason(e)}") from e
    except ModelError as e:
        # Bad key, unknown model name, rejected request: retrying won't help.
        raise LLMError(f"Gemini rejected the request: {_google_reason(e)}") from e


def _google_reason(error: Exception) -> str:
    # LangChain wraps Google's error, whose text is a long JSON dump. The original error
    # (its __cause__) carries Google's one-line message, e.g. "API key not valid. ...".
    message = getattr(error.__cause__, "message", None)
    return message if isinstance(message, str) and message else str(error)


def _quota_details(error: Exception) -> tuple[str | None, float | None]:
    """Which quota a 429 hit and how long Google says to wait, when it says.

    Gemini's error body lists e.g. {"@type": "...QuotaFailure", "violations": [{"quotaId":
    "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]} and {"@type": "...RetryInfo", "retryDelay": "37s"}.
    """
    details = getattr(error.__cause__, "details", None)
    try:
        items = details["error"]["details"]  # type: ignore[index]
    except (TypeError, KeyError):
        return None, None
    quota, retry_after = None, None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("@type", ""))
        if kind.endswith("QuotaFailure"):
            violations = item.get("violations") or [{}]
            quota = violations[0].get("quotaId") if isinstance(violations[0], dict) else None
        elif kind.endswith("RetryInfo"):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(item.get("retryDelay", "")))
            if match:
                retry_after = float(match.group(1))
    return quota, retry_after


def _to_langchain(messages: list[Message]) -> list[BaseMessage]:
    converted: list[BaseMessage] = []
    for m in messages:
        if m.role == "system":
            converted.append(SystemMessage(content=m.content))
        elif m.role == "user":
            converted.append(HumanMessage(content=m.content))
        elif m.role == "tool":
            converted.append(ToolMessage(content=m.content, tool_call_id=m.tool_call_id or "", name=m.name))
        elif isinstance(m.raw, AIMessage):
            converted.append(m.raw)  # the model's own turn, signatures intact
        else:
            calls = [{"id": c.id, "name": c.name, "args": c.args} for c in m.tool_calls]
            converted.append(AIMessage(content=m.content, tool_calls=calls))
    return converted


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
                settings.llm_reasoning_effort,
            )


# --- Staying within the provider's rate limit ----------------------------------------


class RateLimiter:
    """Allows at most `per_minute` calls in any 60-second window. A caller over the limit waits
    for a free slot instead of sending a request that would come back as a 429.

    Shared by every run in the process, so two tasks running at once share the budget.
    """

    def __init__(
        self,
        per_minute: int,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.per_minute = per_minute
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self.per_minute <= 0:  # 0 turns the limiter off
            return
        while True:
            with self._lock:
                now = self._clock()
                while self._sent and now - self._sent[0] >= 60:
                    self._sent.popleft()
                if len(self._sent) < self.per_minute:
                    self._sent.append(now)
                    return
                wait = 60 - (now - self._sent[0])
            self._sleep(wait)


limiter = RateLimiter(settings.llm_requests_per_minute)


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
    - A rate-limited call (429) is retried after the wait Gemini asks for (or 5s, then 10s),
      and so is a temporary outage ("model overloaded", connection refused).

    `sleep` is a parameter so tests can run the back-off instantly.
    """
    try:
        return _call_model(lambda: llm.generate(messages, schema), sleep)
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
            return _call_model(lambda: llm.generate(repair, schema), sleep)
        except InvalidOutput as second:
            raise LLMOutputError(
                f"The model's reply did not match the expected format, even after a retry: {second.problem}"
            ) from second


def call_tools(
    llm: LLMProvider,
    messages: list[Message],
    tools: list[ToolSpec],
    *,
    sleep: Callable[[float], None] | None = None,
) -> AssistantTurn:
    """One turn of a tool-using agent, with the same rate limiting and back-off as generate_structured."""
    return _call_model(lambda: llm.call_tools(messages, tools), sleep)


MAX_BACKOFF_SECONDS = 60


def _call_model(call: Callable[[], R], sleep: Callable[[float], None] | None) -> R:
    # Looked up at call time (not as a default argument) so tests can patch time.sleep.
    sleep = sleep or time.sleep
    attempts = settings.llm_rate_limit_attempts
    for attempt in range(1, attempts + 1):
        limiter.acquire()
        try:
            return call()
        except LLMRateLimitError as e:
            if e.daily:
                raise  # hours away; retrying in seconds would only waste the remaining attempts
            if attempt == attempts:
                raise LLMRateLimitError(f"Gemini rate limit reached; gave up after {attempts} attempts") from None
            sleep(min(e.retry_after or 5 * 2 ** (attempt - 1), MAX_BACKOFF_SECONDS))
        except LLMUnavailableError as e:
            # Google's 503 "high demand" and dropped connections are usually over in seconds.
            # Timeouts are not retried: a call that already took the whole limit would likely do so again.
            if attempt == attempts:
                raise LLMUnavailableError(f"{e}; gave up after {attempts} attempts") from None
            sleep(5 * 2 ** (attempt - 1))
    raise AssertionError("unreachable")

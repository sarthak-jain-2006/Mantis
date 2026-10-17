import httpx
import pytest
from langchain_core.exceptions import ModelAuthenticationError, ModelRateLimitError
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from conftest import SAMPLE_PLAN, FakeLLM
from llm import (
    GeminiProvider,
    InvalidOutput,
    LLMError,
    LLMOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
    Message,
    generate_structured,
)
from schemas import ImplementationPlan

MESSAGES = [Message("system", "You plan tasks."), Message("user", "<request>Add pagination</request>")]


class RecordSleep:
    def __init__(self):
        self.waits = []

    def __call__(self, seconds):
        self.waits.append(seconds)


# --- generate_structured: retry rules ---------------------------------------------


def test_returns_valid_output_on_first_try():
    llm = FakeLLM(SAMPLE_PLAN)
    assert generate_structured(llm, MESSAGES, ImplementationPlan) == SAMPLE_PLAN
    assert len(llm.calls) == 1


def test_bad_output_gets_one_repair_attempt():
    llm = FakeLLM(InvalidOutput('{"summary": "x"}', "subtasks: Field required"), SAMPLE_PLAN)

    assert generate_structured(llm, MESSAGES, ImplementationPlan) == SAMPLE_PLAN

    # The retry shows the model its own reply and what was wrong with it.
    repair = llm.calls[1]
    assert repair[: len(MESSAGES)] == MESSAGES
    assert repair[-2] == Message("assistant", '{"summary": "x"}')
    assert "subtasks: Field required" in repair[-1].content


def test_second_bad_output_fails_the_step():
    llm = FakeLLM(InvalidOutput("nope", "not JSON"), InvalidOutput("still nope", "not JSON"))

    with pytest.raises(LLMOutputError, match="even after a retry"):
        generate_structured(llm, MESSAGES, ImplementationPlan)
    assert len(llm.calls) == 2


def test_rate_limit_backs_off_then_succeeds():
    sleep = RecordSleep()
    llm = FakeLLM(LLMRateLimitError("429"), LLMRateLimitError("429"), SAMPLE_PLAN)

    assert generate_structured(llm, MESSAGES, ImplementationPlan, sleep=sleep) == SAMPLE_PLAN
    assert sleep.waits == [2, 4]


def test_rate_limit_gives_up_after_three_attempts():
    sleep = RecordSleep()
    llm = FakeLLM(*[LLMRateLimitError("429")] * 3)

    with pytest.raises(LLMRateLimitError, match="after 3 attempts"):
        generate_structured(llm, MESSAGES, ImplementationPlan, sleep=sleep)
    assert sleep.waits == [2, 4]


def test_timeouts_are_not_retried():
    llm = FakeLLM(LLMTimeoutError("slow"), SAMPLE_PLAN)

    with pytest.raises(LLMTimeoutError):
        generate_structured(llm, MESSAGES, ImplementationPlan, sleep=RecordSleep())
    assert len(llm.calls) == 1


# --- GeminiProvider: turning SDK results and errors into ours ------------------------


class FakeStructured:
    def __init__(self, outcome):
        self.outcome = outcome

    def invoke(self, _messages):
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeChat:
    def __init__(self, outcome):
        self.outcome = outcome

    def with_structured_output(self, schema, **_kwargs):
        return FakeStructured(self.outcome)


def gemini_returning(outcome) -> GeminiProvider:
    provider = GeminiProvider("gemini-test", api_key="test-key", timeout_seconds=30)
    provider._chat = FakeChat(outcome)  # no network: swap the SDK client for a fake
    return provider


def test_gemini_returns_parsed_plan():
    provider = gemini_returning({"raw": AIMessage(content="{}"), "parsed": SAMPLE_PLAN, "parsing_error": None})
    assert provider.generate(MESSAGES, ImplementationPlan) == SAMPLE_PLAN


def test_gemini_reports_unparseable_reply_with_its_text():
    provider = gemini_returning(
        {"raw": AIMessage(content="Sure! Here is a plan"), "parsed": None, "parsing_error": ValueError("bad JSON")}
    )
    with pytest.raises(InvalidOutput) as info:
        provider.generate(MESSAGES, ImplementationPlan)
    assert info.value.raw_text == "Sure! Here is a plan"
    assert "bad JSON" in info.value.problem


@pytest.mark.parametrize(
    ("sdk_error", "ours"),
    [
        (ModelRateLimitError("quota"), LLMRateLimitError),
        (httpx.ReadTimeout("timed out"), LLMTimeoutError),
        (ModelAuthenticationError("API key not valid"), LLMError),
    ],
)
def test_gemini_maps_sdk_errors(sdk_error, ours):
    with pytest.raises(ours):
        gemini_returning(sdk_error).generate(MESSAGES, ImplementationPlan)


def test_gemini_rejection_shows_googles_short_message():
    class GoogleError(Exception):
        message = "API key not valid. Please pass a valid API key."

    wrapped = ModelAuthenticationError("Error calling model (INVALID_ARGUMENT): 400 {'error': {...long JSON...}}")
    wrapped.__cause__ = GoogleError()

    with pytest.raises(LLMError) as info:
        gemini_returning(wrapped).generate(MESSAGES, ImplementationPlan)
    assert str(info.value) == "Gemini rejected the request: API key not valid. Please pass a valid API key."


# --- The plan schema ----------------------------------------------------------------


def test_plan_rejects_blank_and_missing_steps():
    with pytest.raises(ValidationError):
        ImplementationPlan(
            summary="  ",
            subtasks=[],
            acceptance_criteria=["ok"],
            constraints=[],
            needs_research=False,
        )

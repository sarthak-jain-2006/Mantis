import json

import httpx
import pytest
from langchain_core.exceptions import ModelAPIError, ModelAuthenticationError, ModelRateLimitError
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from agents.analyst import AnalysisReport
from conftest import SAMPLE_PLAN, FakeLLM, ask
from llm import (
    GeminiProvider,
    InvalidOutput,
    LLMError,
    LLMOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
    Message,
    RateLimiter,
    ToolRequest,
    ToolSpec,
    _gemini_schema,
    _to_langchain,
    call_tools,
    generate_structured,
)
from schemas import ImplementationPlan
from tools.registry import ReadFileArgs

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
    assert sleep.waits == [5, 10]


def test_rate_limit_waits_as_long_as_google_asks():
    sleep = RecordSleep()
    llm = FakeLLM(LLMRateLimitError("429", retry_after=37), SAMPLE_PLAN)

    generate_structured(llm, MESSAGES, ImplementationPlan, sleep=sleep)
    assert sleep.waits == [37]


def test_rate_limit_gives_up_after_three_attempts():
    sleep = RecordSleep()
    llm = FakeLLM(*[LLMRateLimitError("429")] * 3)

    with pytest.raises(LLMRateLimitError, match="after 3 attempts"):
        generate_structured(llm, MESSAGES, ImplementationPlan, sleep=sleep)
    assert sleep.waits == [5, 10]


def test_a_used_up_daily_quota_fails_at_once():
    sleep = RecordSleep()
    llm = FakeLLM(LLMRateLimitError("daily quota used up", retry_after=42000, daily=True), SAMPLE_PLAN)

    with pytest.raises(LLMRateLimitError, match="daily quota"):
        generate_structured(llm, MESSAGES, ImplementationPlan, sleep=sleep)
    assert sleep.waits == []
    assert len(llm.calls) == 1


def test_a_temporary_outage_is_retried():
    sleep = RecordSleep()
    llm = FakeLLM(LLMUnavailableError("Gemini had a server error: high demand"), SAMPLE_PLAN)

    assert generate_structured(llm, MESSAGES, ImplementationPlan, sleep=sleep) == SAMPLE_PLAN
    assert sleep.waits == [5]


def test_timeouts_are_not_retried():
    llm = FakeLLM(LLMTimeoutError("slow"), SAMPLE_PLAN)

    with pytest.raises(LLMTimeoutError):
        generate_structured(llm, MESSAGES, ImplementationPlan, sleep=RecordSleep())
    assert len(llm.calls) == 1


def test_call_tools_uses_the_same_retry_rules():
    sleep = RecordSleep()
    turn = ask(("read_file", {"path": "app/main.py"}))
    llm = FakeLLM(turns=[LLMRateLimitError("429"), turn])

    assert call_tools(llm, MESSAGES, [], sleep=sleep) == turn
    assert sleep.waits == [5]


# --- RateLimiter --------------------------------------------------------------------


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def test_limiter_lets_calls_through_up_to_the_limit():
    clock = FakeClock()
    limiter = RateLimiter(3, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        limiter.acquire()
    assert clock.now == 1000.0  # no waiting


def test_limiter_makes_the_next_call_wait_for_a_free_slot():
    clock = FakeClock()
    limiter = RateLimiter(2, clock=clock, sleep=clock.sleep)
    limiter.acquire()
    clock.now += 10
    limiter.acquire()
    limiter.acquire()  # the oldest call was 10s ago, so this waits 50s for it to leave the window
    assert clock.now == 1060.0


def test_limiter_set_to_zero_is_off():
    clock = FakeClock()
    limiter = RateLimiter(0, clock=clock, sleep=clock.sleep)
    for _ in range(100):
        limiter.acquire()
    assert clock.now == 1000.0


# --- GeminiProvider: turning SDK results and errors into ours ------------------------


class FakeStructured:
    def __init__(self, outcome):
        self.outcome = outcome
        self.received = None

    def invoke(self, messages):
        self.received = messages
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeChat:
    def __init__(self, outcome):
        self.runnable = FakeStructured(outcome)
        self.bound_tools = None
        self.tool_choice = None

    def with_structured_output(self, schema, **_kwargs):
        return self.runnable

    def bind_tools(self, tools, tool_choice=None):
        self.bound_tools, self.tool_choice = tools, tool_choice
        return self.runnable


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
        (ModelAPIError("503 high demand"), LLMUnavailableError),
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


def _google_429(quota_id, retry_delay):
    class GoogleError(Exception):
        details = {
            "error": {
                "details": [
                    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": quota_id}]},
                    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": retry_delay},
                ]
            }
        }

    error = ModelRateLimitError("429 RESOURCE_EXHAUSTED")
    error.__cause__ = GoogleError()
    return error


def test_gemini_429_carries_the_wait_google_asks_for():
    with pytest.raises(LLMRateLimitError) as info:
        gemini_returning(_google_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "37s")).generate(
            MESSAGES, ImplementationPlan
        )
    assert info.value.retry_after == 37
    assert info.value.daily is False


def test_gemini_daily_quota_is_explained():
    with pytest.raises(LLMRateLimitError) as info:
        gemini_returning(_google_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", "42092s")).generate(
            MESSAGES, ImplementationPlan
        )
    assert info.value.daily is True
    assert str(info.value).startswith("Gemini's free daily quota for gemini-test is used up; it resets in about 12 h")


def test_gemini_tool_turn_forces_a_tool_call_and_keeps_the_raw_reply():
    reply = AIMessage(content="", tool_calls=[{"id": "abc", "name": "read_file", "args": {"path": "app/main.py"}}])
    provider = gemini_returning(reply)

    turn = provider.call_tools(MESSAGES, [ToolSpec("read_file", "Read a file.", ReadFileArgs)])

    assert turn.tool_calls == (ToolRequest("abc", "read_file", {"path": "app/main.py"}),)
    assert turn.raw is reply  # sent back unchanged next turn, so Gemini's thought signatures survive
    assert provider._chat.tool_choice == "any"
    declared = provider._chat.bound_tools[0]["function"]
    assert declared["name"] == "read_file"
    assert "additionalProperties" not in json.dumps(declared)


def test_conversation_converts_to_langchain_messages():
    raw = AIMessage(content="", tool_calls=[{"id": "abc", "name": "read_file", "args": {"path": "a.py"}}])
    converted = _to_langchain(
        [
            Message("system", "rules"),
            Message("user", "task"),
            Message("assistant", "", tool_calls=(ToolRequest("abc", "read_file", {"path": "a.py"}),), raw=raw),
            Message("tool", '{"ok": true}', tool_call_id="abc", name="read_file"),
        ]
    )
    assert [type(m) for m in converted] == [SystemMessage, HumanMessage, AIMessage, ToolMessage]
    assert converted[2] is raw
    assert converted[3].tool_call_id == "abc"


def test_nested_schemas_are_inlined_for_gemini():
    schema = json.dumps(_gemini_schema(AnalysisReport))
    assert "$defs" not in schema and "$ref" not in schema
    assert '"reason"' in schema  # the nested RelevantFile fields are spelled out


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

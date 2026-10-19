import hashlib
import sqlite3

import httpx
import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from conftest import SAMPLE_PLAN, SAMPLE_REPO, TEST_TOKEN, FakeLLM, RecordingSink, ask, needs_rg
from llm import LLMRateLimitError
from runs import HttpEventSink, Runner, describe_tool_call, execute_run, report_interrupted_runs
from tools.registry import ToolCall

REQUEST = "Add limit and offset pagination to GET /products"

ANALYSIS = {
    "summary": "GET /products is handled by list_products in the products router.",
    "relevant_files": [{"path": "app/routers/products.py", "reason": "Defines GET /products."}],
    "test_files": ["tests/test_products.py"],
    "test_command": "pytest -q",
    "conventions": ["Validation uses Pydantic Field constraints"],
}

PAGINATED = (SAMPLE_REPO / "app" / "routers" / "products.py").read_text().replace(
    "def list_products():\n    return sorted(store.products.values(), key=lambda p: p.id)",
    "def list_products(limit: int = 20, offset: int = 0):\n"
    "    return sorted(store.products.values(), key=lambda p: p.id)[offset : offset + limit]",
)


def happy_llm():
    """A scripted model that plans, analyses and edits the products endpoint."""
    return FakeLLM(
        SAMPLE_PLAN,
        turns=[
            # Analyst
            ask(("search_code", {"pattern": "def list_products"})),
            ask(("submit_analysis", ANALYSIS)),
            # Coder
            ask(("write_file", {"path": "app/routers/products.py", "content": PAGINATED})),
            ask(("git_diff", {})),
            ask(("submit_changes", {"summary": "Added limit and offset.", "notes": []})),
        ],
    )


def run(tmp_path, llm, task_id="task-1", **kwargs):
    sink = RecordingSink()
    execute_run(task_id, REQUEST, llm, sink, workspaces_dir=tmp_path / "workspaces", **kwargs)
    return sink


@needs_rg
def test_a_run_goes_from_request_to_a_real_diff(tmp_path):
    sink = run(tmp_path, happy_llm())

    steps = [(t, p.get("step") or p.get("tool")) for t, p in sink.events if t != "run_completed"]
    assert steps == [
        ("run_started", None),
        ("step_started", "planner"),
        ("step_finished", "planner"),
        ("step_started", "analyst"),
        ("tool_called", "list_files"),  # listed by code before the analyst's first turn
        ("tool_called", "search_code"),
        ("step_finished", "analyst"),
        ("step_started", "coder"),
        ("tool_called", "read_file"),  # the analyst's files, read by code for the coder
        ("tool_called", "read_file"),
        ("tool_called", "write_file"),
        ("tool_called", "git_diff"),
        # (the submit check "did anything change?" is Mantis's own git call, not an agent's tool call)
        ("step_finished", "coder"),
    ]

    done = sink.last("run_completed")
    assert done["plan"] == SAMPLE_PLAN.model_dump()
    assert done["analysis"]["relevant_files"][0]["path"] == "app/routers/products.py"
    changes = done["changes"]
    assert "+def list_products(limit: int = 20, offset: int = 0):" in changes["diff"]
    assert changes["changed_files"] == [{"path": "app/routers/products.py", "change": "modified"}]
    assert changes["diff_hash"] == hashlib.sha256(changes["diff"].encode()).hexdigest()
    assert changes["summary"] == "Added limit and offset."
    assert done["model"] == "fake-model"


@needs_rg
def test_the_coder_starts_with_the_analysts_files(tmp_path):
    llm = happy_llm()
    run(tmp_path, llm)
    coder_first_turn, _ = llm.tool_conversations[2]
    prompt = coder_first_turn[-1].content
    assert '<file path="app/routers/products.py">' in prompt
    assert '<file path="tests/test_products.py">' in prompt


@needs_rg
def test_the_original_repository_is_untouched(tmp_path):
    before = (SAMPLE_REPO / "app" / "routers" / "products.py").read_text()
    run(tmp_path, happy_llm())
    assert (SAMPLE_REPO / "app" / "routers" / "products.py").read_text() == before


def test_a_model_failure_ends_the_run_with_the_reason_and_step(tmp_path):
    sink = run(tmp_path, FakeLLM(LLMRateLimitError("Gemini's free daily quota is used up", daily=True)))
    assert sink.types() == ["run_started", "step_started", "run_failed"]
    assert sink.last("run_failed") == {"error": "Gemini's free daily quota is used up", "step": "planner"}


def test_an_analyst_that_never_finishes_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr("runs.settings.analyst_max_turns", 2)
    llm = FakeLLM(SAMPLE_PLAN, turns=[ask(("list_files", {"path": "."}))] * 2)
    sink = run(tmp_path, llm)
    assert sink.last("run_failed") == {"error": "The analyst did not finish within 2 turns", "step": "analyst"}


def test_the_coder_cannot_submit_without_changing_anything(tmp_path):
    llm = FakeLLM(
        SAMPLE_PLAN,
        turns=[
            ask(("submit_analysis", ANALYSIS)),
            ask(("submit_changes", {"summary": "Done!", "notes": []})),  # but nothing was written
            ask(("write_file", {"path": "app/routers/products.py", "content": PAGINATED})),
            ask(("submit_changes", {"summary": "Added limit and offset.", "notes": []})),
        ],
    )
    sink = run(tmp_path, llm)
    assert "run_completed" in sink.types()
    rejected = llm.tool_conversations[2][0][-1]
    assert "no files have been changed yet" in rejected.content


def test_an_analyst_naming_a_missing_file_is_asked_again(tmp_path):
    bad = {**ANALYSIS, "relevant_files": [{"path": "app/routers/catalog.py", "reason": "guess"}]}
    llm = FakeLLM(
        SAMPLE_PLAN,
        turns=[
            ask(("submit_analysis", bad)),
            ask(("submit_analysis", ANALYSIS)),
            ask(("write_file", {"path": "app/routers/products.py", "content": PAGINATED})),
            ask(("submit_changes", {"summary": "Added limit and offset.", "notes": []})),
        ],
    )
    sink = run(tmp_path, llm)
    assert "run_completed" in sink.types()
    reply = llm.tool_conversations[1][0][-1].content
    assert "these files do not exist: app/routers/catalog.py" in reply


def test_an_unexpected_crash_is_reported_without_internals(tmp_path):
    class Broken(FakeLLM):
        def generate(self, messages, schema):
            raise RuntimeError("secret internal detail")

    sink = run(tmp_path, Broken())
    assert sink.last("run_failed") == {
        "error": "Unexpected error in the AI service (details are in its log)",
        "step": "planner",
    }


@needs_rg
def test_state_is_checkpointed_after_each_step(tmp_path):
    saver = SqliteSaver(sqlite3.connect(tmp_path / "checkpoints.sqlite", check_same_thread=False))
    run(tmp_path, happy_llm(), task_id="task-cp", checkpointer=saver)

    state = saver.get_tuple({"configurable": {"thread_id": "task-cp"}}).checkpoint["channel_values"]
    assert state["implementation_plan"]["summary"] == SAMPLE_PLAN.summary
    assert state["repository_info"]["test_command"] == "pytest -q"
    assert state["proposed_changes"]["summary"] == "Added limit and offset."
    assert "diff" not in state["proposed_changes"]  # the diff stays in the workspace, not in state


# --- Reporting -------------------------------------------------------------------------


def test_tool_call_events_cut_long_arguments():
    call = ToolCall("coder", "write_file", {"path": "a.py", "content": "x" * 1000}, True, {"path": "a.py", "created": True, "bytes": 1000}, None, 3)
    event = describe_tool_call(call)
    assert event["args"]["path"] == "a.py"
    assert len(event["args"]["content"]) == 301
    assert event["summary"] == "created, 1000 bytes"


def test_http_sink_posts_events_with_the_internal_token(monkeypatch):
    sent = []

    def fake_post(url, json, headers, timeout):
        sent.append((url, json, headers))
        return httpx.Response(200, request=httpx.Request("POST", url))

    monkeypatch.setattr("runs.httpx.post", fake_post)
    HttpEventSink("task-1", "http://127.0.0.1:3000/", TEST_TOKEN).emit("step_started", step="planner")

    assert sent == [
        (
            "http://127.0.0.1:3000/internal/tasks/task-1/events",
            {"type": "step_started", "payload": {"step": "planner"}},
            {"X-Internal-Token": TEST_TOKEN},
        )
    ]


@pytest.mark.parametrize(("event", "attempts"), [("run_completed", 3), ("tool_called", 1)])
def test_http_sink_retries_only_final_events(monkeypatch, event, attempts):
    tries = []

    def failing_post(url, **_kwargs):
        tries.append(url)
        raise httpx.ConnectError("backend down")

    monkeypatch.setattr("runs.httpx.post", failing_post)
    monkeypatch.setattr("runs.time.sleep", lambda _: None)
    HttpEventSink("task-1", "http://127.0.0.1:3000", TEST_TOKEN).emit(event)  # never raises
    assert len(tries) == attempts


def test_startup_reports_interrupted_runs(monkeypatch):
    sent = []

    def fake_post(url, headers, timeout):
        sent.append((url, headers))
        return httpx.Response(200, json={"interrupted": 2}, request=httpx.Request("POST", url))

    monkeypatch.setattr("runs.httpx.post", fake_post)
    report_interrupted_runs("http://127.0.0.1:3000", TEST_TOKEN)
    assert sent == [("http://127.0.0.1:3000/internal/runs/interrupted", {"X-Internal-Token": TEST_TOKEN})]


def test_startup_goes_ahead_when_the_backend_is_down(monkeypatch):
    def down(url, **_kwargs):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("runs.httpx.post", down)
    report_interrupted_runs("http://127.0.0.1:3000", TEST_TOKEN)  # logs a warning, never raises


def test_the_service_reports_interrupted_runs_when_it_starts(monkeypatch):
    from fastapi.testclient import TestClient

    from main import app

    calls = []
    monkeypatch.setattr("runs.report_interrupted_runs", lambda url, token: calls.append(url))
    with TestClient(app):  # entering the client runs the startup code
        pass
    assert calls == ["http://127.0.0.1:3000"]


# --- Runner ----------------------------------------------------------------------------


def test_runner_runs_in_the_background_and_refuses_duplicates(tmp_path):
    sinks = {}

    def sink_for(task_id):
        sinks[task_id] = RecordingSink()
        return sinks[task_id]

    runner = Runner(
        llm_factory=lambda: FakeLLM(LLMRateLimitError("quota", daily=True)),
        sink_factory=sink_for,
        max_workers=1,
        workspaces_dir=tmp_path,
        checkpointer_factory=lambda: None,
    )
    assert runner.submit("task-1", REQUEST) is True
    runner.wait()

    assert sinks["task-1"].types()[0] == "run_started"
    assert sinks["task-1"].types()[-1] == "run_failed"


def test_runner_refuses_a_task_already_running(tmp_path):
    runner = Runner(lambda: FakeLLM(), lambda _: RecordingSink(), 1, workspaces_dir=tmp_path, checkpointer_factory=lambda: None)
    runner._active.add("task-1")  # as if a run were in progress
    assert runner.submit("task-1", REQUEST) is False

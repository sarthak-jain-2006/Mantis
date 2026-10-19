"""Running a task in the background and reporting progress to the backend.

POST /runs returns at once; the run happens on a worker thread. Progress goes to the backend as
events, which it stores in agent_events and uses to move the task through its states:

    run_started                       the task is RUNNING
    step_started {step}               planner / analyst / coder began
    tool_called {role, tool, ...}     one tool call, allowed or refused
    step_finished {step, output}      a step's result (plan, analysis, coder summary)
    run_completed {..., diff}         a diff is ready for a person to look at
    run_failed {error, step}          the run stopped; the reason is shown on the task
"""

import hashlib
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

import httpx
from langgraph.checkpoint.sqlite import SqliteSaver

from agents.tool_agent import AgentError
from config import settings
from graph import RunContext, build_graph
from llm import LLMError, LLMProvider
from tools import files
from tools.registry import ToolCall, ToolRegistry
from tools.workspace import ToolError, create_workspace

logger = logging.getLogger("mantis.runs")

# Long arguments (a whole file passed to write_file) are cut in events; the diff shows the real change.
MAX_EVENT_ARG_CHARS = 300


class EventSink(Protocol):
    def emit(self, event_type: str, **payload: Any) -> None: ...


class HttpEventSink:
    """Sends events to the backend's internal endpoint, authenticated with the shared token."""

    # A run's last event decides the task's final state, so it is retried; progress events are not.
    FINAL_EVENTS = {"run_completed", "run_failed"}

    def __init__(self, task_id: str, base_url: str, token: str):
        self.url = f"{base_url.rstrip('/')}/internal/tasks/{task_id}/events"
        self.headers = {"X-Internal-Token": token}

    def emit(self, event_type: str, **payload: Any) -> None:
        attempts = 3 if event_type in self.FINAL_EVENTS else 1
        for attempt in range(1, attempts + 1):
            try:
                response = httpx.post(
                    self.url, json={"type": event_type, "payload": payload}, headers=self.headers, timeout=10
                )
                response.raise_for_status()
                return
            except httpx.HTTPError as e:
                logger.warning("Could not report %s (attempt %d/%d): %s", event_type, attempt, attempts, e)
                if attempt < attempts:
                    time.sleep(attempt)


def describe_tool_call(call: ToolCall) -> dict:
    """A tool call as an event: what was asked, whether it was allowed, and a one-line result."""
    args = {k: (v[:MAX_EVENT_ARG_CHARS] + "…" if isinstance(v, str) and len(v) > MAX_EVENT_ARG_CHARS else v) for k, v in call.args.items()}
    return {
        "role": call.role,
        "tool": call.tool,
        "args": args,
        "ok": call.ok,
        "error": call.error,
        "summary": _summarise(call) if call.ok else None,
        "duration_ms": call.duration_ms,
    }


def _summarise(call: ToolCall) -> str:
    r = call.result or {}
    match call.tool:
        case "list_files":
            return f"{len(r['files'])} files" + (" (truncated)" if r["truncated"] else "")
        case "read_file":
            return f"lines {r['start_line']}-{r['end_line']} of {r['total_lines']}"
        case "search_code":
            return f"{len(r['matches'])} matches" + (" (truncated)" if r["truncated"] else "")
        case "write_file":
            return f"{'created' if r['created'] else 'updated'}, {r['bytes']} bytes"
        case "git_status":
            return f"{len(r['changes'])} changed files"
        case "git_diff":
            return f"{r['diff'].count(chr(10))} diff lines"
    return ""


def execute_run(
    task_id: str,
    user_request: str,
    llm: LLMProvider,
    events: EventSink,
    *,
    repo: Path | None = None,
    workspaces_dir: Path | None = None,
    checkpointer: Any = None,
) -> None:
    """Run one task from start to finish on the calling thread. Never raises: every outcome is an event."""
    events.emit("run_started")
    ctx: RunContext | None = None
    try:
        workspace = create_workspace(repo or settings.default_repo_path, task_id, workspaces_dir or settings.workspaces_dir)
        registry = ToolRegistry(workspace, listener=lambda call: events.emit("tool_called", **describe_tool_call(call)))
        ctx = RunContext(
            task_id=task_id,
            llm=llm,
            registry=registry,
            emit=events.emit,
            analyst_max_turns=settings.analyst_max_turns,
            coder_max_turns=settings.coder_max_turns,
        )
        graph = build_graph(ctx, checkpointer)
        final = graph.invoke(
            {"task_id": task_id, "user_request": user_request},
            config={"configurable": {"thread_id": task_id}},
        )

        diff = files.git_diff(workspace)
        events.emit(
            "run_completed",
            plan=final["implementation_plan"],
            analysis=final["repository_info"],
            changes={
                **final["proposed_changes"],
                "diff": diff["diff"],
                "diff_truncated": diff["truncated"],
                # Identifies this exact diff; Phase 7 binds approvals to it.
                "diff_hash": hashlib.sha256(diff["diff"].encode()).hexdigest(),
            },
            model=llm.model_name,
        )
    except (LLMError, AgentError, ToolError) as e:
        logger.warning("Run %s failed: %s", task_id, e)
        events.emit("run_failed", error=str(e), step=ctx.current_step if ctx else None)
    except Exception:
        logger.exception("Run %s crashed", task_id)
        events.emit(
            "run_failed",
            error="Unexpected error in the AI service (details are in its log)",
            step=ctx.current_step if ctx else None,
        )


def report_interrupted_runs(base_url: str, token: str) -> None:
    """Tell the backend this service just started, so tasks whose runs died with the previous
    process are marked failed instead of showing RUNNING forever. Never raises: if the backend
    is down, the service still starts."""
    try:
        response = httpx.post(
            f"{base_url.rstrip('/')}/internal/runs/interrupted", headers={"X-Internal-Token": token}, timeout=5
        )
        response.raise_for_status()
        count = response.json().get("interrupted", 0)
        if count:
            logger.warning("Marked %d unfinished task(s) as interrupted by the restart", count)
    except (httpx.HTTPError, ValueError) as e:
        logger.warning("Could not report interrupted runs to the backend: %s", e)


@lru_cache
def get_checkpointer() -> SqliteSaver:
    settings.workspaces_dir.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: runs happen on worker threads; SqliteSaver locks around each write.
    connection = sqlite3.connect(settings.checkpoint_db, check_same_thread=False)
    return SqliteSaver(connection)


class Runner:
    """Starts runs on a small thread pool and refuses a second run for a task that is already running."""

    def __init__(
        self,
        llm_factory: Callable[[], LLMProvider],
        sink_factory: Callable[[str], EventSink],
        max_workers: int,
        *,
        workspaces_dir: Path | None = None,
        checkpointer_factory: Callable[[], Any] = lambda: get_checkpointer(),
    ):
        self._llm_factory = llm_factory
        self._sink_factory = sink_factory
        self._workspaces_dir = workspaces_dir
        self._checkpointer_factory = checkpointer_factory
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="run")
        self._active: set[str] = set()
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Block until every submitted run has finished (used by tests)."""
        self._pool.shutdown(wait=True)

    def submit(self, task_id: str, user_request: str) -> bool:
        with self._lock:
            if task_id in self._active:
                return False
            self._active.add(task_id)
        llm = self._llm_factory()
        events = self._sink_factory(task_id)

        def work() -> None:
            try:
                execute_run(
                    task_id,
                    user_request,
                    llm,
                    events,
                    workspaces_dir=self._workspaces_dir,
                    checkpointer=self._checkpointer_factory(),
                )
            finally:
                with self._lock:
                    self._active.discard(task_id)

        self._pool.submit(work)
        return True


@lru_cache
def get_runner() -> Runner:
    """The service's runner. Tests replace it via FastAPI's dependency_overrides."""
    from llm import get_llm

    return Runner(
        llm_factory=get_llm,
        sink_factory=lambda task_id: HttpEventSink(task_id, settings.backend_url, settings.internal_token),
        max_workers=settings.max_concurrent_runs,
    )

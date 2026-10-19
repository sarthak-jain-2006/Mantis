import json

import pytest
from pydantic import BaseModel

from agents.tool_agent import MAX_TOOL_RESULT_CHARS, AgentError, SubmitTool, run_tool_agent
from conftest import FakeLLM, ask
from llm import Message

START = [Message("system", "You explore the repository."), Message("user", "Find the products endpoint.")]


class Answer(BaseModel):
    path: str


def submit_tool(check=lambda _: None):
    return SubmitTool(name="submit_answer", description="Finish.", schema=Answer, check=check)


def tool_replies(llm, turn_index):
    """The tool results the model saw at the start of a given turn."""
    messages, _ = llm.tool_conversations[turn_index]
    return [json.loads(m.content) for m in messages if m.role == "tool"]


def test_runs_tools_until_the_agent_submits(registry):
    llm = FakeLLM(
        turns=[
            ask(("read_file", {"path": "app/routers/products.py", "end_line": 1})),
            ask(("submit_answer", {"path": "app/routers/products.py"})),
        ]
    )
    result = run_tool_agent(llm, registry, "analyst", START, submit_tool(), max_turns=5)

    assert result == Answer(path="app/routers/products.py")
    first_result = tool_replies(llm, 1)[0]
    assert first_result["ok"] is True
    assert first_result["result"]["content"] == "from fastapi import APIRouter, HTTPException\n"


def test_the_agent_is_offered_only_its_roles_tools(registry):
    llm = FakeLLM(turns=[ask(("submit_answer", {"path": "x"}))])
    run_tool_agent(llm, registry, "debugger", START, submit_tool(), max_turns=1)
    _, offered = llm.tool_conversations[0]
    assert offered == ["git_diff", "read_file", "submit_answer"]


def test_a_refused_tool_call_is_reported_back_not_raised(registry):
    llm = FakeLLM(
        turns=[
            ask(("write_file", {"path": "app/main.py", "content": "# hacked"})),
            ask(("submit_answer", {"path": "app/main.py"})),
        ]
    )
    run_tool_agent(llm, registry, "analyst", START, submit_tool(), max_turns=5)

    assert tool_replies(llm, 1)[0] == {"ok": False, "error": "The analyst agent is not allowed to use write_file"}
    assert "hacked" not in (registry.workspace.root / "app" / "main.py").read_text()


def test_an_invalid_submission_is_sent_back_to_be_fixed(registry):
    llm = FakeLLM(turns=[ask(("submit_answer", {"wrong": 1})), ask(("submit_answer", {"path": "app/main.py"}))])
    assert run_tool_agent(llm, registry, "analyst", START, submit_tool(), max_turns=5).path == "app/main.py"
    reply = tool_replies(llm, 1)[0]
    assert reply["ok"] is False
    assert "path: Field required" in reply["error"]


def test_the_submit_check_can_reject_a_result(registry):
    def must_exist(answer):
        return None if (registry.workspace.root / answer.path).exists() else f"{answer.path} does not exist"

    llm = FakeLLM(turns=[ask(("submit_answer", {"path": "nope.py"})), ask(("submit_answer", {"path": "app/main.py"}))])
    run_tool_agent(llm, registry, "analyst", START, submit_tool(must_exist), max_turns=5)
    assert tool_replies(llm, 1)[0] == {"ok": False, "error": "Not accepted: nope.py does not exist"}


def test_every_tool_call_in_a_turn_gets_an_answer(registry):
    llm = FakeLLM(
        turns=[
            ask(("list_files", {"path": "app"}), ("read_file", {"path": "app/main.py"})),
            ask(("submit_answer", {"path": "app/main.py"})),
        ]
    )
    run_tool_agent(llm, registry, "analyst", START, submit_tool(), max_turns=5)
    messages, _ = llm.tool_conversations[1]
    assert [(m.tool_call_id, m.name) for m in messages if m.role == "tool"] == [
        ("call-0", "list_files"),
        ("call-1", "read_file"),
    ]


def test_a_turn_without_tool_calls_gets_a_nudge(registry):
    llm = FakeLLM(turns=[ask(), ask(("submit_answer", {"path": "app/main.py"}))])
    run_tool_agent(llm, registry, "analyst", START, submit_tool(), max_turns=5)
    messages, _ = llm.tool_conversations[1]
    assert messages[-1] == Message("user", "Reply by calling a tool. Call submit_answer when you are done.")


def test_the_turn_budget_stops_a_looping_agent(registry):
    llm = FakeLLM(turns=[ask(("list_files", {"path": "."}))] * 3)
    with pytest.raises(AgentError, match="The analyst did not finish within 3 turns"):
        run_tool_agent(llm, registry, "analyst", START, submit_tool(), max_turns=3)


def test_huge_tool_results_are_cut(registry):
    (registry.workspace.root / "big.txt").write_text("x" * (MAX_TOOL_RESULT_CHARS * 2))
    llm = FakeLLM(turns=[ask(("read_file", {"path": "big.txt"})), ask(("submit_answer", {"path": "big.txt"}))])
    run_tool_agent(llm, registry, "analyst", START, submit_tool(), max_turns=5)

    messages, _ = llm.tool_conversations[1]
    reply = next(m for m in messages if m.role == "tool").content
    assert len(reply) < MAX_TOOL_RESULT_CHARS + 100
    assert reply.endswith("[result cut: too long; ask for a smaller range]")

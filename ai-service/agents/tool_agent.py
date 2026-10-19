"""The loop every tool-using agent runs.

1. Send the conversation and the agent's tools to the model.
2. The model asks for one or more tool calls. Each goes through the ToolRegistry, which applies the
   role's permissions and the path guard, and the result is added to the conversation.
3. Repeat until the model calls the agent's "submit" tool with a valid result, or the turn budget runs out.

Submitting is itself a tool call, so the result arrives as checked JSON, never as free text.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from llm import LLMProvider, Message, ToolSpec, call_tools
from tools.registry import ToolCall, ToolRegistry

T = TypeVar("T", bound=BaseModel)

# A tool result longer than this is cut before it goes back to the model, to keep prompts small.
MAX_TOOL_RESULT_CHARS = 20_000


class AgentError(Exception):
    """An agent could not finish its step. The message is shown on the failed task."""


@dataclass(frozen=True)
class SubmitTool(Generic[T]):
    name: str
    description: str
    schema: type[T]
    # Plain-code checks on the submitted result, e.g. "do these files exist?".
    # Returns a problem to send back to the model, or None to accept.
    check: Callable[[T], str | None] = lambda _: None


def run_tool_agent(
    llm: LLMProvider,
    registry: ToolRegistry,
    role: str,
    messages: list[Message],
    submit: SubmitTool[T],
    max_turns: int,
) -> T:
    tools = [ToolSpec(t.name, t.description, t.args) for t in registry.tools_for(role)]  # type: ignore[arg-type]
    tools.append(ToolSpec(submit.name, submit.description, submit.schema))
    history = list(messages)

    for _ in range(max_turns):
        turn = call_tools(llm, history, tools)
        history.append(Message("assistant", turn.text, tool_calls=turn.tool_calls, raw=turn.raw))
        if not turn.tool_calls:
            history.append(Message("user", f"Reply by calling a tool. Call {submit.name} when you are done."))
            continue

        accepted: T | None = None
        # Every call gets an answer, even after an accepted submit: the model API expects one per call.
        for request in turn.tool_calls:
            if request.name == submit.name:
                result, reply = _check_submission(submit, request.args)
                if result is not None:
                    accepted = result
            else:
                reply = _tool_reply(registry.call(role, request.name, request.args))
            history.append(Message("tool", reply, tool_call_id=request.id, name=request.name))
        if accepted is not None:
            return accepted

    raise AgentError(f"The {role} did not finish within {max_turns} turns")


def _check_submission(submit: SubmitTool[T], args: dict) -> tuple[T | None, str]:
    try:
        result = submit.schema.model_validate(args)
    except ValidationError as e:
        problems = "; ".join(f"{'.'.join(map(str, p['loc'])) or 'args'}: {p['msg']}" for p in e.errors())
        return None, json.dumps({"ok": False, "error": f"Not accepted: {problems}. Fix it and call {submit.name} again."})
    problem = submit.check(result)
    if problem:
        return None, json.dumps({"ok": False, "error": f"Not accepted: {problem}"})
    return result, json.dumps({"ok": True, "result": "Accepted."})


def _tool_reply(call: ToolCall) -> str:
    body = {"ok": True, "result": call.result} if call.ok else {"ok": False, "error": call.error}
    text = json.dumps(body)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + '..." [result cut: too long; ask for a smaller range]'
    return text

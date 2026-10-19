"""The agent workflow as a LangGraph graph: Planner -> Analyst -> Coder.

Each node reads what it needs from the shared state and returns only the fields it owns.
LangGraph merges those into the state and, with a checkpointer, saves it after every node,
keyed by task id, so later phases can pause a run for approval and resume it.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from agents.analyst import AnalysisReport, analyse
from agents.coder import write_code
from llm import LLMProvider
from planner import plan_task
from schemas import ImplementationPlan
from tools import files
from tools.registry import ToolRegistry


class AgentState(TypedDict, total=False):
    # Kept small and serialisable: plans and summaries, never whole files or diffs.
    # The diff itself stays in the workspace and is read from git when needed.
    task_id: str
    user_request: str
    implementation_plan: dict
    repository_info: dict
    proposed_changes: dict


@dataclass
class RunContext:
    task_id: str
    llm: LLMProvider
    registry: ToolRegistry
    # Reports progress; see runs.py for what each event means.
    emit: Callable[..., None]
    analyst_max_turns: int
    coder_max_turns: int
    current_step: str | None = None

    def start(self, step: str) -> None:
        self.current_step = step
        self.emit("step_started", step=step)


def build_graph(ctx: RunContext, checkpointer: Any = None):
    def planner(state: AgentState) -> AgentState:
        ctx.start("planner")
        plan = plan_task(ctx.llm, state["user_request"])
        ctx.emit("step_finished", step="planner", output=plan.model_dump())
        return {"implementation_plan": plan.model_dump()}

    def analyst(state: AgentState) -> AgentState:
        ctx.start("analyst")
        plan = ImplementationPlan.model_validate(state["implementation_plan"])
        report = analyse(ctx.llm, ctx.registry, state["user_request"], plan, ctx.analyst_max_turns)
        ctx.emit("step_finished", step="analyst", output=report.model_dump())
        return {"repository_info": report.model_dump()}

    def coder(state: AgentState) -> AgentState:
        ctx.start("coder")
        result = write_code(
            ctx.llm,
            ctx.registry,
            state["user_request"],
            ImplementationPlan.model_validate(state["implementation_plan"]),
            AnalysisReport.model_validate(state["repository_info"]),
            ctx.coder_max_turns,
        )
        # Which files changed is read from git, not taken from the model's word.
        changed = files.git_status(ctx.registry.workspace)["changes"]
        output = {**result.model_dump(), "changed_files": changed}
        ctx.emit("step_finished", step="coder", output=output)
        return {"proposed_changes": output}

    graph = StateGraph(AgentState)
    graph.add_node("planner", planner)
    graph.add_node("analyst", analyst)
    graph.add_node("coder", coder)
    graph.add_edge(START, "planner")
    graph.add_edge("planner", "analyst")
    graph.add_edge("analyst", "coder")
    graph.add_edge("coder", END)
    return graph.compile(checkpointer=checkpointer)

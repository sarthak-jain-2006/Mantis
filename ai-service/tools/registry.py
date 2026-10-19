"""The one door agents use to run tools.

An agent asks for a tool by name with JSON arguments. The registry checks, in order: the tool
exists, this agent's role may use it, the arguments are valid. Only then does the tool run.
Every call, allowed or refused, comes back as a ToolCall record (saved to the database from Phase 4).
"""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from tools import files, search
from tools.workspace import ToolError, Workspace

logger = logging.getLogger("mantis.tools")

Role = Literal["planner", "analyst", "research", "coder", "testing", "debugger", "reviewer"]


class _Args(BaseModel):
    # An unexpected argument is an error rather than silently ignored.
    model_config = ConfigDict(extra="forbid")


class ListFilesArgs(_Args):
    path: str = Field(default=".", description="Folder to list, relative to the repository root.")


class ReadFileArgs(_Args):
    path: str = Field(description="File to read, relative to the repository root.")
    start_line: int | None = Field(default=None, ge=1, description="First line to return (1-based).")
    end_line: int | None = Field(default=None, ge=1, description="Last line to return (inclusive).")


class SearchCodeArgs(_Args):
    pattern: str = Field(min_length=1, max_length=500, description="Regular expression (or plain text) to find.")
    path: str = Field(default=".", description="Folder or file to search in.")
    fixed_string: bool = Field(default=False, description="Treat the pattern as plain text, not a regex.")


class WriteFileArgs(_Args):
    path: str = Field(description="File to create or replace, relative to the repository root.")
    content: str = Field(description="The complete new content of the file.")


class GitStatusArgs(_Args):
    pass


class GitDiffArgs(_Args):
    path: str | None = Field(default=None, description="Limit the diff to this file or folder.")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[_Args]
    run: Callable[[Workspace, Any], dict]


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in [
        Tool("list_files", "List the files under a folder.", ListFilesArgs,
             lambda ws, a: files.list_files(ws, a.path)),
        Tool("read_file", "Read a text file, optionally only a range of lines.", ReadFileArgs,
             lambda ws, a: files.read_file(ws, a.path, a.start_line, a.end_line)),
        Tool("search_code", "Search the code for a pattern; returns matching lines with file and line number.",
             SearchCodeArgs, lambda ws, a: search.search_code(ws, a.pattern, a.path, a.fixed_string)),
        Tool("write_file", "Create a file or replace its whole content.", WriteFileArgs,
             lambda ws, a: files.write_file(ws, a.path, a.content)),
        Tool("git_status", "List the files changed since the task started.", GitStatusArgs,
             lambda ws, a: files.git_status(ws)),
        Tool("git_diff", "Show the line-by-line changes made since the task started.", GitDiffArgs,
             lambda ws, a: files.git_diff(ws, a.path)),
    ]
}  # fmt: skip

# Which tools each agent role may call, from the design doc. Enforced here in code: a prompt can
# ask for anything, but a role can only ever run what is listed.
ROLE_TOOLS: dict[Role, frozenset[str]] = {
    "planner": frozenset(),  # reasoning only
    "analyst": frozenset({"list_files", "read_file", "search_code", "git_status"}),
    "research": frozenset(),  # search_docs arrives with RAG (Phase 10)
    "coder": frozenset({"read_file", "search_code", "write_file", "git_diff"}),
    "testing": frozenset(),  # run_tests and run_linter arrive with the sandbox (Phase 5)
    "debugger": frozenset({"read_file", "git_diff"}),  # can look, never write
    "reviewer": frozenset({"read_file", "git_diff"}),
}


@dataclass(frozen=True)
class ToolCall:
    role: str
    tool: str
    args: dict
    ok: bool
    result: dict | None
    error: str | None
    duration_ms: int


class ToolRegistry:
    def __init__(self, workspace: Workspace, listener: Callable[[ToolCall], None] | None = None):
        self.workspace = workspace
        # Called with every ToolCall record, so a run can report each tool use as it happens.
        self.listener = listener

    def tools_for(self, role: Role) -> list[Tool]:
        return [TOOLS[name] for name in sorted(ROLE_TOOLS.get(role, frozenset()))]

    def call(self, role: str, tool_name: str, args: dict | None = None) -> ToolCall:
        args = args or {}
        started = time.perf_counter()
        result, error = None, None
        try:
            tool = TOOLS.get(tool_name)
            if tool is None:
                raise ToolError(f"Unknown tool: {tool_name!r}")
            if tool_name not in ROLE_TOOLS.get(role, frozenset()):  # type: ignore[call-overload]
                raise ToolError(f"The {role} agent is not allowed to use {tool_name}")
            try:
                parsed = tool.args.model_validate(args)
            except ValidationError as e:
                problems = "; ".join(f"{'.'.join(map(str, p['loc'])) or 'args'}: {p['msg']}" for p in e.errors())
                raise ToolError(f"Invalid arguments for {tool_name}: {problems}") from e
            result = tool.run(self.workspace, parsed)
        except ToolError as e:
            error = str(e)
        except OSError as e:
            # e.g. a file locked by another program. Report it without the host path.
            error = f"{tool_name} failed: {e.strerror or e}"

        call = ToolCall(
            role=role,
            tool=tool_name,
            args=args,
            ok=error is None,
            result=result,
            error=error,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )
        logger.info("tool %s by %s: %s (%d ms)", tool_name, role, "ok" if call.ok else f"refused: {error}", call.duration_ms)
        if self.listener:
            self.listener(call)
        return call

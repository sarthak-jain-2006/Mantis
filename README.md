# Mantis

An AI software engineer: it plans a change to a Python repository, edits the code, runs the real tests in a Docker sandbox, debugs failures, reviews its own diff, and waits for human approval.

> Status: Phase 4 (first agent graph) done: a task goes Planner → Analyst → Coder and comes back as a real diff on a copy of the repository. Phase 5 (Docker sandbox, running the tests) is next.

## Architecture

```
Browser (React :5173) → Node backend (Express :3000) → Python AI service (FastAPI :8000)
                                 ↓
                     PostgreSQL + pgvector (Docker :5433)
```

| Folder | What it is |
| --- | --- |
| `frontend/` | React + TypeScript + Vite + Tailwind dashboard |
| `backend/` | Node.js + TypeScript + Express + Prisma API |
| `ai-service/` | Python + FastAPI + LangGraph: the agents, the model layer and the tool layer |
| `sample-repos/` | Small Python projects Mantis practises on ([details](sample-repos/README.md)) |
| `workspaces/` | Per-task copies of a repository, created at runtime (gitignored) |

## Prerequisites

- Node.js 20+
- Python 3.12 via [uv](https://docs.astral.sh/uv/)
- Docker Desktop
- Git and [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) on PATH, used by the repository tools

## Getting started

```bash
cp .env.example .env        # then set INTERNAL_TOKEN to a long random value (32+ characters)
                            # and GEMINI_API_KEY to a free key from https://aistudio.google.com/apikey
docker compose up -d        # start Postgres
```

Then start each service in its own terminal:

```bash
# AI service on :8000
cd ai-service && uv sync && uv run uvicorn main:app --reload --port 8000

# Backend on :3000 (installing also generates the Prisma client)
cd backend && npm install && npm run db:migrate && npm run dev

# Dashboard on :5173 (proxies /api to the backend)
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. The header shows whether the backend, database and AI service are up. Submit a task about the sample shop (for example "Fix the bug where a product can be created with a negative price, and add a test"). The task shows which step it is on, then the plan, the files Mantis found, the proposed diff and an activity log of every tool call. A run usually takes under a minute.

After pulling new code, run `npm run db:migrate` in `backend/` again to apply new database migrations.

## How a run works

1. The backend saves the task (`QUEUED`) and asks the AI service to start a run (`POST /runs`), which answers at once with 202.
2. The AI service copies the target repository (`sample-repos/fastapi-shop` for now) into `workspaces/<task_id>/` and runs a LangGraph graph ([ai-service/graph.py](ai-service/graph.py)):
   - **Planner** ([planner.py](ai-service/planner.py)) turns the request into a plan: steps, acceptance criteria, constraints.
   - **Analyst** ([agents/analyst.py](ai-service/agents/analyst.py)) searches and reads the code with the tools, then submits the relevant files, tests and conventions. Files it names must exist.
   - **Coder** ([agents/coder.py](ai-service/agents/coder.py)) starts with those files, writes the change and the tests, checks `git_diff`, and submits a summary. It can't submit until something has changed.
3. Each agent works in a loop ([agents/tool_agent.py](ai-service/agents/tool_agent.py)): Gemini requests tools, every request goes through the tool registry (role permissions, path guard), and the result goes back to the model. An agent finishes only by calling its submit tool with valid output, within a turn budget.
4. Progress goes to the backend as events (`POST /internal/tasks/:id/events`, protected by the internal token): step started or finished, every tool call, completion or failure. They are stored in `agent_events` and move the task through `RUNNING` (with the current step) to `AWAITING_APPROVAL` with the diff, or `FAILED` with the reason.
5. The dashboard checks for progress every 2 seconds while a task runs and shows the plan, the files found, the diff and an activity log.

Runs live inside the AI service process. If it stops or reloads mid-run (for example `--reload` after a code change), it tells the backend when it starts again, and any task still `QUEUED` or `RUNNING` is marked `FAILED` with "Interrupted: the AI service restarted during the run". Submit those tasks again.

A finished run stops at `AWAITING_APPROVAL`, not `COMPLETED`: running the tests (Phase 5) and approving the diff (Phase 7) come first. LangGraph saves the run's state after every step (`workspaces/checkpoints.sqlite`) so a later phase can pause and resume runs.

### Model calls and the free tier

[ai-service/llm.py](ai-service/llm.py) keeps model calls safe and within quota:

- **Structured output.** Plans and submissions are checked against Pydantic schemas. Bad output gets one repair attempt.
- **Rate limiter.** At most `LLM_REQUESTS_PER_MINUTE` calls per minute across all runs. Calls over the limit wait their turn instead of failing.
- **Back-off.** A 429 is retried after the wait Gemini asks for. A temporary outage ("high demand", 503) is retried after 5s, then 10s. A used-up **daily** quota fails at once with a clear message.
- **Speed.** `LLM_REASONING_EFFORT=low` keeps a tool-calling turn to a few seconds. Higher levels were measured at 20–50s per turn.

> **Free-tier quotas are per model and per day.** On this project's free tier, `gemini-flash-latest` allowed 20 requests a day, while one task makes about 10–20 calls. The default is therefore `gemini-flash-lite-latest`, which has its own allowance. Check your limits in [AI Studio](https://aistudio.google.com/).

The model sits behind a small `LLMProvider` interface, so another provider (such as Ollama) can be added without changing the agents.

## Repository tools

Agents never touch the filesystem, git or a shell directly. They ask the tool layer ([ai-service/tools/](ai-service/tools/)) to run a named tool, and it checks the request before anything runs.

| Tool | What it does |
| --- | --- |
| `list_files` | List the files under a folder |
| `read_file` | Read a text file, optionally a range of lines |
| `search_code` | Search the code with ripgrep |
| `write_file` | Create or replace a file |
| `git_status` | List files changed since the task started |
| `git_diff` | Show the line-by-line changes since the task started |

**Workspace.** Each task works on its own copy under `workspaces/<task_id>/`, with a fresh git baseline, so the diff is exactly what the agents changed and the original repository is never edited. Secrets (`.env`, keys), dependencies and the original git history are not copied.

**Path guard.** Every path must stay inside the workspace. Absolute paths, `..`, drive letters, and symlinks or junctions pointing outside are refused, as are `.git`, `node_modules` and secret files (including Windows spellings like `.GIT` or `.env.`). Writing CI configuration (`.github/` and similar) needs an explicit flag. Reads, writes, search results and diffs have size caps.

**Permissions by role.** The registry ([ai-service/tools/registry.py](ai-service/tools/registry.py)) enforces which tools each agent may call: only the Coder can write; the Debugger and Reviewer can read and diff. There is deliberately no tool that runs arbitrary commands.

**Git isolation.** Git runs pinned to the workspace's own repository and ignores your personal git config, so signing prompts, hooks and aliases never interfere.

To watch the tools work on a copy of `sample-repos/fastapi-shop`, including refused requests:

```bash
cd ai-service && uv run python scripts/try_tools.py          # add --keep to keep the copy under workspaces/
```

## Tests

```bash
cd ai-service && uv run pytest      # API, agents, graph, LLM retry rules and tool guards (scripted fake model,
                                    # no API key or quota used; search tests are skipped if rg isn't installed)
cd backend && npm test              # needs Postgres running (with migrations applied)
cd frontend && npm test             # UI with a faked backend
```

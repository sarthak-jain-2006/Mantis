# Mantis

An AI software engineer: it plans a change to a Python repository, edits the code, runs the real tests in a Docker sandbox, debugs failures, reviews its own diff, and waits for human approval.

> Status: Phase 3 (repository tools) done: agents can safely list, read, search and edit a task's own copy of a repository. Phase 4 (first agent graph) is next.

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
| `ai-service/` | Python + FastAPI: the LLM planner and the tool layer (LangGraph from Phase 4) |
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

Open http://localhost:5173. The header shows whether the backend, database and AI service are up. Submitting a task saves it, asks Gemini to plan it, and shows the plan: a summary, ordered steps, acceptance criteria, constraints and whether documentation lookup is needed. Planning takes a few seconds.

## How planning works

1. The backend saves the task and sends its text to the AI service (`POST /runs`).
2. The Planner ([ai-service/planner.py](ai-service/planner.py)) asks Gemini for JSON matching the `ImplementationPlan` schema ([ai-service/schemas.py](ai-service/schemas.py)).
3. [ai-service/llm.py](ai-service/llm.py) checks the reply against the schema. A reply that doesn't match gets one repair attempt; a second bad reply fails the task. Rate-limited calls (HTTP 429) are retried with back-off (2s, 4s), and each call times out after `LLM_TIMEOUT_SECONDS`.
4. The plan is stored on the task. Any failure leaves the task `FAILED` with the reason, for example "Gemini rate limit reached".

The model sits behind a small `LLMProvider` interface, so another provider (such as Ollama) can be added without changing the Planner.

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
cd ai-service && uv run pytest      # API, planner, LLM retry rules and tool guards (fake model, no API key needed;
                                    # search tests are skipped if rg isn't installed)
cd backend && npm test              # needs Postgres running
cd frontend && npm test             # UI with a faked backend
```

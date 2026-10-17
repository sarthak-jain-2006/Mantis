# Mantis

An AI software engineer: it plans a change to a Python repository, edits the code, runs the real tests in a Docker sandbox, debugs failures, reviews its own diff, and waits for human approval.

> Status: Phase 2 (LLM planning) done: a task description comes back as a validated plan from Gemini. Phase 3 (repository tools) is next.

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
| `ai-service/` | Python + FastAPI (+ LangGraph from Phase 2) |

## Prerequisites

- Node.js 20+
- Python 3.12 via [uv](https://docs.astral.sh/uv/)
- Docker Desktop

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

## Tests

```bash
cd ai-service && uv run pytest      # API, token checks, planner and LLM retry rules (fake model, no API key needed)
cd backend && npm test              # needs Postgres running
cd frontend && npm test             # UI with a faked backend
```

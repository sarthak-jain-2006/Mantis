# Mantis

An AI software engineer: it plans a change to a Python repository, edits the code, runs the real tests in a Docker sandbox, debugs failures, reviews its own diff, and waits for human approval.

> Status: Phase 1 (foundation) done: browser → Node → Python round trip works. Phase 2 (LLM planning) is next.

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

Open http://localhost:5173. The header shows whether the backend, database and AI service are up; submitting a task saves it and shows the AI service's reply.

## Tests

```bash
cd ai-service && uv run pytest      # API and token checks
cd backend && npm test              # needs Postgres running
cd frontend && npm test             # UI with a faked backend
```

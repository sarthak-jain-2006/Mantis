# Mantis

An AI software engineer: it plans a change to a Python repository, edits the code, runs the real tests in a Docker sandbox, debugs failures, reviews its own diff, and waits for human approval.

> Status: Phase 1 (foundation) in progress.

## Architecture

```
Browser (React :5173) → Node backend (Express) → Python AI service (FastAPI :8000)
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
cp .env.example .env        # then set INTERNAL_TOKEN to a long random value
docker compose up -d        # start Postgres
```

Service setup steps are added as each phase lands.

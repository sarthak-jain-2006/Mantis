import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status

import runs
from config import settings
from runs import Runner, get_runner
from schemas import RunAccepted, RunRequest
from security import require_internal_token

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    # Runs before the service accepts requests. Any run of the previous process (stopped,
    # crashed or reloaded) is gone, so its task is marked failed rather than left RUNNING.
    await asyncio.to_thread(runs.report_interrupted_runs, settings.backend_url, settings.internal_token)
    yield


app = FastAPI(title="Mantis AI service", version="0.4.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ai-service"}


@app.post(
    "/runs",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=RunAccepted,
    dependencies=[Depends(require_internal_token)],
)
def start_run(body: RunRequest, runner: Runner = Depends(get_runner)) -> RunAccepted:
    # Starts Planner -> Analyst -> Coder in the background and answers at once (202 Accepted).
    # A run makes many model calls and can take minutes; progress reaches the backend as events.
    if not runner.submit(body.task_id, body.user_request):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="This task is already running")
    return RunAccepted(task_id=body.task_id, status="accepted")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=settings.ai_service_port, reload=True)

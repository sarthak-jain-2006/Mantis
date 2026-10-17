import logging

from fastapi import Depends, FastAPI, HTTPException, status

from config import settings
from llm import LLMError, LLMProvider, LLMRateLimitError, LLMTimeoutError, LLMUnavailableError, get_llm
from planner import plan_task
from schemas import RunRequest, RunResponse
from security import require_internal_token

logger = logging.getLogger("mantis.ai")

app = FastAPI(title="Mantis AI service", version="0.2.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ai-service"}


# Which HTTP status each model failure becomes. The backend shows the message on the task.
_ERROR_STATUS: list[tuple[type[LLMError], int]] = [
    (LLMRateLimitError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (LLMUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
    (LLMTimeoutError, status.HTTP_504_GATEWAY_TIMEOUT),
]


@app.post("/runs", response_model=RunResponse, dependencies=[Depends(require_internal_token)])
def start_run(body: RunRequest, llm: LLMProvider = Depends(get_llm)) -> RunResponse:
    # Phase 2: plan the task and return the plan. Phase 4 turns this into a LangGraph run
    # that goes on to analyse the repo and write code.
    # A plain `def` (not `async def`): FastAPI runs it in a worker thread, so a slow
    # model call doesn't block other requests such as /health.
    try:
        plan = plan_task(llm, body.user_request)
    except LLMError as e:
        code = next((c for kind, c in _ERROR_STATUS if isinstance(e, kind)), status.HTTP_502_BAD_GATEWAY)
        logger.warning("Planning failed for task %s: %s", body.task_id, e)
        raise HTTPException(status_code=code, detail=str(e)) from e

    return RunResponse(task_id=body.task_id, status="planned", plan=plan, model=llm.model_name)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=settings.ai_service_port, reload=True)

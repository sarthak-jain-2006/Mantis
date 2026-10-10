from fastapi import Depends, FastAPI

from config import settings
from schemas import RunRequest, RunResponse
from security import require_internal_token

app = FastAPI(title="Mantis AI service", version="0.1.0")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "ai-service"}


@app.post("/runs", response_model=RunResponse, dependencies=[Depends(require_internal_token)])
def start_run(body: RunRequest) -> RunResponse:
    # Phase 1 stub: echo the task back so the full Browser -> Node -> Python path can be tested.
    # Phase 2 replaces this with an LLM call; Phase 4 with a LangGraph run.
    return RunResponse(
        task_id=body.task_id,
        status="received",
        message=f"AI service received: {body.user_request}",
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=settings.ai_service_port, reload=True)

import pytest
from fastapi.testclient import TestClient

from conftest import SAMPLE_PLAN, TEST_TOKEN, FakeLLM
from llm import LLMError, LLMRateLimitError, LLMTimeoutError, LLMUnavailableError, get_llm
from main import app

client = TestClient(app)

VALID_BODY = {"task_id": "task-1", "user_request": "Add pagination to /products"}
AUTH = {"X-Internal-Token": TEST_TOKEN}


@pytest.fixture
def fake_llm():
    """Replace the real Gemini provider with a fake for one test."""

    def use(*replies):
        fake = FakeLLM(*replies)
        app.dependency_overrides[get_llm] = lambda: fake
        return fake

    yield use
    app.dependency_overrides.clear()


def test_health_is_public():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ai-service"}


def test_runs_rejects_missing_token(fake_llm):
    fake = fake_llm(SAMPLE_PLAN)
    response = client.post("/runs", json=VALID_BODY)
    assert response.status_code == 401
    assert fake.calls == []  # rejected before the model is called


def test_runs_rejects_wrong_token(fake_llm):
    fake_llm(SAMPLE_PLAN)
    response = client.post("/runs", json=VALID_BODY, headers={"X-Internal-Token": "wrong"})
    assert response.status_code == 401


def test_runs_returns_a_plan(fake_llm):
    fake = fake_llm(SAMPLE_PLAN)
    response = client.post("/runs", json=VALID_BODY, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-1",
        "status": "planned",
        "plan": SAMPLE_PLAN.model_dump(),
        "model": "fake-model",
    }
    # The user's text reached the model.
    assert "Add pagination to /products" in fake.calls[0][-1].content


def test_runs_rejects_empty_request(fake_llm):
    fake_llm(SAMPLE_PLAN)
    body = {"task_id": "task-1", "user_request": ""}
    response = client.post("/runs", json=body, headers=AUTH)
    assert response.status_code == 422


@pytest.mark.parametrize(
    ("error", "status_code", "detail"),
    [
        (LLMRateLimitError("429"), 503, "Gemini rate limit reached; gave up after 3 attempts"),
        (LLMUnavailableError("Could not reach Gemini"), 503, "Could not reach Gemini"),
        (LLMTimeoutError("Gemini did not answer within 30s"), 504, "Gemini did not answer within 30s"),
        (LLMError("Gemini rejected the request: API key not valid"), 502, "Gemini rejected the request: API key not valid"),
    ],
)
def test_runs_reports_model_failures(fake_llm, monkeypatch, error, status_code, detail):
    # Skip the rate-limit back-off so the test doesn't wait.
    monkeypatch.setattr("llm.time.sleep", lambda _: None)
    fake_llm(error, error, error)

    response = client.post("/runs", json=VALID_BODY, headers=AUTH)

    assert response.status_code == status_code
    # The reason travels to the backend, which shows it on the failed task.
    assert response.json()["detail"] == detail

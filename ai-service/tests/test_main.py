import pytest
from fastapi.testclient import TestClient

from conftest import TEST_TOKEN
from main import app
from runs import get_runner

client = TestClient(app)

VALID_BODY = {"task_id": "task-1", "user_request": "Add pagination to /products"}
AUTH = {"X-Internal-Token": TEST_TOKEN}


class FakeRunner:
    def __init__(self, accept=True):
        self.accept = accept
        self.submitted = []

    def submit(self, task_id, user_request):
        self.submitted.append((task_id, user_request))
        return self.accept


@pytest.fixture
def runner():
    """Replace the real runner (threads, Gemini, backend) with one that only records submissions."""

    def use(accept=True):
        fake = FakeRunner(accept)
        app.dependency_overrides[get_runner] = lambda: fake
        return fake

    yield use
    app.dependency_overrides.clear()


def test_health_is_public():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ai-service"}


def test_runs_rejects_missing_token(runner):
    fake = runner()
    response = client.post("/runs", json=VALID_BODY)
    assert response.status_code == 401
    assert fake.submitted == []  # rejected before anything starts


def test_runs_rejects_wrong_token(runner):
    fake = runner()
    response = client.post("/runs", json=VALID_BODY, headers={"X-Internal-Token": "wrong"})
    assert response.status_code == 401
    assert fake.submitted == []


def test_runs_starts_the_run_and_answers_at_once(runner):
    fake = runner()
    response = client.post("/runs", json=VALID_BODY, headers=AUTH)

    assert response.status_code == 202
    assert response.json() == {"task_id": "task-1", "status": "accepted"}
    assert fake.submitted == [("task-1", "Add pagination to /products")]


def test_runs_refuses_a_task_that_is_already_running(runner):
    runner(accept=False)
    response = client.post("/runs", json=VALID_BODY, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["detail"] == "This task is already running"


def test_runs_rejects_empty_request(runner):
    fake = runner()
    body = {"task_id": "task-1", "user_request": ""}
    response = client.post("/runs", json=body, headers=AUTH)
    assert response.status_code == 422
    assert fake.submitted == []

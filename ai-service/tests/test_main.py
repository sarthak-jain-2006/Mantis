from fastapi.testclient import TestClient

from conftest import TEST_TOKEN
from main import app

client = TestClient(app)

VALID_BODY = {"task_id": "task-1", "user_request": "Add pagination to /products"}


def test_health_is_public():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ai-service"}


def test_runs_rejects_missing_token():
    response = client.post("/runs", json=VALID_BODY)
    assert response.status_code == 401


def test_runs_rejects_wrong_token():
    response = client.post("/runs", json=VALID_BODY, headers={"X-Internal-Token": "wrong"})
    assert response.status_code == 401


def test_runs_echoes_task_with_valid_token():
    response = client.post("/runs", json=VALID_BODY, headers={"X-Internal-Token": TEST_TOKEN})
    assert response.status_code == 200
    assert response.json() == {
        "task_id": "task-1",
        "status": "received",
        "message": "AI service received: Add pagination to /products",
    }


def test_runs_rejects_empty_request():
    body = {"task_id": "task-1", "user_request": ""}
    response = client.post("/runs", json=body, headers={"X-Internal-Token": TEST_TOKEN})
    assert response.status_code == 422

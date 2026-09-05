import pytest
from fastapi.testclient import TestClient

from taskforge.api import create_app


@pytest.fixture
def client(service):
    app = create_app(service.settings, service.sessions, service.broker)
    with TestClient(app) as client:
        client.headers["X-API-Key"] = service.settings.api_key
        yield client


def test_auth(client):
    client.headers.pop("X-API-Key")
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/jobs", json={"task": "fibonacci"}).status_code == 401
    assert client.get("/health/live").status_code == 200


def test_submit_and_history(client):
    response = client.post("/api/jobs", json={"task": "fibonacci"})
    assert response.status_code == 202
    job = response.json()
    assert job["payload"]["n"] == 100
    assert "lease_token" not in job
    detail = client.get(response.headers["location"])
    assert detail.json()["history"][0]["kind"] == "submitted"
    assert client.get("/api/jobs").json()["total"] == 1
    assert client.get("/api/stats").json()["counts"]["queued"] == 1


def test_api_idempotency(client):
    first = client.post("/api/jobs", json={"task": "primes"}, headers={"Idempotency-Key": "abc"})
    again = client.post("/api/jobs", json={"task": "primes"}, headers={"Idempotency-Key": "abc"})
    conflict = client.post("/api/jobs", json={"task": "hash"}, headers={"Idempotency-Key": "abc"})
    assert first.status_code == 202 and again.status_code == 200 and conflict.status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        {"task": "exec"},
        {"task": "primes", "priority": 4},
        {"task": "primes", "payload": {"limit": 2000000}},
        {"task": "sleep", "timeout_seconds": 0},
        {"task": "hash", "max_attempts": 100},
    ],
)
def test_validation(client, body):
    assert client.post("/api/jobs", json=body).status_code == 422


def test_api_edges(client):
    assert client.get("/api/jobs/missing").status_code == 404
    assert client.get("/api/jobs?limit=1000").status_code == 422
    assert client.get("/api/jobs?status=invalid").status_code == 422
    assert client.get("/api/workers").json()["items"] == []
    assert client.get("/").status_code == 200


def test_body_limit(client):
    assert client.post("/api/jobs", content="x" * 20000).status_code == 413


def test_readiness_dependency_failure(client, service, monkeypatch):
    monkeypatch.setattr(service.broker.client, "ping", lambda: (_ for _ in ()).throw(ConnectionError()))
    assert client.get("/health/ready").status_code == 503

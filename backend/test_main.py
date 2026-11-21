import os
import tempfile

fd, path = tempfile.mkstemp()
os.close(fd)
os.environ["DATABASE_URL"] = "sqlite:///" + path
os.environ["LLM_API_KEYS"] = "demo_key_1,demo_key_2"
os.environ["ENABLE_FAILURE_SIMULATION"] = "true"

from fastapi.testclient import TestClient
from main import app

client = TestClient(app)


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cache_and_history():
    session = "cache-test"
    first = client.post("/chat", json={"session_id": session, "message": "What is AI?"})
    assert first.status_code == 200
    assert first.json()["metadata"]["resolution_source"] == "LIVE_INFERENCE"

    second = client.post("/chat", json={"session_id": "cache-test-2", "message": "What is AI?"})
    assert second.status_code == 200
    assert second.json()["metadata"]["resolution_source"] == "LLM_CACHE"
    assert second.json()["reply"] == first.json()["reply"]
    assert second.json()["metadata"]["key_id"] is None


def test_failover_header():
    response = client.post("/chat", json={"session_id": "failover", "message": "hello"}, headers={"X-Simulate-Failure": "key_1=429"})
    assert response.status_code == 200
    assert response.json()["metadata"]["key_id"] == "key_2"


def test_all_failures_do_not_store_history():
    response = client.post("/chat", json={"session_id": "all-failed", "message": "hello"}, headers={"X-Simulate-Failure": "*=429"})
    assert response.status_code == 503
    assert response.json()["detail"] == "all providers unavailable"
    history = client.get("/sessions/all-failed/messages")
    assert history.status_code == 404


def test_validation():
    response = client.post("/chat", json={"session_id": "x", "message": "   "})
    assert response.status_code == 422

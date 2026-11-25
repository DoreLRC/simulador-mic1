"""Testes de fumaça do servidor: garantem que o ambiente está montado corretamente."""

from fastapi.testclient import TestClient

import mic1
from server.app import app

client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == mic1.__version__


def test_index_page_is_served():
    response = client.get("/")

    assert response.status_code == 200
    assert "Simulador MIC-1" in response.text


def test_api_docs_available():
    assert client.get("/docs").status_code == 200

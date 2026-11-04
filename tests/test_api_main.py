"""Unit tests for FastAPI Application Setup, Middlewares & Exception Handlers."""

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api.main import app


@pytest.fixture
def client():
    """Cria um cliente de teste síncrono para o app FastAPI."""
    with TestClient(app) as test_client:
        yield test_client


def test_root_endpoint(client):
    """Valida o endpoint raiz com metadados do projeto."""
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["project"] == "Trivio"
    assert data["status"] == "online"
    assert data["version"] == "1.0.0"
    assert "timestamp" in data
    assert "X-Process-Time-Ms" in response.headers


def test_health_check_endpoints(client):
    """Valida ambos os endpoints de healthcheck (/health e /api/v1/health)."""
    # 1. /health
    res1 = client.get("/health")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] in ("healthy", "degraded")
    assert "database" in data1
    assert "X-Process-Time-Ms" in res1.headers

    # 2. /api/v1/health
    res2 = client.get("/api/v1/health")
    assert res2.status_code == 200
    data2 = res2.json()
    assert data2["status"] in ("healthy", "degraded")


def test_cors_headers(client):
    """Valida que cabeçalhos CORS permitem conexões do frontend Next.js."""
    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "GET",
    }
    response = client.options("/health", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_http_exception_handler(client):
    """Valida tratamento padronizado de HTTPExceptions."""
    # Rota que não existe (404)
    response = client.get("/rota-inexistente-12345")
    assert response.status_code == 404
    data = response.json()
    assert data["error"] is True
    assert data["status_code"] == 404
    assert "message" in data
    assert data["path"] == "/rota-inexistente-12345"


def test_unhandled_exception_handler():
    """Valida captura de exceções genéricas com retorno seguro 500."""
    from fastapi import FastAPI
    from app.api.main import generic_exception_handler

    test_app = FastAPI()
    test_app.add_exception_handler(Exception, generic_exception_handler)

    @test_app.get("/trigger-crash")
    def trigger_crash():
        raise RuntimeError("Falha inesperada simulada")

    with TestClient(test_app, raise_server_exceptions=False) as c:
        response = c.get("/trigger-crash")
        assert response.status_code == 500
        data = response.json()
        assert data["error"] is True
        assert data["status_code"] == 500
        assert "erro interno" in data["message"].lower()

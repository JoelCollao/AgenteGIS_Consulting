"""
POST /api/v1/orchestrator de punta a punta dentro de agent-service:
JWT → grafo LangGraph → agente → tool → backend simulado.

Comprueba lo que no ven los tests unitarios: que el JWT validado por la
dependencia de FastAPI llega como Authorization a la llamada de la tool, que
la respuesta conserva el contrato del frontend y que /health es público.
"""

import time

import httpx
import jwt
import pytest
from fastapi.testclient import TestClient

from agent_service import models
from agent_service.clients.backend import BackendClient
from agent_service.config import settings
from agent_service.models.illm_provider import ILLMProvider, LLMResponse, LLMToolCall
from agent_service.tools.base import set_backend_client


class ScriptedLLM(ILLMProvider):
    def __init__(self, script):
        self.script = list(script)

    def get_provider_type(self):
        return "fake"

    def get_model(self):
        return "fake-model"

    def is_available(self):
        return True

    def get_last_error(self):
        return None

    def chat(self, messages, tools=None, tool_choice="auto", timeout=None):
        raise AssertionError("se espera achat")

    async def achat(self, messages, tools=None, tool_choice="auto", timeout=None):
        return self.script.pop(0)


def _token(sub="ana", **extra):
    payload = {"sub": sub, "iss": settings.jwt_issuer, "exp": int(time.time()) + 600, **extra}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


@pytest.fixture
def app_client(monkeypatch):
    llm = ScriptedLLM([])
    monkeypatch.setattr(models, "_default_provider", llm)

    from agent_service.orchestrator import langgraph_flow

    # El singleton del grafo se construye con el provider activo
    graph = langgraph_flow.LangGraphOrchestrator()
    monkeypatch.setattr(langgraph_flow, "orchestrator", graph)

    from agent_service.main import create_app

    seen = []

    def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, json={"type": "analysis_result", "operation": "count_features",
                                         "layer": "puntos", "count": 21, "success": True})

    with TestClient(create_app()) as client:
        # Tras el startup: sustituir el cliente real por el simulado
        set_backend_client(BackendClient("http://backend", transport=httpx.MockTransport(handler)))
        yield client, llm, seen


def test_sin_token_401(app_client):
    client, _, _ = app_client
    r = client.post("/api/v1/orchestrator", json={"message": "hola"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_token_de_otro_emisor_401(app_client):
    client, _, _ = app_client
    r = client.post("/api/v1/orchestrator", json={"message": "hola"},
                    headers={"Authorization": f"Bearer {_token(iss='otro')}"})
    assert r.status_code == 401


def test_chat_con_tool_reenvia_el_jwt_al_backend(app_client):
    client, llm, seen = app_client
    llm.script[:] = [
        LLMResponse(tool_calls=[LLMToolCall(id="c1", name="count_features", arguments='{"layer": "puntos"}')]),
        LLMResponse(content="La capa puntos tiene 21 registros."),
    ]
    token = _token()

    r = client.post(
        "/api/v1/orchestrator",
        json={"message": "¿cuántos puntos hay?", "session_id": "s1"},
        headers={"Authorization": f"Bearer {token}", "X-Request-ID": "req-e2e"},
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["reply"] == "La capa puntos tiene 21 registros."
    assert body["source"] == "skill"
    assert body["tools_executed"] == [{"name": "count_features", "success": True}]
    assert r.headers["x-request-id"] == "req-e2e"

    (call,) = seen
    assert call.url.path == "/api/v1/analisis/conteo"
    assert call.headers["authorization"] == f"Bearer {token}"
    assert call.headers["x-request-id"] == "req-e2e"
    assert call.headers["x-session-id"] == "s1"


def test_health_es_publico(app_client):
    client, _, _ = app_client
    assert client.get("/api/v1/health").json()["status"] == "healthy"

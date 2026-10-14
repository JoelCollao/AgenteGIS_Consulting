"""
Tools contra el backend simulado (httpx.MockTransport)
=======================================================
Sustituyen a los tests que habrían necesitado PostGIS: lo que se comprueba
aquí es el contrato (docs/desacople/02-contrato.md) visto desde el agente:
ruta, body, headers propagados y cómo cada error vuelve al modelo.
"""

import asyncio
import json
from typing import Any, Dict, List

import httpx
import pytest

from agent_service.clients.backend import BackendClient, auth_token_var
from agent_service.policies.engine import BLOCKED, PolicyEngine, set_policy_engine
from agent_service.telemetry.observability import request_id_var, session_id_var
from agent_service.tools import registry
from agent_service.tools.base import set_backend_client

FC = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": None, "properties": {"id": 1}}]}


class FakeBackend:
    """Registra cada petición y responde lo que diga `routes[path]`."""

    def __init__(self, routes: Dict[str, Any]):
        self.routes = routes
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        answer = self.routes.get(request.url.path)
        if answer is None:
            return httpx.Response(404, json={"detail": "Not Found"})
        if callable(answer):
            return answer(request)
        status, body = answer
        return httpx.Response(status, json=body)

    def body(self, i: int = -1) -> Dict[str, Any]:
        return json.loads(self.requests[i].content)


@pytest.fixture
def backend():
    fake = FakeBackend({})
    set_backend_client(BackendClient("http://backend", transport=httpx.MockTransport(fake)))
    return fake


def _run(tool: str, params: dict) -> dict:
    return asyncio.run(registry.execute_skill(tool, params))


# ---------------------------------------------------------------------------
# Rutas y bodies
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tool, params, path",
    [
        ("buffer_analysis", {"table": "puntos", "distance": 50}, "/api/v1/analisis/buffer"),
        ("distance", {"table": "puntos", "longitude": -77.0, "latitude": -12.0}, "/api/v1/analisis/distancia"),
        ("intersect", {"table1": "a", "table2": "b"}, "/api/v1/analisis/interseccion"),
        ("count_features", {"layer": "puntos"}, "/api/v1/analisis/conteo"),
        ("filter_features", {"layer": "puntos", "column": "tipo", "value": "x"}, "/api/v1/analisis/filtro"),
        ("statistics", {"layer": "puntos", "column": "area"}, "/api/v1/analisis/estadisticas"),
    ],
)
def test_cada_tool_llama_a_su_endpoint_y_devuelve_el_body(backend, tool, params, path):
    ok = {"type": "analysis_result", "operation": tool, "count": 1, "success": True}
    backend.routes[path] = (200, ok)

    result = _run(tool, params)

    assert result == ok
    assert backend.requests[-1].method == "POST"
    assert backend.requests[-1].url.path == path


def test_el_body_lleva_los_argumentos_validados_con_defaults(backend):
    backend.routes["/api/v1/analisis/filtro"] = (200, {"success": True})
    _run("filter_features", {"layer": "Usos", "column": "tipo", "operator": "ilike", "value": "%a%"})

    assert backend.body() == {
        "layer": "Usos", "column": "tipo", "operator": "ILIKE", "value": "%a%",
        "limit": 100, "geom_column": "geom",
    }


def test_buffer_postgis_no_manda_features(backend):
    backend.routes["/api/v1/analisis/buffer"] = (200, {"success": True})
    _run("buffer_analysis", {"table": "puntos", "distance": 1, "unit": "km"})

    body = backend.body()
    assert "features" not in body
    assert body["unit"] == "km" and body["distance"] == 1


def test_buffer_in_memory_no_llama_al_backend(backend):
    feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-77.03, -12.05]}, "properties": {}}
    result = _run("buffer_analysis", {"features": [feature], "distance": 100, "table": "importada"})

    assert result["success"] is True
    assert result["source"] == "in_memory"
    assert result["geojson"]["features"][0]["geometry"]["type"] == "Polygon"
    assert backend.requests == []


def test_tools_de_mapa_no_llaman_al_backend(backend):
    assert _run("zoom_layer", {"layer": "puntos"})["success"] is True
    assert _run("highlight_features", {"layer": "puntos", "feature_ids": [1, 2]})["success"] is True
    assert backend.requests == []


# ---------------------------------------------------------------------------
# Headers propagados (contrato §3.2)
# ---------------------------------------------------------------------------

def test_propaga_jwt_request_id_y_session_id(backend):
    backend.routes["/api/v1/analisis/conteo"] = (200, {"success": True})

    async def _call():
        auth_token_var.set("jwt-del-usuario")
        request_id_var.set("req-123")
        session_id_var.set("sesion-9")
        return await registry.execute_skill("count_features", {"layer": "puntos"})

    asyncio.run(_call())
    headers = backend.requests[-1].headers
    assert headers["authorization"] == "Bearer jwt-del-usuario"
    assert headers["x-request-id"] == "req-123"
    assert headers["x-session-id"] == "sesion-9"
    assert headers["user-agent"].startswith("agent-service/")


def test_sin_token_no_inventa_authorization(backend):
    backend.routes["/api/v1/analisis/conteo"] = (200, {"success": True})
    _run("count_features", {"layer": "puntos"})
    assert "authorization" not in backend.requests[-1].headers


# ---------------------------------------------------------------------------
# Errores: el modelo recibe el mismo formato que con las skills en proceso
# ---------------------------------------------------------------------------

def test_error_del_contrato_llega_con_su_mensaje_y_la_capa(backend):
    msg = "La capa 'Parcelas' no tiene la columna 'Areaa'. Columnas disponibles: id, Area."
    backend.routes["/api/v1/analisis/estadisticas"] = (
        422, {"error": {"code": "columna_inexistente", "message": msg}},
    )
    result = _run("statistics", {"layer": "Parcelas", "column": "Areaa"})

    assert result == {
        "type": "error", "operation": "statistics", "error": msg,
        "success": False, "layer": "Parcelas",
    }


def test_422_de_fastapi_se_resume_en_una_linea(backend):
    backend.routes["/api/v1/analisis/conteo"] = (
        422, {"detail": [{"loc": ["body", "filter_operator"], "msg": "Input should be '=' or '!='"}]},
    )
    result = _run("count_features", {"layer": "puntos"})
    assert result["success"] is False
    assert result["error"] == "filter_operator: Input should be '=' or '!='"


def test_401_del_backend_se_explica_al_modelo(backend):
    backend.routes["/api/v1/analisis/conteo"] = (401, {"detail": "El usuario ya no tiene acceso"})
    result = _run("count_features", {"layer": "puntos"})
    assert result["error"] == "No autorizado: El usuario ya no tiene acceso"


def test_argumentos_invalidos_no_llegan_al_backend(backend):
    result = _run("count_features", {"layer": "users; DROP TABLE x"})
    assert result["success"] is False
    # _compact_error deja la primera línea, igual que hacía el registro del backend
    assert result["error"] == "1 validation error for CountFeaturesParams"
    assert backend.requests == []


def test_503_se_reintenta_una_vez(backend):
    respuestas = iter([httpx.Response(503, json={"error": {"code": "bd_no_disponible", "message": "BD caída"}}),
                       httpx.Response(200, json={"success": True, "count": 3})])
    backend.routes["/api/v1/analisis/conteo"] = lambda request: next(respuestas)

    result = _run("count_features", {"layer": "puntos"})
    assert result == {"success": True, "count": 3}
    assert len(backend.requests) == 2


def test_backend_caido_no_rompe_el_bucle(backend):
    def _boom(request):
        raise httpx.ConnectError("connection refused", request=request)

    backend.routes["/api/v1/analisis/conteo"] = _boom
    result = _run("count_features", {"layer": "puntos"})
    assert result["success"] is False
    assert "Backend no disponible" in result["error"]
    assert len(backend.requests) == 2


# ---------------------------------------------------------------------------
# Políticas
# ---------------------------------------------------------------------------

def test_tool_bloqueada_no_se_ejecuta(backend):
    set_policy_engine(PolicyEngine(default=BLOCKED, tools={"count_features": BLOCKED}))
    result = _run("count_features", {"layer": "puntos"})
    assert result["success"] is False
    assert "bloqueada" in result["error"]
    assert backend.requests == []


def test_tool_con_aprobacion_no_se_ejecuta(backend):
    set_policy_engine(PolicyEngine.from_dict({"default": "blocked", "tools": {"count_features": "approval"}}))
    result = _run("count_features", {"layer": "puntos"})
    assert "requiere aprobación" in result["error"]
    assert backend.requests == []


def test_policies_yaml_real_permite_todas_las_tools_actuales():
    engine = PolicyEngine.from_file()
    for tool in registry.list_skills():
        assert engine.decide(tool) == "auto", tool
    assert engine.decide("tool_desconocida") == BLOCKED

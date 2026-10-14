"""
Contrato con el backend: las validaciones de las tools (tools/params.py) y los
schemas de /api/v1/analisis/* deben coincidir (docs/desacople/02-contrato.md §2).

El backend es la autoridad; agent-service lleva una copia para no gastar una
llamada en argumentos que se van a rechazar. Si las dos divergen, el modelo
recibe un 422 que no esperaba (o el agente rechaza algo que el backend
aceptaría). Este test lo detecta sin importar el backend: compara contra una
copia de su OpenAPI, `tests/contract/backend_openapi_analisis.json`, que
`python scripts/check_desacople.py --update` regenera desde la raíz.
"""

import json
from pathlib import Path

import pytest

from agent_service.tools import params

SNAPSHOT = Path(__file__).parent / "contract" / "backend_openapi_analisis.json"

# tool (modelo del agente) → (schema del request en el backend, ruta)
CONTRATO = {
    "BufferParams": ("BufferAnalisisRequest", "/api/v1/analisis/buffer"),
    "DistanceParams": ("DistanciaRequest", "/api/v1/analisis/distancia"),
    "IntersectParams": ("InterseccionRequest", "/api/v1/analisis/interseccion"),
    "CountFeaturesParams": ("ConteoRequest", "/api/v1/analisis/conteo"),
    "FilterFeaturesParams": ("FiltroRequest", "/api/v1/analisis/filtro"),
    "StatisticsParams": ("EstadisticasRequest", "/api/v1/analisis/estadisticas"),
}

# Campos que solo existen en el agente (no viajan al backend)
SOLO_AGENTE = {"BufferParams": {"features"}}

_LIMITES = ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minLength", "maxLength")


def _limites(prop: dict) -> dict:
    """Restricciones de un campo, también dentro de anyOf (Optional)."""
    found = {k: prop[k] for k in _LIMITES if k in prop}
    for alt in prop.get("anyOf", []):
        found.update({k: alt[k] for k in _LIMITES if k in alt})
    return found


@pytest.fixture(scope="module")
def openapi():
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@pytest.mark.parametrize("modelo", sorted(CONTRATO))
def test_validaciones_coinciden_con_el_backend(openapi, modelo):
    schema_backend, ruta = CONTRATO[modelo]
    assert ruta in openapi["paths"], f"el backend ya no expone {ruta}"

    backend = openapi["components"]["schemas"][schema_backend]
    agente = getattr(params, modelo).model_json_schema()
    campos_agente = set(agente["properties"]) - SOLO_AGENTE.get(modelo, set())

    assert campos_agente == set(backend["properties"]), "los campos difieren"
    # BufferParams pide table O features: en el agente `table` es opcional
    requeridos_agente = set(agente.get("required", [])) | ({"table"} if modelo == "BufferParams" else set())
    assert requeridos_agente == set(backend.get("required", [])), "los campos obligatorios difieren"

    for campo in campos_agente:
        a, b = agente["properties"][campo], backend["properties"][campo]
        assert _limites(a) == _limites(b), f"{campo}: límites {_limites(a)} != {_limites(b)}"
        assert a.get("default") == b.get("default"), f"{campo}: default {a.get('default')} != {b.get('default')}"

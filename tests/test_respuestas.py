"""
Mapeo a código HTTP del endpoint de chat y errores de tool en una línea.
Movidos desde tests/unit/test_respuestas_y_catalogo.py del backend (la parte
de catálogo se queda allí).
"""

import asyncio

from agent_service.api.orchestrator import _http_status
from agent_service.tools import registry


def test_skill_fallida_con_respuesta_es_200():
    response = {"success": False, "source": "skill", "reply": "No encontré la columna…"}
    assert _http_status(response) == 200


def test_codigos_de_error():
    assert _http_status({"success": False, "error": {"code": "empty_message"}}) == 400
    assert _http_status({"success": False, "error": {"code": "internal_error"}}) == 500
    assert _http_status({"success": False, "source": "fallback", "intent": "error"}) == 503
    assert _http_status({"success": True, "source": "conversational"}) == 200


def test_error_de_skill_queda_en_una_linea(monkeypatch):
    sql_error = (
        "(psycopg2.errors.UndefinedTable) no existe la relación «x»\n"
        "LINE 9: FROM x\n[SQL: SELECT ...]\n(Background on this error at: ...)"
    )
    monkeypatch.setitem(
        registry.skills_registry, "fake", lambda p: {"success": False, "error": sql_error}
    )
    result = asyncio.run(registry.execute_skill("fake", {}))
    assert result["error"] == "(psycopg2.errors.UndefinedTable) no existe la relación «x»"

"""
Tests de observabilidad
========================
Cubren lo que, si se rompe, no falla de forma visible: que los logs lleven el
request_id, que cada llamada al LLM deje tokens y latencia, que los hilos del
timeout hereden el contexto y que tools y llamadas al modelo generen spans.

Sin red ni Application Insights: los spans se capturan con un exportador en
memoria.
"""

import asyncio
import json
import logging
import threading

import pytest
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from agent_service.tools import registry
from agent_service.models.llm_settings import KIND_OPENAI_COMPATIBLE, LLMConfig
from agent_service.telemetry.observability import (
    JsonFormatter,
    _ContextFilter,
    request_id_var,
    run_in_context,
)
from agent_service.models.base import OpenAIChatProviderBase

# ---------------------------------------------------------------------------
# Exportador en memoria (el TracerProvider global solo se puede fijar una vez)
# ---------------------------------------------------------------------------

_EXPORTER = InMemorySpanExporter()
if not isinstance(trace.get_tracer_provider(), TracerProvider):
    _provider = TracerProvider()
    _provider.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    trace.set_tracer_provider(_provider)
else:  # pragma: no cover - otro test ya fijó un provider
    trace.get_tracer_provider().add_span_processor(SimpleSpanProcessor(_EXPORTER))


@pytest.fixture(autouse=True)
def _clear_spans():
    _EXPORTER.clear()
    yield


# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _chat_completion(content="hola", tool_calls=None, usage=True):
    message = _Obj(content=content, tool_calls=tool_calls or [], reasoning_details=None)
    raw = _Obj(choices=[_Obj(message=message)], model="gpt-4.1-mini")
    raw.usage = _Obj(prompt_tokens=120, completion_tokens=30) if usage else None
    return raw


class _FakeProvider(OpenAIChatProviderBase):
    def __init__(self, raw=None, error=None):
        super().__init__(
            LLMConfig(provider="fake", kind=KIND_OPENAI_COMPATIBLE, model="gpt-4.1-mini")
        )
        self._client = object()
        self._raw = raw
        self._error = error
        self.seen_request_id = None

    def get_provider_type(self) -> str:
        return "fake"

    def _invoke(self, client, kwargs):
        # Corre en el hilo del timeout: debe ver el request_id de la petición
        self.seen_request_id = request_id_var.get()
        if self._error:
            raise self._error
        return self._raw


def _spans(name_prefix):
    return [s for s in _EXPORTER.get_finished_spans() if s.name.startswith(name_prefix)]


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def _record(msg="evento", **extra):
    record = logging.LogRecord("agent_service.test", logging.INFO, __file__, 1, msg, (), None)
    record.__dict__.update(extra)
    _ContextFilter().filter(record)
    return record


def test_json_formatter_incluye_request_id_y_extras():
    token = request_id_var.set("req-123")
    try:
        line = JsonFormatter().format(_record(event="llm.call", tokens_in=10))
    finally:
        request_id_var.reset(token)

    data = json.loads(line)
    assert data["request_id"] == "req-123"
    assert data["msg"] == "evento"
    assert data["event"] == "llm.call"
    assert data["tokens_in"] == 10
    assert data["level"] == "INFO"


def test_json_formatter_sin_peticion_usa_guion():
    data = json.loads(JsonFormatter().format(_record()))
    assert data["request_id"] == "-"
    assert data["session_id"] == "-"


def test_run_in_context_propaga_request_id_a_hilo_manual():
    seen = []
    token = request_id_var.set("req-hilo")
    try:
        thread = threading.Thread(target=run_in_context(lambda: seen.append(request_id_var.get())))
        thread.start()
        thread.join()
    finally:
        request_id_var.reset(token)
    assert seen == ["req-hilo"]


# ---------------------------------------------------------------------------
# Llamadas al LLM
# ---------------------------------------------------------------------------


def test_chat_registra_tokens_y_latencia():
    provider = _FakeProvider(raw=_chat_completion())
    response = provider.chat([{"role": "user", "content": "hola"}])

    assert response.input_tokens == 120
    assert response.output_tokens == 30
    assert response.latency_ms is not None and response.latency_ms >= 0


def test_chat_sin_usage_deja_tokens_en_none():
    provider = _FakeProvider(raw=_chat_completion(usage=False))
    response = provider.chat([{"role": "user", "content": "hola"}])
    assert response.input_tokens is None
    assert response.output_tokens is None


def test_extract_usage_responses_api():
    raw = _Obj(usage=_Obj(input_tokens=50, output_tokens=7))
    assert OpenAIChatProviderBase._extract_usage(raw) == (50, 7)


def test_chat_emite_span_genai():
    tool_call = _Obj(id="c1", function=_Obj(name="buffer_analysis", arguments="{}"))
    provider = _FakeProvider(raw=_chat_completion(content=None, tool_calls=[tool_call]))
    provider.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])

    (span,) = _spans("chat ")
    assert span.name == "chat gpt-4.1-mini"
    assert span.attributes["gen_ai.operation.name"] == "chat"
    assert span.attributes["gen_ai.usage.input_tokens"] == 120
    assert span.attributes["gen_ai.usage.output_tokens"] == 30
    assert span.attributes["gen_ai.response.tool_calls"] == "buffer_analysis"
    assert span.attributes["gen_ai.request.tool_count"] == 1


def test_chat_con_error_marca_span_y_relanza():
    provider = _FakeProvider(error=ValueError("400 bad request"))
    with pytest.raises(ValueError):
        provider.chat([{"role": "user", "content": "x"}])

    (span,) = _spans("chat ")
    assert span.status.status_code == trace.StatusCode.ERROR


def test_hilo_del_timeout_hereda_request_id():
    provider = _FakeProvider(raw=_chat_completion())
    token = request_id_var.set("req-llm")
    try:
        provider.chat([{"role": "user", "content": "hola"}])
    finally:
        request_id_var.reset(token)
    assert provider.seen_request_id == "req-llm"


def test_chat_deja_log_llm_call(caplog):
    provider = _FakeProvider(raw=_chat_completion())
    with caplog.at_level(logging.INFO, logger="agent_service.models.base"):
        provider.chat([{"role": "user", "content": "hola"}])

    (record,) = [r for r in caplog.records if getattr(r, "event", None) == "llm.call"]
    assert record.tokens_in == 120
    assert record.llm_outcome == "text"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_execute_skill_emite_span_con_resultado(monkeypatch):
    monkeypatch.setitem(registry.skills_registry, "fake_tool", lambda p: {"success": True})
    asyncio.run(registry.execute_skill("fake_tool", {"features": [{}, {}]}))

    (span,) = _spans("execute_tool ")
    assert span.attributes["gen_ai.tool.name"] == "fake_tool"
    assert span.attributes["tool.success"] is True


def test_execute_skill_que_lanza_queda_como_fallo(monkeypatch):
    def _boom(_):
        raise RuntimeError("PostGIS caído")

    monkeypatch.setitem(registry.skills_registry, "fake_tool", _boom)
    result = asyncio.run(registry.execute_skill("fake_tool", {}))

    assert result["success"] is False
    (span,) = _spans("execute_tool ")
    assert span.attributes["tool.success"] is False

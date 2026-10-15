"""
Observabilidad: logs correlacionados y trazas OpenTelemetry
============================================================
Tres piezas que se usan desde el resto del código:

  · `request_id_var` / `session_id_var`: contexto de la petición en curso.
    Viven en contextvars, así que siguen a la petición a través de `await`
    y de `asyncio.to_thread` (que copia el contexto al hilo). Los hilos
    creados a mano con `threading.Thread` NO lo heredan: para esos está
    `run_in_context`.
  · `configure_logging()`: cada línea de log lleva `request_id` y
    `session_id`. En producción sale en JSON (una línea por evento, lo que
    Log Analytics / Container Apps parsean sin regex); en desarrollo, texto
    legible.
  · `configure_telemetry()` + `span()`: trazas hacia Application Insights
    con la distro de Azure Monitor. Sin APPLICATIONINSIGHTS_CONNECTION_STRING
    no se exporta nada y `span()` sigue funcionando (el tracer del API de
    OpenTelemetry sin SDK configurado no hace nada), así que el código
    instrumentado no necesita saber si la telemetría está activa.

Los atributos de los spans de LLM siguen las convenciones semánticas GenAI de
OpenTelemetry (`gen_ai.*`), que es lo que el visor de App Insights y el
tracing de Foundry saben agrupar.
"""

import contextvars
import json
import logging
import os
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, Optional

request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)
session_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "session_id", default="-"
)

_TRACER_NAME = "agent-service"
_telemetry_enabled = False


# ── Logging ──────────────────────────────────────────────────────────────


class _ContextFilter(logging.Filter):
    """Añade request_id, session_id y trace_id a cada registro."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        record.session_id = session_id_var.get()
        record.trace_id = _current_trace_id()
        return True


class JsonFormatter(logging.Formatter):
    """Una línea JSON por evento; `extra={...}` se vuelca como campos."""

    _STD_ATTRS = set(
        logging.LogRecord("", 0, "", 0, "", (), None).__dict__
    ) | {"message", "asctime", "request_id", "session_id", "trace_id"}

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
            "session_id": getattr(record, "session_id", "-"),
        }
        trace_id = getattr(record, "trace_id", None)
        if trace_id:
            payload["trace_id"] = trace_id
        for key, value in record.__dict__.items():
            if key not in self._STD_ATTRS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(json_logs: bool) -> None:
    """Configura el logger raíz. Idempotente (reemplaza los handlers)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_ContextFilter())
    if json_logs:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s.%(msecs)03d %(levelname)s [%(request_id)s] %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(logging.INFO)

    # Silenciar logs verbosos de Azure SDK y del exportador
    for noisy in ("azure.identity", "azure.core", "azure.ai", "azure.monitor"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


# ── Trazas ───────────────────────────────────────────────────────────────


def configure_telemetry(app: Any) -> bool:
    """
    Activa la exportación a Application Insights si hay connection string.

    Instrumenta FastAPI (un span por request), las llamadas HTTP salientes
    (requests/urllib, que es lo que usan los SDK de OpenAI y Azure por
    debajo vía httpx). Devuelve True si quedó activa.
    """
    global _telemetry_enabled  # pylint: disable=global-statement

    if not os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        return False

    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    # cloud_RoleName en App Insights: separa este servicio del backend en el
    # mapa de aplicación aunque compartan recurso.
    os.environ.setdefault("OTEL_SERVICE_NAME", _TRACER_NAME)

    configure_azure_monitor(
        logger_name="agent_service",  # solo los logs de la app viajan como traces de AI
        instrumentation_options={"fastapi": {"enabled": False}},
    )
    # Se instrumenta la instancia concreta (la distro solo engancha la clase
    # al importar, y aquí FastAPI ya está importado).
    FastAPIInstrumentor.instrument_app(app, excluded_urls="api/v1/health,api/v1/ready")
    # La distro no instrumenta httpx, que es lo que usan el cliente del
    # backend y el SDK de OpenAI. Sin esto no se propaga `traceparent` al
    # backend y las llamadas al LLM no aparecen como dependencias.
    HTTPXClientInstrumentor().instrument()
    _telemetry_enabled = True
    return True


def telemetry_enabled() -> bool:
    return _telemetry_enabled


def _current_trace_id() -> Optional[str]:
    try:
        from opentelemetry import trace

        ctx = trace.get_current_span().get_span_context()
        return format(ctx.trace_id, "032x") if ctx.is_valid else None
    except Exception:  # pylint: disable=broad-except
        return None


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Any]:
    """
    Span hijo del span actual. Las excepciones se registran en el span y se
    relanzan. Atributos None se omiten (OTel no los admite).
    """
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    tracer = trace.get_tracer(_TRACER_NAME)
    with tracer.start_as_current_span(name, record_exception=True) as current:
        for key, value in attributes.items():
            if value is not None:
                current.set_attribute(key, value)
        try:
            yield current
        except Exception as exc:
            current.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def run_in_context(target: Callable[..., Any]) -> Callable[..., Any]:
    """
    Envuelve `target` para ejecutarlo con el contexto actual (request_id y
    span activo) dentro de un `threading.Thread` creado a mano.
    """
    ctx = contextvars.copy_context()

    def _runner(*args: Any, **kwargs: Any) -> Any:
        return ctx.run(target, *args, **kwargs)

    return _runner

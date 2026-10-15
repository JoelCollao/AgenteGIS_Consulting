"""
Canal de eventos para POST /api/v1/orchestrator/stream
=======================================================
El endpoint de streaming fija aquí un *sink* (una función que recibe cada
evento) antes de lanzar el grafo. El agente lo consulta: si hay sink, pide el
texto al modelo en streaming y emite cada fragmento; si no (endpoint normal),
todo sigue igual que siempre.

Va en un ContextVar para no tener que pasarlo por el estado de LangGraph: las
tareas asyncio que crea el grafo heredan el contexto del que lo lanza.

Eventos (los serializa el endpoint como SSE):
    {"type": "status", "text": "..."}   el agente está ejecutando tools
    {"type": "delta",  "text": "..."}   fragmento del texto de la respuesta
    {"type": "done",   "status": 200, "response": {...}}   respuesta completa
"""

from contextvars import ContextVar
from typing import Any, Callable, Dict, Optional

EventSink = Callable[[Dict[str, Any]], None]

stream_sink_var: ContextVar[Optional[EventSink]] = ContextVar("stream_sink", default=None)


def emit(event: Dict[str, Any]) -> None:
    """Entrega el evento al sink activo; sin sink no hace nada."""
    sink = stream_sink_var.get()
    if sink is not None:
        sink(event)


def is_streaming() -> bool:
    return stream_sink_var.get() is not None

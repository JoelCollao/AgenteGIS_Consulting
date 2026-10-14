"""
ConversationMemory - Memoria Conversacional por Sesión
======================================================
Almacena historial de conversación por session_id con LRU eviction.
Permite diálogos multi-turno donde el agente recuerda el contexto previo.
Thread-safe para uso concurrente con FastAPI.
"""

import logging
from collections import OrderedDict
from threading import Lock
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class ConversationMemory:
    """
    Memoria in-memory con eviction LRU por sesión.

    Parámetros:
        max_sessions: Número máximo de sesiones activas en memoria.
        max_turns:    Número máximo de turnos (mensajes) por sesión.
    """

    def __init__(self, max_sessions: int = 500, max_turns: int = 20):
        self._store: OrderedDict[str, List[Dict]] = OrderedDict()
        self._max_sessions = max_sessions
        self._max_turns = max_turns
        self._lock = Lock()

    def add_turn(self, session_id: str, role: str, content: str) -> None:
        """
        Agrega un turno de conversación a la sesión.

        Args:
            session_id: Identificador único de la sesión.
            role:       'user' o 'assistant'.
            content:    Contenido del mensaje.
        """
        if not session_id or not content:
            return

        with self._lock:
            if session_id not in self._store:
                # Evict oldest session if at capacity
                if len(self._store) >= self._max_sessions:
                    evicted_key, _ = self._store.popitem(last=False)
                    logger.debug("Session evicted (LRU): %s", evicted_key)
                self._store[session_id] = []

            self._store[session_id].append({"role": role, "content": content})

            # Mantener solo los últimos N turnos
            if len(self._store[session_id]) > self._max_turns:
                self._store[session_id] = self._store[session_id][-self._max_turns:]

            # Mover sesión al final (más reciente) del OrderedDict
            self._store.move_to_end(session_id)

    def get_history(self, session_id: str) -> List[Dict]:
        """
        Retorna el historial de conversación de una sesión.

        Args:
            session_id: Identificador único de la sesión.

        Returns:
            Lista de mensajes [{role, content}, ...] o lista vacía.
        """
        if not session_id:
            return []
        with self._lock:
            return list(self._store.get(session_id, []))

    def clear_session(self, session_id: str) -> None:
        """Elimina el historial de una sesión."""
        with self._lock:
            self._store.pop(session_id, None)

    def active_sessions(self) -> int:
        """Retorna el número de sesiones activas."""
        with self._lock:
            return len(self._store)


# ---------------------------------------------------------------------------
# Instancia global (singleton)
# ---------------------------------------------------------------------------
conversation_memory = ConversationMemory()

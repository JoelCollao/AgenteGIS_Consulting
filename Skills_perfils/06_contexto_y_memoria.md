# Contexto y memoria
**Requisito(s) del perfil:** R5, F2 — manejo de contexto y memoria de LLMs en producción.
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Controlar qué entra en la ventana de contexto (y cuánto cuesta), resumir historiales, persistir memoria de forma compartida entre instancias y separar memoria de corto plazo (conversación) de largo plazo (hechos/preferencias).

## 2. Qué tengo hoy (evidencia)
- `src/application/services/conversation_memory.py` → `OrderedDict` + `threading.Lock`, `max_sessions=500`, `max_turns=20`. Ventana por **número de mensajes**, no por tokens.
- `src/presentation/api/orchestrator.py` → clave `len(user):user:session_id`, evita lectura cruzada entre usuarios. Bien razonado.
- `agenteConsultaGIS.py` (`_build_messages`, `_enrich_message`) → inyecta resumen de capas del mapa al mensaje; `_summarize_result` evita mandar GeoJSON completo al modelo.
- Solo se guardan `user` y `assistant` finales; no se guardan tool calls/resultados.

## 3. Brechas
- **Por qué un LRU en memoria no escala**: (1) se pierde en cada reinicio/deploy/scale-to-zero; (2) con N réplicas cada una tiene su memoria → el usuario "pierde" contexto según el balanceo; (3) consume RAM del proceso y no tiene TTL; (4) no auditable (banca exige saber qué vio el modelo).
- Sin conteo de tokens (`tiktoken`) ni resumen de historial.
- Sin memoria de largo plazo.
- `layersData`/`layersGeojson` del front pueden inflar el prompt sin límite.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| M1 | Checkpointer de LangGraph `AsyncPostgresSaver` (`langgraph-checkpoint-postgres`) con `thread_id = session_key`; `messages` en el estado con reducer `add_messages` | `state.py`, `langgraph_flow.py` | Reinicio no pierde historial; 2 réplicas comparten | M |
| M2 | Alternativa ligera: `IConversationMemory` (puerto) + adaptador Redis (LIST + TTL 24 h) | `src/domain/ports/`, `src/infrastructure/redis_memory/` | Tests del adaptador con fakeredis | S |
| M3 | Presupuesto de tokens con `tiktoken`: system + tools + contexto + historial ≤ N; recorta lo más viejo | `agenteConsultaGIS.py` | Test con historial largo | S |
| M4 | Resumen incremental cuando el historial supera X tokens (nodo `summarize` en el grafo, modelo pequeño) | `langgraph_flow.py` | Conversación de 50 turnos se mantiene < N tokens | M |
| M5 | Límite de tamaño y muestreo de `layersData` | `schemas.py`, `_enrich_message` | 422 o truncado con aviso | S |
| M6 | Memoria largo plazo opcional (preferencias: capas favoritas, unidades) en tabla `user_memory` o `Store` de LangGraph | — | Demo | M |

## 5. Teoría que debo dominar para la entrevista
- Ventana de contexto ≠ memoria: la memoria es lo que **yo decido** reinyectar.
- Estrategias: ventana deslizante, resumen, recuperación semántica de turnos viejos (memoria episódica en vector store), memoria de entidades.
- Costo: cada turno reenvía todo; tokens de entrada dominan el costo en chats largos. Prompt caching (Azure OpenAI lo aplica a prefijos ≥ 1024 tokens) → poner system + tools al inicio y estables.
- En Foundry Agent Service, los **threads/conversations** guardan el historial en el servicio (memoria gestionada), con trade-off de control y residencia de datos.
- Errores típicos: cortar entre un `tool_call` y su `tool` result (400), filtrar PII a memoria persistente sin enmascarar.

## 6. Preguntas probables y respuesta guía
- *¿Por qué tu memoria es LRU en proceso?* Fue suficiente para una réplica; sé que no escala y la migraría a checkpointer Postgres porque ya tengo Postgres y me da también estado de flujo y HITL.
- *¿Cómo controlas el costo?* Presupuesto de tokens, resumen, resultados de tools resumidos (ya lo hago con GeoJSON), caching de prefijo.

## 7. Cómo lo demuestro
Demo: conversación → reinicio del contenedor → "¿y en el otro distrito?" mantiene contexto. Log con tokens por turno.

## 8. Recursos
- https://langchain-ai.github.io/langgraph/concepts/persistence/
- https://langchain-ai.github.io/langgraph/concepts/memory/
- https://learn.microsoft.com/azure/ai-foundry/openai/how-to/prompt-caching

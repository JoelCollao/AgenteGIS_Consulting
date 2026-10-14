# Servicios asíncronos, latencia y escalabilidad
**Requisito(s) del perfil:** R2 (servicios async y APIs en producción con foco en latencia y escalabilidad), F1 (servicios async que orquestan agentes), F5 (latencia y escalabilidad en tiempo real).
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Servicios que atienden muchas conversaciones simultáneas con latencia percibida baja (streaming), escalan horizontalmente sin estado local, aplican backpressure y no se caen cuando el LLM se pone lento.

## 2. Qué tengo hoy (evidencia)
- **Tracking en tiempo real (fuerte):** `src/presentation/ws/tracking_ws.py` + `connection_manager.py` (WS por grupo, difusión local y vía Redis pub/sub entre réplicas), `src/infrastructure/redis_tracking/` (GEOADD/GEOSEARCH, cortocircuito, modo degradado).
- **Pool de BD:** `src/infrastructure/database/connection.py:26-33` (`pool_size`, `pool_pre_ping`, `pool_recycle`, `pool_timeout`, `connect_timeout`).
- **Escalado:** `infra/provision-container-apps.ps1:143-144` (`--min-replicas 0 --max-replicas 3`) — solo script, despliegue no activo.
- **Chat:** `orchestrator.py:150` request/response con `to_thread`; sin streaming (0 coincidencias de `StreamingResponse`/`stream=True`). Front: `src/widgets/chat/ChatGeoIA.tsx` espera la respuesta completa.
- **Latencia medida:** solo `processing_time_ms` en la respuesta (`langgraph_flow.py`).

## 3. Brechas
- Sin streaming → TTFT = latencia total (2 llamadas LLM en el camino con tool).
- Estado local (memoria LRU) impide escalar el chat.
- `min-replicas 0` → cold start + cold start de credenciales Entra ID.
- Sin colas para trabajos largos (geoprocesos grandes).
- Sin pruebas de carga ni SLO.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| L1 | SSE `POST /api/v1/chat/stream` con `graph.astream_events(version="v2")`, eventos `token`, `tool_start`, `tool_end`, `final` | `src/presentation/api/chat_stream.py`, `ChatGeoIA.tsx` | TTFT p95 < 1.5 s | M |
| L2 | Estado fuera del proceso (ver archivo 06) | — | 2 réplicas sin afinidad funcionan | M |
| L3 | Jobs largos: `POST /geoprocessing/jobs` → 202 + `job_id`; worker con `arq` (Redis) o Azure Service Bus; estado en tabla `jobs` | `src/application/jobs/`, migración SQL | Buffer de 100k features no bloquea API | M |
| L4 | Backpressure: `Semaphore` global de llamadas LLM + 429 si cola llena | `base.py`, middleware | Bajo saturación responde 429 rápido en vez de timeouts | S |
| L5 | `min-replicas 1` en prod + regla KEDA HTTP (`concurrentRequests`) | Bicep | Sin cold start; escala a 3 con carga | S |
| L6 | Locust + SLO (p50/p95/p99, TTFT, error rate) | `tests/load/` | Informe guardado en `docs/` | S |
| L7 | Caché de respuestas deterministas (capas, conteos) en Redis | queries CQRS | Hit ratio visible en métricas | S |

## 5. Teoría que debo dominar para la entrevista
- **Latencia LLM** = cola + TTFT + tokens×tiempo/token. Streaming mejora la percibida, no la total. Reducir tokens de entrada (contexto) baja TTFT.
- **SSE vs WebSocket**: SSE para respuestas servidor→cliente (chat), WS para bidireccional (mi tracking). SSE pasa mejor por proxies y es HTTP simple.
- **Escalado horizontal**: stateless + estado en Redis/Postgres; sticky sessions son un parche.
- **Little's law**: concurrencia = throughput × latencia; con LLM de 5 s y 20 req/s necesito 100 slots.
- **Backpressure y load shedding**: mejor rechazar rápido (429) que acumular timeouts.
- **Pools**: tamaño de pool DB × réplicas ≤ `max_connections` de Postgres (usar PgBouncer).

## 6. Preguntas probables y respuesta guía
- *¿Cómo escalaste los WebSockets a varias réplicas?* Redis pub/sub como bus; cada réplica entrega a sus conexiones locales; cortocircuito si Redis cae (evidencia en `connection_manager.py`).
- *¿Cómo bajarías la latencia del chat?* Streaming, router determinista antes del LLM (ya lo hago con regex en `MapContextAgent`), modelo pequeño para clasificar, recortar contexto, tools en paralelo, caché.
- *¿Qué pasa si Azure OpenAI tarda 30 s?* Timeout + retry acotado + fallback + 503 claro; semáforo evita agotar el servicio.

## 7. Cómo lo demuestro
Video del chat con streaming + gráfico locust p95 antes/después + demo de dos réplicas recibiendo eventos de tracking.

## 8. Recursos
- https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse
- https://langchain-ai.github.io/langgraph/how-tos/streaming/
- https://learn.microsoft.com/azure/container-apps/scale-app

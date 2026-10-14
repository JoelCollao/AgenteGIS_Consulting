# Python avanzado y asyncio
**Requisito(s) del perfil:** R3 — Python avanzado y programación asíncrona (asyncio).
**Estado en mi proyecto:** 🟡

## 1. Qué pide el perfil, en concreto
Saber escribir servicios donde el event loop nunca se bloquea, coordinar concurrencia (gather/TaskGroup/semáforos), manejar cancelación y timeouts de forma cooperativa, y elegir cuándo usar hilos o procesos.

## 2. Qué tengo hoy (evidencia)
- `src/application/commands/actualizar_ubicacion_command.py:115,174` → `asyncio.to_thread` para I/O síncrono de PostGIS y `asyncio.gather(..., return_exceptions=True)` para drenar tareas. Cuenta.
- `src/infrastructure/redis_tracking/connection.py` → cliente `redis.asyncio` con reconexión y cortocircuito (`_Circuito`, línea 87). Cuenta.
- `src/presentation/ws/connection_manager.py` → difusión async a WebSockets y puente pub/sub (`iniciar_puente`, `detener_puente`). Cuenta.
- `src/presentation/api/orchestrator.py:150` → el chat se ejecuta con `asyncio.to_thread(process_query)`. Evita bloquear, pero es async "de fachada".
- `src/infrastructure/llm/base.py:184` → timeout duro con `threading.Thread` + `join(timeout)`. El hilo **sigue vivo** tras el timeout: fuga de recursos bajo carga.
- `src/presentation/api/health.py:22` → `/ready` es `async def` pero ejecuta `engine.connect()` síncrono: **bloquea el loop**.
- `app.py:282` → warm-up del LLM en `threading.Thread` dentro de `on_event("startup")` (deprecado).
- Uso idiomático: dataclasses, ABC, Pydantic v2 con `field_validator`, `Enum(str)`.

## 3. Brechas
- No hay `asyncio.timeout`, `TaskGroup`, `Semaphore`, ni manejo de `CancelledError`.
- Drivers síncronos (psycopg2, `requests` en `geoserver/client.py` y `martin/client.py`).
- El pool por defecto de `to_thread` limita la concurrencia real del chat.

## 4. Qué implementar (tareas concretas)
| ID | Tarea | Archivos | Criterio de terminado | Esfuerzo |
|---|---|---|---|---|
| PY1 | `achat()` con `AsyncAzureOpenAI` y `async with asyncio.timeout(t)` | `illm_provider.py`, `base.py`, `azure_agent.py` | Test: timeout cancela la petición (no quedan tareas vivas) | M |
| PY2 | `/ready` con `await asyncio.to_thread(...)` y ping a Redis | `health.py` | El loop no se bloquea (test con `asyncio` debug mode) | S |
| PY3 | Tools en paralelo con `asyncio.TaskGroup` + `Semaphore(4)` | `agenteConsultaGIS.py` | 2 tools tardan ~max(t1,t2) | S |
| PY4 | `httpx.AsyncClient` compartido (lifespan) para GeoServer/Martin | `infrastructure/geoserver/client.py`, `martin/client.py` | Sin `requests.` en código llamado desde async | M |
| PY5 | `lifespan` en vez de `on_event`, warm-up como `asyncio.create_task` | `app.py` | Sin warnings de deprecación | S |
| PY6 | Activar `PYTHONASYNCIODEBUG=1` en tests para detectar bloqueos > 100 ms | `tests/conftest.py` | Log de slow callbacks vacío | S |

## 5. Teoría que debo dominar para la entrevista
- **Event loop**: un hilo; cualquier llamada bloqueante congela a todos los clientes. `async def` no hace nada asíncrono por sí solo.
- **`to_thread` vs `run_in_executor` vs `ProcessPool`**: hilos para I/O bloqueante de librerías sync; procesos para CPU (p. ej. geoprocesamiento pesado con shapely por el GIL).
- **Cancelación**: `asyncio.timeout` cancela la corrutina y el cliente HTTP cierra la conexión; un hilo no se puede cancelar (mi problema actual en `base.py`).
- **TaskGroup (3.11)** vs `gather`: TaskGroup cancela hermanas si una falla; `gather(return_exceptions=True)` las deja terminar.
- **Backpressure**: `Semaphore` y colas acotadas (`asyncio.Queue(maxsize)`).
- Errores típicos: olvidar `await`, crear tareas sin guardar referencia (GC), mezclar SQLAlchemy sync en handlers async, `time.sleep` en vez de `asyncio.sleep`.

## 6. Preguntas probables y respuesta guía
- *¿Qué pasa si llamas a psycopg2 dentro de un `async def`?* Bloquea el loop; latencia de todos sube. Soluciones: `to_thread`, `asyncpg`, o SQLAlchemy `AsyncSession`.
- *¿Cómo implementarías un timeout a una llamada LLM?* `asyncio.timeout` + timeout del propio cliente httpx; nunca hilos daemon. Explico que en mi código lo hice con hilos y por qué lo cambiaría.
- *gather vs TaskGroup?* Ver teoría.
- *¿Cómo limitas llamadas concurrentes a Azure OpenAI?* `Semaphore` por proceso + rate limit distribuido en Redis por TPM.

## 7. Cómo lo demuestro
Antes/después de PY1 con locust: 50 usuarios concurrentes, mostrar que la latencia p95 del endpoint `/health` no se degrada mientras el chat está bajo carga.

## 8. Recursos
- https://docs.python.org/3/library/asyncio-task.html
- https://docs.python.org/3/library/asyncio-dev.html
- https://github.com/openai/openai-python#async-usage

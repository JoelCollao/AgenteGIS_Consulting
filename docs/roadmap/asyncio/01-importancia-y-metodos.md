# Importancia de asyncio y métodos usados

## Por qué importa en este proyecto

Una petición de chat hace, en el peor caso, hasta 5 rondas (`MAX_TOOL_STEPS`) de: llamar al LLM (segundos) → ejecutar varias tools contra el backend (cientos de ms cada una). Casi todo ese tiempo el proceso **no calcula nada, solo espera respuestas de red**.

- **Sin asyncio** (código síncrono): cada petición ocupa un hilo/worker mientras espera. Con pocos workers, unos pocos usuarios bloquean el servicio.
- **Con asyncio**: un único event loop atiende muchas peticiones; mientras una espera al LLM, otra avanza. Y dentro de una misma petición, las tools independientes se ejecutan **a la vez**.

Beneficios concretos:

1. **Latencia**: si el modelo pide 3 tools que tardan 300 ms cada una, la ronda tarda ~300 ms y no ~900 ms (`asyncio.gather`).
2. **Robustez**: un LLM que no responde no deja la petición colgada; `asyncio.timeout` la cancela de verdad.
3. **No bloquear el loop**: lo síncrono (credenciales de Azure, Shapely) se manda a hilos con `asyncio.to_thread`.
4. **Contexto por petición**: el JWT y el `request_id` viajan con `ContextVar` hasta cada tool, aunque corran en paralelo.

## Métodos de `asyncio` usados

| método | dónde | para qué |
|---|---|---|
| `asyncio.gather` | `agents/agente_consulta_gis.py` → `_run_tools` | ejecutar en paralelo las tools de una ronda, conservando el orden de los resultados |
| `asyncio.Semaphore` | mismo método (`MAX_PARALLEL_TOOLS = 4`) | que no corran más de 4 tools a la vez en una ronda |
| `asyncio.timeout` | `models/base.py` → `achat` | límite de tiempo a la llamada al LLM; al vencer, **cancela** la petición |
| `asyncio.to_thread` | `agente_consulta_gis.py` (`is_available`), `models/base.py` (`_ensure_client`, token de Entra ID), `models/illm_provider.py` (`chat` síncrono), `tools/registry.py` (skills síncronas), `tools/buffer.py` (Shapely) | ejecutar código bloqueante o de CPU sin congelar el event loop |
| `asyncio.sleep` | `clients/backend.py` → `call` | pausa de 0,2 s no bloqueante antes del único reintento |

## Bibliotecas asíncronas que se apoyan en asyncio

| biblioteca | dónde | para qué |
|---|---|---|
| FastAPI / uvicorn | `main.py`, `api/*.py` | uvicorn crea el event loop; los endpoints son `async def` |
| LangGraph | `orchestrator/langgraph_flow.py` | `await self.graph.ainvoke(...)` ejecuta los nodos async del grafo |
| `httpx.AsyncClient` | `clients/backend.py` | llamadas HTTP al backend con pool de conexiones (`httpx.Limits`) |
| `openai.AsyncOpenAI` | `models/base.py` | llamadas al LLM (Azure OpenAI / Foundry) sin bloquear |
| `contextvars` | `clients/backend.py`, `telemetry/observability.py` | JWT, request-id y session-id por petición, heredados por tareas e hilos |

## Lo que NO se usa (y por qué no hace falta hoy)

`asyncio.run` (lo hace uvicorn), `create_task` / `TaskGroup` (no hay tareas de fondo propias), `Queue`, `Event`, `asyncio.Lock` (la conexión al LLM se protege con `threading.Lock` porque ocurre en hilos), `as_completed`, `wait_for` (se usa la API moderna `asyncio.timeout`).

## Mejoras aplicadas tras la revisión

| observación | cambio |
|---|---|
| docstring de `_run_tools` hablaba de psycopg2 (ya no existe aquí) | corregido: las tools usan httpx async |
| el `Semaphore(4)` es por ronda, no global: N peticiones → hasta 4×N conexiones | `httpx.Limits(max_connections=...)` en `BackendClient`, configurable con `BACKEND_MAX_CONNECTIONS` (20 por defecto) |
| el `AsyncOpenAI` nunca se cerraba | `aclose()` en el proveedor, llamado en el `shutdown` |
| en la ruta síncrona (`_create_with_timeout`) el hilo sigue vivo tras el timeout | sin cambio: Python no permite matar hilos. Solo afecta a adaptadores sin cliente async; los de OpenAI/Azure usan la ruta async que sí cancela |

# Casos de uso para aplicar asyncio

Propuestas, **no implementadas**. Cada una parte de código real; se indica dónde aplicaría.

Contexto del backend (`gis-backend-app`): FastAPI con SQLAlchemy **síncrono** sobre PostGIS (`DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5`), endpoints `/api/v1/analisis/*` que delegan con `asyncio.to_thread`, y tracking en tiempo real con Redis async + WebSockets.

---

## 1. Backend · `/ready` bloquea el event loop

**Dónde:** `gis-backend-app/src/presentation/api/health.py` → `readiness_check`

**Problema:** es `async def` pero ejecuta `engine.connect()` y `SELECT 1` síncronos. Si PostGIS tarda (hasta `connect_timeout`), **todo** el backend se congela, incluidos los WebSockets.

```python
@router.get("/ready")
async def readiness_check():
    def _ping():
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    try:
        async with asyncio.timeout(3):
            await asyncio.to_thread(_ping)
        return {"status": "ready", "database": "reachable"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "not_ready", "error": str(e)})
```

**Métodos:** `to_thread`, `timeout`.

---

## 2. Backend · difusión WebSocket secuencial

**Dónde:** `gis-backend-app/src/presentation/ws/connection_manager.py` → `difundir_local`

**Problema:** envía el evento a cada socket uno tras otro (`for websocket in suscritos: await websocket.send_json(...)`). Un móvil con mala cobertura retrasa a todos los demás del grupo.

```python
async def _enviar(ws):
    try:
        async with asyncio.timeout(2):
            await ws.send_json(evento)
        return ws, True
    except Exception:
        return ws, False

resultados = await asyncio.gather(*(_enviar(ws) for ws in suscritos))
muertos = [ws for ws, ok in resultados if not ok]
entregados = len(resultados) - len(muertos)
```

**Métodos:** `gather`, `timeout`. Un cliente lento ya no frena al resto.

---

## 3. Backend · más hilos que conexiones en el pool

**Dónde:** `gis-backend-app/src/presentation/api/analisis.py` → `_ejecutar`

**Problema:** cada consulta va a `asyncio.to_thread`; el pool de hilos por defecto admite hasta `min(32, núcleos + 4)` hilos, pero el pool de SQLAlchemy solo tiene 10 conexiones (5 + 5). Con picos (p. ej. el agente lanzando varias tools por usuario), los hilos sobrantes esperan `pool_timeout` y fallan.

```python
_DB_SLOTS = asyncio.Semaphore(DB_POOL_SIZE + DB_MAX_OVERFLOW)

async def _ejecutar(operacion, query, **kwargs):
    async with _DB_SLOTS:  # espera en el loop, sin ocupar un hilo
        return await asyncio.to_thread(query.execute, **kwargs)
```

**Métodos:** `Semaphore`, `to_thread`. Complementa el `httpx.Limits` del agent-service.

---

## 4. Agent-service · timeout por tool

**Dónde:** `src/agent_service/agents/agente_consulta_gis.py` → `_bounded` dentro de `_run_tools`

**Problema:** el backend tiene un timeout de lectura de 30 s, pero el buffer en memoria (Shapely en `to_thread`) no tiene límite. Una tool lenta retrasa toda la ronda.

```python
async def _bounded(call):
    async with semaphore:
        try:
            async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                return await self._run_tool(call, context)
        except TimeoutError:
            return _ToolRun(call, {"type": "error", "operation": call.name,
                                   "error": "La operación tardó demasiado", "success": False})
```

**Métodos:** `timeout`. Ojo: el hilo de Shapely sigue corriendo; lo que se libera es la ronda.

---

## 5. Agent-service · readiness que comprueba dependencias en paralelo

**Dónde:** `src/agent_service/api/health.py` → `readiness_check`

**Idea:** comprobar a la vez que el backend responde y que el LLM está disponible, en vez de una cosa tras otra.

```python
backend_ok, llm_ok = await asyncio.gather(
    _ping_backend(),                                   # GET /health con httpx
    asyncio.to_thread(default_llm_provider().is_available),
    return_exceptions=True,
)
ready = backend_ok is True and llm_ok is True
```

**Métodos:** `gather(return_exceptions=True)`, `to_thread`.

---

## 6. Agent-service · progreso en tiempo real con `asyncio.Queue`

**Dónde:** nuevo endpoint de streaming (SSE) junto a `src/agent_service/api/orchestrator.py`

**Idea:** el usuario ve "Calculando buffer…", "Contando parques…" mientras el agente trabaja, en lugar de esperar en blanco.

```python
async def stream(request):
    queue: asyncio.Queue = asyncio.Queue()
    task = asyncio.create_task(agent.process_query(..., on_event=queue.put_nowait))
    task.add_done_callback(lambda _: queue.put_nowait(None))
    try:
        while (evento := await queue.get()) is not None:
            yield f"data: {json.dumps(evento)}\n\n"
        yield f"data: {json.dumps(await task)}\n\n"
    finally:
        task.cancel()  # si el cliente se desconecta
```

**Métodos:** `Queue`, `create_task`, cancelación.

---

## 7. Agent-service · migrar `on_event` a `lifespan`

**Dónde:** `src/agent_service/main.py`

**Idea:** FastAPI marca `@app.on_event` como obsoleto. `lifespan` agrupa arranque y cierre en una sola función async y garantiza el cierre aunque falle algo.

```python
@asynccontextmanager
async def lifespan(app):
    set_backend_client(BackendClient(...))
    try:
        yield
    finally:
        await backend().aclose()
        await default_llm_provider().aclose()

app = FastAPI(lifespan=lifespan)
```

---

## Prioridad sugerida

| # | caso | proyecto | impacto | esfuerzo |
|---|---|---|---|---|
| 1 | `/ready` bloqueante | backend | alto (congela el servicio) | bajo |
| 3 | semáforo de BD | backend | alto en picos | bajo |
| 2 | difusión WS en paralelo | backend | medio | bajo |
| 4 | timeout por tool | agent-service | medio | bajo |
| 5 | readiness en paralelo | agent-service | bajo | bajo |
| 7 | `lifespan` | agent-service | bajo (mantenimiento) | bajo |
| 6 | streaming de progreso | agent-service + frontend | alto en UX | medio |

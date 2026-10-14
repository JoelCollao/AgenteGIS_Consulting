# Diagramas de uso de asyncio

## 1. Visión general de una petición

```mermaid
sequenceDiagram
    participant FE as Frontend
    participant API as FastAPI (event loop)
    participant G as LangGraph ainvoke
    participant A as AgenteGIS
    participant LLM as LLM (AsyncOpenAI)
    participant BE as Backend (httpx)

    FE->>API: POST /api/v1/orchestrator
    API->>G: await arun()
    G->>A: await process_query()
    loop hasta 5 rondas
        A->>LLM: await achat() dentro de asyncio.timeout
        LLM-->>A: pide tools
        par gather + Semaphore(4)
            A->>BE: await backend().call(tool 1)
            A->>BE: await backend().call(tool 2)
            A->>BE: await backend().call(tool 3)
        end
        BE-->>A: resultados en orden
    end
    A-->>FE: respuesta + payload de mapa
```

## 2. `asyncio.gather` + `asyncio.Semaphore`

El modelo pide 6 tools. Como máximo 4 corren a la vez; las otras esperan un hueco. `gather` devuelve los resultados **en el mismo orden** en que se pidieron.

```mermaid
gantt
    dateFormat X
    axisFormat %L ms
    section hueco 1
    tool 1 :0, 300
    tool 5 :300, 500
    section hueco 2
    tool 2 :0, 200
    tool 6 :200, 450
    section hueco 3
    tool 3 :0, 250
    section hueco 4
    tool 4 :0, 150
```

Total ≈ 500 ms en vez de la suma (≈ 1 600 ms).

```python
semaphore = asyncio.Semaphore(MAX_PARALLEL_TOOLS)

async def _bounded(call):
    async with semaphore:          # espera si ya hay 4 dentro
        return await self._run_tool(call, context)

return list(await asyncio.gather(*(_bounded(tc) for tc in tool_calls)))
```

`_run_tool` nunca lanza excepciones (las convierte en `{"type": "error", ...}`), así que un fallo no tumba al resto.

## 3. `asyncio.timeout` (cancelación real)

```mermaid
flowchart LR
    A[achat] --> B{"async with asyncio.timeout(limit)"}
    B -->|responde a tiempo| C[LLMResponse]
    B -->|vence el tiempo| D[CancelledError dentro del SDK]
    D --> E[httpx cierra la conexión]
    E --> F[TimeoutError con mensaje claro]
```

## 4. `asyncio.to_thread` (no bloquear el loop)

```mermaid
flowchart LR
    subgraph Loop["Event loop (1 hilo)"]
        R1[petición 1: await to_thread]
        R2[petición 2 sigue avanzando]
        R3[petición 3 sigue avanzando]
    end
    subgraph Pool["Pool de hilos"]
        T1["az login / token Entra ID"]
        T2["Shapely buffer (CPU)"]
    end
    R1 -->|delegar| T1
    T1 -->|resultado| R1
```

Si se llamara a esas funciones directamente en una corrutina, **todas** las peticiones quedarían congeladas mientras tanto.

## 5. `asyncio.sleep` en el reintento al backend

```mermaid
flowchart LR
    P[POST al backend] -->|TransportError o 503| S["await asyncio.sleep(0.2)"]
    S --> P2[segundo intento]
    P2 -->|falla| E["{type: error}"]
    P -->|ok| OK[JSON]
    P2 -->|ok| OK
```

`asyncio.sleep` cede el control; `time.sleep` bloquearía todo el servicio.

## 6. Ciclo de vida de los clientes

```mermaid
flowchart LR
    S[startup] --> B["BackendClient(httpx.AsyncClient, Limits)"]
    S --> W["hilo de precalentamiento del LLM"]
    B --> U[peticiones]
    U --> D[shutdown]
    D --> C1["await backend().aclose()"]
    D --> C2["await default_llm_provider().aclose()"]
```

# asyncio en agent-service

Material de referencia sobre cómo y por qué el agent-service usa `asyncio`, basado en el código real de `src/agent_service`.

| documento | contenido |
|---|---|
| [01-importancia-y-metodos.md](01-importancia-y-metodos.md) | por qué asyncio importa en este servicio y qué métodos se usan, dónde y para qué |
| [02-diagramas.md](02-diagramas.md) | diagramas de cada patrón: `gather` + `Semaphore`, `timeout`, `to_thread`, `sleep`, cierre de recursos |
| [03-entrevista.md](03-entrevista.md) | cómo explicarlo en una entrevista técnica y preguntas con respuesta para juniors |
| [04-casos-de-uso.md](04-casos-de-uso.md) | casos de uso aplicables en agent-service y en el backend (`gis-backend-app`), con ejemplos cortos |

## Contexto en una línea

```
frontend ─► agent-service (FastAPI + LangGraph, async) ─► LLM (AsyncOpenAI)
                                                      └─► backend (httpx.AsyncClient) ─► PostGIS
```

El agent-service pasa casi todo su tiempo **esperando por red**: al modelo de lenguaje y al backend. Ese es exactamente el escenario donde asyncio rinde.

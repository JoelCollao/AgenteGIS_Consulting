# agent-service

Orquestación del asistente GIS: grafo LangGraph, agente con function calling, prompts, proveedores de LLM, memoria conversacional y tools.

Para obtener datos **solo habla con el backend por HTTP**, a través de su API (contrato en `../docs/desacople/02-contrato.md`). No tiene acceso a la base de datos ni a sus drivers.

```
frontend ──POST /api/v1/orchestrator──► agent-service ──POST /api/v1/analisis/*──► backend ──► PostGIS
             (JWT del usuario)              │  (reenvía el mismo JWT)
                                            └──► proveedor LLM (Azure OpenAI / Foundry / …)
```

## Estructura

| ruta | qué hay |
|---|---|
| `src/agent_service/main.py` | punto de entrada: FastAPI, middlewares y cableado de dependencias |
| `config.py` | settings desde el entorno (sin BD) |
| `api/` | `POST /api/v1/orchestrator` (chat), `/api/ai/*` (deprecados), health, validación del JWT |
| `orchestrator/` | grafo LangGraph: IntentRouter → MapContext \| GISAgent → Validator |
| `agents/` | `agente_consulta_gis.py`: rol, prompt y lista cerrada de tools del agente GIS |
| `prompts/` | `gis_system.md` (system prompt) y `gis_tools.json` (esquemas de function calling) |
| `policies/` + `config/policies.yaml` | qué tool corre sola, cuál requiere aprobación y cuál está bloqueada |
| `tools/` | un adaptador por tool: valida con Pydantic (`params.py`) y llama al backend |
| `clients/backend.py` | cliente httpx del backend: propaga JWT, `X-Request-ID` y `X-Session-ID` |
| `models/` | proveedores de LLM (`ILLMProvider` y sus adaptadores, mismo patrón que tenía el backend) |
| `memory/` | memoria conversacional por sesión (LRU en RAM) |
| `telemetry/` | logs JSON, spans OpenTelemetry `gen_ai.*`, tokens, costo y duración por llamada |
| `tests/` | tests unitarios y de tools con el backend simulado (`httpx.MockTransport`) |
| `evals/` | casos de comportamiento del agente contra un LLM real (`python evals/run_evals.py`) |

## Tools

| tool | dónde se ejecuta |
|---|---|
| `buffer_analysis` con `table` | backend: `POST /api/v1/analisis/buffer` |
| `buffer_analysis` con `features` | aquí, en memoria (Shapely + PyProj) |
| `distance` | backend: `POST /api/v1/analisis/distancia` |
| `intersect` | backend: `POST /api/v1/analisis/interseccion` |
| `count_features` | backend: `POST /api/v1/analisis/conteo` |
| `filter_features` | backend: `POST /api/v1/analisis/filtro` |
| `statistics` | backend: `POST /api/v1/analisis/estadisticas` |
| `highlight_features`, `zoom_layer` | aquí; solo generan un payload para el mapa |

Para añadir una tool: crea `tools/<nombre>.py` con `async def execute(parameters) -> dict`, regístrala en `tools/registry.py`, añade su esquema a `prompts/gis_tools.json` y su nombre a `TOOLS` del agente, y dale política en `config/policies.yaml`. Sin política queda **bloqueada**.

## Ejecutar en local

**1. Primera vez (instalación)**

```bash
cd agent-service
python -m venv .venv
.venv/Scripts/activate          # Windows; en Linux/macOS: source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # completa JWT_SECRET (igual al del backend), BACKEND_URL y el proveedor LLM
```

**2. Levantar el servicio (cada vez)**

```bash
cd agent-service
.venv/Scripts/activate
python -m uvicorn agent_service.main:app --app-dir src --port 8001 --reload
```

`--app-dir src` permite arrancar aunque el paquete no esté instalado con `pip install -e`.

**3. Comprobar que está arriba**

Abre http://127.0.0.1:8001/docs en el navegador: debe cargar la documentación de la API.

El backend tiene que estar levantado en `BACKEND_URL`, con los endpoints `/api/v1/analisis/*` (Fase 4 del desacople).

## Tests y evals

```bash
pytest                         # sin red: LLM y backend simulados
python evals/run_evals.py      # usa el LLM configurado en .env (cuesta tokens)
```

## Variables de entorno

Ver `.env.example`. Las principales son `BACKEND_URL`, `JWT_SECRET` (el mismo que el backend), `LLM_PROVIDER` y las de su proveedor, `CORS_ALLOWED_ORIGINS` y, opcionalmente, `LLM_PRICE_INPUT_PER_1M` / `LLM_PRICE_OUTPUT_PER_1M` para que los logs `llm.call` incluyan el costo estimado.

## Pendiente: despliegue a producción

Este repositorio está reducido para **levantarse solo en local**. Antes de desplegar hay que recuperar o crear:

- [ ] **`Dockerfile`** (eliminado). Imagen `python:3.11-slim`; no necesita GDAL ni libpq porque no accede a la base de datos.
- [ ] **`scripts/sync_agent_tools.py`** (eliminado). Publicaba las tools en la definición del agente de Azure AI Foundry; hace falta si `LLM_PROVIDER` usa un agente de Foundry. `models/azure_agent.py` y `prompts/__init__.py` todavía lo mencionan.
- [ ] **`llm_providers.example.json`** (eliminado). Plantilla opcional para declarar proveedores LLM en `llm_providers.json` sin tocar código.
- [ ] Variables de producción: `ENVIRONMENT=production`, `CORS_ALLOWED_ORIGINS`, `BACKEND_URL` interno y autenticación con Managed Identity en lugar de `az login` (ver comentarios al final de `.env.example`).
- [ ] Pipeline de CI/CD y despliegue (p. ej. Azure Container Apps).

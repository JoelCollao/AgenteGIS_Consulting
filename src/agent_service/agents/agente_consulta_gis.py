"""Agente GIS con Function Calling — agnóstico de proveedor
============================================================
Agente inteligente que decide si responder conversacionalmente o
ejecutar una skill GIS usando function calling.

El LLM recibe el system prompt + tool definitions y decide
autónomamente qué acción tomar según el mensaje del usuario.

Este módulo NO sabe con qué proveedor habla: recibe un ILLMProvider
(Azure OpenAI, Azure AI Foundry, Kimi, DeepSeek, Groq, Ollama…) que el
DI Container elige según LLM_PROVIDER. Ver src/config/llm_settings.py.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from agent_service.agents.iai_agent import IAIAgent
from agent_service.models.illm_provider import ILLMProvider, LLMResponse, LLMToolCall
from agent_service.prompts import GIS_SYSTEM_PROMPT, GIS_TOOL_DEFINITIONS
from agent_service.tools.registry import execute_skill

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Definición del agente: rol, prompt y lista CERRADA de tools
# ---------------------------------------------------------------------------
# El .env ya no se carga aquí: lo hace main.py una sola vez al arrancar.
ROLE = "Asistente GIS: análisis geoespacial y cartografía sobre las capas del usuario"
SYSTEM_PROMPT = GIS_SYSTEM_PROMPT  # prompts/gis_system.md
# Mismo orden que el registro: aparece en el mensaje de "skill no disponible".
TOOLS = (
    "buffer_analysis",
    "intersect",
    "distance",
    "count_features",
    "filter_features",
    "statistics",
    "highlight_features",
    "zoom_layer",
)
TOOL_DEFINITIONS = [t for t in GIS_TOOL_DEFINITIONS if t["function"]["name"] in TOOLS]

# Rondas máximas modelo → tools → modelo. Acota costo y latencia y corta
# bucles en los que el modelo repite la misma llamada sin avanzar.
MAX_TOOL_STEPS = 5
# Skills simultáneas por ronda (cada una ocupa una conexión de PostGIS).
MAX_PARALLEL_TOOLS = 4


@dataclass
class _ToolRun:
    """Una tool call del modelo y el resultado de la skill que la atendió."""

    call: LLMToolCall
    result: Dict[str, Any]


class AgenteConsultaGIS(IAIAgent):
    """
    Agente GIS con Function Calling.

    Usa chat.completions con tools para que el LLM decida:
    - Responder conversacionalmente (sin tool call).
    - Invocar una skill GIS (con tool call + parámetros estructurados).

    Implementa la interfaz IAIAgent (Port del dominio).
    """

    def __init__(self, llm_provider: Optional[ILLMProvider] = None):
        """
        Args:
            llm_provider: Proveedor a usar. Si es None, lo pide al DI Container
                          (que lo elige según LLM_PROVIDER). Inyectarlo permite
                          testear el agente con un doble sin tocar red.
        """
        if llm_provider is None:
            # Import local: el provider se construye la primera vez que se pide
            from agent_service.models import default_llm_provider

            llm_provider = default_llm_provider()

        self.llm = llm_provider
        self._last_error: Optional[str] = None

        logger.info(
            "AgenteGIS listo (provider=%s, model=%s)",
            self.llm.get_provider_type(), self.llm.get_model(),
        )

    @property
    def model(self) -> str:
        """Modelo/deployment en uso — lo decide el proveedor configurado."""
        return self.llm.get_model()

    # ------------------------------------------------------------------
    # Proceso principal con Function Calling
    # ------------------------------------------------------------------

    async def process_query(
        self,
        message: str,
        context: Optional[Dict] = None,
        conversation_history: Optional[List[Dict]] = None,
    ) -> Dict:
        """
        Procesa una consulta con un bucle de function calling.

        1. Construye mensajes con system prompt + historial + contexto.
        2. Llama al modelo con las tools.
        3. Si pide tools, las ejecuta TODAS (en paralelo, acotadas por un
           semáforo), le devuelve los resultados y vuelve a llamarlo: puede
           encadenar operaciones ("filtra X y haz un buffer de eso").
        4. Termina cuando responde con texto o al llegar a MAX_TOOL_STEPS
           rondas, donde se le pide solo redactar con lo que ya tiene.
        """
        logger.info("Procesando consulta: %s...", message[:60])

        # La primera conexión puede tardar (credenciales en frío): fuera del loop
        if not await asyncio.to_thread(self.llm.is_available):
            return self._fallback_response(message)

        context = context or {}
        runs: List[_ToolRun] = []

        try:
            messages = self._build_messages(message, context, conversation_history)
            logger.info(
                "[AgenteGIS] Enviando a %s (model=%s, msgs=%d)...",
                self.llm.get_provider_type(), self.model, len(messages),
            )

            for step in range(1, MAX_TOOL_STEPS + 1):
                try:
                    response = await self.llm.achat(messages, tools=TOOL_DEFINITIONS)
                except Exception:
                    if not runs:
                        raise
                    # Las skills ya corrieron: mejor devolver sus resultados con
                    # una narración básica que perderlos por un fallo del modelo.
                    logger.warning("[AgenteGIS] Falló la llamada tras ejecutar tools", exc_info=True)
                    return self._build_response(self._basic_narrative(runs[-1]), runs)

                if not response.has_tool_calls:
                    reply = response.content or ""
                    logger.info("[AgenteGIS] Respuesta final (paso %d): %s...", step, reply[:80])
                    return self._build_response(reply, runs)

                logger.info(
                    "[AgenteGIS] Paso %d: el modelo pide %d tool(s): %s",
                    step, len(response.tool_calls),
                    ", ".join(tc.name for tc in response.tool_calls),
                )
                messages.append(self._assistant_message(response))
                step_runs = await self._run_tools(response.tool_calls, context)
                runs.extend(step_runs)
                messages.extend(
                    {
                        "role": "tool",
                        "tool_call_id": run.call.id,
                        "content": self._summarize_result(run.result),
                    }
                    for run in step_runs
                )

            # Límite de rondas: que redacte con lo que hay, sin más tools
            logger.warning("[AgenteGIS] Alcanzado MAX_TOOL_STEPS=%d", MAX_TOOL_STEPS)
            return self._build_response(await self._narrate(messages, runs[-1]), runs)

        except Exception as e:
            self._last_error = str(e)
            logger.error("Error en function calling: %s", e, exc_info=True)
            return self._fallback_response(message)

    # ------------------------------------------------------------------
    # Construcción de mensajes
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        message: str,
        context: Optional[Dict],
        conversation_history: Optional[List[Dict]],
    ) -> List[Dict]:
        """Construye la lista de mensajes para chat.completions."""
        messages: List[Dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

        # Historial conversacional (si existe)
        if conversation_history:
            messages.extend(conversation_history)

        # Mensaje del usuario enriquecido con contexto del mapa
        user_content = self._enrich_message(message, context)
        messages.append({"role": "user", "content": user_content})

        return messages

    def _enrich_message(self, message: str, context: Optional[Dict]) -> str:
        """Enriquece el mensaje con contexto de capas del mapa."""
        if not context:
            return message

        parts = [message]

        # Inyectar información de capas disponibles
        layers_data = context.get("layersData")
        if layers_data:
            layers = layers_data.get("layers", [])
            if layers:
                layer_summaries = []
                for lyr in layers:
                    name = lyr.get("name", "?")
                    geom = lyr.get("geometryType", "?")
                    fc = lyr.get("featureCount", "?")
                    fields = lyr.get("fields", [])
                    fields_str = ", ".join(fields[:10]) if fields else "sin campos"
                    layer_summaries.append(
                        f"  - {name} [{geom}, {fc} features] campos: {fields_str}"
                    )
                parts.append(
                    f"\n[Capas cargadas en el mapa ({len(layers)}):\n"
                    + "\n".join(layer_summaries)
                    + "]"
                )

        # Contexto de target layers (operación específica del frontend)
        target_layers = context.get("targetLayers")
        if target_layers:
            names = [
                tl.get("name", tl) if isinstance(tl, dict) else str(tl)
                for tl in target_layers
            ]
            parts.append(f"\n[Capas objetivo seleccionadas: {', '.join(names)}]")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # Ejecución de Tool Calls
    # ------------------------------------------------------------------

    async def _run_tools(self, tool_calls: List[LLMToolCall], context: Dict) -> List[_ToolRun]:
        """
        Ejecuta todas las tool calls de una ronda en paralelo, conservando el
        orden (cada resultado debe responder a su `tool_call_id`).

        Las skills son corrutinas que llaman al backend por httpx (las
        síncronas, p. ej. el buffer en memoria, van a un hilo). El semáforo
        acota cuántas corren a la vez dentro de una ronda; el tope global de
        conexiones al backend, entre peticiones, lo pone `httpx.Limits` en
        BackendClient. `gather` sin cancelación en cascada a
        propósito: `_run_tool` nunca lanza (convierte todo fallo en un
        resultado de error que el modelo puede leer y corregir).
        """
        semaphore = asyncio.Semaphore(MAX_PARALLEL_TOOLS)

        async def _bounded(call: LLMToolCall) -> _ToolRun:
            async with semaphore:
                return await self._run_tool(call, context)

        return list(await asyncio.gather(*(_bounded(tc) for tc in tool_calls)))

    async def _run_tool(self, call: LLMToolCall, context: Dict) -> _ToolRun:
        """Valida argumentos, los enriquece con el contexto y ejecuta la skill."""
        try:
            params = json.loads(call.arguments or "{}")
            if not isinstance(params, dict):
                raise ValueError("se esperaba un objeto JSON")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            # Antes se ejecutaba con {} en silencio. Devolverle el error al
            # modelo le permite corregir los argumentos en la ronda siguiente.
            logger.warning("[AgenteGIS] Argumentos inválidos para %s: %s", call.name, exc)
            return _ToolRun(
                call,
                {
                    "type": "error",
                    "operation": call.name,
                    "success": False,
                    "error": f"Argumentos no válidos ({exc}). Envía un objeto JSON "
                             "que cumpla el esquema de la función.",
                },
            )

        logger.info("[AgenteGIS] Tool call: %s(%s)", call.name, list(params))

        try:
            params = self._enrich_skill_params(call.name, params, context)
            if call.name == "buffer_analysis" and not params.get("features"):
                params = self._inject_frontend_features(params, context)
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("[AgenteGIS] No se pudieron enriquecer params de %s: %s", call.name, exc)

        if call.name not in TOOLS:
            # Lista cerrada: mismo mensaje que daba el registro para una skill inexistente
            return _ToolRun(
                call,
                {
                    "type": "error",
                    "operation": call.name,
                    "error": f"Skill '{call.name}' no disponible. Skills válidas: {list(TOOLS)}",
                    "success": False,
                },
            )

        # execute_skill ya convierte excepciones de la skill en {"success": False}.
        # Es async: las tools llaman al backend por HTTP.
        result = await execute_skill(call.name, params)
        return _ToolRun(call, result)

    @staticmethod
    def _assistant_message(response: LLMResponse) -> Dict[str, Any]:
        """Mensaje assistant con TODAS las tool calls de la ronda."""
        msg: Dict[str, Any] = {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ],
        }
        if response.reasoning_details:
            msg["reasoning_details"] = response.reasoning_details
        return msg

    async def _narrate(self, messages: List[Dict], last_run: _ToolRun) -> str:
        """Pide al modelo que redacte sin tools; si falla, narración básica."""
        try:
            narration = await self.llm.achat(messages)
            return narration.content or self._basic_narrative(last_run)
        except Exception as e:  # pylint: disable=broad-except
            logger.warning("Error generando narrativa: %s", e)
            return self._basic_narrative(last_run)

    @staticmethod
    def _basic_narrative(run: _ToolRun) -> str:
        if run.result.get("success"):
            return (
                f"Operación '{run.call.name}' completada. "
                f"Se procesaron {run.result.get('count', '?')} features."
            )
        return f"Error en la operación: {run.result.get('error', 'desconocido')}"

    def _build_response(self, reply: str, runs: List[_ToolRun]) -> Dict:
        """
        Respuesta para el frontend. Sin tools, conversacional. Con tools, el
        contrato de siempre (bufferGeojson/filteredGeojson, newLayerId…)
        tomado de las skills en orden, de modo que si se ejecutó más de una
        gana la última; `tools_executed` lista todas.
        """
        if not runs:
            return {
                "reply": reply,
                "source": "conversational",
                "intent": "conversational",
                "agent_type": "conversational",
                "success": True,
            }

        last = runs[-1]
        response: Dict[str, Any] = {
            "reply": reply,
            "source": "skill",
            "intent": "action",
            "agent_type": last.call.name,
            "confidence": 1.0,
            "success": bool(last.result.get("success", False)),
            "tools_executed": [
                {"name": run.call.name, "success": bool(run.result.get("success", False))}
                for run in runs
            ],
        }

        for run in runs:
            name, result = run.call.name, run.result
            if result.get("geojson"):
                if name == "buffer_analysis":
                    response["bufferGeojson"] = result["geojson"]
                    response["agent"] = "AgenteBuffer"
                elif name == "filter_features":
                    response["filteredGeojson"] = result["geojson"]
                    response["agent"] = "AgenteFiltro"
            for extra_key in ("newLayerId", "layerName", "count"):
                if extra_key in result:
                    response[extra_key] = result[extra_key]

        # Alias "result" para el frontend
        if any(run.call.name in ("buffer_analysis", "filter_features") for run in runs):
            response["result"] = reply

        return response

    @staticmethod
    def _summarize_result(skill_result: Dict) -> str:
        """Resume el resultado de la skill para el LLM (evita tokens excesivos)."""
        summary = {k: v for k, v in skill_result.items() if k != "geojson"}

        # Incluir solo conteo de features del GeoJSON, no la geometría completa
        geojson = skill_result.get("geojson")
        if geojson and isinstance(geojson, dict):
            features = geojson.get("features", [])
            summary["geojson_feature_count"] = len(features)
            # Incluir propiedades del primer feature como ejemplo
            if features:
                summary["sample_feature_properties"] = features[0].get("properties", {})

        return json.dumps(summary, ensure_ascii=False, default=str)

    # ------------------------------------------------------------------
    # Enriquecimiento de parámetros con contexto del frontend
    # ------------------------------------------------------------------

    _SKILL_TABLE_KEY = {
        "buffer_analysis": "table",
        "distance": "table",
        "intersect": "table1",
        "count_features": "layer",
        "filter_features": "layer",
        "statistics": "layer",
        "highlight_features": "layer",
        "zoom_layer": "layer",
    }

    def _enrich_skill_params(
        self, skill_name: str, params: Dict, context: Dict
    ) -> Dict:
        """Enriquece parámetros con datos del frontend (targetLayers, bufferRequest, etc.)."""
        import re as _re

        key = self._SKILL_TABLE_KEY.get(skill_name)

        # Resolver nombre de tabla desde targetLayers del frontend
        target_layers = context.get("targetLayers") or []
        if not target_layers:
            ld = context.get("layersData") or {}
            target_layers = ld.get("layers", [])

        if target_layers and key:
            llm_name = (params.get(key) or "").strip()
            chosen = self._match_layer(llm_name, target_layers)
            # Si el LLM no envió nombre, usar la primera targetLayer como default
            if not chosen and not llm_name and len(target_layers) >= 1:
                first = target_layers[0]
                if isinstance(first, dict):
                    chosen = first
            if chosen:
                real_name = chosen.get("name", "").strip()
                if real_name:
                    params[key] = real_name

        # Parámetros de buffer desde bufferRequest del frontend
        if skill_name == "buffer_analysis":
            br = context.get("bufferRequest") or {}
            if br.get("distance"):
                params.setdefault("distance", br["distance"])
            if br.get("unit"):
                # Normalizar unidades del frontend
                unit_map = {"kilometers": "km", "feet": "meters"}
                raw_unit = br["unit"]
                params.setdefault("unit", unit_map.get(raw_unit, raw_unit))

        # Parámetros de filtro desde filterRequest del frontend
        if skill_name == "filter_features":
            fr = context.get("filterRequest") or {}
            if fr.get("column"):
                params["column"] = str(fr["column"])
            if fr.get("operator"):
                params["operator"] = str(fr["operator"])
            if fr.get("value") is not None:
                params["value"] = fr["value"]

        return params

    @staticmethod
    def _match_layer(hint: str, layers: list) -> Optional[Dict]:
        """Busca la mejor coincidencia de capa por nombre fuzzy."""
        import re as _re

        if not hint:
            # Sin hint no adivinamos: dejamos que _inject_frontend_features resuelva
            return None

        def _norm(s: str) -> str:
            return _re.sub(r"[\s_\-]+", " ", s.lower()).strip()

        hint_norm = _norm(hint)
        for lyr in layers:
            name = _norm(lyr.get("name", "")) if isinstance(lyr, dict) else _norm(str(lyr))
            if name == hint_norm or hint_norm in name or name in hint_norm:
                return lyr
        return None

    @staticmethod
    def _sanitize_table_name(name: str) -> str:
        """
        Limpia un nombre de capa para convertirlo en identificador SQL válido.
        Quita extensiones de archivo, reemplaza espacios/guiones, elimina caracteres inválidos.
        """
        import re as _re

        cleaned = name.strip()
        cleaned = _re.sub(
            r"\.(geojson|shp|json|gpkg|kml|kmz|gml|csv|xlsx?|dbf|prj|shx)$",
            "", cleaned, flags=_re.IGNORECASE,
        )
        cleaned = _re.sub(r"[\s\-]+", "_", cleaned)
        cleaned = _re.sub(r"[^a-zA-Z0-9_]", "", cleaned)
        if cleaned and cleaned[0].isdigit():
            cleaned = f"_{cleaned}"
        return cleaned.lower() if cleaned else name

    @staticmethod
    def _inject_frontend_features(params: Dict, context: Dict) -> Dict:
        """
        Si la capa no existe en PostGIS (es un archivo importado),
        inyecta las features GeoJSON del contexto del frontend para
        procesamiento in-memory con Shapely.
        """
        import re as _re

        target_name = (params.get("table") or "").strip()

        def _norm(s: str) -> str:
            return _re.sub(r"[\s_\-]+", " ", s.lower()).strip()

        def _strip_ext(s: str) -> str:
            return _re.sub(
                r"\.(geojson|shp|json|gpkg|kml|kmz)$", "", s, flags=_re.IGNORECASE
            )

        layers_geojson = context.get("layersGeojson") or []

        if not target_name:
            # Sin nombre de tabla: usar las primeras features disponibles
            injected = False
            if layers_geojson:
                for lg_entry in layers_geojson:
                    features = lg_entry.get("features", [])
                    # Fallback: features envueltas en FeatureCollection
                    if not features:
                        geojson_obj = lg_entry.get("geojson")
                        if isinstance(geojson_obj, dict):
                            features = geojson_obj.get("features", [])
                    if features:
                        params["features"] = features
                        clean = _strip_ext(lg_entry.get("name", "capa_importada"))
                        params["table"] = _re.sub(r"[^a-zA-Z0-9_]", "", clean) or "capa_importada"
                        logger.info(
                            "[AgenteGIS] Features inyectadas de layersGeojson '%s': %d features",
                            lg_entry.get("name", "?"), len(features),
                        )
                        injected = True
                        break
            # Fallback: features directas del contexto
            if not injected:
                direct_features = context.get("features") or []
                if direct_features:
                    params["features"] = direct_features
                    params.setdefault("table", "capa_importada")
                    logger.info(
                        "[AgenteGIS] Features inyectadas de context['features']: %d features",
                        len(direct_features),
                    )
            return params

        # Buscar por nombre en layersGeojson
        clean_name_norm = _norm(_strip_ext(target_name))

        for lg in layers_geojson:
            lg_name_norm = _norm(_strip_ext(lg.get("name", "")))
            if (lg_name_norm == clean_name_norm
                    or clean_name_norm in lg_name_norm
                    or lg_name_norm in clean_name_norm):
                features = lg.get("features", [])
                # Fallback: features envueltas en FeatureCollection
                if not features:
                    geojson_obj = lg.get("geojson")
                    if isinstance(geojson_obj, dict):
                        features = geojson_obj.get("features", [])
                if features:
                    params["features"] = features
                    clean = _strip_ext(target_name)
                    params["table"] = _re.sub(r"[^a-zA-Z0-9_]", "", clean) or "capa_importada"
                    logger.info(
                        "[AgenteGIS] Features inyectadas para '%s': %d features (in-memory)",
                        params["table"], len(features),
                    )
                    return params

        # No se encontraron features → sanitizar nombre y dejar que intente PostGIS
        clean = _strip_ext(target_name)
        params["table"] = _re.sub(r"[^a-zA-Z0-9_]", "", clean) or target_name
        return params

    # ------------------------------------------------------------------
    # Fallback y utilidades
    # ------------------------------------------------------------------

    def _fallback_response(self, message: str) -> Dict:
        """Respuesta de emergencia cuando el proveedor de LLM no está disponible."""
        detail = self._last_error or self.llm.get_last_error()
        error_info = f" | Error: {detail}" if detail else ""
        return {
            "reply": f"No se pudo conectar al proveedor de IA "
                     f"'{self.llm.get_provider_type()}'{error_info}",
            "source": "fallback",
            "intent": "error",
            "agent_type": "fallback",
            "success": False,
        }

    def is_available(self) -> bool:
        """Verificar si el agente está disponible."""
        return self.llm.is_available()

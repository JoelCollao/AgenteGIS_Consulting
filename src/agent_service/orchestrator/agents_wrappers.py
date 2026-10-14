"""
Wrappers de agentes para integración LangGraph
================================================
Adaptan agentes existentes al flujo multiagente con routing condicional.

Arquitectura:
  IntentRouter → (condicional)
    ├── map_context  → MapContextAgent  → END
    ├── skill/conversational → GISAgent (function calling) → Validator → END
    └── (default)    → GISAgent → Validator → END

El AgenteConsultaGIS con function calling decide internamente si responder
conversacionalmente o invocar una skill. El IntentRouter solo desvía cuando
hay layersData y la pregunta es sobre el contexto del mapa.
"""

import logging
import re

from agent_service.agents.agente_consulta_gis import AgenteConsultaGIS

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patrones de intención sobre capas del mapa (se responden con layersData)
# ---------------------------------------------------------------------------
_MAP_CONTEXT_RE = re.compile(
    r"cu[aá]nt[ao]s?\s+capas|capas\s+hay|capas\s+en\s+el\s+mapa|"
    r"qu[eé]\s+capas|cu[aá]les\s+capas|listado?\s*de\s+capas|"
    r"capas\s+activas?|capas\s+visibles?|capas\s+cargadas?|"
    r"ver\s+(?:las\s+)?capas|mostrar\s+(?:las\s+)?capas|"
    r"cu[aá]ntos?\s+features?|features?\s+en\s+(?:el\s+)?(?:total|mapa)|"
    r"total\s+de\s+features?|total\s+de\s+registros?|"
    r"cu[aá]ntos?\s+(?:registros?|puntos?|pol[ií]gonos?|l[ií]neas?)\s+(?:hay|tiene)|"
    r"qu[eé]\s+campos|campos\s+(?:de\s+la\s+capa|tiene)|atributos?\s+(?:de\s+la\s+capa|que\s+tiene)|"
    r"geometr[ií]a\s+de\s+la\s+capa|tipo\s+de\s+geometr[ií]a|"
    r"informaci[oó]n\s+(?:del?\s+)?mapa|datos?\s+del?\s+mapa|"
    r"cuanto\s+mide|cu[aá]nto\s+(?:pesa|ocupa)",
    re.IGNORECASE,
)


# ═══════════════════════════════════════════════════════════════════════════
# Nodo 1: IntentRouter
# ═══════════════════════════════════════════════════════════════════════════

class IntentRouterAgent:
    """
    Clasifica rápidamente el intent del mensaje para routing del grafo.

    Planes posibles:
    - "map_context": hay layersData y la pregunta es sobre capas del mapa.
    - "gis_agent":   todo lo demás (el AgenteConsultaGIS decide con function calling).
    """

    def __call__(self, state):
        logger.info("[IntentRouter] Clasificando intent...")

        layers_data = (
            state.context.get("layersData")
            if isinstance(state.context, dict)
            else None
        )

        if layers_data and _MAP_CONTEXT_RE.search(state.message):
            state.result["plan"] = "map_context"
            logger.info("[IntentRouter] Plan: map_context")
        else:
            state.result["plan"] = "gis_agent"
            logger.info("[IntentRouter] Plan: gis_agent (function calling)")

        return state


# ═══════════════════════════════════════════════════════════════════════════
# Nodo 2a: MapContextAgent (responde con datos del frontend, sin LLM)
# ═══════════════════════════════════════════════════════════════════════════

class MapContextAgent:
    """Responde preguntas sobre capas del mapa usando layersData del frontend."""

    def __call__(self, state):
        logger.info("[MapContext] Respondiendo con contexto del mapa...")

        layers_data = (
            state.context.get("layersData")
            if isinstance(state.context, dict)
            else None
        )

        if not layers_data:
            state.result["reply"] = "No hay información de capas disponible en el contexto."
            state.result["source"] = "map_context"
            state.result["agent_type"] = "map_context"
            return state

        layers = layers_data.get("layers", [])
        total_visible = layers_data.get("totalVisibleLayers", len(layers))
        total_features = layers_data.get("totalFeatures", 0)
        msg = state.message.lower()

        reply = self._build_reply(msg, layers, total_visible, total_features)

        state.result["reply"] = reply
        state.result["source"] = "map_context"
        state.result["agent_type"] = "map_context"
        state.result["intent"] = "map_context"
        logger.info("[MapContext] Reply: %s", reply[:120])
        return state

    def _build_reply(self, msg, layers, total_visible, total_features):
        """Construye respuesta en lenguaje natural según la pregunta."""

        def _norm(s: str) -> str:
            return re.sub(r"[\s_\-]+", " ", s.lower()).strip()

        def _find_layer(hint: str):
            hint_norm = _norm(hint)
            for lyr in layers:
                if _norm(lyr.get("name", "")) == hint_norm:
                    return lyr
            for lyr in layers:
                if hint_norm in _norm(lyr.get("name", "")):
                    return lyr
            return None

        def _layer_from_msg():
            m = re.search(
                r"(?:capa|layer)\s+(?:de\s+(?:la\s+)?|del?\s+|la\s+|el\s+)?"
                r"([a-z\u00e0-\u00fc_][a-z\u00e0-\u00fc0-9_\s]{1,40}?)(?:\?|$|\.|\s+tiene|\s+hay)",
                msg, re.IGNORECASE,
            )
            if m:
                found = _find_layer(m.group(1).strip())
                if found:
                    return found
            for lyr in layers:
                if _norm(lyr.get("name", "")) in _norm(msg):
                    return lyr
            return None

        def _geom_summary():
            counts = {}
            for lyr in layers:
                gt = lyr.get("geometryType", "")
                if gt:
                    counts[gt] = counts.get(gt, 0) + 1
            if not counts:
                return ""
            parts = [f"{c} Tipo {t}" for t, c in counts.items()]
            return f"\n\n**Por tipo de geometría:** {', '.join(parts)}"

        # Registros de una capa específica
        if re.search(
            r"cu[aá]ntos?\s+(?:features?|registros?|puntos?|pol[ií]gonos?|l[ií]neas?)"
            r"\s+(?:tiene|hay\s+en)", msg,
        ):
            found = _layer_from_msg()
            if found:
                return (
                    f"La capa **'{found['name']}'** tiene "
                    f"**{found.get('featureCount', '?')} registros** "
                    f"de tipo {found.get('geometryType', 'desconocida')}."
                )

        # Total de features
        if re.search(
            r"cu[aá]ntos?\s+features?|total\s+de\s+(features?|registros?)", msg,
        ):
            return (
                f"En el mapa hay **{total_features} features en total** "
                f"distribuidos en {total_visible} "
                f"{'capa' if total_visible == 1 else 'capas'}."
                + _geom_summary()
            )

        # Campos / atributos
        if re.search(r"campos?|atributos?|columnas?", msg):
            found = _layer_from_msg()
            if found:
                fields = found.get("fields", [])
                return (
                    f"La capa **'{found['name']}'** tiene {len(fields)} campos: "
                    f"**{', '.join(fields)}**. "
                    f"Contiene {found.get('featureCount', '?')} features."
                )

        # Listar capas
        if re.search(r"capas?", msg):
            if not layers:
                return "No hay capas cargadas en el mapa."
            lines = []
            for lyr in layers:
                geom = lyr.get("geometryType", "")
                fc = lyr.get("featureCount", "?")
                lines.append(f"- **{lyr.get('name', '?')}** [{geom}] — {fc} features")
            return (
                f"Hay **{total_visible} capas** en el mapa "
                f"({total_features} features en total):\n\n"
                + "\n".join(lines)
                + _geom_summary()
            )

        # Genérica
        if not layers:
            return "No hay capas cargadas en el mapa."
        names = ", ".join(f"'{l.get('name', '?')}'" for l in layers[:5])
        return (
            f"El mapa tiene **{total_visible} capas** con "
            f"**{total_features} features**. Capas: {names}."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Nodo 2b: GISAgent (AgenteConsultaGIS con function calling)
# ═══════════════════════════════════════════════════════════════════════════

class GISAgent:
    """
    Delega al AgenteConsultaGIS que usa function calling.
    El LLM decide si responder conversacionalmente o invocar una skill.
    """

    def __init__(self):
        self.agent = AgenteConsultaGIS()

    async def __call__(self, state):
        logger.info("[GISAgent] Procesando con function calling...")

        context = state.context if isinstance(state.context, dict) else {}
        history = state.conversation_history if state.conversation_history else []

        response = await self.agent.process_query(
            message=state.message,
            context=context,
            conversation_history=history,
        )

        if isinstance(response, dict):
            state.result.update(response)
        else:
            state.result["reply"] = str(response)
            state.result["source"] = "gis_agent"

        logger.info(
            "[GISAgent] Reply: %s... | Source: %s",
            state.result.get("reply", "")[:80],
            state.result.get("source", "?"),
        )
        return state


# ═══════════════════════════════════════════════════════════════════════════
# Nodo 3: Validator
# ═══════════════════════════════════════════════════════════════════════════

class ValidatorAgent:
    """Valida que el resultado contenga una respuesta válida."""

    def __call__(self, state):
        logger.info("[Validator] Validando resultado...")

        if not isinstance(state.result, dict):
            state.result = {"valid": False, "reply": "Error interno de validación."}
            return state

        reply = state.result.get("reply", "")
        valid = bool(reply and len(reply.strip()) > 0)
        state.result["valid"] = valid

        if not valid:
            state.result["reply"] = "No se pudo generar una respuesta."
            state.result["source"] = "fallback"
            logger.warning("[Validator] Reply vacío o inválido")
        else:
            logger.info("[Validator] Resultado válido: %s...", reply[:60])

        return state
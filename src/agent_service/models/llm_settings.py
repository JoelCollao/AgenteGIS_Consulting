"""
Configuración del proveedor de LLM
===================================
Resuelve qué modelo usar y cómo conectarse, sin atar el código a ningún
proveedor. Mismo espíritu que LAYER_SOURCE_PROVIDER para las capas GIS.

Cómo se elige el proveedor
---------------------------
1. `LLM_PROVIDER` en el entorno (gana siempre). Ej: `azure`, `kimi`, `deepseek`.
2. Si no está: autodetección retro-compatible con la configuración actual —
   hay `AZURE_AGENT_NAME` → `azure_agent`; hay `AZURE_OPENAI_API_KEY` →
   `azure`; solo hay `AZURE_PROJECT_ENDPOINT` → `azure_foundry`
   (Managed Identity / az login).

Modelo "en blanco" vs. agente
------------------------------
`azure` y `azure_foundry` hablan con un modelo sin personalidad: el system
prompt y las tools viajan desde el código en cada petición. `azure_agent`
habla con un agente ya definido en el portal de Foundry, que trae sus propias
instrucciones y sus herramientas gestionadas (RAG sobre tus documentos,
búsqueda web…). Los cuatro modos conviven; el selector decide cuál se usa.

Presets y archivo de configuración
-----------------------------------
Los presets de abajo traen la `base_url` de cada proveedor conocido. Para
añadir uno nuevo sin tocar código, crea `llm_providers.json` en la raíz
(o apunta `LLM_PROVIDERS_FILE` a otra ruta):

    {
      "mi-vllm": {
        "kind": "openai_compatible",
        "base_url": "http://10.0.0.9:8000/v1",
        "model": "Qwen2.5-72B-Instruct",
        "api_key_env": "MI_VLLM_KEY"
      }
    }

Y luego `LLM_PROVIDER=mi-vllm`. Las entradas del archivo sobreescriben a los
presets internos del mismo nombre.

Overrides genéricos (aplican a cualquier proveedor):
    LLM_MODEL, LLM_API_KEY, LLM_BASE_URL, LLM_TIMEOUT
"""

import copy
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Raíz del repo: src/config/llm_settings.py → ../../..
_PROJECT_ROOT = Path(__file__).resolve().parents[3]  # raíz de agent-service/

# Adaptadores disponibles (ver src/infrastructure/llm/)
KIND_AZURE_OPENAI = "azure_openai"
KIND_AZURE_FOUNDRY = "azure_foundry"
KIND_AZURE_AGENT = "azure_agent"
KIND_OPENAI_COMPATIBLE = "openai_compatible"

_DEFAULT_TIMEOUT = 60.0

# Esfuerzo de razonamiento admitido por los modelos de razonamiento (gpt-5*,
# o-series). Menos esfuerzo = menos tokens de razonamiento = menos latencia
# y costo; en tool calling GIS "low" suele bastar.
_REASONING_EFFORTS = ("minimal", "low", "medium", "high")

# De dónde salen las instrucciones cuando se habla con un agente de Foundry.
#   portal → manda la definición del agente; el system prompt del código se
#            descarta (default: evita tener dos jefes dando órdenes distintas)
#   code   → manda el system prompt del código y se ignora el del portal
#   both   → instrucciones del agente + system prompt del código, en ese orden
AGENT_INSTRUCTIONS_MODES = ("portal", "code", "both")
_DEFAULT_AGENT_INSTRUCTIONS = "portal"

# ---------------------------------------------------------------------------
# Presets de proveedores conocidos
# ---------------------------------------------------------------------------
# `model` es solo un default razonable: los proveedores renombran modelos a
# menudo, así que confirma el identificador exacto en su documentación y
# sobreescríbelo con LLM_MODEL cuando haga falta.
# Todos los de kind=openai_compatible hablan el mismo /chat/completions con
# `tools`, que es lo único que necesita el agente GIS.
# Base común de los presets de OpenRouter. Se copia en profundidad al usarla:
# `load_llm_config` añade claves a `extra_body` y no debe compartirse entre presets.
_OPENROUTER_PRESET: Dict[str, Any] = {
    "kind": KIND_OPENAI_COMPATIBLE,
    "base_url": "https://openrouter.ai/api/v1",
    # Catálogo: https://openrouter.ai/models — el sufijo ":free" marca los
    # modelos gratuitos (con límite de peticiones). Cámbialo con LLM_MODEL.
    "model": "qwen/qwen3.8-27b:free",
    "api_key_env": "OPENROUTER_API_KEY",
    # Razonamiento activado; el SDK de OpenAI lo envía en el cuerpo tal cual.
    # Los modelos sin razonamiento lo ignoran.
    "extra_body": {"reasoning": {"enabled": True}},
}

_BUILTIN_PRESETS: Dict[str, Dict[str, Any]] = {
    # ── Azure ────────────────────────────────────────────────────────────
    "azure": {
        "kind": KIND_AZURE_OPENAI,
        # Nombre del deployment; lo normal es fijarlo con AZURE_OPENAI_DEPLOYMENT
        "model": "gpt-4.1-mini",
        "api_key_env": "AZURE_OPENAI_API_KEY",
    },
    "azure_foundry": {
        "kind": KIND_AZURE_FOUNDRY,
        "model": "gpt-4.1-mini",
    },
    "azure_agent": {
        # Sin "model" a propósito: el deployment lo fija la definición del
        # agente en el portal, así que no se envía en la petición.
        "kind": KIND_AZURE_AGENT,
    },
    # ── Compatibles con la API de OpenAI ─────────────────────────────────
    "openai": {
        "kind": KIND_OPENAI_COMPATIBLE,
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4.1-mini",
        "api_key_env": "OPENAI_API_KEY",
    },
    "kimi": {
        "kind": KIND_OPENAI_COMPATIBLE,
        # Endpoint global de Moonshot. Para la cuenta de China: api.moonshot.cn
        "base_url": "https://api.moonshot.ai/v1",
        # Alternativas habituales: kimi-k2-0905-preview, kimi-latest
        "model": "moonshot-v1-32k",
        "api_key_env": "MOONSHOT_API_KEY",
    },
    "deepseek": {
        "kind": KIND_OPENAI_COMPATIBLE,
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "api_key_env": "DEEPSEEK_API_KEY",
    },
    "groq": {
        "kind": KIND_OPENAI_COMPATIBLE,
        "base_url": "https://api.groq.com/openai/v1",
        "model": "llama-3.3-70b-versatile",
        "api_key_env": "GROQ_API_KEY",
    },
    "openrouter": copy.deepcopy(_OPENROUTER_PRESET),
    "openrouter_free": {
        # Igual que "openrouter", pero con modelos gratuitos de reserva: si el
        # principal devuelve 429 (pool compartido saturado), OpenRouter prueba
        # el siguiente de la lista en la MISMA petición. Verifica los nombres en
        # https://openrouter.ai/models?q=free — los :free cambian a menudo.
        # Lista propia sin tocar código: LLM_FALLBACK_MODELS=a:free,b:free
        **copy.deepcopy(_OPENROUTER_PRESET),
        "fallback_models": [
            "google/gemma-4-31b-it:free",
            "meta-llama/llama-3.3-70b-instruct:free",
        ],
    },
    "mistral": {
        "kind": KIND_OPENAI_COMPATIBLE,
        "base_url": "https://api.mistral.ai/v1",
        "model": "mistral-large-latest",
        "api_key_env": "MISTRAL_API_KEY",
    },
    "ollama": {
        "kind": KIND_OPENAI_COMPATIBLE,
        "base_url": "http://localhost:11434/v1",
        "model": "llama3.1",
        "api_key_env": None,  # Ollama ignora la key, pero el SDK exige una
    },
}


@dataclass
class LLMConfig:
    """Configuración resuelta y lista para construir el adaptador."""

    provider: str  # Nombre del preset elegido (ej: "kimi")
    kind: str  # Adaptador a instanciar
    model: str  # Modelo o *deployment* en Azure
    api_key: Optional[str] = None
    base_url: Optional[str] = None  # openai_compatible
    endpoint: Optional[str] = None  # azure_openai
    api_version: Optional[str] = None  # azure_openai
    project_endpoint: Optional[str] = None  # azure_foundry, azure_agent
    tenant_id: Optional[str] = None  # azure_foundry, azure_agent
    agent_name: Optional[str] = None  # azure_agent
    agent_version: Optional[str] = None  # azure_agent (None = última publicada)
    agent_instructions: str = _DEFAULT_AGENT_INSTRUCTIONS  # azure_agent
    timeout: float = _DEFAULT_TIMEOUT
    headers: Dict[str, str] = field(default_factory=dict)
    # Campos extra del cuerpo de la petición (p. ej. `reasoning` en OpenRouter)
    extra_body: Dict[str, Any] = field(default_factory=dict)
    # `reasoning_effort` de chat.completions (None = no se envía y el modelo
    # usa su valor por defecto). Solo para modelos de razonamiento: gpt-4.1 y
    # similares rechazan el parámetro con un 400.
    reasoning_effort: Optional[str] = None

    def describe(self) -> str:
        """Resumen para logs — nunca incluye la API key."""
        target = self.base_url or self.endpoint or self.project_endpoint or "?"
        if self.agent_name:
            target = f"{target} [agente={self.agent_name}]"
        return (
            f"{self.provider} (kind={self.kind}, model={self.model}, target={target})"
        )


# ---------------------------------------------------------------------------
# Carga de presets
# ---------------------------------------------------------------------------


def _load_presets() -> Dict[str, Dict[str, Any]]:
    """Presets internos + overrides del archivo JSON, si existe."""
    presets = {name: dict(cfg) for name, cfg in _BUILTIN_PRESETS.items()}

    file_path = os.getenv("LLM_PROVIDERS_FILE") or str(
        _PROJECT_ROOT / "llm_providers.json"
    )
    path = Path(file_path)
    if not path.is_file():
        return presets

    try:
        with path.open(encoding="utf-8") as fh:
            custom = json.load(fh)
        if not isinstance(custom, dict):
            raise ValueError("El archivo debe contener un objeto {nombre: config}")
        for name, cfg in custom.items():
            if not isinstance(cfg, dict):
                logger.warning(
                    "[llm_settings] Preset '%s' ignorado: no es un objeto", name
                )
                continue
            presets[name] = {**presets.get(name, {}), **cfg}
        logger.info("[llm_settings] %d presets cargados de %s", len(custom), path)
    except Exception as exc:
        logger.error("[llm_settings] No se pudo leer %s: %s", path, exc)

    return presets


def _autodetect_provider() -> str:
    """
    Retro-compatibilidad: sin LLM_PROVIDER, deduce el proveedor de las
    variables Azure que ya existen en el .env actual.

    AZURE_AGENT_NAME va primero porque solo se define a propósito: si está,
    la intención es hablar con el agente aunque siga habiendo una API key
    heredada en el archivo.
    """
    if os.getenv("AZURE_AGENT_NAME"):
        return "azure_agent"
    if os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_AI_API_KEY"):
        return "azure"
    if os.getenv("AZURE_PROJECT_ENDPOINT"):
        return "azure_foundry"
    return "azure"


def _resolve_agent_instructions() -> str:
    """Modo de instrucciones para azure_agent, validado contra la lista."""
    raw = (os.getenv("AZURE_AGENT_INSTRUCTIONS") or "").strip().lower()
    if not raw:
        return _DEFAULT_AGENT_INSTRUCTIONS
    if raw not in AGENT_INSTRUCTIONS_MODES:
        logger.warning(
            "[llm_settings] AZURE_AGENT_INSTRUCTIONS='%s' no es válido "
            "(%s); uso '%s'",
            raw,
            ", ".join(AGENT_INSTRUCTIONS_MODES),
            _DEFAULT_AGENT_INSTRUCTIONS,
        )
        return _DEFAULT_AGENT_INSTRUCTIONS
    return raw


def _resolve_azure_endpoint() -> Optional[str]:
    """
    Endpoint base de Azure OpenAI.
    Preferimos AZURE_OPENAI_ENDPOINT; si no está, lo derivamos del project
    endpoint de Foundry (https://<rec>.services.ai.azure.com/api/projects/<p>).
    """
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    if endpoint:
        return endpoint.rstrip("/")

    project_endpoint = os.getenv("AZURE_PROJECT_ENDPOINT")
    if project_endpoint:
        return project_endpoint.split("/api/projects")[0].rstrip("/")

    return None


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "[llm_settings] %s='%s' no es numérico; uso %s", name, raw, default
        )
        return default


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def _resolve_reasoning_effort(preset: Dict[str, Any]) -> Optional[str]:
    """LLM_REASONING_EFFORT gana sobre el preset; vacío = no enviar."""
    value = (os.getenv("LLM_REASONING_EFFORT") or preset.get("reasoning_effort") or "")
    value = value.strip().lower()
    if not value:
        return None
    if value not in _REASONING_EFFORTS:
        raise ValueError(
            f"LLM_REASONING_EFFORT='{value}' no es válido. "
            f"Opciones: {', '.join(_REASONING_EFFORTS)}."
        )
    return value


def load_llm_config() -> LLMConfig:
    """Resuelve la configuración del LLM desde el entorno y los presets."""
    presets = _load_presets()

    provider = (os.getenv("LLM_PROVIDER") or _autodetect_provider()).strip().lower()
    if provider not in presets:
        raise ValueError(
            f"LLM_PROVIDER='{provider}' no existe. "
            f"Disponibles: {', '.join(sorted(presets))}. "
            "Añade el tuyo en llm_providers.json si no está en la lista."
        )

    preset = presets[provider]
    kind = preset.get("kind", KIND_OPENAI_COMPATIBLE)
    timeout = _env_float(
        "LLM_TIMEOUT", float(preset.get("timeout") or _DEFAULT_TIMEOUT)
    )

    # API key: LLM_API_KEY gana; si no, la variable declarada por el preset.
    api_key = os.getenv("LLM_API_KEY")
    if not api_key:
        key_env = preset.get("api_key_env")
        if key_env:
            api_key = os.getenv(key_env)
    if not api_key and kind == KIND_AZURE_OPENAI:
        # El .env histórico admitía las dos variantes
        api_key = os.getenv("AZURE_OPENAI_API_KEY") or os.getenv("AZURE_AI_API_KEY")

    # Nombre del agente: solo lo usa azure_agent, pero se resuelve antes que
    # el modelo porque es de donde sale su identificador por defecto.
    agent_name = os.getenv("AZURE_AGENT_NAME") or preset.get("agent_name")
    if kind == KIND_AZURE_AGENT and not agent_name:
        raise ValueError(
            f"LLM_PROVIDER='{provider}' necesita AZURE_AGENT_NAME con el nombre "
            "del agente publicado en el proyecto de Foundry. Si lo que quieres es "
            "el modelo sin agente, usa LLM_PROVIDER=azure_foundry."
        )

    # Modelo: LLM_MODEL gana. En Azure, el nombre del deployment.
    model = os.getenv("LLM_MODEL")
    if not model and kind in (KIND_AZURE_OPENAI, KIND_AZURE_FOUNDRY):
        model = os.getenv("AZURE_OPENAI_DEPLOYMENT")
    if not model:
        model = preset.get("model")
    if not model and kind == KIND_AZURE_AGENT:
        # Placeholder para logs: el deployment real lo trae la definición del
        # agente y el adaptador lo sustituye al conectar.
        model = f"agent:{agent_name}"
    if not model:
        raise ValueError(
            f"No hay modelo configurado para LLM_PROVIDER='{provider}'. "
            "Define LLM_MODEL en el entorno o 'model' en el preset."
        )

    config = LLMConfig(
        provider=provider,
        kind=kind,
        model=model,
        api_key=api_key,
        timeout=timeout,
        headers=dict(preset.get("headers") or {}),
        extra_body=dict(preset.get("extra_body") or {}),
        reasoning_effort=_resolve_reasoning_effort(preset),
    )

    # Modelos de reserva (OpenRouter): [principal, *reserva] en `models`.
    # LLM_FALLBACK_MODELS (separados por coma) gana sobre el preset.
    env_fallbacks = os.getenv("LLM_FALLBACK_MODELS")
    fallbacks = (
        [m.strip() for m in env_fallbacks.split(",") if m.strip()]
        if env_fallbacks
        else list(preset.get("fallback_models") or [])
    )
    if fallbacks:
        config.extra_body["models"] = [model] + [m for m in fallbacks if m != model]

    if kind == KIND_AZURE_OPENAI:
        config.endpoint = (
            os.getenv("LLM_BASE_URL")
            or preset.get("base_url")
            or _resolve_azure_endpoint()
        )
        config.api_version = (
            os.getenv("AZURE_OPENAI_API_VERSION")
            or os.getenv("OPENAI_API_VERSION")
            or preset.get("api_version")
            or "2024-10-21"
        )
    elif kind == KIND_AZURE_FOUNDRY:
        config.project_endpoint = os.getenv("AZURE_PROJECT_ENDPOINT") or preset.get(
            "project_endpoint"
        )
        config.tenant_id = os.getenv("AZURE_TENANT_ID")
    elif kind == KIND_AZURE_AGENT:
        config.project_endpoint = os.getenv("AZURE_PROJECT_ENDPOINT") or preset.get(
            "project_endpoint"
        )
        config.tenant_id = os.getenv("AZURE_TENANT_ID")
        config.agent_name = agent_name
        config.agent_version = os.getenv("AZURE_AGENT_VERSION") or preset.get(
            "agent_version"
        )
        config.agent_instructions = _resolve_agent_instructions()
    else:
        config.base_url = os.getenv("LLM_BASE_URL") or preset.get("base_url")

    return config

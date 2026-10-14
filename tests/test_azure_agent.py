"""
Tests del adaptador de agentes de Foundry
==========================================
Lo que se cubre es la traducción entre el port (chat.completions) y la
Responses API, que es donde está el riesgo: un fallo ahí no revienta, se
manifiesta como "el agente ignora las tools" o "el RAG no se activa".

No tocan red: el cliente se inyecta a mano.
"""

import json
import os

from agent_service.prompts import GIS_TOOL_DEFINITIONS
from agent_service.models.llm_settings import (
    KIND_AZURE_AGENT,
    LLMConfig,
    load_llm_config,
)
from agent_service.models import build_llm_provider
from agent_service.models.azure_agent import AzureAgentProvider
from agent_service.models.misconfigured import MisconfiguredProvider

# ---------------------------------------------------------------------------
# Dobles
# ---------------------------------------------------------------------------


class _Obj:
    """Objeto con atributos arbitrarios, como los que devuelve el SDK."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeResponses:
    def __init__(self, result):
        self._result = result
        self.last_kwargs = None

    def create(self, **kwargs):
        self.last_kwargs = kwargs
        return self._result


class _FakeClient:
    def __init__(self, result):
        self.responses = _FakeResponses(result)


def _config(**overrides) -> LLMConfig:
    base = {
        "provider": "azure_agent",
        "kind": KIND_AZURE_AGENT,
        "model": "agent:gis-experto",
        "project_endpoint": "https://r.services.ai.azure.com/api/projects/p",
        "agent_name": "gis-experto",
    }
    base.update(overrides)
    return LLMConfig(**base)


def _provider(result=None, **overrides) -> AzureAgentProvider:
    provider = AzureAgentProvider(_config(**overrides))
    provider._client = _FakeClient(result or _Obj(output=[], model="gpt-4.1-mini"))
    return provider


# ---------------------------------------------------------------------------
# Historial → input de la Responses API
# ---------------------------------------------------------------------------


def test_input_descarta_system_y_conserva_el_orden():
    provider = _provider()
    items = provider._to_responses_input(
        [
            {"role": "system", "content": "Eres un asistente GIS"},
            {"role": "user", "content": "¿Cuántos predios hay?"},
            {"role": "assistant", "content": "Hay 120."},
        ]
    )

    assert items == [
        {"role": "user", "content": "¿Cuántos predios hay?"},
        {"role": "assistant", "content": "Hay 120."},
    ]


def test_input_traduce_la_ida_y_vuelta_de_una_tool():
    """El paso de narración reenvía la llamada y su resultado."""
    provider = _provider()
    items = provider._to_responses_input(
        [
            {"role": "user", "content": "Buffer de 50m sobre predios"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_abc",
                        "type": "function",
                        "function": {
                            "name": "buffer_analysis",
                            "arguments": '{"distance": 50}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call_abc",
                "content": '{"success": true}',
            },
        ]
    )

    assert items[1] == {
        "type": "function_call",
        "call_id": "call_abc",
        "name": "buffer_analysis",
        "arguments": '{"distance": 50}',
    }
    # El resultado va después de la llamada: la Responses API lo exige
    assert items[2] == {
        "type": "function_call_output",
        "call_id": "call_abc",
        "output": '{"success": true}',
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def test_tools_se_aplanan_conservando_el_esquema():
    provider = _provider()
    flattened = provider._to_responses_tools(GIS_TOOL_DEFINITIONS)

    assert len(flattened) == len(GIS_TOOL_DEFINITIONS)

    buffer_tool = next(t for t in flattened if t["name"] == "buffer_analysis")
    original = next(
        t["function"]
        for t in GIS_TOOL_DEFINITIONS
        if t["function"]["name"] == "buffer_analysis"
    )

    assert buffer_tool["type"] == "function"
    assert "function" not in buffer_tool  # ya no está anidado
    assert buffer_tool["parameters"] == original["parameters"]
    assert buffer_tool["strict"] is False


def test_nunca_se_envian_tools_en_la_peticion():
    """
    El endpoint del agente responde 400 'Not allowed when agent is specified'
    a cualquier `tools`. Las funciones GIS van en su definición, publicadas
    con scripts/sync_agent_tools.py.
    """
    provider = _provider()

    provider.chat([{"role": "user", "content": "hola"}], tools=GIS_TOOL_DEFINITIONS)

    assert "tools" not in provider._client.responses.last_kwargs


def test_declared_tools_lee_todas_las_del_agente():
    """El sync script necesita las gestionadas y las funciones por separado."""
    provider = _provider()
    definition = _Obj(
        tools=[
            _Obj(as_dict=lambda: {"type": "file_search", "vector_store_ids": ["v"]}),
            _Obj(as_dict=lambda: {"type": "function", "name": "buffer_analysis"}),
        ]
    )

    declared = provider._declared_tools(definition)

    assert [t["type"] for t in declared] == ["file_search", "function"]


def test_la_narracion_prohibe_volver_a_actuar():
    """Tras ejecutar la skill solo queda redactar."""
    provider = _provider()
    provider.chat(
        [
            {"role": "user", "content": "Buffer de 50m"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "function": {"name": "buffer_analysis", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        ]
    )

    kwargs = provider._client.responses.last_kwargs
    assert kwargs["tool_choice"] == "none"
    assert "tools" not in kwargs


def test_el_primer_turno_deja_decidir_al_agente():
    """Con tools, tool_choice no se fuerza: el agente elige."""
    provider = _provider()
    provider.chat([{"role": "user", "content": "hola"}], tools=GIS_TOOL_DEFINITIONS)

    assert "tool_choice" not in provider._client.responses.last_kwargs


def test_no_se_envian_model_ni_temperature():
    """Ambos pertenecen a la definición del agente y devuelven 400."""
    provider = _provider()
    provider.chat([{"role": "user", "content": "hola"}])

    kwargs = provider._client.responses.last_kwargs
    assert "model" not in kwargs
    assert "temperature" not in kwargs


# ---------------------------------------------------------------------------
# Instrucciones
# ---------------------------------------------------------------------------


_MESSAGES_CON_SYSTEM = [
    {"role": "system", "content": "PROMPT DEL CODIGO"},
    {"role": "user", "content": "hola"},
]


def test_nunca_se_envian_instrucciones_sea_cual_sea_el_modo():
    """
    El endpoint rechaza `instructions` igual que `tools`. El modo solo lo lee
    scripts/sync_agent_tools.py para decidir qué prompt escribe en el agente.
    """
    for modo in ("portal", "code", "both"):
        provider = _provider(agent_instructions=modo)
        provider._agent_instructions = "PROMPT DEL PORTAL"

        provider.chat(_MESSAGES_CON_SYSTEM)

        kwargs = provider._client.responses.last_kwargs
        assert "instructions" not in kwargs, f"modo {modo} envió instructions"


def test_el_system_prompt_del_codigo_no_se_cuela_en_el_input():
    """Si viajara como mensaje, competiría con las instrucciones del agente."""
    provider = _provider()
    provider.chat(_MESSAGES_CON_SYSTEM)

    enviado = provider._client.responses.last_kwargs["input"]

    assert enviado == [{"role": "user", "content": "hola"}]


# ---------------------------------------------------------------------------
# Respuesta → port
# ---------------------------------------------------------------------------


def test_normalize_extrae_texto():
    provider = _provider()
    raw = _Obj(
        output=[
            _Obj(
                type="message",
                content=[_Obj(type="output_text", text="Un buffer es una zona.")],
            )
        ],
        model="gpt-4.1-mini",
    )

    response = provider._normalize(raw)

    assert response.content == "Un buffer es una zona."
    assert response.has_tool_calls is False
    assert response.model == "gpt-4.1-mini"


def test_normalize_extrae_la_llamada_con_su_call_id():
    provider = _provider()
    raw = _Obj(
        output=[
            _Obj(
                type="function_call",
                id="fc_interno",
                call_id="call_abc",
                name="buffer_analysis",
                arguments='{"distance": 50}',
            )
        ],
        model="gpt-4.1-mini",
    )

    response = provider._normalize(raw)

    assert response.has_tool_calls is True
    # Es call_id, no id: es el que hay que devolver con el resultado
    assert response.tool_calls[0].id == "call_abc"
    assert response.tool_calls[0].name == "buffer_analysis"
    assert json.loads(response.tool_calls[0].arguments) == {"distance": 50}


def test_normalize_ignora_el_trabajo_interno_del_agente():
    """Las búsquedas de RAG y web son ruido: su conclusión ya está en el texto."""
    provider = _provider()
    raw = _Obj(
        output=[
            _Obj(type="file_search_call", id="fs_1", queries=["norma cartografica"]),
            _Obj(
                type="message",
                content=[_Obj(type="output_text", text="Según la norma...")],
            ),
        ],
        model="gpt-4.1-mini",
    )

    response = provider._normalize(raw)

    assert response.content == "Según la norma..."
    assert response.tool_calls == []


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------


def _con_entorno(**variables):
    """Aplica variables y devuelve una función para restaurar el entorno."""
    previos = {k: os.environ.get(k) for k in variables}
    for key, value in variables.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value

    def restaurar():
        for key, value in previos.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    return restaurar


_ENTORNO_AGENTE = {
    "LLM_PROVIDER": "azure_agent",
    "AZURE_PROJECT_ENDPOINT": "https://r.services.ai.azure.com/api/projects/p",
    "AZURE_AGENT_NAME": "gis-experto",
    "LLM_MODEL": None,
    "AZURE_AGENT_VERSION": None,
    "AZURE_AGENT_INSTRUCTIONS": None,
}


def test_config_resuelve_el_modo_agente():
    restaurar = _con_entorno(**_ENTORNO_AGENTE)
    try:
        config = load_llm_config()
        assert config.kind == KIND_AZURE_AGENT
        assert config.agent_name == "gis-experto"
        assert config.agent_instructions == "portal"
        assert config.model == "agent:gis-experto"
    finally:
        restaurar()


def test_config_autodetecta_el_agente_sin_selector():
    restaurar = _con_entorno(**{**_ENTORNO_AGENTE, "LLM_PROVIDER": None})
    try:
        assert load_llm_config().kind == KIND_AZURE_AGENT
    finally:
        restaurar()


def test_sin_nombre_de_agente_la_api_sigue_levantando():
    """Mal configurado no puede tumbar la app: las capas no dependen del LLM."""
    restaurar = _con_entorno(**{**_ENTORNO_AGENTE, "AZURE_AGENT_NAME": None})
    try:
        provider = build_llm_provider()
        assert isinstance(provider, MisconfiguredProvider)
        assert "AZURE_AGENT_NAME" in provider.get_last_error()
        assert provider.is_available() is False
    finally:
        restaurar()


def test_la_factory_devuelve_el_adaptador_de_agente():
    restaurar = _con_entorno(**_ENTORNO_AGENTE)
    try:
        provider = build_llm_provider()
        assert isinstance(provider, AzureAgentProvider)
        assert provider.get_provider_type() == "azure_agent"
    finally:
        restaurar()

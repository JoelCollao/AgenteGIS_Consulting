"""
Adaptador: Agente de Azure AI Foundry (Entra ID, sin API key)
==============================================================
Cuarto modo del proveedor de LLM. Los otros tres hablan con un modelo "en
blanco": el system prompt y las tools viajan desde el código en cada
petición. Este habla con un AGENTE ya publicado en el proyecto de Foundry,
que trae de fábrica sus instrucciones y sus herramientas gestionadas —RAG
sobre tus documentos (file_search / azure_ai_search), búsqueda web
(bing_grounding), etc.— configuradas desde el portal.

El endpoint del agente es SELLADO
---------------------------------
Todo lo que forma parte de la definición del agente se rechaza si se manda por
petición. Comprobado contra el servicio:

    input, tool_choice, model  →  se aceptan
    tools, instructions, temperature  →  400 invalid_payload
                                         "Not allowed when agent is specified"

De ahí la consecuencia importante: **las funciones GIS no se pueden declarar
por petición como en los otros adaptadores; tienen que estar en la definición
del agente**. Se publican desde el código con:

    python scripts/sync_agent_tools.py --apply

que toma GIS_TOOL_DEFINITIONS y crea una versión nueva del agente
conservando sus herramientas gestionadas y sus instrucciones. El código sigue
siendo la fuente de verdad de las funciones; el portal, la del prompt.

Si el agente no las tiene declaradas, la aplicación no falla: responde
conversacionalmente y nunca ejecuta una skill. Por eso se avisa por log al
conectar.

Reparto de herramientas
------------------------
El agente ejecuta por su cuenta las gestionadas (RAG, búsqueda web) y vuelve
con la respuesta ya documentada. Las operaciones GIS son lo contrario: viven
en esta máquina y en la base de datos, así que él solo las pide y este código
las ejecuta y le devuelve el resultado.

Protocolo
----------
El endpoint del agente habla la Responses API, no chat.completions. El port
del dominio es chat.completions, así que aquí se traduce en ambos sentidos
(ver `_to_responses_input` y `_normalize`).

Autenticación: DefaultAzureCredential (Managed Identity en Azure, `az login`
en local). No admite API key — para eso están LLM_PROVIDER=azure o
azure_foundry.

Variables:
    AZURE_PROJECT_ENDPOINT       https://<recurso>.services.ai.azure.com/api/projects/<proyecto>
    AZURE_AGENT_NAME             Nombre del agente publicado en el proyecto
    AZURE_AGENT_VERSION          Versión a fijar (opcional; default: la última)
    AZURE_AGENT_INSTRUCTIONS     portal | code | both. NO afecta a la petición
                                 (el endpoint rechaza `instructions`): lo lee
                                 scripts/sync_agent_tools.py para decidir qué
                                 prompt escribe en la definición del agente.
    AZURE_TENANT_ID              Tenant de Entra ID (opcional, para multi-tenant)
"""

import logging
from typing import Any, Dict, List, Optional

from agent_service.models.illm_provider import ILLMProvider, LLMResponse, LLMToolCall
from agent_service.models.base import OpenAIChatProviderBase

logger = logging.getLogger(__name__)


class AzureAgentProvider(OpenAIChatProviderBase):
    """Agente de Azure AI Foundry consumido por su endpoint OpenAI"""

    # Igual que el adaptador de Foundry: obtener el token puede colgarse
    # (IMDS inalcanzable, tenant equivocado…) y sin este tope la app no
    # arrancaría. Cubre también la lectura de la definición del agente.
    _CONNECT_TIMEOUT_SECONDS = 20

    # El endpoint de agentes valida contra este scope, no contra el de
    # cognitiveservices que usa el modelo suelto.
    _TOKEN_SCOPE = "https://ai.azure.com/.default"

    def __init__(self, config):
        super().__init__(config)
        # Se rellenan al conectar, leyendo la definición publicada
        self._agent_tools: Optional[List[Dict[str, Any]]] = None
        self._agent_instructions: Optional[str] = None
        self._agent_model: Optional[str] = None

    def get_provider_type(self) -> str:
        return "azure_agent"

    def get_model(self) -> str:
        """El deployment real del agente, si se pudo leer su definición."""
        return self._agent_model or self._config.model

    # ── Conexión ──────────────────────────────────────────────────────

    def _build_client(self) -> Any:
        """
        Construye el cliente. Se ejecuta en el hilo en segundo plano que
        gestiona `OpenAIChatProviderBase._ensure_client` — bloquea sin miedo,
        el límite de espera hacia el usuario ya lo aplica esa capa, y si
        tarda más de `_CONNECT_TIMEOUT_SECONDS` (típico de un 'az login' en
        frío) el intento sigue avanzando aquí y el próximo request lo
        adopta en vez de relanzarlo desde cero.
        """
        cfg = self._config

        if not cfg.project_endpoint:
            raise ValueError(
                "Falta AZURE_PROJECT_ENDPOINT "
                "(https://<recurso>.services.ai.azure.com/api/projects/<proyecto>)."
            )
        if not cfg.agent_name:
            raise ValueError(
                "Falta AZURE_AGENT_NAME con el nombre del agente publicado en el "
                "proyecto de Foundry."
            )

        logger.info(
            "[LLM] Conectando al agente '%s' de Azure AI Foundry...",
            cfg.agent_name,
        )

        from azure.ai.projects import AIProjectClient

        from agent_service.models.entra import build_credential

        credential = build_credential(cfg.tenant_id)
        credential.get_token(self._TOKEN_SCOPE)

        # allow_preview es obligatorio para apuntar el cliente OpenAI al
        # endpoint del agente en vez de al del proyecto.
        project = AIProjectClient(
            endpoint=cfg.project_endpoint,
            credential=credential,
            allow_preview=True,
        )
        self._load_agent_definition(project)
        return project.get_openai_client(agent_name=cfg.agent_name)

    def _load_agent_definition(self, project: Any) -> None:
        """
        Lee la definición publicada: modelo, instrucciones y herramientas.

        Es un diagnóstico, no un requisito: si falla, el chat sigue
        funcionando. Puede que la identidad tenga permiso para invocar el
        agente pero no para leer su ficha.

        Lo que sí importa de aquí es la lista de FUNCIONES declaradas. Si está
        vacía, el agente nunca pedirá ejecutar una skill GIS y la aplicación
        se quedará en modo conversacional sin que nada falle — un síntoma
        difícil de diagnosticar si no se avisa.
        """
        cfg = self._config
        try:
            if cfg.agent_version:
                details = project.agents.get_version(cfg.agent_name, cfg.agent_version)
            else:
                details = project.agents.get(cfg.agent_name).versions.latest

            definition = getattr(details, "definition", None)
            self._agent_model = getattr(definition, "model", None)
            self._agent_instructions = getattr(definition, "instructions", None)
            self._agent_tools = self._declared_tools(definition)

            managed = [t for t in self._agent_tools if t.get("type") != "function"]
            functions = [t for t in self._agent_tools if t.get("type") == "function"]

            logger.info(
                "[LLM] Agente '%s' v%s → model=%s | gestionadas: %s | funciones: %s",
                cfg.agent_name,
                getattr(details, "version", "?"),
                self._agent_model or "?",
                ", ".join(t.get("type", "?") for t in managed) or "ninguna",
                ", ".join(t.get("name", "?") for t in functions) or "ninguna",
            )

            if not functions:
                logger.warning(
                    "[LLM] El agente '%s' no declara ninguna función: responderá "
                    "conversacionalmente pero NUNCA ejecutará una skill GIS. "
                    "Publícalas con: python scripts/sync_agent_tools.py --apply",
                    cfg.agent_name,
                )
        except Exception as exc:  # pylint: disable=broad-except
            self._agent_tools = None
            logger.warning(
                "[LLM] No se pudo leer la definición del agente '%s': %s. "
                "El chat funcionará, pero no puedo avisarte de si tiene las "
                "funciones GIS declaradas ni de qué herramientas gestionadas usa.",
                cfg.agent_name,
                exc,
            )

    @staticmethod
    def _declared_tools(definition: Any) -> List[Dict[str, Any]]:
        """Todas las herramientas de la definición, como diccionarios."""
        tools: List[Dict[str, Any]] = []
        for tool in getattr(definition, "tools", None) or []:
            as_dict = getattr(tool, "as_dict", None)
            tools.append(as_dict() if callable(as_dict) else dict(tool))
        return tools

    # ── Traducción: port (chat.completions) → Responses API ───────────

    @staticmethod
    def _to_responses_input(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Convierte el historial canónico en items de la Responses API.

        Los mensajes `system` no viajan aquí: se resuelven aparte como
        `instructions`. El orden se respeta, que es lo que garantiza que cada
        `function_call` preceda a su `function_call_output`.
        """
        items: List[Dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")

            if role == "system":
                continue

            if role == "tool":
                items.append(
                    {
                        "type": "function_call_output",
                        "call_id": msg.get("tool_call_id") or "",
                        "output": msg.get("content") or "",
                    }
                )
                continue

            for call in msg.get("tool_calls") or []:
                function = call.get("function") or {}
                items.append(
                    {
                        "type": "function_call",
                        "call_id": call.get("id") or "",
                        "name": function.get("name") or "",
                        "arguments": function.get("arguments") or "{}",
                    }
                )

            content = msg.get("content")
            if content:
                items.append({"role": role or "user", "content": content})

        return items

    @staticmethod
    def _to_responses_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Aplana las definiciones de function calling.

        chat.completions las anida bajo "function"; la Responses API las
        quiere al mismo nivel que el tipo.
        """
        flattened: List[Dict[str, Any]] = []

        for tool in tools:
            function = tool.get("function") or {}
            name = function.get("name") or tool.get("name")
            if not name:
                continue
            flattened.append(
                {
                    "type": "function",
                    "name": name,
                    "description": function.get("description")
                    or tool.get("description")
                    or "",
                    "parameters": function.get("parameters")
                    or tool.get("parameters")
                    or {"type": "object", "properties": {}},
                    # Los esquemas GIS no marcan todos los campos como
                    # obligatorios, que es lo que exige el modo estricto.
                    "strict": False,
                }
            )

        return flattened

    # ── Petición ──────────────────────────────────────────────────────

    def _request_kwargs(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]],
        tool_choice: str,
    ) -> Dict[str, Any]:
        """
        Cuerpo de la petición al agente (lo usan `chat` y `achat` de la base).

        El parámetro `tools` se ignora a propósito. El endpoint del agente
        rechaza con 400 ("Not allowed when agent is specified") todo lo que
        pertenezca a su definición — `tools`, `instructions`, `temperature`—,
        así que las funciones GIS tienen que estar declaradas EN EL AGENTE.
        Para publicarlas desde el código: `python scripts/sync_agent_tools.py`.

        Lo único que sí admite por petición es `tool_choice`, y se usa para el
        turno de narración: ahí ya se ejecutó la skill y solo queda redactar.
        """
        # Sin "model", "tools" ni "instructions": lo fija la definición.
        kwargs: Dict[str, Any] = {"input": self._to_responses_input(messages)}

        if not tools:
            # Turno de narración: que no vuelva a invocar nada.
            kwargs["tool_choice"] = "none"

        if self._config.headers:
            kwargs["extra_headers"] = self._config.headers

        return kwargs

    def _unavailable_message(self) -> str:
        return (
            f"Agente '{self._config.agent_name}' no disponible: "
            f"{self._last_error or 'sin credenciales configuradas'}"
        )

    async def _ainvoke(self, aclient: Any, kwargs: Dict[str, Any]) -> Any:
        return await aclient.responses.create(**kwargs)

    def _invoke(self, client: Any, kwargs: Dict[str, Any]) -> Any:
        return client.responses.create(**kwargs)

    # Los eventos de stream de la Responses API no son chunks de
    # chat.completions: se usa la versión del port (respuesta entera de una vez).
    astream_chat = ILLMProvider.astream_chat

    # ── Traducción: Responses API → port ──────────────────────────────

    def _normalize(self, raw: Any) -> LLMResponse:
        """
        Extrae texto y llamadas a función de la salida de la Responses API.

        Los items de herramientas gestionadas (búsquedas de RAG, consultas
        web) se ignoran a propósito: son trabajo interno del agente y su
        conclusión ya viene incorporada en el texto.
        """
        tool_calls: List[LLMToolCall] = []
        text_parts: List[str] = []

        for item in getattr(raw, "output", None) or []:
            item_type = getattr(item, "type", None)

            if item_type == "function_call":
                tool_calls.append(
                    LLMToolCall(
                        # call_id es el que hay que devolver en el resultado;
                        # id identifica al item dentro de la respuesta.
                        id=getattr(item, "call_id", None)
                        or getattr(item, "id", "")
                        or "",
                        name=getattr(item, "name", "") or "",
                        arguments=getattr(item, "arguments", "") or "{}",
                    )
                )
            elif item_type == "message":
                for part in getattr(item, "content", None) or []:
                    text = getattr(part, "text", None)
                    if text:
                        text_parts.append(text)

        content = getattr(raw, "output_text", None) or "\n".join(text_parts) or None

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=getattr(raw, "model", None),
            raw=raw,
        )

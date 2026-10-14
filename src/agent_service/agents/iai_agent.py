"""
Port: IAIAgent
===============
Interfaz abstracta para agentes de IA.
Permite intercambiar la implementación del agente (Azure Foundry, OpenAI, etc.)
sin afectar la lógica de orquestación.
"""

from abc import ABC, abstractmethod
from typing import Dict, Optional


class IAIAgent(ABC):
    """Interfaz para agentes conversacionales de IA"""

    @abstractmethod
    async def process_query(self, message: str, context: Optional[Dict] = None) -> Dict:
        """Procesar una consulta del usuario y retornar respuesta (async: espera al LLM)"""
        ...

    @abstractmethod
    def is_available(self) -> bool:
        """Verificar si el agente está disponible"""
        ...

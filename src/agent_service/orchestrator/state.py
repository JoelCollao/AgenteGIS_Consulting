"""
MultiAgentState - Estado compartido del flujo multiagente
==========================================================
Dataclass reutilizable que define el estado que fluye
entre los nodos del grafo LangGraph.
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class MultiAgentState:
    """Estado compartido entre los agentes del flujo LangGraph"""

    message: str
    context: Dict = field(default_factory=dict)
    result: Dict = field(default_factory=dict)
    conversation_history: List[Dict] = field(default_factory=list)
    session_id: str = ""

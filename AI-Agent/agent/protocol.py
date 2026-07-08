from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional


ToolName = str


@dataclass
class ToolCallRequest:
    tool: ToolName
    args: Dict[str, Any]


@dataclass
class AgentPlannerDecision:
    kind: Literal["tool_call", "final_answer"]
    tool_call: Optional[ToolCallRequest] = None
    final_answer: Optional[str] = None


@dataclass
class ActionRequest:
    request_id: str
    action_type: str
    description: str
    payload: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "action_type": self.action_type,
            "description": self.description,
            "payload": self.payload,
        }


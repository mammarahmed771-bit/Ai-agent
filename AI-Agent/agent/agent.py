from __future__ import annotations

from typing import Any, Dict, List

from google import genai

from agent.executor import AgentExecutor
from agent.memory import SQLiteMemory
from agent.planner import Planner
from agent.tool_router import ToolRouter


class Agent:
    """High-level facade for the planner, router, and multi-step executor."""

    def __init__(
        self,
        client: genai.Client,
        tools: Dict[str, Any],
        memory: SQLiteMemory,
        permission_callback: Any,
        model: str = "gemini-2.5-flash",
        max_steps: int = 24,
    ) -> None:
        planner = Planner(client=client, model=model)
        router = ToolRouter(tools=tools, permission_callback=permission_callback)
        self.executor = AgentExecutor(
            planner=planner,
            tool_router=router,
            memory=memory,
            max_steps=max_steps,
        )

    def run(
        self,
        user_text: str,
        history: List[Dict[str, Any]],
        context: Dict[str, Any],
        tool_events: List[Dict[str, Any]],
        session_state: Dict[str, Any],
        start_step: int = 0,
    ) -> Dict[str, Any]:
        return self.executor.run(
            user_text=user_text,
            history=history,
            context=context,
            tool_summaries=tool_events,
            session_state=session_state,
            start_step=start_step,
        )

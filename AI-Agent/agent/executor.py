from __future__ import annotations

import time
from typing import Any, Dict, List

from agent.memory import SQLiteMemory
from agent.planner import Planner
from agent.protocol import ActionRequest, ToolCallRequest
from agent.tool_router import ToolRouter


class AgentExecutor:
    """Run planner/tool cycles and suspend cleanly for user approvals."""

    def __init__(
        self,
        planner: Planner,
        tool_router: ToolRouter,
        memory: SQLiteMemory,
        max_steps: int = 24,
    ):
        self.planner = planner
        self.tool_router = tool_router
        self.memory = memory
        self.max_steps = max_steps

    def run(
        self,
        user_text: str,
        history: List[Dict[str, Any]],
        context: Dict[str, Any],
        tool_summaries: List[Dict[str, Any]],
        session_state: Dict[str, Any],
        start_step: int = 0,
    ) -> Dict[str, Any]:
        history_text = self._history_text(history)
        next_step = start_step

        while next_step < self.max_steps:
            decision = self.planner.decide(
                user_text=user_text,
                context=context,
                tool_summaries=tool_summaries,
                history_text=history_text,
            )
            if decision.kind == "final_answer":
                return {
                    "success": True,
                    "final_reply": decision.final_answer or "",
                    "tool_events": tool_summaries,
                    "final_step": next_step,
                }

            if decision.tool_call is None:
                return {
                    "success": False,
                    "error": "Planner produced an empty tool call.",
                    "tool_events": tool_summaries,
                    "next_step": next_step,
                }

            tool_call: ToolCallRequest = decision.tool_call
            started = time.perf_counter()
            tool_result = self.tool_router.run_tool(tool_call, session_state=session_state)
            duration_ms = round((time.perf_counter() - started) * 1_000, 1)

            if isinstance(tool_result, ActionRequest):
                return {
                    "success": False,
                    "action_request": tool_result.to_dict(),
                    "pending_tool": {"tool": tool_call.tool, "args": tool_call.args},
                    "tool_events": tool_summaries,
                    "next_step": next_step,
                }

            if isinstance(tool_result, dict) and tool_result.get("action_request"):
                return {
                    "success": False,
                    "action_request": tool_result["action_request"],
                    "pending_tool": {"tool": tool_call.tool, "args": tool_call.args},
                    "tool_events": tool_summaries,
                    "next_step": next_step,
                }

            event = {
                "tool": tool_call.tool,
                "action": tool_call.args.get("action"),
                "args": self._redact_args(tool_call.args),
                "result": tool_result,
                "status": "completed" if tool_result.get("ok") else "failed",
                "step": next_step,
                "duration_ms": duration_ms,
                "timestamp": time.time(),
            }
            tool_summaries.append(event)
            history_text += "\nTOOL_EVENT: " + str(event)[-8_000:]
            next_step += 1

        return {
            "success": False,
            "error": f"Maximum of {self.max_steps} agent steps reached.",
            "tool_events": tool_summaries,
            "next_step": next_step,
        }

    @staticmethod
    def _history_text(history: List[Dict[str, Any]]) -> str:
        lines = []
        for message in history[-12:]:
            sender = str(message.get("sender", ""))
            text = str(message.get("text", ""))
            lines.append(f"{sender}: {text[:4_000]}")
        return "\n".join(lines)

    @staticmethod
    def _redact_args(args: Dict[str, Any]) -> Dict[str, Any]:
        redacted = dict(args)
        if "content" in redacted and len(str(redacted["content"])) > 1_000:
            redacted["content"] = str(redacted["content"])[:1_000] + "…"
        if "code" in redacted and len(str(redacted["code"])) > 1_000:
            redacted["code"] = str(redacted["code"])[:1_000] + "…"
        if "files" in redacted:
            redacted["files"] = "[batch file contents omitted from event]"
        return redacted

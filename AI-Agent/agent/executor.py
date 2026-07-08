from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional

from agent.memory import SQLiteMemory
from agent.planner import Planner
from agent.protocol import ActionRequest, ToolCallRequest
from agent.tool_router import ToolRouter


LOGGER = logging.getLogger(__name__)
ProgressCallback = Callable[[Dict[str, Any]], None]


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
        pending_tool: Optional[Dict[str, Any]] = None,
        progress_callback: Optional[ProgressCallback] = None,
    ) -> Dict[str, Any]:
        history_text = self._history_text(history)
        next_step = start_step
        resumed_tool = self._pending_tool_call(pending_tool)
        failed_signatures: Dict[str, int] = {}

        while next_step < self.max_steps:
            if resumed_tool is not None:
                tool_call = resumed_tool
                resumed_tool = None
            else:
                try:
                    decision = self.planner.decide(
                        user_text=user_text,
                        context=context,
                        tool_summaries=tool_summaries,
                        history_text=history_text,
                    )
                except Exception as exc:
                    if self._is_retryable_planner_error(exc):
                        LOGGER.warning(
                            "Planner provider failed transiently at step %s: %s",
                            next_step,
                            exc,
                        )
                        raise
                    LOGGER.exception("Planner decision failed at step %s", next_step)
                    return self._failure(
                        f"Planner failed at step {next_step}: {exc}", tool_summaries, next_step
                    )
                if decision.kind == "final_answer":
                    self._notify(progress_callback, {"status": "completed", "step": next_step})
                    return {
                        "success": True,
                        "final_reply": decision.final_answer or "",
                        "tool_events": tool_summaries,
                        "final_step": next_step,
                    }
                if decision.tool_call is None:
                    return self._failure(
                        "Planner produced an empty tool call.", tool_summaries, next_step
                    )
                tool_call = decision.tool_call

            self._notify(
                progress_callback,
                {
                    "status": "running_tool",
                    "step": next_step,
                    "tool": tool_call.tool,
                    "action": tool_call.args.get("action"),
                },
            )
            started = time.perf_counter()
            tool_result = self.tool_router.run_tool(tool_call, session_state=session_state)
            duration_ms = round((time.perf_counter() - started) * 1_000, 1)

            if isinstance(tool_result, ActionRequest):
                response = {
                    "success": False,
                    "action_request": tool_result.to_dict(),
                    "pending_tool": {"tool": tool_call.tool, "args": tool_call.args},
                    "tool_events": tool_summaries,
                    "next_step": next_step,
                }
                self._notify(
                    progress_callback,
                    {"status": "waiting_for_approval", "step": next_step, "tool": tool_call.tool},
                )
                return response

            if isinstance(tool_result, dict) and tool_result.get("action_request"):
                response = {
                    "success": False,
                    "action_request": tool_result["action_request"],
                    "pending_tool": {"tool": tool_call.tool, "args": tool_call.args},
                    "tool_events": tool_summaries,
                    "next_step": next_step,
                }
                self._notify(
                    progress_callback,
                    {"status": "waiting_for_approval", "step": next_step, "tool": tool_call.tool},
                )
                return response

            event = {
                "id": f"event_{next_step}_{int(time.time() * 1000)}",
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
            history_text += "\nTOOL_EVENT: " + json.dumps(event, ensure_ascii=False, default=str)[-8_000:]
            next_step += 1
            self._notify(
                progress_callback,
                {
                    "status": event["status"],
                    "step": next_step,
                    "tool": tool_call.tool,
                    "action": tool_call.args.get("action"),
                    "event": event,
                },
            )

            signature = self._tool_signature(tool_call)
            if tool_result.get("ok"):
                failed_signatures.pop(signature, None)
            else:
                failed_signatures[signature] = failed_signatures.get(signature, 0) + 1
                if failed_signatures[signature] >= 3:
                    return self._failure(
                        f"Tool call {tool_call.tool}.{tool_call.args.get('action')} failed three times with unchanged arguments.",
                        tool_summaries,
                        next_step,
                    )

        return self._failure(
            f"Maximum of {self.max_steps} agent steps reached.", tool_summaries, next_step
        )

    @staticmethod
    def _pending_tool_call(value: Optional[Dict[str, Any]]) -> Optional[ToolCallRequest]:
        if not value:
            return None
        tool = str(value.get("tool", "")).strip()
        args = value.get("args", {})
        if not tool or not isinstance(args, dict) or not args.get("action"):
            raise ValueError("Stored pending tool call is invalid")
        return ToolCallRequest(tool=tool, args=dict(args))

    @staticmethod
    def _tool_signature(tool_call: ToolCallRequest) -> str:
        payload = json.dumps(
            {"tool": tool_call.tool, "args": tool_call.args},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _is_retryable_planner_error(exc: Exception) -> bool:
        try:
            status_code = int(getattr(exc, "code", 0) or 0)
        except (TypeError, ValueError):
            status_code = 0
        return status_code in {429, 500, 502, 503, 504}

    @staticmethod
    def _failure(error: str, events: List[Dict[str, Any]], next_step: int) -> Dict[str, Any]:
        return {
            "success": False,
            "error": error,
            "tool_events": events,
            "next_step": next_step,
        }

    @staticmethod
    def _notify(callback: Optional[ProgressCallback], update: Dict[str, Any]) -> None:
        if callback is None:
            return
        try:
            callback(update)
        except Exception:
            LOGGER.exception("Agent progress callback failed")

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

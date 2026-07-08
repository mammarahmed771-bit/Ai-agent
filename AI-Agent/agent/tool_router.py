from __future__ import annotations

import logging
from typing import Any, Dict

from agent.protocol import ActionRequest, ToolCallRequest
from tools import failure


LOGGER = logging.getLogger(__name__)


class ToolRouter:
    def __init__(self, tools: Dict[str, Any], permission_callback):
        self.tools = tools
        self.permission_callback = permission_callback

    def run_tool(
        self,
        tool_call: ToolCallRequest,
        session_state: Dict[str, Any],
    ) -> Dict[str, Any] | ActionRequest:
        tool = self.tools.get(tool_call.tool)
        if not tool:
            return {"ok": False, "error": f"Unknown tool: {tool_call.tool}"}

        try:
            result = tool.run(
                tool_call.args,
                session_state=session_state,
                request_permission=self.permission_callback,
            )
        except Exception as exc:
            LOGGER.exception("Unhandled failure in tool '%s'", tool_call.tool)
            return failure(exc, f"Tool '{tool_call.tool}' crashed")

        if isinstance(result, ActionRequest):
            return result
        if not isinstance(result, dict):
            return failure(
                f"Tool returned unsupported type: {type(result).__name__}",
                f"Tool '{tool_call.tool}' violated the result contract",
            )
        if "ok" not in result:
            return failure(
                "Tool result is missing the 'ok' field",
                f"Tool '{tool_call.tool}' violated the result contract",
            )
        return result


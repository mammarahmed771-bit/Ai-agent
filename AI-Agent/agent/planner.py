from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from google import genai
from google.genai import types

from agent.protocol import AgentPlannerDecision, ToolCallRequest


LOGGER = logging.getLogger(__name__)

PLANNER_SYSTEM = """
You are the planning brain of a local Desktop AI Agent. Decide exactly one next step.

Behavior:
- Work autonomously through multi-step tasks. Do not ask for confirmation between ordinary steps.
- Use only a tool and action present in available_tools. Every tool call must include an action.
- Inspect before modifying when the target state is unknown.
- Prefer project.create_project or project.edit_files for coherent multi-file batches.
- Use terminal/python after writing when verification is useful. If verification fails, inspect the
  error, repair the files, and verify again.
- Do not repeat an already successful tool event. Continue from completed_tool_events.
- A permission response is handled outside the planner; request the dangerous tool normally.
- Treat failed tool results as evidence. Correct arguments or choose another tool.
- For attached images, use image.analyze with attachment_index. For the latest screenshot, omit path.
- Return final_answer only when the user's request is answered or all required work is complete.
- If a genuinely necessary user choice is missing and cannot be inferred safely, explain it in the
  final answer. Do not invent filesystem paths outside the supplied safe roots.
- Never claim a tool action succeeded unless a successful event proves it.

Output one JSON object matching the supplied schema and no prose outside it.
""".strip()

DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": ["tool_call", "final_answer"]},
        "tool_call": {
            "type": "object",
            "properties": {
                "tool": {"type": "string"},
                "args": {"type": "object"},
            },
        },
        "final_answer": {"type": "string"},
    },
    "required": ["kind"],
}


class Planner:
    def __init__(self, client: genai.Client, model: str = "gemini-2.5-flash"):
        self.client = client
        self.model = model

    def decide(
        self,
        user_text: str,
        context: Dict[str, Any],
        tool_summaries: List[Dict[str, Any]],
        history_text: str,
    ) -> AgentPlannerDecision:
        prompt = {
            "user_request": user_text,
            "context": context,
            "recent_conversation": history_text[-20_000:],
            "completed_tool_events": self._bounded_events(tool_summaries),
        }
        last_error = ""
        for attempt in range(2):
            contents = json.dumps(prompt, ensure_ascii=False, default=str)
            if attempt:
                contents += f"\nThe previous decision was invalid: {last_error}. Return valid schema JSON."
            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=PLANNER_SYSTEM,
                    response_mime_type="application/json",
                    response_json_schema=DECISION_SCHEMA,
                    max_output_tokens=1_024,
                    temperature=0.1,
                ),
            )
            raw = (response.text or "").strip()
            try:
                decision = self._parse_decision(raw)
                return decision
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                last_error = str(exc)
                LOGGER.warning("Planner returned invalid decision: %s", raw[:1_000])
        raise RuntimeError(f"Planner failed to return a valid decision: {last_error}")

    @staticmethod
    def _parse_decision(raw: str) -> AgentPlannerDecision:
        data = json.loads(raw)
        kind = data.get("kind")
        if kind == "tool_call":
            tool_call = data.get("tool_call")
            if not isinstance(tool_call, dict) or not tool_call.get("tool"):
                raise ValueError("tool_call requires a tool name")
            args = tool_call.get("args", {})
            if not isinstance(args, dict):
                raise ValueError("tool_call.args must be an object")
            if not args.get("action"):
                raise ValueError("tool_call.args requires an action")
            return AgentPlannerDecision(
                kind="tool_call",
                tool_call=ToolCallRequest(tool=str(tool_call["tool"]), args=args),
            )
        if kind == "final_answer":
            return AgentPlannerDecision(
                kind="final_answer",
                final_answer=str(data.get("final_answer", "")),
            )
        raise ValueError(f"Unsupported planner decision kind: {kind}")

    @staticmethod
    def _bounded_events(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        bounded = []
        for event in events[-24:]:
            serialized = json.dumps(event, ensure_ascii=False, default=str)
            if len(serialized) > 12_000:
                serialized = serialized[:12_000] + '..."}'
                bounded.append({"summary": serialized, "truncated": True})
            else:
                bounded.append(event)
        return bounded

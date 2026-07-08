from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional

from agent.memory import SQLiteMemory
from tools import failure, request_action, success


class MemoryTool:
    """Expose the shared SQLite long-term memory to the planner."""

    name = "memory"
    description = "List/get remembered values and, with approval, remember or forget data."
    actions = ["list", "get", "remember", "forget", "forget_all"]

    def __init__(self, memory: Optional[SQLiteMemory] = None):
        default_path = Path(__file__).resolve().parent.parent / "agent_memory.sqlite"
        self.memory = memory or SQLiteMemory(str(default_path))

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        action = str(args.get("action", "list")).lower()
        try:
            if action == "list":
                return self.list_all()
            if action == "get":
                return self.get(str(args.get("key", "")))
            if action in {"remember", "forget", "forget_all"}:
                payload = {"action": action, "key": str(args.get("key", ""))}
                permission = request_action(
                    request_permission,
                    f"memory_{action}",
                    f"{action.replace('_', ' ').title()} long-term memory",
                    payload,
                )
                if permission is not None:
                    return permission
                if action == "remember":
                    return self.remember(payload["key"], args.get("value"))
                if action == "forget":
                    return self.forget_key(payload["key"])
                return self.forget_all()
            return failure(f"Unsupported memory action: {action}")
        except Exception as exc:
            return failure(exc, f"Memory action '{action}' failed")

    def list_all(self) -> Dict[str, Any]:
        items = self.memory.list()
        return success(f"Loaded {len(items)} memory items", {"items": items})

    def get(self, key: str) -> Dict[str, Any]:
        if not key:
            return failure("Missing key")
        value = self.memory.get(key)
        if value is None:
            return failure(f"No memory found for key: {key}", "Memory key not found")
        return success("Memory item loaded", {"key": key, "value": value})

    def remember(self, key: str, value: Any) -> Dict[str, Any]:
        if not key:
            return failure("Missing key")
        self.memory.put(key, value)
        return success("Memory saved", {"key": key})

    def forget_key(self, key: str) -> Dict[str, Any]:
        if not key:
            return failure("Missing key")
        self.memory.delete_key(key)
        return success("Memory key deleted", {"key": key})

    def forget_all(self) -> Dict[str, Any]:
        self.memory.forget_all()
        return success("All memory deleted")

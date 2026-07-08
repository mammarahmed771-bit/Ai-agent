from __future__ import annotations

from typing import Any, Dict

from tools import failure, request_action, success


class ClipboardTool:
    """Read or write the operating-system text clipboard."""

    name = "clipboard"
    description = "Read clipboard text or write text after user approval."
    actions = ["read", "write"]

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        action = str(args.get("action", "read")).lower()
        if action == "read":
            return self.read()
        if action == "write":
            content = str(args.get("content", ""))
            permission = request_action(
                request_permission,
                "clipboard_write",
                f"Write {len(content)} characters to the system clipboard",
                {"characters": len(content), "preview": content[:200]},
            )
            if permission is not None:
                return permission
            return self.write(content)
        return failure(f"Unsupported clipboard action: {action}")

    def read(self) -> Dict[str, Any]:
        try:
            import pyperclip

            content = pyperclip.paste()
            return success(
                "Clipboard text read",
                {"content": content, "characters": len(content)},
            )
        except Exception as exc:
            return failure(exc, "Unable to read the system clipboard")

    def write(self, content: str) -> Dict[str, Any]:
        try:
            import pyperclip

            pyperclip.copy(content)
            return success("Clipboard text written", {"characters": len(content)})
        except Exception as exc:
            return failure(exc, "Unable to write the system clipboard")

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from typing import Any, Dict, Optional

from tools import failure, request_action, resolve_safe_path, success


LOGGER = logging.getLogger(__name__)


class PythonTool:
    """Execute an approved Python snippet with the application's interpreter."""

    name = "python"
    description = "Execute Python code in the workspace after user approval."
    actions = ["execute"]

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        code = str(args.get("code", ""))
        if not code.strip():
            return failure("Missing code to execute")
        try:
            cwd = resolve_safe_path(str(args.get("cwd", ".")), session_state, must_exist=True)
            if not cwd.is_dir():
                return failure(f"Working directory is not a directory: {cwd}")
            payload = {
                "cwd": str(cwd),
                "code_preview": code[:500],
                "code_sha256": self._digest(code),
            }
            permission = request_action(
                request_permission,
                "script_execution",
                f"Execute a Python script in {cwd}",
                payload,
            )
            if permission is not None:
                return permission
            return self.execute(
                code,
                int(args.get("timeout_seconds", 30)),
                str(cwd),
            )
        except Exception as exc:
            LOGGER.exception("Python execution failed")
            return failure(exc, "Python execution failed")

    def execute(
        self,
        code: str,
        timeout_seconds: int = 30,
        cwd: Optional[str] = None,
    ) -> Dict[str, Any]:
        timeout = max(1, min(int(timeout_seconds), 300))
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            with tempfile.TemporaryDirectory(prefix="desktop-agent-") as temp_dir:
                script_path = os.path.join(temp_dir, "agent_code.py")
                with open(script_path, "w", encoding="utf-8") as script:
                    script.write(code)
                process = subprocess.run(
                    [sys.executable, script_path],
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=timeout,
                    creationflags=creation_flags,
                )
        except subprocess.TimeoutExpired as exc:
            return failure(
                f"Python execution timed out after {timeout}s. Partial output: {exc.stdout or ''}",
                "Python execution timed out",
            )

        data = {
            "cwd": cwd,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "returncode": process.returncode,
        }
        if process.returncode != 0:
            result = failure(
                f"Python exited with code {process.returncode}",
                "Python script failed",
            )
            result["data"] = data
            return result
        return success("Python script completed", data)

    @staticmethod
    def _digest(code: str) -> str:
        import hashlib

        return hashlib.sha256(code.encode("utf-8")).hexdigest()

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
        if len(code.encode("utf-8")) > 1_000_000:
            return failure("Python source exceeds the 1 MB execution limit")
        try:
            cwd = resolve_safe_path(str(args.get("cwd", ".")), session_state, must_exist=True)
            if not cwd.is_dir():
                return failure(f"Working directory is not a directory: {cwd}")
            timeout = max(1, min(int(args.get("timeout_seconds", 30)), 300))
            max_output_chars = max(1_000, min(int(args.get("max_output_chars", 200_000)), 1_000_000))
            payload = {
                "cwd": str(cwd),
                "code_preview": code[:500],
                "code_sha256": self._digest(code),
                "timeout_seconds": timeout,
                "max_output_chars": max_output_chars,
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
                timeout,
                str(cwd),
                max_output_chars,
            )
        except Exception as exc:
            LOGGER.exception("Python execution failed")
            return failure(exc, "Python execution failed")

    def execute(
        self,
        code: str,
        timeout_seconds: int = 30,
        cwd: Optional[str] = None,
        max_output_chars: int = 200_000,
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
        except OSError as exc:
            return failure(exc, "Python process could not be started")

        stdout, stdout_truncated = self._bounded(process.stdout, max_output_chars)
        stderr, stderr_truncated = self._bounded(process.stderr, max_output_chars)
        data = {
            "cwd": cwd,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
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
    def _bounded(value: str, limit: int) -> tuple[str, bool]:
        maximum = max(1_000, min(int(limit), 1_000_000))
        if len(value) <= maximum:
            return value, False
        head = maximum * 3 // 4
        tail = maximum - head
        return value[:head] + "\n... output truncated ...\n" + value[-tail:], True

    @staticmethod
    def _digest(code: str) -> str:
        import hashlib

        return hashlib.sha256(code.encode("utf-8")).hexdigest()

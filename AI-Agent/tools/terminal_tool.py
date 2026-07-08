from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict, Optional

from tools import failure, request_action, resolve_safe_path, success


LOGGER = logging.getLogger(__name__)


class TerminalTool:
    """Run an approved command from a validated workspace directory."""

    name = "terminal"
    description = "Execute a shell command in a workspace directory after user approval."
    actions = ["run"]

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        command = str(args.get("command", "")).strip()
        if not command:
            return failure("Missing command")
        try:
            cwd = resolve_safe_path(str(args.get("cwd", ".")), session_state, must_exist=True)
            if not cwd.is_dir():
                return failure(f"Working directory is not a directory: {cwd}")
            timeout = max(1, min(int(args.get("timeout_seconds", 30)), 300))
            max_output_chars = max(1_000, min(int(args.get("max_output_chars", 200_000)), 1_000_000))
            payload = {
                "command": command,
                "cwd": str(cwd),
                "timeout_seconds": timeout,
                "max_output_chars": max_output_chars,
            }
            permission = request_action(
                request_permission,
                "terminal_execution",
                f"Run this command in {cwd}: {command}",
                payload,
            )
            if permission is not None:
                return permission
            return self.run_command(
                command,
                timeout,
                str(cwd),
                max_output_chars,
            )
        except Exception as exc:
            LOGGER.exception("Terminal execution failed")
            return failure(exc, "Terminal execution failed")

    def run_command(
        self,
        command: str,
        timeout_seconds: int = 30,
        cwd: Optional[str] = None,
        max_output_chars: int = 200_000,
    ) -> Dict[str, Any]:
        timeout = max(1, min(int(timeout_seconds), 300))
        creation_flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            process = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                creationflags=creation_flags,
            )
        except subprocess.TimeoutExpired as exc:
            return failure(
                f"Command timed out after {timeout}s. Partial output: {exc.stdout or ''}",
                "Terminal command timed out",
            )
        except OSError as exc:
            return failure(exc, "Terminal process could not be started")

        stdout, stdout_truncated = self._bounded(process.stdout, max_output_chars)
        stderr, stderr_truncated = self._bounded(process.stderr, max_output_chars)
        data = {
            "command": command,
            "cwd": cwd,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "returncode": process.returncode,
        }
        if process.returncode != 0:
            result = failure(
                f"Command exited with code {process.returncode}",
                "Terminal command failed",
            )
            result["data"] = data
            return result
        return success("Terminal command completed", data)

    @staticmethod
    def _bounded(value: str, limit: int) -> tuple[str, bool]:
        maximum = max(1_000, min(int(limit), 1_000_000))
        if len(value) <= maximum:
            return value, False
        head = maximum * 3 // 4
        tail = maximum - head
        return value[:head] + "\n... output truncated ...\n" + value[-tail:], True

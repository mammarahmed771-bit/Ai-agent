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
            payload = {"command": command, "cwd": str(cwd)}
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
                int(args.get("timeout_seconds", 30)),
                str(cwd),
            )
        except Exception as exc:
            LOGGER.exception("Terminal execution failed")
            return failure(exc, "Terminal execution failed")

    def run_command(
        self,
        command: str,
        timeout_seconds: int = 30,
        cwd: Optional[str] = None,
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

        data = {
            "command": command,
            "cwd": cwd,
            "stdout": process.stdout,
            "stderr": process.stderr,
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

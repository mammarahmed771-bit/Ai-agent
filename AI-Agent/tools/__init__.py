"""Shared contracts and path guards for desktop-agent tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional


ToolResult = Dict[str, Any]


def success(message: str, data: Optional[Dict[str, Any]] = None) -> ToolResult:
    return {"ok": True, "message": message, "data": data or {}}


def failure(error: Any, message: str = "Tool execution failed") -> ToolResult:
    return {
        "ok": False,
        "message": message,
        "error": str(error),
        "error_type": type(error).__name__ if isinstance(error, BaseException) else "ToolError",
    }


def configured_safe_roots(session_state: Optional[Dict[str, Any]] = None) -> List[Path]:
    roots: List[Path] = []
    if session_state:
        roots.extend(Path(value) for value in session_state.get("safe_roots", []) if value)

    project_root = Path(__file__).resolve().parent.parent
    roots.append(project_root)
    if os.getenv("AGENT_SAFE_ROOT"):
        roots.append(Path(os.environ["AGENT_SAFE_ROOT"]))

    unique: List[Path] = []
    for root in roots:
        resolved = root.expanduser().resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def resolve_safe_path(
    value: str,
    session_state: Optional[Dict[str, Any]] = None,
    *,
    must_exist: bool = False,
) -> Path:
    roots = configured_safe_roots(session_state)
    raw = Path(value or ".").expanduser()
    candidate = (roots[0] / raw).resolve() if not raw.is_absolute() else raw.resolve()

    if not any(_is_relative_to(candidate, root) for root in roots):
        raise PermissionError(f"Path is outside configured safe roots: {candidate}")
    if must_exist and not candidate.exists():
        raise FileNotFoundError(candidate)
    return candidate


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def request_action(
    request_permission: Any,
    action_type: str,
    description: str,
    payload: Dict[str, Any],
) -> Any:
    if request_permission is None:
        return failure("Permission callback is unavailable", "Permission required")
    return request_permission(
        action_type=action_type,
        description=description,
        payload=payload,
    )


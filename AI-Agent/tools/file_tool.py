from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from tools import failure, request_action, resolve_safe_path, success


class FileTool:
    """Read and mutate files inside configured workspace roots."""

    name = "file"
    description = (
        "List directories, read files, and (with approval) write, create, rename, "
        "or delete workspace files."
    )
    actions = ["list", "read", "write", "mkdir", "rename", "delete"]

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        action = str(args.get("action", "list")).lower()
        try:
            if action == "list":
                return self.list_directory(str(args.get("path", ".")), session_state)
            if action == "read":
                return self.read_file(
                    str(args.get("path", "")),
                    str(args.get("encoding", "utf-8")),
                    session_state,
                    int(args.get("max_chars", 200_000)),
                )
            if action in {"write", "mkdir", "rename", "delete"}:
                payload = self._mutation_payload(action, args, session_state)
                permission = request_action(
                    request_permission,
                    f"file_{action}",
                    self._permission_description(action, payload),
                    payload,
                )
                if permission is not None:
                    return permission
                return self._mutate(action, args, session_state)
            return failure(f"Unsupported file action: {action}")
        except Exception as exc:
            return failure(exc, f"File action '{action}' failed")

    def list_directory(
        self,
        path: str = ".",
        session_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        target = resolve_safe_path(path, session_state, must_exist=True)
        if not target.is_dir():
            return failure(f"Not a directory: {target}")
        entries = []
        for item in sorted(target.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower())):
            stat = item.stat()
            entries.append(
                {
                    "name": item.name,
                    "path": str(item),
                    "is_dir": item.is_dir(),
                    "size": stat.st_size if item.is_file() else None,
                    "modified": stat.st_mtime,
                }
            )
        return success(
            f"Listed {len(entries)} entries in {target}",
            {"path": str(target), "entries": entries},
        )

    def read_file(
        self,
        path: str,
        encoding: str = "utf-8",
        session_state: Optional[Dict[str, Any]] = None,
        max_chars: int = 200_000,
    ) -> Dict[str, Any]:
        if not path:
            return failure("Missing path")
        target = resolve_safe_path(path, session_state, must_exist=True)
        if not target.is_file():
            return failure(f"Not a file: {target}")
        content = target.read_text(encoding=encoding, errors="replace")
        truncated = len(content) > max_chars
        if truncated:
            content = content[:max_chars]
        return success(
            f"Read {target.name}",
            {
                "path": str(target),
                "content": content,
                "size": target.stat().st_size,
                "truncated": truncated,
            },
        )

    def write_file(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        session_state: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        target = resolve_safe_path(path, session_state)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding=encoding)
        return success(
            f"Wrote {target.name}",
            {"path": str(target), "bytes": target.stat().st_size},
        )

    def _mutation_payload(
        self,
        action: str,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        path = resolve_safe_path(str(args.get("path", "")), session_state)
        payload: Dict[str, Any] = {"action": action, "path": str(path)}
        if action == "write":
            payload["bytes"] = len(str(args.get("content", "")).encode("utf-8"))
            payload["overwrite"] = path.exists()
        if action == "rename":
            payload["destination"] = str(
                resolve_safe_path(str(args.get("destination", "")), session_state)
            )
        if action == "delete":
            payload["recursive"] = bool(args.get("recursive", False))
        return payload

    @staticmethod
    def _permission_description(action: str, payload: Dict[str, Any]) -> str:
        if action == "rename":
            return f"Rename {payload['path']} to {payload['destination']}"
        return f"{action.title()} workspace path: {payload['path']}"

    def _mutate(
        self,
        action: str,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        target = resolve_safe_path(str(args.get("path", "")), session_state)
        if action == "write":
            return self.write_file(
                str(target),
                str(args.get("content", "")),
                str(args.get("encoding", "utf-8")),
                session_state,
            )
        if action == "mkdir":
            target.mkdir(parents=bool(args.get("parents", True)), exist_ok=True)
            return success("Directory created", {"path": str(target)})
        if action == "rename":
            if not target.exists():
                return failure(f"Path does not exist: {target}")
            destination = resolve_safe_path(
                str(args.get("destination", "")), session_state
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            target.rename(destination)
            return success(
                "Path renamed",
                {"path": str(target), "destination": str(destination)},
            )
        if action == "delete":
            if not target.exists():
                return failure(f"Path does not exist: {target}")
            if target.is_dir():
                if not bool(args.get("recursive", False)):
                    target.rmdir()
                else:
                    shutil.rmtree(target)
            else:
                target.unlink()
            return success("Path deleted", {"path": str(target)})
        return failure(f"Unsupported mutation: {action}")

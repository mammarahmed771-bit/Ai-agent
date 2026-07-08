from __future__ import annotations

import shutil
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional

from tools import configured_safe_roots, failure, request_action, resolve_safe_path, success


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
            if action not in self.actions:
                return failure(f"Unsupported file action: {action}")
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
        limit = max(1_000, min(int(max_chars), 2_000_000))
        content = target.read_text(encoding=encoding, errors="replace")
        truncated = len(content) > limit
        if truncated:
            content = content[:limit]
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
        if not path.strip():
            return failure("Missing path")
        target = resolve_safe_path(path, session_state)
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = content.encode(encoding)
        temporary_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=target.parent,
                delete=False,
            ) as temporary:
                temporary.write(encoded)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            temporary_path.replace(target)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
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
        raw_path = str(args.get("path", "")).strip()
        if not raw_path:
            raise ValueError("Missing path")
        path = resolve_safe_path(raw_path, session_state)
        payload: Dict[str, Any] = {"action": action, "path": str(path)}
        if action == "write":
            encoding = str(args.get("encoding", "utf-8"))
            content_bytes = str(args.get("content", "")).encode(encoding)
            payload["bytes"] = len(content_bytes)
            payload["sha256"] = hashlib.sha256(content_bytes).hexdigest()
            payload["encoding"] = encoding
            payload["overwrite"] = path.exists()
        if action == "rename":
            destination_value = str(args.get("destination", "")).strip()
            if not destination_value:
                raise ValueError("Missing destination")
            payload["destination"] = str(
                resolve_safe_path(destination_value, session_state)
            )
            payload["overwrite"] = bool(args.get("overwrite", False))
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
            if destination == target:
                return success("Source and destination are identical", {"path": str(target)})
            if destination.exists() and not bool(args.get("overwrite", False)):
                return failure(f"Destination already exists: {destination}")
            if destination.exists() and destination.is_dir() != target.is_dir():
                return failure("Source and destination types do not match")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if bool(args.get("overwrite", False)):
                target.replace(destination)
            else:
                target.rename(destination)
            return success(
                "Path renamed",
                {"path": str(target), "destination": str(destination)},
            )
        if action == "delete":
            if not target.exists():
                return failure(f"Path does not exist: {target}")
            if any(target == root for root in configured_safe_roots(session_state)):
                return failure("Deleting a configured safe root is not allowed")
            if target.is_symlink():
                target.unlink()
            elif target.is_dir():
                if not bool(args.get("recursive", False)):
                    target.rmdir()
                else:
                    shutil.rmtree(target)
            else:
                target.unlink()
            return success("Path deleted", {"path": str(target)})
        return failure(f"Unsupported mutation: {action}")

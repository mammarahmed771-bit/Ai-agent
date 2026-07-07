from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
import shutil
from typing import Any, Dict, Iterable, List
import zipfile

from tools import failure, request_action, resolve_safe_path, success


LOGGER = logging.getLogger(__name__)
IGNORED_NAMES = {".git", ".idea", ".venv", "venv", "node_modules", "__pycache__"}


class ProjectTool:
    """Understand and modify groups of workspace files."""

    name = "project"
    description = (
        "Scan project structure, read multiple files, batch edit/create projects, "
        "and safely zip or unzip workspace content."
    )
    actions = ["scan", "read_multiple", "edit_files", "create_project", "zip", "unzip"]

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        action = str(args.get("action", "scan")).lower()
        try:
            if action == "scan":
                return self._scan(args, session_state)
            if action == "read_multiple":
                return self._read_multiple(args, session_state)
            if action in {"edit_files", "create_project", "zip", "unzip"}:
                payload = self._mutation_payload(action, args, session_state)
                permission = request_action(
                    request_permission,
                    f"project_{action}",
                    f"Run project operation '{action}' in the workspace",
                    payload,
                )
                if permission is not None:
                    return permission
                return self._mutate(action, args, session_state)
            return failure(f"Unsupported project action: {action}")
        except Exception as exc:
            LOGGER.exception("Project action '%s' failed", action)
            return failure(exc, f"Project action '{action}' failed")

    def _scan(self, args: Dict[str, Any], session_state: Dict[str, Any]) -> Dict[str, Any]:
        root = resolve_safe_path(str(args.get("path", ".")), session_state, must_exist=True)
        if not root.is_dir():
            return failure(f"Not a directory: {root}")
        max_depth = max(1, min(int(args.get("max_depth", 6)), 12))
        max_entries = max(10, min(int(args.get("max_entries", 2_000)), 10_000))
        entries: List[Dict[str, Any]] = []
        languages: Dict[str, int] = {}
        for path in self._walk(root, max_depth):
            if len(entries) >= max_entries:
                break
            relative = path.relative_to(root)
            is_dir = path.is_dir()
            entry = {"path": str(relative), "type": "directory" if is_dir else "file"}
            if path.is_file():
                entry["size"] = path.stat().st_size
                suffix = path.suffix.lower() or "[no extension]"
                languages[suffix] = languages.get(suffix, 0) + 1
            entries.append(entry)
        return success(
            f"Scanned {root}",
            {
                "root": str(root),
                "entries": entries,
                "file_types": dict(sorted(languages.items(), key=lambda item: -item[1])),
                "truncated": len(entries) >= max_entries,
            },
        )

    def _read_multiple(
        self, args: Dict[str, Any], session_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        paths = args.get("paths", [])
        if not isinstance(paths, list) or not paths:
            return failure("paths must be a non-empty list")
        if len(paths) > 100:
            return failure("At most 100 files can be read at once")
        max_chars = max(1_000, min(int(args.get("max_chars_per_file", 100_000)), 500_000))
        files = []
        for value in paths:
            path = resolve_safe_path(str(value), session_state, must_exist=True)
            if not path.is_file():
                files.append({"path": str(path), "error": "Not a file"})
                continue
            content = path.read_text(encoding="utf-8", errors="replace")
            files.append(
                {
                    "path": str(path),
                    "content": content[:max_chars],
                    "truncated": len(content) > max_chars,
                    "size": path.stat().st_size,
                }
            )
        return success(f"Read {len(files)} project files", {"files": files})

    def _mutation_payload(
        self,
        action: str,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        if action in {"edit_files", "create_project"}:
            root_value = args.get("path", ".")
            root = resolve_safe_path(str(root_value), session_state)
            files = self._normalize_files(args.get("files", []))
            summary = [
                {
                    "path": item["path"],
                    "bytes": len(item["content"].encode("utf-8")),
                    "sha256": hashlib.sha256(item["content"].encode("utf-8")).hexdigest(),
                }
                for item in files
            ]
            return {"action": action, "root": str(root), "files": summary}
        if action == "zip":
            return {
                "action": action,
                "source": str(resolve_safe_path(str(args.get("path", ".")), session_state)),
                "output": str(resolve_safe_path(str(args.get("output_path", "project.zip")), session_state)),
            }
        return {
            "action": action,
            "archive": str(resolve_safe_path(str(args.get("path", "")), session_state)),
            "destination": str(resolve_safe_path(str(args.get("destination", ".")), session_state)),
        }

    def _mutate(
        self,
        action: str,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        if action in {"edit_files", "create_project"}:
            root = resolve_safe_path(str(args.get("path", ".")), session_state)
            files = self._normalize_files(args.get("files", []))
            root.mkdir(parents=True, exist_ok=True)
            written = []
            for item in files:
                target = resolve_safe_path(str(root / item["path"]), session_state)
                if root not in target.parents and target != root:
                    raise PermissionError(f"Project file escapes target root: {item['path']}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(item["content"], encoding=item.get("encoding", "utf-8"))
                written.append(str(target))
            return success(
                "Project files written",
                {"root": str(root), "files": written, "count": len(written)},
            )
        if action == "zip":
            source = resolve_safe_path(str(args.get("path", ".")), session_state, must_exist=True)
            output = resolve_safe_path(str(args.get("output_path", "project.zip")), session_state)
            if output.suffix.lower() != ".zip":
                output = output.with_suffix(".zip")
            output.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
                if source.is_file():
                    archive.write(source, source.name)
                else:
                    for path in self._walk(source, max_depth=100):
                        if path.is_file() and path != output:
                            archive.write(path, path.relative_to(source))
            return success("Project archive created", {"path": str(output), "bytes": output.stat().st_size})
        if action == "unzip":
            archive_path = resolve_safe_path(str(args.get("path", "")), session_state, must_exist=True)
            destination = resolve_safe_path(str(args.get("destination", ".")), session_state)
            destination.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive_path, "r") as archive:
                self._validate_archive_members(archive, destination)
                archive.extractall(destination)
                members = archive.namelist()
            return success(
                "Project archive extracted",
                {"destination": str(destination), "files": members, "count": len(members)},
            )
        return failure(f"Unsupported project mutation: {action}")

    @staticmethod
    def _normalize_files(value: Any) -> List[Dict[str, str]]:
        if isinstance(value, dict):
            files = [{"path": path, "content": content} for path, content in value.items()]
        elif isinstance(value, list):
            files = value
        else:
            raise ValueError("files must be a mapping or list of {path, content}")
        if not files or len(files) > 200:
            raise ValueError("files must contain between 1 and 200 entries")
        normalized = []
        total_bytes = 0
        for item in files:
            path = str(item.get("path", "")).strip()
            if not path or Path(path).is_absolute():
                raise ValueError("Project file paths must be non-empty and relative")
            content = str(item.get("content", ""))
            total_bytes += len(content.encode("utf-8"))
            normalized.append({"path": path, "content": content, "encoding": str(item.get("encoding", "utf-8"))})
        if total_bytes > 10_000_000:
            raise ValueError("Batch file content exceeds 10 MB")
        return normalized

    @staticmethod
    def _walk(root: Path, max_depth: int) -> Iterable[Path]:
        for path in root.rglob("*"):
            relative = path.relative_to(root)
            if any(part in IGNORED_NAMES for part in relative.parts):
                continue
            if len(relative.parts) <= max_depth:
                yield path

    @staticmethod
    def _validate_archive_members(archive: zipfile.ZipFile, destination: Path) -> None:
        destination = destination.resolve()
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if destination != target and destination not in target.parents:
                raise ValueError(f"Unsafe archive member: {member.filename}")
            if member.file_size > 200_000_000:
                raise ValueError(f"Archive member is too large: {member.filename}")

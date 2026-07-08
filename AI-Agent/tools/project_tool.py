from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Iterable, List, Set
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
            return {
                "action": action,
                "root": str(root),
                "files": summary,
                "overwrite": bool(args.get("overwrite", False)),
            }
        if action == "zip":
            return {
                "action": action,
                "source": str(resolve_safe_path(str(args.get("path", ".")), session_state, must_exist=True)),
                "output": str(resolve_safe_path(str(args.get("output_path", "project.zip")), session_state)),
            }
        return {
            "action": action,
            "archive": str(resolve_safe_path(str(args.get("path", "")), session_state, must_exist=True)),
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
            if root.exists() and not root.is_dir():
                return failure(f"Project root is not a directory: {root}")
            if action == "create_project" and root.exists() and any(root.iterdir()):
                if not bool(args.get("overwrite", False)):
                    return failure(
                        f"Project directory is not empty: {root}. Set overwrite=true to update it."
                    )
            root.mkdir(parents=True, exist_ok=True)
            written = self._write_batch_atomically(root, files, session_state)
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
            temporary_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    prefix=f".{output.stem}.", suffix=".zip.tmp", dir=output.parent, delete=False
                ) as temporary:
                    temporary_path = Path(temporary.name)
                with zipfile.ZipFile(temporary_path, "w", zipfile.ZIP_DEFLATED) as archive:
                    if source.is_file():
                        archive.write(source, source.name)
                    else:
                        for path in self._walk(source, max_depth=100):
                            if path.is_file() and path not in {output, temporary_path}:
                                archive.write(path, path.relative_to(source))
                temporary_path.replace(output)
            finally:
                if temporary_path is not None:
                    temporary_path.unlink(missing_ok=True)
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
        seen: Set[str] = set()
        total_bytes = 0
        for item in files:
            if not isinstance(item, dict):
                raise ValueError("Each file entry must be an object")
            path = str(item.get("path", "")).strip()
            relative = Path(path)
            if (
                not path
                or relative.is_absolute()
                or path in {".", ".."}
                or ".." in relative.parts
            ):
                raise ValueError("Project file paths must be non-empty and relative")
            normalized_path = relative.as_posix()
            if normalized_path in seen:
                raise ValueError(f"Duplicate project file path: {normalized_path}")
            seen.add(normalized_path)
            content = str(item.get("content", ""))
            encoding = str(item.get("encoding", "utf-8"))
            encoded = content.encode(encoding)
            total_bytes += len(encoded)
            normalized.append({"path": normalized_path, "content": content, "encoding": encoding})
        if total_bytes > 10_000_000:
            raise ValueError("Batch file content exceeds 10 MB")
        return normalized

    @staticmethod
    def _walk(root: Path, max_depth: int) -> Iterable[Path]:
        for current, directories, files in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            relative_current = current_path.relative_to(root)
            depth = 0 if relative_current == Path(".") else len(relative_current.parts)
            directories[:] = sorted(
                name for name in directories if name not in IGNORED_NAMES
            )
            if depth >= max_depth:
                directories[:] = []
            for name in directories:
                yield current_path / name
            for name in sorted(files):
                path = current_path / name
                if len(path.relative_to(root).parts) <= max_depth:
                    yield path

    @staticmethod
    def _write_batch_atomically(
        root: Path,
        files: List[Dict[str, str]],
        session_state: Dict[str, Any],
    ) -> List[str]:
        written: List[Path] = []
        backups: Dict[Path, Path] = {}
        with tempfile.TemporaryDirectory(prefix=".agent-stage-", dir=root) as temporary:
            staging_root = Path(temporary)
            staged: List[tuple[Path, Path]] = []
            for index, item in enumerate(files):
                target = resolve_safe_path(str(root / item["path"]), session_state)
                if root != target and root not in target.parents:
                    raise PermissionError(f"Project file escapes target root: {item['path']}")
                if target.exists() and not target.is_file():
                    raise IsADirectoryError(target)
                stage = staging_root / "new" / str(index)
                stage.parent.mkdir(parents=True, exist_ok=True)
                stage.write_bytes(item["content"].encode(item.get("encoding", "utf-8")))
                staged.append((stage, target))
            try:
                for index, (stage, target) in enumerate(staged):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        backup = staging_root / "backup" / str(index)
                        backup.parent.mkdir(parents=True, exist_ok=True)
                        target.replace(backup)
                        backups[target] = backup
                    stage.replace(target)
                    written.append(target)
            except Exception:
                for target in reversed(written):
                    target.unlink(missing_ok=True)
                for target, backup in backups.items():
                    if backup.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        backup.replace(target)
                raise
        return [str(path) for path in written]

    @staticmethod
    def _validate_archive_members(archive: zipfile.ZipFile, destination: Path) -> None:
        destination = destination.resolve()
        if len(archive.infolist()) > 10_000:
            raise ValueError("Archive contains too many entries")
        total_size = 0
        for member in archive.infolist():
            if member.flag_bits & 0x1:
                raise ValueError(f"Encrypted archive member is not supported: {member.filename}")
            unix_mode = member.external_attr >> 16
            if (unix_mode & 0o170000) == 0o120000:
                raise ValueError(f"Symbolic links are not allowed in archives: {member.filename}")
            target = (destination / member.filename).resolve()
            if destination != target and destination not in target.parents:
                raise ValueError(f"Unsafe archive member: {member.filename}")
            if member.file_size > 200_000_000:
                raise ValueError(f"Archive member is too large: {member.filename}")
            total_size += member.file_size
            if total_size > 1_000_000_000:
                raise ValueError("Archive expands beyond the 1 GB safety limit")

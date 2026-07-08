from __future__ import annotations

import logging
from typing import Any, Dict, List

from google import genai

from agent.memory import SQLiteMemory
from tools.browser_tool import BrowserTool
from tools.clipboard_tool import ClipboardTool
from tools.file_tool import FileTool
from tools.image_tool import ImageTool
from tools.internet_tool import InternetTool
from tools.memory_tool import MemoryTool
from tools.project_tool import ProjectTool
from tools.python_tool import PythonTool
from tools.screenshot_tool import ScreenshotTool
from tools.terminal_tool import TerminalTool


LOGGER = logging.getLogger(__name__)

# Concise planner-facing contracts. Keeping these in the registry makes the
# registry the single source of truth for action discovery without exposing
# implementation details or Python signatures to the model.
ACTION_ARGUMENTS: Dict[str, Dict[str, Dict[str, str]]] = {
    "file": {
        "list": {"path": "directory path (default '.')"},
        "read": {"path": "required file path", "max_chars": "optional integer"},
        "write": {"path": "required file path", "content": "required full text"},
        "mkdir": {"path": "required directory path", "parents": "optional boolean"},
        "rename": {"path": "required source", "destination": "required destination"},
        "delete": {"path": "required path", "recursive": "required for non-empty directories"},
    },
    "terminal": {
        "run": {"command": "required shell command", "cwd": "workspace directory", "timeout_seconds": "1-300"}
    },
    "python": {
        "execute": {"code": "required Python source", "cwd": "workspace directory", "timeout_seconds": "1-300"}
    },
    "browser": {
        "launch": {"headless": "optional boolean", "profile_path": "optional workspace path"},
        "status": {}, "list_tabs": {},
        "new_tab": {"url": "optional URL"},
        "switch_tab": {"index": "required zero-based index"},
        "close_tab": {"index": "optional zero-based index"},
        "open_url": {"url": "required http(s) URL"},
        "search": {"query": "required search query"},
        "click": {"selector": "required Playwright selector"},
        "fill": {"selector": "required selector", "value": "text value"},
        "keyboard": {"key": "Playwright key, or provide text", "text": "text to type"},
        "scroll": {"selector": "optional selector", "delta_x": "integer", "delta_y": "integer"},
        "wait": {"selector": "optional selector", "milliseconds": "when selector omitted"},
        "screenshot": {"path": "optional workspace PNG path", "full_page": "boolean"},
        "extract_text": {"selector": "default body", "max_chars": "optional integer"},
        "download": {"selector": "required download trigger", "directory": "workspace directory", "filename": "optional safe filename"},
        "upload": {"selector": "required file input selector", "paths": "required workspace path list"},
        "close_browser": {},
    },
    "screenshot": {"capture": {"path": "optional workspace image path", "bbox": "optional [left,top,right,bottom]", "all_screens": "boolean"}},
    "clipboard": {"read": {}, "write": {"content": "required text"}},
    "memory": {
        "list": {}, "get": {"key": "required key"},
        "remember": {"key": "required key", "value": "JSON value"},
        "forget": {"key": "required key"}, "forget_all": {},
    },
    "internet": {
        "search": {"query": "required query"},
        "open_url": {"url": "required public http(s) URL", "max_chars": "optional integer"},
    },
    "image": {
        "analyze": {"path": "workspace image path, or attachment_index", "attachment_index": "zero-based attachment", "prompt": "optional analysis request"},
        "generate": {"prompt": "required", "output_path": "optional workspace path", "aspect_ratio": "optional", "image_size": "optional"},
        "edit": {"prompt": "required", "path": "source path, or attachment_index", "attachment_index": "zero-based attachment", "output_path": "optional workspace path"},
    },
    "project": {
        "scan": {"path": "project directory", "max_depth": "1-12", "max_entries": "10-10000"},
        "read_multiple": {"paths": "required file path list", "max_chars_per_file": "optional integer"},
        "edit_files": {"path": "project root", "files": "mapping or list of {path,content,encoding}"},
        "create_project": {"path": "new project root", "files": "mapping or list of {path,content,encoding}", "overwrite": "optional boolean"},
        "zip": {"path": "source", "output_path": "destination zip"},
        "unzip": {"path": "archive zip", "destination": "workspace directory"},
    },
}


def build_tool_registry(
    client: genai.Client,
    memory: SQLiteMemory,
) -> Dict[str, Any]:
    """Build one routed instance of every available desktop-agent tool."""
    instances = [
        FileTool(),
        TerminalTool(),
        PythonTool(),
        BrowserTool(),
        ScreenshotTool(),
        ClipboardTool(),
        MemoryTool(memory),
        InternetTool(),
        ImageTool(client),
        ProjectTool(),
    ]
    registry: Dict[str, Any] = {}
    for tool in instances:
        name = str(getattr(tool, "name", "")).strip()
        actions = list(getattr(tool, "actions", []))
        if not name or not callable(getattr(tool, "run", None)) or not actions:
            raise TypeError(f"Invalid tool registration: {tool!r}")
        if name in registry:
            raise ValueError(f"Duplicate tool registration: {name}")
        registry[name] = tool
    LOGGER.info("Registered tools: %s", ", ".join(sorted(registry)))
    return registry


def build_tool_manifest(registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return concise tool instructions without exposing Python implementation details."""
    return [
        {
            "name": name,
            "description": getattr(tool, "description", ""),
            "actions": [
                {
                    "name": action,
                    "arguments": ACTION_ARGUMENTS.get(name, {}).get(action, {}),
                }
                for action in getattr(tool, "actions", [])
            ],
        }
        for name, tool in sorted(registry.items())
    ]

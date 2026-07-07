from __future__ import annotations

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
    return {tool.name: tool for tool in instances}


def build_tool_manifest(registry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return concise tool instructions without exposing Python implementation details."""
    return [
        {
            "name": name,
            "description": getattr(tool, "description", ""),
            "actions": list(getattr(tool, "actions", [])),
        }
        for name, tool in sorted(registry.items())
    ]

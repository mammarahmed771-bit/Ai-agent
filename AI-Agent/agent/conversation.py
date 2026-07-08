from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_context(
    preferences: Dict[str, Any],
    safe_roots: List[str],
    available_tools: Optional[List[Dict[str, Any]]] = None,
    file_context: str = "",
    attachments: Optional[List[Dict[str, Any]]] = None,
    audio_context: str = "",
) -> Dict[str, Any]:
    return {
        "preferences": preferences or {},
        "safe_roots": safe_roots,
        "available_tools": available_tools or [],
        "attached_file_context": file_context[:100_000],
        "attachments": attachments or [],
        "audio_context": audio_context[:50_000],
    }


def build_history_for_agent(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = []
    for message in (history or [])[-50:]:
        sender = "user" if message.get("sender") == "user" else "ai"
        normalized.append(
            {
                "sender": sender,
                "text": str(message.get("text", ""))[:20_000],
            }
        )
    return normalized


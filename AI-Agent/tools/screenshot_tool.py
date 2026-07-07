from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import ImageGrab

from tools import failure, request_action, resolve_safe_path, success


LOGGER = logging.getLogger(__name__)


class ScreenshotTool:
    """Capture the desktop to a PNG inside the workspace."""

    name = "screenshot"
    description = "Capture the full desktop or a bounding box to an image file."
    actions = ["capture"]

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        if str(args.get("action", "capture")).lower() != "capture":
            return failure("Unsupported screenshot action")
        try:
            output_path = self._output_path(args.get("path"), session_state)
            bbox = self._parse_bbox(args.get("bbox"))
            payload = {
                "path": str(output_path),
                "bbox": list(bbox) if bbox else None,
                "all_screens": bool(args.get("all_screens", True)),
            }
            permission = request_action(
                request_permission,
                "screenshot_capture",
                "Capture the visible desktop and save it inside the workspace",
                payload,
            )
            if permission is not None:
                return permission
            output_path.parent.mkdir(parents=True, exist_ok=True)
            image = ImageGrab.grab(
                bbox=bbox,
                all_screens=bool(args.get("all_screens", True)),
            )
            image.save(output_path, format="PNG")
            session_state["latest_screenshot"] = str(output_path)
            return success(
                "Desktop screenshot captured",
                {
                    "path": str(output_path),
                    "mime_type": "image/png",
                    "width": image.width,
                    "height": image.height,
                    "bytes": output_path.stat().st_size,
                },
            )
        except Exception as exc:
            LOGGER.exception("Desktop screenshot capture failed")
            return failure(exc, "Desktop screenshot capture failed")

    @staticmethod
    def _parse_bbox(value: Any) -> Optional[Tuple[int, int, int, int]]:
        if value in (None, ""):
            return None
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError("bbox must be [left, top, right, bottom]")
        bbox = tuple(int(item) for item in value)
        if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
            raise ValueError("bbox right/bottom must exceed left/top")
        return bbox  # type: ignore[return-value]

    @staticmethod
    def _output_path(value: Any, session_state: Dict[str, Any]) -> Path:
        if value:
            target = resolve_safe_path(str(value), session_state)
        else:
            stamp = str(session_state.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            target = resolve_safe_path(
                f"artifacts/screenshots/desktop-{stamp}.png", session_state
            )
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        return target

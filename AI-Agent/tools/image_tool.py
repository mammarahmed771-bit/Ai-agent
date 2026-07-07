from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from google import genai
from google.genai import types

from tools import failure, request_action, resolve_safe_path, success


LOGGER = logging.getLogger(__name__)


class ImageTool:
    """Analyze, generate, and edit images with Gemini."""

    name = "image"
    description = (
        "Analyze an image, generate a new image from text, or edit an existing image "
        "with Gemini. Images may come from a workspace path, attachment_index, or the "
        "latest desktop screenshot."
    )
    actions = ["analyze", "generate", "edit"]

    def __init__(self, client: genai.Client):
        self.client = client
        self.analysis_model = os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-2.5-flash")
        self.image_model = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        action = str(args.get("action", "analyze")).lower()
        try:
            if action == "analyze":
                return self._analyze(args, session_state)
            if action in {"generate", "edit"}:
                prompt = str(args.get("prompt", "")).strip()
                if not prompt:
                    return failure("Missing image prompt")
                output = self._output_path(args.get("output_path"), session_state, action)
                payload: Dict[str, Any] = {
                    "action": action,
                    "output_path": str(output),
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "prompt_preview": prompt[:300],
                }
                if action == "edit":
                    _, _, source = self._load_source(args, session_state)
                    payload["source"] = source
                permission = request_action(
                    request_permission,
                    f"image_{action}",
                    f"{action.title()} an image and save it to {output}",
                    payload,
                )
                if permission is not None:
                    return permission
                return self._create_or_edit(action, prompt, output, args, session_state)
            return failure(f"Unsupported image action: {action}")
        except Exception as exc:
            LOGGER.exception("Image action '%s' failed", action)
            return failure(exc, f"Image action '{action}' failed")

    def _analyze(
        self, args: Dict[str, Any], session_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        image_bytes, mime_type, source = self._load_source(args, session_state)
        prompt = str(
            args.get(
                "prompt",
                "Analyze this image carefully. Describe important visual details, text, "
                "layout, and any issues relevant to the user's request.",
            )
        )
        response = self.client.models.generate_content(
            model=str(args.get("model", self.analysis_model)),
            contents=[
                types.Part.from_text(text=prompt),
                types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
            ],
        )
        text = (response.text or "").strip()
        return success(
            "Image analyzed with Gemini",
            {"analysis": text, "source": source, "mime_type": mime_type},
        )

    def _create_or_edit(
        self,
        action: str,
        prompt: str,
        output: Path,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        contents: list[Any] = [types.Part.from_text(text=prompt)]
        if action == "edit":
            image_bytes, mime_type, _ = self._load_source(args, session_state)
            contents.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))

        image_config_args: Dict[str, Any] = {}
        if args.get("aspect_ratio"):
            image_config_args["aspect_ratio"] = str(args["aspect_ratio"])
        if args.get("image_size"):
            image_config_args["image_size"] = str(args["image_size"])

        config_args: Dict[str, Any] = {"response_modalities": ["TEXT", "IMAGE"]}
        if image_config_args:
            config_args["image_config"] = types.ImageConfig(**image_config_args)
        response = self.client.models.generate_content(
            model=str(args.get("model", self.image_model)),
            contents=contents,
            config=types.GenerateContentConfig(**config_args),
        )

        generated, mime_type, response_text = self._extract_generated_image(response)
        output = output.with_suffix(mimetypes.guess_extension(mime_type) or output.suffix or ".png")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(generated)
        session_state["latest_image"] = str(output)
        return success(
            f"Image {action} completed",
            {
                "path": str(output),
                "mime_type": mime_type,
                "bytes": len(generated),
                "model": str(args.get("model", self.image_model)),
                "text": response_text,
            },
        )

    @staticmethod
    def _extract_generated_image(response: Any) -> Tuple[bytes, str, str]:
        text_parts = []
        parts = getattr(response, "parts", None)
        if parts is None and getattr(response, "candidates", None):
            parts = response.candidates[0].content.parts
        for part in parts or []:
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                data = inline.data
                if isinstance(data, str):
                    data = base64.b64decode(data)
                return bytes(data), inline.mime_type or "image/png", "\n".join(text_parts)
            if getattr(part, "text", None):
                text_parts.append(part.text)
        raise RuntimeError("Gemini returned no image data")

    def _load_source(
        self, args: Dict[str, Any], session_state: Dict[str, Any]
    ) -> Tuple[bytes, str, str]:
        if args.get("path"):
            path = resolve_safe_path(str(args["path"]), session_state, must_exist=True)
            if not path.is_file():
                raise ValueError(f"Image path is not a file: {path}")
            mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
            return path.read_bytes(), mime_type, str(path)

        if args.get("attachment_index") is not None:
            index = int(args["attachment_index"])
            images = session_state.get("images", [])
            if index < 0 or index >= len(images):
                raise IndexError(f"Image attachment index out of range: {index}")
            item = images[index]
            data = base64.b64decode(item.get("base64", ""), validate=True)
            return data, item.get("type", "image/png"), item.get("name", f"attachment-{index}")

        latest = session_state.get("latest_screenshot") or session_state.get("latest_image")
        if latest:
            path = resolve_safe_path(str(latest), session_state, must_exist=True)
            return (
                path.read_bytes(),
                mimetypes.guess_type(path.name)[0] or "image/png",
                str(path),
            )
        raise ValueError("Provide path or attachment_index, or capture a screenshot first")

    @staticmethod
    def _output_path(value: Any, session_state: Dict[str, Any], action: str) -> Path:
        if value:
            return resolve_safe_path(str(value), session_state)
        stamp = str(session_state.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
        return resolve_safe_path(f"artifacts/images/{action}-{stamp}.png", session_state)

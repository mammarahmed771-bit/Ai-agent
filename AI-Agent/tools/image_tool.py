from __future__ import annotations

import base64
from datetime import datetime, timezone
import hashlib
import io
import logging
import mimetypes
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional, Tuple

from google import genai
from google.genai import errors, types
from PIL import Image

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
                    "overwrite": output.exists(),
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
        except errors.ClientError as exc:
            status_code = int(getattr(exc, "code", 0) or 0)
            if status_code == 429:
                LOGGER.warning("Gemini quota is unavailable for image action '%s'", action)
                result = failure(
                    "The configured Gemini project has no quota available for this image action.",
                    "Gemini quota exceeded",
                )
                result.update({"status_code": 429, "retryable": True})
                return result
            LOGGER.exception("Gemini rejected image action '%s'", action)
            result = failure(exc, f"Gemini rejected image action '{action}'")
            result["status_code"] = status_code or None
            return result
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
        if not text:
            raise RuntimeError("Gemini returned an empty image analysis")
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
        output.parent.mkdir(parents=True, exist_ok=True)
        stored_bytes = self._write_image_atomically(output, generated)
        session_state["latest_image"] = str(output)
        return success(
            f"Image {action} completed",
            {
                "path": str(output),
                "mime_type": mime_type,
                "bytes": stored_bytes,
                "model": str(args.get("model", self.image_model)),
                "text": response_text,
            },
        )

    @staticmethod
    def _extract_generated_image(response: Any) -> Tuple[bytes, str, str]:
        text_parts = []
        generated: Optional[bytes] = None
        mime_type = "image/png"
        parts = getattr(response, "parts", None)
        if parts is None and getattr(response, "candidates", None):
            parts = response.candidates[0].content.parts
        for part in parts or []:
            inline = getattr(part, "inline_data", None)
            if generated is None and inline and getattr(inline, "data", None):
                data = inline.data
                if isinstance(data, str):
                    data = base64.b64decode(data)
                generated = bytes(data)
                mime_type = inline.mime_type or "image/png"
            if getattr(part, "text", None):
                text_parts.append(part.text)
        if generated is None:
            raise RuntimeError("Gemini returned no image data")
        return generated, mime_type, "\n".join(text_parts)

    def _load_source(
        self, args: Dict[str, Any], session_state: Dict[str, Any]
    ) -> Tuple[bytes, str, str]:
        if args.get("path"):
            path = resolve_safe_path(str(args["path"]), session_state, must_exist=True)
            if not path.is_file():
                raise ValueError(f"Image path is not a file: {path}")
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            data = path.read_bytes()
            self._validate_image(data, mime_type)
            return data, mime_type, str(path)

        if args.get("attachment_index") is not None:
            index = int(args["attachment_index"])
            images = session_state.get("images", [])
            if index < 0 or index >= len(images):
                raise IndexError(f"Image attachment index out of range: {index}")
            item = images[index]
            data = base64.b64decode(item.get("base64", ""), validate=True)
            mime_type = str(item.get("type", "application/octet-stream"))
            self._validate_image(data, mime_type)
            return data, mime_type, item.get("name", f"attachment-{index}")

        latest = session_state.get("latest_screenshot") or session_state.get("latest_image")
        if latest:
            path = resolve_safe_path(str(latest), session_state, must_exist=True)
            data = path.read_bytes()
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self._validate_image(data, mime_type)
            return data, mime_type, str(path)
        raise ValueError("Provide path or attachment_index, or capture a screenshot first")

    @staticmethod
    def _output_path(value: Any, session_state: Dict[str, Any], action: str) -> Path:
        if value:
            output = resolve_safe_path(str(value), session_state)
        else:
            stamp = str(session_state.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            output = resolve_safe_path(f"artifacts/images/{action}-{stamp}.png", session_state)
        if not output.suffix:
            output = output.with_suffix(".png")
        if output.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError("Image output must use .png, .jpg, .jpeg, or .webp")
        return output

    @staticmethod
    def _validate_image(data: bytes, mime_type: str) -> None:
        if len(data) > 30_000_000:
            raise ValueError("Image exceeds the 30 MB limit")
        if not mime_type.startswith("image/"):
            raise ValueError(f"Unsupported image MIME type: {mime_type}")
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.verify()
        except Exception as exc:
            raise ValueError(f"Invalid image data: {exc}") from exc

    @staticmethod
    def _write_image_atomically(output: Path, data: bytes) -> int:
        temporary_path: Optional[Path] = None
        try:
            with Image.open(io.BytesIO(data)) as image:
                image.load()
                suffix = output.suffix.lower()
                image_format = {".jpg": "JPEG", ".jpeg": "JPEG", ".webp": "WEBP"}.get(
                    suffix, "PNG"
                )
                if image_format == "JPEG" and image.mode not in {"RGB", "L"}:
                    image = image.convert("RGB")
                with tempfile.NamedTemporaryFile(
                    prefix=f".{output.stem}.",
                    suffix=output.suffix,
                    dir=output.parent,
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                image.save(temporary_path, format=image_format)
            temporary_path.replace(output)
            return output.stat().st_size
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)

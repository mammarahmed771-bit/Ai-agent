from __future__ import annotations

import base64
from collections import deque
import logging
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, send_file
from google import genai
from google.genai import types
import yt_dlp

from agent.agent import Agent
from agent.allow_action import ApprovalStore
from agent.conversation import build_context, build_history_for_agent
from agent.memory import SQLiteMemory
from agent.protocol import ActionRequest
from agent.tool_registry import build_tool_manifest, build_tool_registry
from tools import configured_safe_roots, failure, resolve_safe_path


load_dotenv()
logging.basicConfig(
    level=getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
LOGGER = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent
ARTIFACT_ROOT = PROJECT_ROOT / "artifacts"
DB_PATH = PROJECT_ROOT / "agent_memory.sqlite"
DEFAULT_MAX_QUOTA = int(os.getenv("GEMINI_DISPLAY_QUOTA", "1500"))
TASK_TTL_SECONDS = 3_600
RUN_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_REQUEST_BYTES", str(50 * 1024 * 1024)))

approval_store = ApprovalStore()
memory = SQLiteMemory(str(DB_PATH))
state_lock = threading.RLock()
task_sessions: Dict[str, Dict[str, Any]] = {}
task_history: deque[Dict[str, Any]] = deque(maxlen=50)

gemini_clients: List[Dict[str, Any]] = []
quota_tracker: Dict[int, Dict[str, int]] = {}
for key_index in range(1, 6):
    api_key = os.getenv(f"GEMINI_API_KEY_{key_index}", "").strip()
    if not api_key:
        continue
    # Create one Gemini client per configured key.
    gemini_clients.append({"index": key_index, "client": genai.Client(api_key=api_key)})
    quota_tracker[key_index] = {"used": 0, "max": DEFAULT_MAX_QUOTA}

current_client_index = 0
shared_tool_registry: Optional[Dict[str, Any]] = None
LOGGER.info("Loaded %s Gemini client slot(s)", len(gemini_clients))


def _is_retryable_gemini_exception(exc: Exception) -> bool:
    """
    Retryable errors for Gemini client rotation.
    Do NOT rotate for non-retryable user/prompt/malformed request failures.
    """
    # Numeric code (google-genai may expose `.code`; some exceptions may not)
    code = getattr(exc, "code", None)
    try:
        code_int = int(code) if code is not None else None
    except (TypeError, ValueError):
        code_int = None

    if code_int in {429, 500, 502, 503, 504}:
        return True

    # Timeout / connection patterns (exception types vary by underlying transport)
    exc_name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "timeout" in exc_name or "timeout" in msg:
        return True
    if "temporarily" in msg or "temporarily unavailable" in msg:
        return True
    if "connection" in msg or "network" in msg or "broken pipe" in msg:
        return True

    # Gemini-specific / quota / rate-limit markers
    retry_markers = (
        "resource_exhausted",
        "quota",
        "rate limit",
        "rate_limit",
        "too many requests",
        "throttl",
        "service unavailable",
        "internal error",
        "bad gateway",
        "gateway timeout",
        "unavailable",
    )
    return any(marker in msg for marker in retry_markers)


def _is_retryable_gemini_tool_event(tool_events: List[Dict[str, Any]]) -> bool:
    """
    Tool-level rotation trigger (e.g., ImageTool returns retryable=True for 429).
    Only rotates when the tool explicitly marks the error as retryable.
    """
    for event in tool_events:
        if not isinstance(event, dict):
            continue
        result = event.get("result") or {}
        if isinstance(result, dict):
            if bool(result.get("retryable")) is True:
                return True
            status_code = result.get("status_code")
            try:
                if int(status_code) in {429, 500, 502, 503, 504}:
                    return True
            except (TypeError, ValueError):
                pass
            # Defensive: some tools may not set status_code but include clear text.
            err_text = str(result.get("error") or "").lower()
            if "quota" in err_text or "rate limit" in err_text or "resource_exhausted" in err_text:
                return True
    return False


def download_youtube_audio_bytes(video_url: str) -> Tuple[bytes, str]:
    options = {
        "format": "bestaudio/best",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "128",
            }
        ],
        "outtmpl": os.path.join(tempfile.gettempdir(), "desktop-agent-%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        info = downloader.extract_info(video_url, download=True)
        original = downloader.prepare_filename(info)
        audio_path = os.path.splitext(original)[0] + ".m4a"
        if not os.path.exists(audio_path):
            raise FileNotFoundError("yt-dlp did not produce the expected M4A audio track")
        try:
            return Path(audio_path).read_bytes(), "audio/m4a"
        finally:
            Path(audio_path).unlink(missing_ok=True)


def analyze_inline_media(
    client: genai.Client,
    prompt: str,
    media_bytes: bytes,
    mime_type: str,
) -> str:
    response = client.models.generate_content(
        model=os.getenv("GEMINI_ANALYSIS_MODEL", "gemini-2.5-flash"),
        contents=[
            types.Part.from_text(text=prompt),
            types.Part.from_bytes(data=media_bytes, mime_type=mime_type),
        ],
    )
    return (response.text or "").strip()


def get_tool_registry(client: genai.Client) -> Dict[str, Any]:
    global shared_tool_registry
    with state_lock:
        if shared_tool_registry is None:
            shared_tool_registry = build_tool_registry(client=client, memory=memory)
        else:
            shared_tool_registry["image"].client = client
        return shared_tool_registry


def build_permission_callback(run_id: str) -> Any:
    def callback(
        *, action_type: str, description: str, payload: Dict[str, Any]
    ) -> Optional[Dict[str, Any] | ActionRequest]:
        scoped_payload = dict(payload)
        scoped_payload["run_id"] = run_id
        request_id = approval_store.request_id(action_type, scoped_payload)
        decision = approval_store.consume(request_id)
        if decision is True:
            return None
        if decision is False:
            return failure("The user denied this action", "Action denied")
        approval_store.register(
            request_id,
            {"run_id": run_id, "action_type": action_type, "payload": scoped_payload},
        )
        return ActionRequest(
            request_id=request_id,
            action_type=action_type,
            description=description,
            payload=scoped_payload,
        )

    return callback


def purge_expired_tasks() -> None:
    cutoff = time.time() - TASK_TTL_SECONDS
    with state_lock:
        expired = [run_id for run_id, item in task_sessions.items() if item["updated_at"] < cutoff]
        for run_id in expired:
            task_sessions.pop(run_id, None)


def normalize_run_id(value: str, *, allow_empty: bool = False) -> str:
    normalized = value.strip().lower().replace("-", "")
    if not normalized and allow_empty:
        return ""
    if not RUN_ID_PATTERN.fullmatch(normalized):
        raise ValueError("run_id must be a 32-character hexadecimal UUID")
    return normalized


def build_progress_callback(run_id: str) -> Any:
    def callback(update: Dict[str, Any]) -> None:
        with state_lock:
            task = task_sessions.get(run_id)
            if task is None:
                return
            step = max(0, int(update.get("step", task.get("next_step", 0))))
            task["next_step"] = step
            task["status"] = str(update.get("status", task.get("status", "running")))
            if "tool" in update:
                task["current_tool"] = update.get("tool")
            if "action" in update:
                task["current_action"] = update.get("action")
            task["progress"] = (
                100
                if task["status"] == "completed"
                else min(95, round((step / 24) * 100))
            )
            task["updated_at"] = time.time()

    return callback


def archive_task(run_id: str, status: str, error: Optional[str] = None) -> Dict[str, Any]:
    """Move a terminal task into bounded history with consistent final progress."""
    with state_lock:
        task = task_sessions.get(run_id)
        if task is None:
            return {}
        task["status"] = status
        task["progress"] = 100
        task["updated_at"] = time.time()
        history_item = {
            "run_id": run_id,
            "status": status,
            "request": task["user_text"][:200],
            "steps": len(task["tool_events"]),
            "progress": 100,
            "tool_events": list(task["tool_events"]),
            "error": error,
            "updated_at": task["updated_at"],
        }
        task_history.appendleft(history_item)
        task_sessions.pop(run_id, None)
        return history_item


def attachment_metadata(images: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "index": index,
            "name": str(item.get("name", f"attachment-{index}")),
            "mime_type": str(item.get("type", "application/octet-stream")),
            "base64_characters": len(str(item.get("base64", ""))),
        }
        for index, item in enumerate(images)
    ]


def validate_images(value: Any) -> List[Dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list) or len(value) > 10:
        raise ValueError("images must be a list containing at most 10 attachments")
    validated = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValueError(f"Attachment {index} must be an object")
        encoded = str(item.get("base64", ""))
        if not encoded:
            raise ValueError(f"Attachment {index} has no base64 data")
        if len(encoded) > 40_000_000:
            raise ValueError(f"Attachment {index} exceeds the 30 MB inline limit")
        validated.append(
            {
                "name": str(item.get("name", f"attachment-{index}")),
                "type": str(item.get("type", "application/octet-stream")),
                "base64": encoded,
            }
        )
    return validated


@app.get("/")
def home() -> str:
    return render_template("index.html")


@app.get("/quota_status")
def get_quota_status() -> Any:
    return jsonify({"success": True, "tracker": quota_tracker})


@app.get("/agent/tasks")
def get_task_history() -> Any:
    with state_lock:
        active = [
            {
                "run_id": run_id,
                "status": item.get("status", "running"),
                "request": item["user_text"][:200],
                "steps": len(item["tool_events"]),
                "progress": item.get("progress", 0),
                "current_tool": item.get("current_tool"),
                "current_action": item.get("current_action"),
                "updated_at": item["updated_at"],
            }
            for run_id, item in task_sessions.items()
        ]
        return jsonify({"success": True, "active": active, "history": list(task_history)})


@app.get("/agent/tasks/<run_id>")
def get_task_status(run_id: str) -> Any:
    try:
        normalized = normalize_run_id(run_id)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    with state_lock:
        task = task_sessions.get(normalized)
        if task is None:
            history_item = next(
                (item for item in task_history if item.get("run_id") == normalized), None
            )
            if history_item is None:
                return jsonify({"success": False, "error": "Task not found"}), 404
            return jsonify({"success": True, "task": history_item})
        return jsonify(
            {
                "success": True,
                "task": {
                    "run_id": normalized,
                    "status": task.get("status", "running"),
                    "request": task["user_text"][:200],
                    "steps": len(task["tool_events"]),
                    "next_step": task.get("next_step", 0),
                    "progress": task.get("progress", 0),
                    "current_tool": task.get("current_tool"),
                    "current_action": task.get("current_action"),
                    "tool_events": list(task["tool_events"]),
                    "action_request": task.get("pending_action"),
                    "created_at": task["created_at"],
                    "updated_at": task["updated_at"],
                },
            }
        )


@app.get("/agent/browser_status")
def browser_status() -> Any:
    if shared_tool_registry is None:
        return jsonify({"ok": True, "message": "Browser is not running", "data": {"running": False, "tabs": []}})
    result = shared_tool_registry["browser"].run(
        {"action": "status"},
        {"safe_roots": [str(path) for path in configured_safe_roots()]},
        None,
    )
    return jsonify(result)


@app.get("/agent/artifacts/<path:filename>")
def get_artifact(filename: str) -> Any:
    target = resolve_safe_path(str(ARTIFACT_ROOT / filename), {"safe_roots": [str(ARTIFACT_ROOT)]}, must_exist=True)
    if not target.is_file():
        return jsonify({"success": False, "error": "Artifact is not a file"}), 404
    return send_file(target)


@app.post("/agent/allow_action")
def allow_action() -> Any:
    data = request.get_json(silent=True) or {}
    request_id = str(data.get("request_id", "")).strip()
    if not request_id:
        return jsonify({"success": False, "error": "Missing request_id"}), 400
    approved = data.get("approved")
    if not isinstance(approved, bool):
        return jsonify({"success": False, "error": "approved must be a Boolean"}), 400
    with state_lock:
        task = next(
            (
                item
                for item in task_sessions.values()
                if (item.get("pending_action") or {}).get("request_id") == request_id
            ),
            None,
        )
        if task is None:
            return jsonify({"success": False, "error": "Pending action not found"}), 404
        if not approval_store.decide(
            request_id,
            approved,
            meta={"remote_addr": request.remote_addr},
        ):
            return jsonify({"success": False, "error": "Action was already decided or expired"}), 409
        task["status"] = "approval_granted" if approved else "approval_denied"
        task["updated_at"] = time.time()
    return jsonify({"success": True, "request_id": request_id, "approved": approved})


@app.post("/agent/chat")
def agent_chat() -> Any:
    global current_client_index
    purge_expired_tasks()
    run_id = ""
    last_error: Optional[Exception] = None
    try:
        data = request.get_json(silent=True) or {}
        requested_run_id = normalize_run_id(str(data.get("run_id", "")).strip(), allow_empty=True)
        with state_lock:
            task = task_sessions.get(requested_run_id) if requested_run_id else None

        if requested_run_id and task is None:
            return jsonify({"success": False, "run_id": requested_run_id, "error": "Task not found or expired"}), 404

        if task is None:
            text = str(data.get("text", "")).strip()
            images = validate_images(data.get("images", []))
            if not text and not images:
                return jsonify({"success": False, "error": "Prompt or attachment is required"}), 400
            if not gemini_clients:
                return jsonify({"success": False, "error": "No Gemini API keys are configured"}), 503

            run_id = requested_run_id or uuid.uuid4().hex
            history = build_history_for_agent(data.get("history", []))
            preferences = data.get("preferences", {}) if isinstance(data.get("preferences", {}), dict) else {}
            safe_roots = [str(path) for path in configured_safe_roots()]
            task = {
                "run_id": run_id,
                "user_text": text or "Analyze the attached media.",
                "history": history,
                "file_context": str(data.get("file_context", ""))[:100_000],
                "preferences": preferences,
                "images": images,
                "safe_roots": safe_roots,
                "tool_events": [],
                "next_step": 0,
                "audio_context": "",
                "created_at": time.time(),
                "updated_at": time.time(),
                "waiting": False,
                "status": "queued",
                "progress": 0,
                "current_tool": None,
                "current_action": None,
                "pending_tool": None,
                "pending_action": None,
            }
            task["session_state"] = {
                "run_id": run_id,
                "safe_roots": safe_roots,
                "images": images,
            }
            with state_lock:
                task_sessions[run_id] = task
        else:
            run_id = requested_run_id
            task["waiting"] = False
            task["status"] = "running"
            task["updated_at"] = time.time()

            total_clients = len(gemini_clients)
            last_error = None
            for attempt in range(total_clients):
                slot_position = (current_client_index + attempt) % total_clients
                slot = gemini_clients[slot_position]
                client = slot["client"]
                key_number = slot["index"]

                # Reset task state per key attempt only for Gemini-dependent computation.
                # Planner/tool events already in `task` should be allowed to proceed, but if we
                # hit retryable Gemini errors we will retry the whole run with the next key.
                try:
                    registry = get_tool_registry(client)
                    if not task["audio_context"]:
                        task["audio_context"] = build_media_context(client, task)

                    context = build_context(
                        preferences=task["preferences"],
                        safe_roots=task["safe_roots"],
                        available_tools=build_tool_manifest(registry),
                        file_context=task["file_context"],
                        attachments=attachment_metadata(task["images"]),
                        audio_context=task["audio_context"],
                    )

                    session_state = task["session_state"]
                    pending_tool = task.get("pending_tool")

                    agent = Agent(
                        client=client,
                        tools=registry,
                        memory=memory,
                        permission_callback=build_permission_callback(run_id),
                        model=os.getenv("GEMINI_PLANNER_MODEL", "gemini-2.5-flash"),
                    )

                    result = agent.run(
                        user_text=task["user_text"],
                        history=task["history"],
                        context=context,
                        tool_events=task["tool_events"],
                        session_state=session_state,
                        start_step=task["next_step"],
                        pending_tool=pending_tool,
                        progress_callback=build_progress_callback(run_id),
                    )

                    task["tool_events"] = result.get("tool_events", task["tool_events"])
                    task["next_step"] = result.get(
                        "next_step", result.get("final_step", task["next_step"])
                    )
                    task["updated_at"] = time.time()
                    task["pending_tool"] = None
                    task["pending_action"] = None

                    # Rotate if tool-level Gemini failures indicate retryable conditions.
                    if result.get("success") is False and _is_retryable_gemini_tool_event(task["tool_events"]):
                        last_error = Exception("Retryable Gemini tool error")
                        LOGGER.warning("Gemini slot %s produced retryable tool errors; rotating keys", key_number)
                        continue

                    # Successful completion: update quota tracking + advance rotation pointer.
                    quota_tracker[key_number]["used"] = min(
                        quota_tracker[key_number]["used"] + 1,
                        quota_tracker[key_number]["max"],
                    )
                    current_client_index = (slot_position + 1) % total_clients

                    if result.get("action_request"):
                        task["waiting"] = True
                        task["status"] = "waiting_for_approval"
                        task["pending_tool"] = result.get("pending_tool")
                        task["pending_action"] = result["action_request"]
                        return jsonify(
                            {
                                "success": False,
                                "run_id": run_id,
                                "action_request": result["action_request"],
                                "pending_tool": result.get("pending_tool"),
                                "tool_events": task["tool_events"],
                                "progress": task.get("progress", 0),
                            }
                        )

                    completed = bool(result.get("success"))
                    final_status = "completed" if completed else "failed"
                    history_item = archive_task(run_id, final_status, result.get("error"))
                    status_code = 200 if completed else 422
                    return (
                        jsonify(
                            {
                                "success": completed,
                                "reply": result.get("final_reply", ""),
                                "error": result.get("error"),
                                "run_id": run_id,
                                "tool_events": task["tool_events"],
                                "query_used": f"Served by Key Slot {key_number}",
                                "is_image_creation": any(
                                    event.get("tool") == "image" and event.get("action") in {"generate", "edit"}
                                    for event in task["tool_events"]
                                ),
                                "progress": history_item["progress"],
                            }
                        ),
                        status_code,
                    )
                except Exception as exc:
                    last_error = exc
                    if _is_retryable_gemini_exception(exc):
                        LOGGER.warning("Gemini slot %s failed with retryable error; rotating keys: %s", key_number, exc)
                        continue
                    LOGGER.exception("Gemini slot %s failed with non-retryable error", key_number)
                    # Non-retryable: stop immediately to avoid rotating on bad prompts/user errors.
                    break

        error = f"All Gemini client slots failed: {last_error}"
        history_item = archive_task(run_id, "failed", error)
        return jsonify(
            {
                "success": False,
                "run_id": run_id,
                "error": error,
                "tool_events": history_item.get("tool_events", []),
                "progress": history_item.get("progress", 100),
            }
        ), 502
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:
        LOGGER.exception("Unhandled /agent/chat failure")
        history_item = archive_task(run_id, "failed", "Agent gateway error") if run_id else {}
        return jsonify(
            {
                "success": False,
                "run_id": run_id or None,
                "error": "Agent gateway error",
                "progress": history_item.get("progress", 100),
            }
        ), 500


def build_media_context(client: genai.Client, task: Dict[str, Any]) -> str:
    analyses: List[str] = []
    youtube_match = re.search(
        r"(https?://(?:www\.)?(?:youtube\.com|youtu\.be)/\S+)",
        task["user_text"],
        re.IGNORECASE,
    )
    if youtube_match:
        audio, mime_type = download_youtube_audio_bytes(youtube_match.group(1))
        analyses.append(
            "YouTube audio analysis:\n"
            + analyze_inline_media(
                client,
                "Analyze this audio thoroughly for the user's request. Include timestamped details when possible.",
                audio,
                mime_type,
            )
        )
    for index, item in enumerate(task["images"]):
        if str(item.get("type", "")).startswith(("video/", "audio/")):
            media = base64.b64decode(item["base64"], validate=True)
            analyses.append(
                f"Attachment {index} media analysis:\n"
                + analyze_inline_media(
                    client,
                    "Analyze this media thoroughly for the user's request. Include timestamped details when possible.",
                    media,
                    item["type"],
                )
            )
    return "\n\n".join(analyses)


@app.errorhandler(413)
def request_too_large(_: Any) -> Any:
    return jsonify({"success": False, "error": "Request payload is too large"}), 413


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)

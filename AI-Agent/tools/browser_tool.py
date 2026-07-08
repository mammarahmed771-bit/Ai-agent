from __future__ import annotations

import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import threading
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus, urlparse

from tools import failure, request_action, resolve_safe_path, success


LOGGER = logging.getLogger(__name__)


class BrowserTool:
    """Managed multi-tab Playwright browser controlled from a worker loop."""

    name = "browser"
    description = (
        "Launch and control a Chromium browser with optional persistent profiles. Supports tabs, open_url, "
        "Google search, click, fill, keyboard, scroll, wait, screenshot, extract_text, "
        "download, upload, status, and close operations."
    )
    actions = [
        "launch",
        "status",
        "list_tabs",
        "new_tab",
        "switch_tab",
        "close_tab",
        "open_url",
        "search",
        "click",
        "fill",
        "keyboard",
        "scroll",
        "wait",
        "screenshot",
        "extract_text",
        "download",
        "upload",
        "close_browser",
    ]

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._loop_ready = threading.Event()
        self._dispatch_lock = threading.Lock()
        self._playwright: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._active_index = 0

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        action = str(args.get("action", "status")).lower()
        if action not in self.actions:
            return failure(f"Unsupported browser action: {action}")
        future: Any = None
        try:
            permission = self._permission_for(action, args, session_state, request_permission)
            if permission is not None:
                return permission
            self._ensure_loop()
            assert self._loop is not None
            timeout = max(5, min(int(args.get("operation_timeout_seconds", 90)), 300))
            with self._dispatch_lock:
                future = asyncio.run_coroutine_threadsafe(
                    self._dispatch(action, args, session_state), self._loop
                )
                return future.result(timeout=timeout)
        except FutureTimeoutError:
            if future is not None:
                future.cancel()
            return failure("Browser operation timed out", "Browser action timed out")
        except ModuleNotFoundError:
            return failure(
                "Playwright is not installed. Run: pip install playwright; playwright install chromium",
                "Browser dependency is unavailable",
            )
        except Exception as exc:
            LOGGER.exception("Browser action '%s' failed", action)
            return failure(exc, f"Browser action '{action}' failed")

    def _permission_for(
        self,
        action: str,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Any:
        if action == "launch" and (args.get("profile_path") or args.get("persistent")):
            profile = resolve_safe_path(
                str(args.get("profile_path", "artifacts/browser-profile")), session_state
            )
            permission = request_action(
                request_permission,
                "browser_profile_write",
                f"Launch Chromium with a persistent profile at {profile}",
                {
                    "profile_path": str(profile),
                    "headless": bool(args.get("headless", False)),
                },
            )
            if permission is not None:
                return permission
        if action == "upload":
            paths = self._upload_paths(args, session_state)
            return request_action(
                request_permission,
                "browser_upload",
                f"Upload {len(paths)} local file(s) to the current web page",
                {"selector": str(args.get("selector", "")), "paths": paths},
            )
        if action == "download":
            directory = resolve_safe_path(str(args.get("directory", "artifacts/downloads")), session_state)
            return request_action(
                request_permission,
                "browser_download",
                f"Download a file from the current page to {directory}",
                {
                    "selector": str(args.get("selector", "")),
                    "directory": str(directory),
                    "filename": str(args.get("filename", "")),
                },
            )
        if action == "screenshot":
            path = self._screenshot_path(args.get("path"), session_state)
            return request_action(
                request_permission,
                "browser_screenshot",
                f"Save a browser screenshot to {path}",
                {"path": str(path), "full_page": bool(args.get("full_page", True))},
            )
        return None

    def _ensure_loop(self) -> None:
        if self._thread and self._thread.is_alive() and self._loop:
            return
        self._loop_ready.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="desktop-agent-browser",
            daemon=True,
        )
        self._thread.start()
        if not self._loop_ready.wait(timeout=5):
            raise RuntimeError("Browser worker loop failed to start")

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        loop.run_forever()

    async def _dispatch(
        self, action: str, args: Dict[str, Any], session_state: Dict[str, Any]
    ) -> Dict[str, Any]:
        if action == "launch":
            await self._launch(args, session_state)
            return await self._status("Browser launched")
        if action == "status":
            return await self._status()
        if action == "close_browser":
            await self._close_browser()
            return success("Browser closed", {"running": False, "tabs": []})

        await self._launch({}, session_state)
        if action == "list_tabs":
            return await self._status("Browser tabs listed")
        if action == "new_tab":
            page = await self._context.new_page()
            self._active_index = len(self._context.pages) - 1
            if args.get("url"):
                await page.goto(self._normalize_url(str(args["url"])), wait_until="domcontentloaded")
            return await self._status("New browser tab opened")
        if action == "switch_tab":
            self._active_index = self._tab_index(args)
            await self._page().bring_to_front()
            return await self._status("Active browser tab changed")
        if action == "close_tab":
            index = self._tab_index(args)
            await self._context.pages[index].close()
            self._active_index = max(0, min(self._active_index, len(self._context.pages) - 1))
            if not self._context.pages:
                await self._context.new_page()
            return await self._status("Browser tab closed")

        page = self._page()
        timeout_ms = max(1_000, min(int(args.get("timeout_ms", 30_000)), 120_000))
        page.set_default_timeout(timeout_ms)
        if action == "open_url":
            url = self._normalize_url(str(args.get("url", "")))
            wait_until = str(args.get("wait_until", "domcontentloaded"))
            if wait_until not in {"commit", "domcontentloaded", "load", "networkidle"}:
                return failure(f"Unsupported wait_until value: {wait_until}")
            await page.goto(url, wait_until=wait_until, timeout=timeout_ms)
            return await self._page_result("URL opened")
        if action == "search":
            query = str(args.get("query", "")).strip()
            if not query:
                return failure("Missing search query")
            await page.goto(
                f"https://www.google.com/search?q={quote_plus(query)}",
                wait_until="domcontentloaded",
                timeout=timeout_ms,
            )
            return await self._page_result("Google search completed", {"query": query})
        if action == "click":
            selector = self._require(args, "selector")
            await page.locator(selector).first.click(timeout=timeout_ms)
            return await self._page_result("Element clicked", {"selector": selector})
        if action == "fill":
            selector = self._require(args, "selector")
            value = str(args.get("value", ""))
            await page.locator(selector).first.fill(value, timeout=timeout_ms)
            return await self._page_result("Field filled", {"selector": selector, "characters": len(value)})
        if action == "keyboard":
            key = str(args.get("key", ""))
            text = args.get("text")
            if key:
                await page.keyboard.press(key)
            elif text is not None:
                await page.keyboard.type(str(text), delay=max(0, int(args.get("delay_ms", 0))))
            else:
                return failure("Provide key or text for keyboard action")
            return await self._page_result("Keyboard input sent")
        if action == "scroll":
            selector = str(args.get("selector", ""))
            delta_x = int(args.get("delta_x", 0))
            delta_y = int(args.get("delta_y", 700))
            if selector:
                await page.locator(selector).first.evaluate(
                    "(el, delta) => el.scrollBy(delta.x, delta.y)",
                    {"x": delta_x, "y": delta_y},
                )
            else:
                await page.mouse.wheel(delta_x, delta_y)
            return await self._page_result("Page scrolled", {"delta_x": delta_x, "delta_y": delta_y})
        if action == "wait":
            if args.get("selector"):
                await page.locator(str(args["selector"])).first.wait_for(
                    state=str(args.get("state", "visible")), timeout=timeout_ms
                )
            else:
                await page.wait_for_timeout(max(0, min(int(args.get("milliseconds", 1_000)), 60_000)))
            return await self._page_result("Browser wait completed")
        if action == "extract_text":
            selector = str(args.get("selector", "body"))
            text = await page.locator(selector).first.inner_text(timeout=timeout_ms)
            limit = max(1_000, min(int(args.get("max_chars", 50_000)), 250_000))
            return success(
                "Page text extracted",
                {
                    "url": page.url,
                    "selector": selector,
                    "text": text[:limit],
                    "truncated": len(text) > limit,
                },
            )
        if action == "screenshot":
            path = self._screenshot_path(args.get("path"), session_state)
            path.parent.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(path), full_page=bool(args.get("full_page", True)))
            session_state["latest_image"] = str(path)
            return success("Browser screenshot saved", {"path": str(path), "url": page.url})
        if action == "download":
            return await self._download(page, args, session_state, timeout_ms)
        if action == "upload":
            selector = self._require(args, "selector")
            paths = self._upload_paths(args, session_state)
            if not paths:
                return failure("Provide at least one upload path")
            await page.locator(selector).first.set_input_files(paths, timeout=timeout_ms)
            return success("Files selected for upload", {"selector": selector, "paths": paths})
        return failure(f"Unsupported browser action: {action}")

    async def _launch(self, args: Dict[str, Any], session_state: Dict[str, Any]) -> None:
        if self._context is not None:
            return
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()
        try:
            headless = bool(
                args.get("headless", os.getenv("BROWSER_HEADLESS", "false").lower() == "true")
            )
            viewport = {
                "width": max(320, min(int(args.get("width", 1440)), 7680)),
                "height": max(240, min(int(args.get("height", 900)), 4320)),
            }
            if args.get("profile_path") or args.get("persistent"):
                profile = resolve_safe_path(
                    str(args.get("profile_path", "artifacts/browser-profile")), session_state
                )
                profile.mkdir(parents=True, exist_ok=True)
                self._context = await self._playwright.chromium.launch_persistent_context(
                    user_data_dir=str(profile),
                    headless=headless,
                    accept_downloads=True,
                    viewport=viewport,
                )
            else:
                self._browser = await self._playwright.chromium.launch(headless=headless)
                self._context = await self._browser.new_context(
                    accept_downloads=True,
                    viewport=viewport,
                )
        except Exception:
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            await self._playwright.stop()
            self._playwright = None
            raise
        if not self._context.pages:
            await self._context.new_page()
        self._active_index = 0

    async def _close_browser(self) -> None:
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None
        self._active_index = 0

    async def _download(
        self,
        page: Any,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        timeout_ms: int,
    ) -> Dict[str, Any]:
        selector = self._require(args, "selector")
        directory = resolve_safe_path(str(args.get("directory", "artifacts/downloads")), session_state)
        directory.mkdir(parents=True, exist_ok=True)
        async with page.expect_download(timeout=timeout_ms) as download_info:
            await page.locator(selector).first.click(timeout=timeout_ms)
        download = await download_info.value
        filename = str(args.get("filename", "")).strip() or download.suggested_filename
        filename = Path(filename).name
        target = resolve_safe_path(str(directory / filename), session_state)
        if target.exists() and not bool(args.get("overwrite", False)):
            return failure(f"Download target already exists: {target}")
        await download.save_as(str(target))
        return success(
            "Browser download saved",
            {"path": str(target), "suggested_filename": download.suggested_filename, "url": download.url},
        )

    async def _status(self, message: str = "Browser status loaded") -> Dict[str, Any]:
        if self._context is None:
            return success(message, {"running": False, "tabs": [], "active_index": None})
        tabs = []
        for index, page in enumerate(self._context.pages):
            try:
                title = await page.title()
            except Exception:
                title = ""
            tabs.append({"index": index, "title": title, "url": page.url, "active": index == self._active_index})
        return success(message, {"running": True, "tabs": tabs, "active_index": self._active_index})

    async def _page_result(
        self, message: str, extra: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        page = self._page()
        data = {"url": page.url, "title": await page.title(), "active_index": self._active_index}
        data.update(extra or {})
        return success(message, data)

    def _page(self) -> Any:
        if self._context is None or not self._context.pages:
            raise RuntimeError("Browser is not running")
        self._active_index = max(0, min(self._active_index, len(self._context.pages) - 1))
        return self._context.pages[self._active_index]

    def _tab_index(self, args: Dict[str, Any]) -> int:
        if self._context is None or not self._context.pages:
            raise RuntimeError("Browser has no open tabs")
        index = int(args.get("index", self._active_index))
        if index < 0 or index >= len(self._context.pages):
            raise IndexError(f"Tab index out of range: {index}")
        return index

    @staticmethod
    def _normalize_url(url: str) -> str:
        value = url.strip()
        if not value:
            raise ValueError("Missing URL")
        if "://" not in value:
            value = f"https://{value}"
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Browser URLs must be fully-qualified HTTP(S) URLs")
        return value

    def _upload_paths(
        self, args: Dict[str, Any], session_state: Dict[str, Any]
    ) -> List[str]:
        values = self._as_list(args.get("paths", args.get("path", [])))
        if not values:
            raise ValueError("Provide at least one upload path")
        if len(values) > 20:
            raise ValueError("At most 20 files can be uploaded at once")
        paths: List[str] = []
        for value in values:
            path = resolve_safe_path(str(value), session_state, must_exist=True)
            if not path.is_file():
                raise ValueError(f"Upload path is not a file: {path}")
            paths.append(str(path))
        return paths

    @staticmethod
    def _require(args: Dict[str, Any], key: str) -> str:
        value = str(args.get(key, "")).strip()
        if not value:
            raise ValueError(f"Missing {key}")
        return value

    @staticmethod
    def _as_list(value: Any) -> List[Any]:
        if value in (None, ""):
            return []
        return value if isinstance(value, list) else [value]

    @staticmethod
    def _screenshot_path(value: Any, session_state: Dict[str, Any]) -> Path:
        if value:
            path = resolve_safe_path(str(value), session_state)
        else:
            stamp = str(session_state.get("run_id") or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
            path = resolve_safe_path(f"artifacts/browser/browser-{stamp}.png", session_state)
        return path if path.suffix.lower() == ".png" else path.with_suffix(".png")

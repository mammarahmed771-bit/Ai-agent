from __future__ import annotations

import ipaddress
import socket
from typing import Any, Dict
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from tools import failure, success


class InternetTool:
    """Perform lightweight, read-only web search and text retrieval."""

    name = "internet"
    description = "Search the web or retrieve readable text from a public HTTP(S) URL."
    actions = ["search", "open_url"]

    def run(
        self,
        args: Dict[str, Any],
        session_state: Dict[str, Any],
        request_permission: Any,
    ) -> Dict[str, Any]:
        action = str(args.get("action", "search")).lower()
        if action == "search":
            return self.search(str(args.get("query", "")))
        if action == "open_url":
            return self.open_url(
                str(args.get("url", "")), int(args.get("max_chars", 20_000))
            )
        return failure(f"Unsupported internet action: {action}")

    def open_url(self, url: str, max_chars: int = 20_000) -> Dict[str, Any]:
        if not url:
            return failure("Missing URL")
        try:
            self._validate_public_url(url)
            response = requests.get(
                url,
                timeout=20,
                headers={"User-Agent": "DesktopAIAgent/1.0 Mozilla/5.0"},
                allow_redirects=True,
            )
            self._validate_public_url(response.url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            for element in soup(["script", "style", "noscript"]):
                element.decompose()
            title = soup.title.get_text(strip=True) if soup.title else ""
            text = soup.get_text(" ", strip=True)
            limit = max(1_000, min(max_chars, 100_000))
            return success(
                f"Retrieved {response.url}",
                {
                    "url": response.url,
                    "title": title,
                    "text": text[:limit],
                    "truncated": len(text) > limit,
                    "status_code": response.status_code,
                },
            )
        except Exception as exc:
            return failure(exc, "Unable to retrieve URL")

    def search(self, query: str) -> Dict[str, Any]:
        if not query.strip():
            return failure("Missing search query")
        try:
            response = requests.get(
                "https://html.duckduckgo.com/html/",
                params={"q": query},
                timeout=20,
                headers={"User-Agent": "DesktopAIAgent/1.0 Mozilla/5.0"},
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            results = []
            for link in soup.select("a.result__a")[:8]:
                results.append(
                    {
                        "title": link.get_text(" ", strip=True),
                        "url": link.get("href", ""),
                    }
                )
            return success(
                f"Found {len(results)} results for '{query}'",
                {"query": query, "results": results},
            )
        except Exception as exc:
            return failure(exc, "Web search failed")

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Only fully-qualified HTTP(S) URLs are supported")
        for info in socket.getaddrinfo(parsed.hostname, parsed.port or 443):
            address = ipaddress.ip_address(info[4][0])
            if not address.is_global:
                raise ValueError("Private, loopback, and link-local URLs are blocked")

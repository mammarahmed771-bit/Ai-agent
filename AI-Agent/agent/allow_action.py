
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import threading
import time
from typing import Any, Dict, List, Optional


@dataclass
class Approval:
    request_id: str
    meta: Dict[str, Any]
    decided_at: float
    approved: bool


class ApprovalStore:
    """Thread-safe, expiring, one-action approval store."""

    def __init__(self, ttl_seconds: int = 900):
        self._lock = threading.Lock()
        self._approvals: Dict[str, Approval] = {}
        self.ttl_seconds = ttl_seconds

    @staticmethod
    def request_id(action_type: str, payload: Dict[str, Any]) -> str:
        """Return a stable ID so a resumed plan can request the same action."""
        import json

        canonical = json.dumps(
            {"action_type": action_type, "payload": payload},
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]
        return f"action_{digest}"

    def decide(
        self,
        request_id: str,
        approved: bool,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not request_id:
            return
        with self._lock:
            self._purge_expired_locked()
            self._approvals[request_id] = Approval(
                request_id=request_id,
                meta=meta or {},
                decided_at=time.time(),
                approved=approved,
            )

    def approve(self, request_id: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self.decide(request_id, True, meta)

    def deny(self, request_id: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self.decide(request_id, False, meta)

    def consume(self, request_id: str) -> Optional[bool]:
        """Consume and return the decision, or ``None`` if it is undecided."""
        if not request_id:
            return None
        with self._lock:
            self._purge_expired_locked()
            approval = self._approvals.pop(request_id, None)
            return approval.approved if approval else None

    def is_approved(self, request_id: str) -> bool:
        with self._lock:
            self._purge_expired_locked()
            approval = self._approvals.get(request_id)
            return bool(approval and approval.approved)

    def _purge_expired_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [
            request_id
            for request_id, approval in self._approvals.items()
            if approval.decided_at < cutoff
        ]
        for request_id in expired:
            self._approvals.pop(request_id, None)

    def dump(self) -> List[Dict[str, Any]]:
        with self._lock:
            self._purge_expired_locked()
            return [
                {
                    "request_id": a.request_id,
                    "meta": a.meta,
                    "decided_at": a.decided_at,
                    "approved": a.approved,
                }
                for a in self._approvals.values()
            ]


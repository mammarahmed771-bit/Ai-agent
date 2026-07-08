
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
    created_at: float
    decided_at: Optional[float] = None
    approved: Optional[bool] = None


class ApprovalStore:
    """Thread-safe, expiring store for registered one-action decisions."""

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

    def register(self, request_id: str, meta: Optional[Dict[str, Any]] = None) -> None:
        """Register an action before a client is allowed to decide it."""
        if not request_id:
            raise ValueError("request_id is required")
        with self._lock:
            self._purge_expired_locked()
            if request_id not in self._approvals:
                self._approvals[request_id] = Approval(
                    request_id=request_id,
                    meta=dict(meta or {}),
                    created_at=time.time(),
                )

    def decide(
        self,
        request_id: str,
        approved: bool,
        meta: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not request_id:
            return False
        with self._lock:
            self._purge_expired_locked()
            approval = self._approvals.get(request_id)
            if approval is None or approval.approved is not None:
                return False
            approval.meta.update(meta or {})
            approval.decided_at = time.time()
            approval.approved = bool(approved)
            return True

    def approve(self, request_id: str, meta: Optional[Dict[str, Any]] = None) -> bool:
        return self.decide(request_id, True, meta)

    def deny(self, request_id: str, meta: Optional[Dict[str, Any]] = None) -> bool:
        return self.decide(request_id, False, meta)

    def consume(self, request_id: str) -> Optional[bool]:
        """Consume and return the decision, or ``None`` if it is undecided."""
        if not request_id:
            return None
        with self._lock:
            self._purge_expired_locked()
            approval = self._approvals.get(request_id)
            if approval is None or approval.approved is None:
                return None
            self._approvals.pop(request_id, None)
            return approval.approved

    def is_approved(self, request_id: str) -> bool:
        with self._lock:
            self._purge_expired_locked()
            approval = self._approvals.get(request_id)
            return bool(approval and approval.approved is True)

    def is_pending(self, request_id: str) -> bool:
        with self._lock:
            self._purge_expired_locked()
            approval = self._approvals.get(request_id)
            return bool(approval and approval.approved is None)

    def _purge_expired_locked(self) -> None:
        cutoff = time.time() - self.ttl_seconds
        expired = [
            request_id
            for request_id, approval in self._approvals.items()
            if (approval.decided_at or approval.created_at) < cutoff
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
                    "created_at": a.created_at,
                    "decided_at": a.decided_at,
                    "approved": a.approved,
                }
                for a in self._approvals.values()
            ]


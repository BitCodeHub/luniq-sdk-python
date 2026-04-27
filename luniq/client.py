"""Luniq server-side SDK for Python.

Mirrors the @luniq/node SDK: track, identify, flags, flag, flush, shutdown.
Same wire format (camelCase keys, X-Luniq-Key auth, /v1/events batches of 100).
"""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import requests

from ._redact import redact_object


class Luniq:
    """Server-side SDK client for Luniq.AI.

    Args:
        api_key: Workspace API key with write or admin scope.
        endpoint: Base URL of your Luniq deployment.
            Defaults to ``https://uselunaai.com``.
        environment: ``PRD`` / ``STG`` / ``DEV``. Default ``PRD``.
        flush_interval_ms: Background flush interval. Default 10000.
        max_queue_size: Hard cap on buffered events. Default 10000.
        redact_pii: Auto-redact emails, phones, cards, SSNs. Default ``True``.
        session: Optional ``requests.Session`` for connection reuse / testing.
    """

    def __init__(
        self,
        api_key: str,
        endpoint: str = "https://uselunaai.com",
        environment: str = "PRD",
        flush_interval_ms: int = 10000,
        max_queue_size: int = 10000,
        redact_pii: bool = True,
        session: Optional[requests.Session] = None,
    ) -> None:
        if not api_key:
            raise ValueError("Luniq: api_key required")
        if not endpoint:
            raise ValueError("Luniq: endpoint required")

        self.api_key = api_key
        self.endpoint = endpoint.rstrip("/")
        self.environment = environment
        self.flush_interval_ms = flush_interval_ms
        self.max_queue_size = max_queue_size
        self.redact_pii = redact_pii
        self._session = session or requests.Session()

        self._queue: list = []
        self._queue_lock = threading.Lock()
        self._flag_cache: Dict[str, Dict[str, Any]] = {}
        self._cache_lock = threading.Lock()

        self._stop = threading.Event()
        self._timer = threading.Thread(target=self._run_flush_loop, daemon=True)
        self._timer.start()

    # --- public API -------------------------------------------------------

    def track(
        self,
        name: str,
        visitor_id: str,
        account_id: Optional[str] = None,
        properties: Optional[Dict[str, Any]] = None,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Buffer an event for batched async upload."""
        if not visitor_id:
            raise ValueError("track(): visitor_id required")

        props = dict(properties or {})
        props["os_type"] = "SERVER"
        props["env"] = self.environment
        props["brand"] = "H"
        if self.redact_pii:
            redact_object(props)

        ts = timestamp if isinstance(timestamp, datetime) else datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        event = {
            "id": str(uuid.uuid4()),
            "name": str(name),
            "properties": props,
            "timestamp": ts.isoformat().replace("+00:00", "Z"),
            "sessionId": None,
            "visitorId": str(visitor_id),
            "accountId": str(account_id) if account_id else None,
        }

        with self._queue_lock:
            self._queue.append(event)
            if len(self._queue) > self.max_queue_size:
                # Drop the oldest events; keep only the newest max_queue_size.
                drop = len(self._queue) - self.max_queue_size
                del self._queue[:drop]

    def identify(
        self,
        visitor_id: str,
        account_id: Optional[str] = None,
        traits: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Emit ``$identify`` with the supplied trait set."""
        self.track(
            "$identify",
            visitor_id=visitor_id,
            account_id=account_id,
            properties=traits or {},
        )

    def flags(
        self,
        visitor_id: str,
        account_id: Optional[str] = None,
        traits: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Fetch flag evaluation for a visitor. Caches result for ``flag()``."""
        if not visitor_id:
            raise ValueError("flags(): visitor_id required")
        try:
            r = self._session.post(
                f"{self.endpoint}/v1/sdk/flags/evaluate",
                json={
                    "visitorId": visitor_id,
                    "accountId": account_id or "",
                    "traits": traits or {},
                },
                headers={
                    "Content-Type": "application/json",
                    "X-Luniq-Key": self.api_key,
                },
                timeout=10,
            )
        except requests.RequestException:
            return {}
        if not r.ok:
            return {}
        try:
            data = r.json()
        except ValueError:
            return {}
        if not isinstance(data, dict):
            return {}
        with self._cache_lock:
            self._flag_cache[visitor_id] = data
        return data

    def flag(self, visitor_id: str, key: str) -> Any:
        """Sync getter for a previously fetched flag. Returns ``False`` on miss."""
        with self._cache_lock:
            m = self._flag_cache.get(visitor_id)
        if not m:
            return False
        return m.get(key, False)

    def flush(self) -> None:
        """Drain the queue, sending events in batches of 100. Blocks."""
        while True:
            with self._queue_lock:
                if not self._queue:
                    return
                batch = self._queue[:100]
                del self._queue[: len(batch)]
            try:
                r = self._session.post(
                    f"{self.endpoint}/v1/events",
                    json={"events": batch},
                    headers={
                        "Content-Type": "application/json",
                        "X-Luniq-Key": self.api_key,
                    },
                    timeout=10,
                )
                ok = r.ok
            except requests.RequestException:
                ok = False
            if not ok:
                # Re-queue at the head and stop draining; try again next tick.
                with self._queue_lock:
                    self._queue[:0] = batch
                return

    def shutdown(self) -> None:
        """Stop the flush thread and flush one last time. Call before exit."""
        self._stop.set()
        # Don't join — daemon thread; the wait below would block flush().
        self.flush()

    # --- internals --------------------------------------------------------

    def _run_flush_loop(self) -> None:
        interval = max(self.flush_interval_ms / 1000.0, 0.05)
        while not self._stop.wait(interval):
            try:
                self.flush()
            except Exception:
                # Never let the daemon thread die on a transient error.
                pass

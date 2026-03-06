"""
In-memory TTL cache for the web server.
Thread-safe, no external dependencies.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from typing import Any


class Cache:
    """
    Simple key-value cache with per-entry TTL.
    Keys are built from namespace + arbitrary args (JSON-serialized + md5).
    """

    def __init__(self) -> None:
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = threading.Lock()

    def _make_key(self, namespace: str, *args: Any) -> str:
        payload = json.dumps([namespace, *args], sort_keys=True, default=str)
        return hashlib.md5(payload.encode()).hexdigest()

    def get(self, namespace: str, *args: Any) -> Any | None:
        key = self._make_key(namespace, *args)
        with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, expires_at = entry
            if time.time() > expires_at:
                del self._data[key]
                return None
            return value

    def set(self, namespace: str, *args: Any, value: Any, ttl: int) -> None:
        key = self._make_key(namespace, *args)
        with self._lock:
            self._data[key] = (value, time.time() + ttl)

    def invalidate(self, namespace: str) -> None:
        prefix = hashlib.md5(json.dumps([namespace]).encode()).hexdigest()[:8]
        with self._lock:
            keys_to_del = [k for k in self._data if k.startswith(prefix)]
            for k in keys_to_del:
                del self._data[k]

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock:
            expired = [k for k, (_, exp) in self._data.items() if now > exp]
            for k in expired:
                del self._data[k]
        return len(expired)

"""
Pending state storage for multi-step approval flows.

Pattern: tool A generates data and saves it as "pending".
         tool B (approve_*) reads pending data and persists it.
This avoids requiring the LLM to echo back large JSON objects as tool params.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SKILL_DIR = Path(__file__).parent.parent


class PendingStore:
    """File-based store for pending approval data."""

    def __init__(self, skill_dir: Path | None = None) -> None:
        self._dir = (skill_dir or _SKILL_DIR) / ".pending"
        self._dir.mkdir(exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = key.replace("/", "_").replace(".", "_")
        return self._dir / f"{safe}.json"

    def save(self, key: str, data: Any) -> None:
        """Save pending data under key."""
        path = self._path(key)
        path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
        log.debug("pending.save key=%s path=%s", key, path)

    def load(self, key: str) -> Any | None:
        """Load pending data. Returns None if not found."""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("pending.load failed key=%s: %s", key, exc)
            return None

    def clear(self, key: str) -> None:
        """Delete pending data after it has been approved."""
        path = self._path(key)
        if path.exists():
            path.unlink()

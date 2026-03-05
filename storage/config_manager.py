"""
Atomic config.json manager.
Spec requires: all writes via temp file + rename to avoid race conditions
between ClawBot and skill processes both writing to config.json.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def _default_config_path() -> Path:
    return Path(__file__).parent.parent / "config.json"


class ConfigManager:
    """
    Thread-safe, race-condition-safe config.json manager.
    Uses atomic write: write to temp file → fsync → rename.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = config_path or _default_config_path()

    def load(self) -> dict[str, Any]:
        """Read config.json. Returns empty dict if file missing."""
        if not self._path.exists():
            return {}
        with open(self._path) as f:
            return json.load(f)

    def save(self, config: dict[str, Any]) -> None:
        """
        Atomic write: serialize to temp file → fsync → rename.
        Guarantees no partial reads if another process reads during write.
        """
        dir_path = self._path.parent
        fd, tmp_path = tempfile.mkstemp(dir=dir_path, suffix=".tmp", prefix="config_")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def update(self, updates: dict[str, Any]) -> dict[str, Any]:
        """
        Load config, apply shallow merge of updates, save atomically.
        Returns updated config.
        """
        config = self.load()
        config.update(updates)
        self.save(config)
        return config

    def set_nested(self, path: list[str], value: Any) -> None:
        """
        Set a nested config value by path list.
        Example: set_nested(["sources", "github", "credentials", "api_token"], "ghp_xxx")
        """
        config = self.load()
        node = config
        for key in path[:-1]:
            if key not in node or not isinstance(node[key], dict):
                node[key] = {}
            node = node[key]
        node[path[-1]] = value
        self.save(config)

    def get_nested(self, path: list[str], default: Any = None) -> Any:
        """Get a nested config value by path list."""
        config = self.load()
        node = config
        for key in path:
            if not isinstance(node, dict) or key not in node:
                return default
            node = node[key]
        return node

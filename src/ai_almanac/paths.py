"""Filesystem layout for ai-almanac.

All app state lives under a single configurable root directory. By default we use
the platform-appropriate user data directory (e.g. `~/.local/share/ai-almanac/`
on Linux, `~/Library/Application Support/ai-almanac/` on macOS). This can be
overridden with the `AI_ALMANAC_DATA_DIR` environment variable.
"""

from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path

_APP_NAME = "ai-almanac"


def data_root() -> Path:
    """Resolve the app data root, honoring `$AI_ALMANAC_DATA_DIR` if set."""
    override = os.environ.get("AI_ALMANAC_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return user_data_path(_APP_NAME, appauthor=False, ensure_exists=False)


def database_path() -> Path:
    return data_root() / "almanac.db"


def uploads_dir() -> Path:
    return data_root() / "uploads"


def jobs_dir() -> Path:
    return data_root() / "jobs"


def job_dir(job_id: str) -> Path:
    return jobs_dir() / job_id


def benchmark_env_dir() -> Path:
    return data_root() / "benchmark-env"


def cache_dir() -> Path:
    return data_root() / "cache"


def settings_file() -> Path:
    return data_root() / "settings.json"


def ensure_layout() -> Path:
    """Create the standard subdirectories under `data_root()`. Idempotent."""
    root = data_root()
    for d in (root, uploads_dir(), jobs_dir(), cache_dir()):
        d.mkdir(parents=True, exist_ok=True)
    return root

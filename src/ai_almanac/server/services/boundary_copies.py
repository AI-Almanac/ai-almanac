"""Copies of geoBoundaries data kept in a deployment's data bucket.

A cloud host can find geoBoundaries unreachable, so a deployment reads outlines
from these copies first and asks the live service only for what was never
copied. ``scripts/copy_boundaries.py`` writes them from a machine that can reach
geoBoundaries; the app only reads them.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import fsspec

from ai_almanac.settings import settings

logger = logging.getLogger(__name__)

FOLDER = "boundaries"


def root_in(bucket: str) -> str:
    """Where the copies live in a data bucket."""
    return f"gs://{bucket}/{FOLDER}"


def deployment_root() -> str | None:
    """The copies this deployment reads, or None when it has no data bucket."""
    bucket = settings.gcs_data_bucket.strip()
    if settings.storage_backend.lower() != "gcs" or not bucket:
        return None
    return root_in(bucket)


def countries_path(root: str) -> str:
    return f"{root}/countries.json"


def boundary_path(root: str, iso: str, boundary_type: str) -> str:
    return f"{root}/{iso}/{boundary_type}.json"


async def read(path: str) -> Any | None:
    """The JSON copied to ``path``. None when nothing was copied there or the copy
    cannot be read, so the caller asks the live service instead."""
    try:
        return await asyncio.to_thread(_read_file, path)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Could not read the boundary copy at %s: %s", path, type(exc).__name__)
        return None


def write(path: str, value: Any) -> None:
    fs, location = fsspec.core.url_to_fs(path)
    fs.makedirs(fs._parent(location), exist_ok=True)
    with fs.open(location, "w") as file:
        json.dump(value, file)


def _read_file(path: str) -> Any:
    with fsspec.open(path, "r") as file:
        return json.load(file)

"""Map payloads derived from a complete job's outputs, built on first read and stored beside them.

A complete job's outputs never change, so a stored payload stays valid for the
life of the job and is deleted with it. Payload names carry a version: bump it
when a payload's shape changes and every job rebuilds on its next read.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from .storage import get_storage

logger = logging.getLogger(__name__)


async def load_or_build(job_id: str, name: str, build: Callable[[], bytes | None]) -> bytes | None:
    """Return the stored payload, or build and store it. ``build`` returns None when
    the job has nothing to derive yet; that result is passed through, not stored."""
    storage = get_storage()
    stored = await asyncio.to_thread(storage.read_derived, job_id, name)
    if stored is not None:
        return stored
    data = await asyncio.to_thread(build)
    if data is None:
        return None
    try:
        await asyncio.to_thread(storage.write_derived, job_id, name, data)
    except Exception:  # noqa: BLE001 — serving the freshly built payload still works
        logger.warning("Could not store %s for job %s", name, job_id, exc_info=True)
    return data

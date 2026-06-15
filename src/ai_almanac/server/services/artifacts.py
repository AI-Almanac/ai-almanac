"""Artifact indexing.

Bridges the filesystem ArtifactStore (which computes artifact records) and the
job_artifacts table (the indexed system of record). Publication runs in the
application process — never the runner — and is atomic per job: all records and
the publication marker commit together, so a successful job publishes its full,
indexed output set exactly once and a failed job never publishes partial output.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services.artifact_store import get_artifact_store

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


async def index_job_artifacts(job_id: str) -> int:
    """Index a completed job's outputs into job_artifacts and mark it published.

    Atomic: artifact rows and the publication marker commit in one transaction.
    Returns the number of artifacts indexed.
    """
    store = get_artifact_store()
    artifacts = await asyncio.to_thread(store.publish, job_id)

    async with get_db() as conn:
        for artifact in artifacts:
            await conn.execute(
                text(
                    "INSERT INTO job_artifacts "
                    "(id, job_id, kind, filename, media_type, size_bytes, "
                    "checksum, storage_key, created_at) VALUES "
                    "(:id, :job_id, :kind, :filename, :media_type, :size_bytes, "
                    ":checksum, :storage_key, :created_at)"
                ),
                {
                    "id": artifact.id,
                    "job_id": artifact.job_id,
                    "kind": artifact.kind,
                    "filename": artifact.filename,
                    "media_type": artifact.media_type,
                    "size_bytes": artifact.size_bytes,
                    "checksum": artifact.checksum,
                    "storage_key": artifact.storage_key,
                    "created_at": artifact.created_at,
                },
            )
        await conn.execute(
            text("UPDATE jobs SET artifacts_published_at = :now WHERE id = :id"),
            {"now": _now(), "id": job_id},
        )
    return len(artifacts)


async def publish_pending() -> None:
    """Index artifacts for completed jobs that have not been published yet."""
    async with get_db() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT id FROM jobs "
                    "WHERE status = 'complete' AND artifacts_published_at IS NULL"
                )
            )
        ).fetchall()
    for (job_id,) in rows:
        try:
            await index_job_artifacts(job_id)
        except Exception as exc:  # noqa: BLE001 — one bad job must not block others
            logger.warning("artifact indexing failed for job %s: %s", job_id, exc)


async def list_job_artifacts(job_id: str) -> list[dict]:
    async with get_db() as conn:
        rows = (
            (
                await conn.execute(
                    text(
                        "SELECT id, kind, filename, media_type, size_bytes, "
                        "checksum, created_at FROM job_artifacts "
                        "WHERE job_id = :id ORDER BY kind, filename"
                    ),
                    {"id": job_id},
                )
            )
            .mappings()
            .fetchall()
        )
    return [dict(row) for row in rows]

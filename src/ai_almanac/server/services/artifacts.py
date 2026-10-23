"""Artifact indexing.

Bridges the filesystem ArtifactStore (which computes artifact records) and the
job_artifacts table (the indexed system of record). Publication runs in the
application process — never the runner — and is atomic per job: all records and
the publication marker commit together, so a successful job publishes its full,
indexed output set exactly once and a failed job never publishes partial output.

A blend whose day-level blend trains in its own call (day_level_blends) adds its
outputs after the job completes, so it is indexed a second time once that call
finishes, under its own claim; files the first pass already indexed are kept.
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


# The jobs columns that claim one indexing pass: the job's own outputs, then the
# outputs its day-level blend adds after it completes.
_CLAIMS = ("artifacts_published_at", "day_level_published_at")


async def index_job_artifacts(job_id: str, claim: str = "artifacts_published_at") -> int:
    """Index a job's outputs into job_artifacts and mark the `claim` pass done.

    Atomic: artifact rows and the publication marker commit in one transaction.
    Rows for files an earlier pass indexed are left as they are. Returns the
    number of artifacts listed (0 if another instance already claimed the pass).
    """
    if claim not in _CLAIMS:
        raise ValueError(f"Unknown artifact publication claim: {claim!r}")
    store = get_artifact_store()
    artifacts = await asyncio.to_thread(store.publish, job_id)

    async with get_db() as conn:
        # Every instance's reconciler loop publishes pending jobs; claiming the
        # job first means exactly one of them inserts its rows.
        claimed = await conn.execute(
            text(f"UPDATE jobs SET {claim} = :now WHERE id = :id AND {claim} IS NULL"),
            {"now": _now(), "id": job_id},
        )
        if not claimed.rowcount:
            return 0
        for artifact in artifacts:
            await conn.execute(
                text(
                    "INSERT INTO job_artifacts "
                    "(id, job_id, kind, filename, media_type, size_bytes, "
                    "checksum, storage_key, created_at) VALUES "
                    "(:id, :job_id, :kind, :filename, :media_type, :size_bytes, "
                    ":checksum, :storage_key, :created_at) "
                    "ON CONFLICT (job_id, kind, filename) DO NOTHING"
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
    return len(artifacts)


async def publish_pending() -> None:
    """Index artifacts for completed jobs, and for finished day-level blends,
    that have not been published yet."""
    async with get_db() as conn:
        pending = [
            (job_id, "artifacts_published_at")
            for (job_id,) in (
                await conn.execute(
                    text(
                        "SELECT id FROM jobs "
                        "WHERE status = 'complete' AND artifacts_published_at IS NULL"
                    )
                )
            ).fetchall()
        ] + [
            (job_id, "day_level_published_at")
            for (job_id,) in (
                await conn.execute(
                    text(
                        "SELECT id FROM jobs WHERE status = 'complete' "
                        "AND artifacts_published_at IS NOT NULL "
                        "AND day_level_status = 'complete' AND day_level_published_at IS NULL"
                    )
                )
            ).fetchall()
        ]
    for job_id, claim in pending:
        try:
            await index_job_artifacts(job_id, claim)
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

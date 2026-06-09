"""Phase 6 (2/2) — artifact publication, indexing, listing, and deletion."""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import httpx
import pytest

from ai_almanac.paths import database_path
from ai_almanac.server.services.artifacts import list_job_artifacts, publish_pending
from ai_almanac.server.services.storage import get_storage


def _insert_job(job_id: str, status: str = "complete") -> None:
    with sqlite3.connect(database_path()) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (id, external_id, created_at) "
            "VALUES ('pub-u', 'pub-u', '2026-01-01T00:00:00')"
        )
        conn.execute(
            "INSERT INTO jobs (id, user_id, dataset_id, status, created_at) "
            "VALUES (?, 'pub-u', 'ds', ?, '2026-01-01T00:00:00')",
            (job_id, status),
        )
        conn.commit()


def _write_outputs(job_id: str) -> None:
    output, figure = get_storage().job_output_uri(job_id)
    (Path(output) / "spatial_metrics_m_1-7.nc").write_bytes(b"netcdf-bytes")
    (Path(figure) / "portrait_m.png").write_bytes(b"\x89PNG\r\n\x1a\npng")


def _published_at(job_id: str) -> str | None:
    with sqlite3.connect(database_path()) as conn:
        return conn.execute(
            "SELECT artifacts_published_at FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()[0]


@pytest.mark.asyncio
async def test_publish_pending_indexes_complete_job(client: httpx.AsyncClient) -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="complete")
    _write_outputs(job_id)

    await publish_pending()

    artifacts = await list_job_artifacts(job_id)
    assert {a["filename"] for a in artifacts} == {
        "spatial_metrics_m_1-7.nc",
        "portrait_m.png",
    }
    assert all(a["checksum"] and a["size_bytes"] > 0 for a in artifacts)
    assert _published_at(job_id) is not None


@pytest.mark.asyncio
async def test_failed_job_is_not_published(client: httpx.AsyncClient) -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="failed")
    _write_outputs(job_id)  # logs/outputs on disk, but the job failed

    await publish_pending()

    assert await list_job_artifacts(job_id) == []
    assert _published_at(job_id) is None


@pytest.mark.asyncio
async def test_publish_pending_is_idempotent(client: httpx.AsyncClient) -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="complete")
    _write_outputs(job_id)

    await publish_pending()
    await publish_pending()

    assert len(await list_job_artifacts(job_id)) == 2


@pytest.mark.asyncio
async def test_artifacts_endpoint_lists_indexed_outputs(
    client: httpx.AsyncClient,
) -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="complete")
    _write_outputs(job_id)
    await publish_pending()

    resp = await client.get(f"/jobs/{job_id}/artifacts")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    assert all(a["url"].startswith(f"/jobs/{job_id}/results/") for a in body)


@pytest.mark.asyncio
async def test_delete_job_removes_artifacts_and_files(
    client: httpx.AsyncClient,
) -> None:
    job_id = str(uuid.uuid4())
    _insert_job(job_id, status="complete")
    _write_outputs(job_id)
    await publish_pending()
    assert await list_job_artifacts(job_id)  # indexed

    resp = await client.delete(f"/jobs/{job_id}")
    assert resp.status_code == 204
    assert await list_job_artifacts(job_id) == []
    assert not get_storage().job_dir(job_id).exists()

"""Reads of the ledger and of stamped files that only tests make."""

from __future__ import annotations

import json
from pathlib import Path

import sqlalchemy as sa

from ai_almanac.server import tables
from ai_almanac.server.db import get_db
from ai_almanac.server.services import provenance


async def get_approval(approval_id: str) -> dict | None:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    sa.select(tables.approvals).where(tables.approvals.c.id == approval_id)
                )
            )
            .mappings()
            .fetchone()
        )
    return dict(row) if row else None


def read_history(path: Path) -> list[dict] | None:
    """The provenance chain stamped into a file, or None when it carries none."""
    fmt = provenance.format_of(path)
    if fmt == "netcdf":
        import h5netcdf

        with h5netcdf.File(path, "r") as handle:
            raw = handle.attrs.get(provenance.HISTORY_ATTR)
        return json.loads(raw) if raw else None
    if fmt == "png":
        marker = provenance.HISTORY_ATTR.encode() + b"\x00"
        for chunk_type, body in provenance._png_chunks(path.read_bytes()):
            if chunk_type == b"tEXt" and body.startswith(marker):
                return json.loads(body.split(b"\x00", 1)[1].decode("latin-1"))
    return None

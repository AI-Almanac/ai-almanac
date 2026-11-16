from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.settings import get_packaged_regions


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _row_dict(row) -> dict:
    region = dict(row)
    for key in ("land_only", "shp_only", "is_builtin"):
        region[key] = bool(region.get(key))
    return region


async def seed_packaged_regions() -> int:
    inserted = 0
    now = _now()
    async with get_db() as conn:
        for region in get_packaged_regions():
            result = await conn.execute(
                text(
                    "INSERT INTO regions "
                    "(id, display_name, description, romp_name, boundary_iso, "
                    "lat_min, lat_max, lon_min, lon_max, land_only, shp_only, "
                    "is_builtin, created_at, updated_at) "
                    "VALUES (:id, :display_name, :description, :romp_name, :boundary_iso, "
                    ":lat_min, :lat_max, :lon_min, :lon_max, :land_only, :shp_only, "
                    "TRUE, :now, :now) "
                    "ON CONFLICT(id) DO NOTHING"
                ),
                {
                    "id": region["id"],
                    "display_name": region["display_name"],
                    "description": region.get("description", ""),
                    "romp_name": region.get("romp_name"),
                    "boundary_iso": region.get("boundary_iso"),
                    "lat_min": region.get("lat_min"),
                    "lat_max": region.get("lat_max"),
                    "lon_min": region.get("lon_min"),
                    "lon_max": region.get("lon_max"),
                    "land_only": bool(region.get("land_only", False)),
                    "shp_only": bool(region.get("shp_only", False)),
                    "now": now,
                },
            )
            inserted += result.rowcount
    return inserted


async def list_regions() -> list[dict]:
    async with get_db() as conn:
        result = await conn.execute(
            text("SELECT * FROM regions ORDER BY is_builtin DESC, display_name")
        )
        return [_row_dict(row) for row in result.mappings().fetchall()]


async def get_region(region_id: str) -> dict | None:
    async with get_db() as conn:
        row = (
            (
                await conn.execute(
                    text("SELECT * FROM regions WHERE lower(id) = :id"),
                    {"id": region_id.strip().lower()},
                )
            )
            .mappings()
            .fetchone()
        )
    return _row_dict(row) if row else None

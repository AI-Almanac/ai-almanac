from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.settings import get_packaged_regions

_SLUG_PATTERN = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def region_slug(display_name: str) -> str:
    slug = _SLUG_PATTERN.sub("-", display_name.strip().lower()).strip("-")
    return slug or "region"


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


async def create_region(
    *,
    display_name: str,
    description: str,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    land_only: bool,
) -> dict:
    base_id = region_slug(display_name)
    region_id = base_id
    suffix = 2
    async with get_db() as conn:
        while (
            await conn.execute(text("SELECT 1 FROM regions WHERE id = :id"), {"id": region_id})
        ).scalar_one_or_none():
            region_id = f"{base_id}-{suffix}"
            suffix += 1

        now = _now()
        result = await conn.execute(
            text(
                "INSERT INTO regions "
                "(id, display_name, description, lat_min, lat_max, lon_min, lon_max, "
                "land_only, shp_only, is_builtin, created_at, updated_at) "
                "VALUES (:id, :display_name, :description, :lat_min, :lat_max, "
                ":lon_min, :lon_max, :land_only, FALSE, FALSE, :now, :now) RETURNING *"
            ),
            {
                "id": region_id,
                "display_name": display_name.strip(),
                "description": description.strip(),
                "lat_min": lat_min,
                "lat_max": lat_max,
                "lon_min": lon_min,
                "lon_max": lon_max,
                "land_only": land_only,
                "now": now,
            },
        )
        return _row_dict(result.mappings().fetchone())


async def update_region(
    region_id: str,
    *,
    display_name: str,
    description: str,
    lat_min: float,
    lat_max: float,
    lon_min: float,
    lon_max: float,
    land_only: bool,
) -> dict | None:
    async with get_db() as conn:
        result = await conn.execute(
            text(
                "UPDATE regions SET display_name = :display_name, description = :description, "
                "lat_min = :lat_min, lat_max = :lat_max, lon_min = :lon_min, "
                "lon_max = :lon_max, land_only = :land_only, updated_at = :now "
                "WHERE id = :id AND is_builtin = FALSE RETURNING *"
            ),
            {
                "id": region_id,
                "display_name": display_name.strip(),
                "description": description.strip(),
                "lat_min": lat_min,
                "lat_max": lat_max,
                "lon_min": lon_min,
                "lon_max": lon_max,
                "land_only": land_only,
                "now": _now(),
            },
        )
        row = result.mappings().fetchone()
        return _row_dict(row) if row else None


async def count_region_sources(region_id: str) -> int:
    async with get_db() as conn:
        return int(
            (
                await conn.execute(
                    text("SELECT COUNT(*) FROM data_sources WHERE region = :id"),
                    {"id": region_id},
                )
            ).scalar_one()
        )


async def delete_region(region_id: str) -> bool:
    async with get_db() as conn:
        result = await conn.execute(
            text("DELETE FROM regions WHERE id = :id AND is_builtin = FALSE"),
            {"id": region_id},
        )
        return result.rowcount > 0

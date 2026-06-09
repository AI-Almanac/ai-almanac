"""Promote regions to persisted domain entities.

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "regions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("romp_name", sa.Text()),
        sa.Column("boundary_iso", sa.Text()),
        sa.Column("lat_min", sa.Float()),
        sa.Column("lat_max", sa.Float()),
        sa.Column("lon_min", sa.Float()),
        sa.Column("lon_max", sa.Float()),
        sa.Column("land_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("shp_only", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text()),
        sa.CheckConstraint("lat_min IS NULL OR lat_min >= -90", name="ck_regions_lat_min"),
        sa.CheckConstraint("lat_max IS NULL OR lat_max <= 90", name="ck_regions_lat_max"),
        sa.CheckConstraint("lon_min IS NULL OR lon_min >= -180", name="ck_regions_lon_min"),
        sa.CheckConstraint("lon_max IS NULL OR lon_max <= 180", name="ck_regions_lon_max"),
        sa.CheckConstraint(
            "lat_min IS NULL OR lat_max IS NULL OR lat_min < lat_max",
            name="ck_regions_lat_order",
        ),
        sa.CheckConstraint(
            "lon_min IS NULL OR lon_max IS NULL OR lon_min < lon_max",
            name="ck_regions_lon_order",
        ),
    )
    op.create_index("idx_regions_display_name", "regions", ["display_name"])


def downgrade() -> None:
    op.drop_index("idx_regions_display_name", table_name="regions")
    op.drop_table("regions")

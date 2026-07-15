"""Repurpose forecast_runs as the trajectory-set generation tracking table.

The old per-run `forecast_runs` table is dead everywhere but the migrations.
The forecast redesign reuses it to track one row per trajectory *set* — a
`(model_name, init_source, season)` triple — recording generation status and
the init dates already rolled out and cached.

Additive, idempotent columns (mirrors the guard pattern from 0006):

- `init_source`: the forecast initialization data source (e.g. "gfs"). Part of
  the set identity — a trajectory from the wrong init source is silently wrong.
- `season`: the season the set covers (e.g. "2026").
- `covered_init_dates`: JSON list of ISO date strings already rolled out & cached.

A unique index over `(model_name, init_source, season)` enforces the one-row-
per-set invariant.

Revision ID: 0016
Revises: 0015
Create Date: 2026-07-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def _create_index_if_missing(
    index_name: str, table_name: str, columns: list[str], *, unique: bool = False
) -> None:
    if index_name not in _index_names(table_name):
        op.create_index(index_name, table_name, columns, unique=unique)


def upgrade() -> None:
    _add_column_if_missing("forecast_runs", sa.Column("init_source", sa.Text(), nullable=True))
    _add_column_if_missing("forecast_runs", sa.Column("season", sa.Text(), nullable=True))
    _add_column_if_missing(
        "forecast_runs", sa.Column("covered_init_dates", sa.JSON(), nullable=True)
    )
    # One row per trajectory set, not per run.
    _create_index_if_missing(
        "uq_forecast_runs_set",
        "forecast_runs",
        ["model_name", "init_source", "season"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_forecast_runs_set", table_name="forecast_runs")
    op.drop_column("forecast_runs", "covered_init_dates")
    op.drop_column("forecast_runs", "season")
    op.drop_column("forecast_runs", "init_source")

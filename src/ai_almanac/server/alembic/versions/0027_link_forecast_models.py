"""Link existing model data sources to their live forecast model.

Model data sources now name the live forecast model that produced them in
`metadata.forecast_model_id`, chosen at registration. Before that, the link was
guessed at forecast time by matching the data source's normalized name against
forecast_models.yaml ids, display names, and aliases. This backfills the link
for every source that guess resolved, so existing blends stay forecastable.

The match table is frozen here rather than read from forecast_models.yaml, so
re-running the migration later gives the same result. A source whose grid
differs from its matched model's stays unlinked, as it could not run live
forecasts before either.

Old code on this schema stays correct: it ignores the extra metadata key.
Downgrade keeps the links, which old code also ignores.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-08
"""

from __future__ import annotations

import json
import math
import re

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

# Normalized data-source name → (forecast model id, grid step in degrees), as
# resolved by the name matching this migration replaces.
_NAME_MATCHES = {
    "aifs": ("aifs", 0.25),
    "aifsens": ("aifsens", 0.25),
    "aifs_ens": ("aifsens", 0.25),
    "aifs2": ("aifs2", 0.25),
    "aifs_single_v2": ("aifs2", 0.25),
    "aifs2ens": ("aifs2ens", 0.25),
    "aifs2_ens": ("aifs2ens", 0.25),
    "aifs_ensemble_v2": ("aifs2ens", 0.25),
    "graphcast": ("graphcast", 1.0),
    "graphcast_small": ("graphcast", 1.0),
    "graphcast_operational": ("graphcast_operational", 0.25),
    "gencast": ("gencast", 1.0),
    "gencast_mini": ("gencast", 1.0),
    "fuxi": ("fuxi", 0.25),
}

data_sources = sa.table(
    "data_sources",
    sa.column("id", sa.Text()),
    sa.column("kind", sa.Text()),
    sa.column("name", sa.Text()),
    sa.column("metadata", sa.JSON()),
)


def _decoded(metadata) -> dict:
    # SQLite rows written through text() hold the JSON as a string.
    if isinstance(metadata, str):
        return json.loads(metadata or "{}")
    return dict(metadata or {})


def _linked_model_id(name: str, metadata: dict) -> str | None:
    match = _NAME_MATCHES.get(re.sub(r"[^0-9a-z]+", "_", name.lower()).strip("_"))
    if match is None:
        return None
    model_id, model_step = match
    archive_step = metadata.get("grid_step_deg")
    if isinstance(archive_step, int | float) and not math.isclose(
        archive_step, model_step, abs_tol=1e-6
    ):
        return None
    return model_id


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.select(data_sources.c.id, data_sources.c.name, data_sources.c.metadata).where(
            data_sources.c.kind == "model"
        )
    ).fetchall()
    for row in rows:
        metadata = _decoded(row.metadata)
        if metadata.get("forecast_model_id"):
            continue
        model_id = _linked_model_id(row.name, metadata)
        if model_id is None:
            continue
        bind.execute(
            sa.update(data_sources)
            .where(data_sources.c.id == row.id)
            .values(metadata={**metadata, "forecast_model_id": model_id})
        )


def downgrade() -> None:
    pass

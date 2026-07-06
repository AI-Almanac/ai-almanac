"""Drop vestigial bangladesh/custom built-in regions.

These shipped as test scaffolding for the E2S feature and leaked into the
region picker as un-deletable "Built In" options. Removed from
config/regions.yaml; this migration clears the rows the seeder already
wrote to existing databases (seeding is insert-only and never deletes).

Revision ID: 0015
Revises: 0014
Create Date: 2026-07-06
"""

from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None

def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        "DELETE FROM data_sources WHERE region IN ('bangladesh', 'custom')"
    )
    conn.exec_driver_sql(
        "DELETE FROM regions WHERE id IN ('bangladesh', 'custom') AND is_builtin"
    )


def downgrade() -> None:
    pass

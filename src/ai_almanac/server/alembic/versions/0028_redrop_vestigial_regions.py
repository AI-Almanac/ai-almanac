"""Drop the vestigial bangladesh/custom built-in regions again.

0015 deleted these rows, but region seeding also runs per request, and
instances still on the release that packaged them re-inserted the rows
during that rollout. No supported release packages them anymore, so this
second pass sticks.

Unlike 0015, data sources are deleted only when their region is the
built-in row: 0015 freed both ids, so a user-created region may now own
`custom` or `bangladesh`, and its sources must survive.

Old code on this schema stays correct: its regions.yaml no longer lists
either region, so nothing re-seeds them.

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-14
"""

from __future__ import annotations

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_VESTIGIAL = "id IN ('bangladesh', 'custom') AND is_builtin"


def upgrade() -> None:
    conn = op.get_bind()
    conn.exec_driver_sql(
        f"DELETE FROM data_sources WHERE region IN (SELECT id FROM regions WHERE {_VESTIGIAL})"
    )
    conn.exec_driver_sql(f"DELETE FROM regions WHERE {_VESTIGIAL}")


def downgrade() -> None:
    pass

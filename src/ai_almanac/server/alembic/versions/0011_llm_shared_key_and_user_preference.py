"""LLM shared key on providers and per-user LLM preference.

Lets an admin attach a shared API key to a provider (used by any user who
hasn't brought their own) and lets each user choose between the shared option
and their own profile.

Revision ID: 0011
Revises: 0010
Create Date: 2026-06-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def _column_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table_name not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table_name)}


def _add_column_if_missing(table_name: str, column: sa.Column) -> None:
    if column.name not in _column_names(table_name):
        op.add_column(table_name, column)


def upgrade() -> None:
    _add_column_if_missing(
        "llm_providers",
        sa.Column("allow_shared", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    _add_column_if_missing("llm_providers", sa.Column("shared_model_name", sa.Text()))
    _add_column_if_missing("llm_providers", sa.Column("shared_key_version", sa.Integer()))
    _add_column_if_missing("llm_providers", sa.Column("shared_key_nonce", sa.LargeBinary()))
    _add_column_if_missing("llm_providers", sa.Column("shared_key_ciphertext", sa.LargeBinary()))
    _add_column_if_missing(
        "users",
        sa.Column("llm_preference", sa.Text(), nullable=False, server_default="auto"),
    )


def downgrade() -> None:
    op.drop_column("users", "llm_preference")
    for column in (
        "shared_key_ciphertext",
        "shared_key_nonce",
        "shared_key_version",
        "shared_model_name",
        "allow_shared",
    ):
        op.drop_column("llm_providers", column)

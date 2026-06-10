from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def record_event(
    conn: AsyncConnection,
    table: str,
    event_type: str,
    *,
    user_id: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    quantity: int | None = None,
    metadata: dict | None = None,
) -> None:
    if table not in {"audit_events", "usage_events"}:
        raise ValueError("unsupported event table")
    await conn.execute(
        text(
            f"INSERT INTO {table} "
            "(id, user_id, event_type, resource_type, resource_id, quantity, metadata, created_at) "
            "VALUES (:id, :uid, :event, :resource_type, :resource_id, :quantity, :metadata, :now)"
        ),
        {
            "id": str(uuid.uuid4()),
            "uid": user_id,
            "event": event_type,
            "resource_type": resource_type,
            "resource_id": resource_id,
            "quantity": quantity,
            "metadata": json.dumps(metadata or {}),
            "now": datetime.now(UTC).isoformat(),
        },
    )


async def audit(conn: AsyncConnection, event_type: str, **kwargs) -> None:
    await record_event(conn, "audit_events", event_type, **kwargs)


async def usage(conn: AsyncConnection, event_type: str, **kwargs) -> None:
    await record_event(conn, "usage_events", event_type, **kwargs)

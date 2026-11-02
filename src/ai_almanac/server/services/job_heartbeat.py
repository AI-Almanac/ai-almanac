"""Whether a job supervisor's heartbeat is recent enough to count as alive."""

from __future__ import annotations

from datetime import UTC, datetime

HEARTBEAT_TIMEOUT_SECONDS = 15


def heartbeat_is_fresh(value: str | None) -> bool:
    if not value:
        return False
    try:
        heartbeat = datetime.fromisoformat(value)
    except ValueError:
        return False
    return (datetime.now(UTC) - heartbeat).total_seconds() <= HEARTBEAT_TIMEOUT_SECONDS

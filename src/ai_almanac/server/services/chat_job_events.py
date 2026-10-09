"""Runs in a chat's scope that finished during the conversation.

One source for two consumers. The chat panel shows each event as a notice with
a follow-up, and the next turn tells the assistant what changed since its last
reply. The assistant only acts when the user writes, so this is how it learns a
run finished, rather than promising to check back.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel

from .benchmark_state import BenchmarkScope
from .job_manager import ACTIVE_STATUSES, TERMINAL_STATUSES

# The API's enum of terminal states; a test keeps it equal to TERMINAL_STATUSES.
FinishedStatus = Literal["complete", "failed", "canceled"]

_STATUS_PHRASES: dict[str, str] = {
    "complete": "finished",
    "failed": "failed",
    "canceled": "was canceled",
}
_LABEL_MAX_CHARS = 80


class ChatJobEvent(BaseModel):
    job_id: str
    label: str
    status: FinishedStatus
    at: datetime


class ChatJobActivity(BaseModel):
    events: list[ChatJobEvent]
    # Runs in scope that have not finished yet, so the panel knows to keep asking.
    active: int


def _utc(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def job_activity(statuses: Iterable[dict], since: datetime | str) -> ChatJobActivity:
    """Runs that finished at or after `since`, oldest first, and how many are still going."""
    start = _utc(since)
    events: list[ChatJobEvent] = []
    active = 0
    for job in statuses:
        if job["status"] in ACTIVE_STATUSES:
            active += 1
        elif (
            job["status"] in TERMINAL_STATUSES
            and job["completed_at"]
            and _utc(job["completed_at"]) >= start
        ):
            events.append(
                ChatJobEvent(
                    job_id=job["job_id"],
                    label=job["label"] or "A run",
                    status=job["status"],
                    at=_utc(job["completed_at"]),
                )
            )
    return ChatJobActivity(events=sorted(events, key=lambda event: event.at), active=active)


def _prompt_safe(label: str) -> str:
    """Model and blend names are user-written; keep one on one short line."""
    return re.sub(r"\s+", " ", label).strip()[:_LABEL_MAX_CHARS]


def platform_note(events: list[ChatJobEvent]) -> str | None:
    """What the assistant is told about runs that changed since its last reply."""
    if not events:
        return None
    lines = [
        f'- "{_prompt_safe(event.label)}" {_STATUS_PHRASES[event.status]} (job {event.job_id})'
        for event in events
    ]
    return (
        "[Platform update — written by the platform, not the user] "
        "Runs in this conversation that changed since your last reply:\n" + "\n".join(lines)
    )


async def scope_job_activity(
    user_id: str, scope: BenchmarkScope, since: datetime | str
) -> ChatJobActivity:
    from .benchmark_domain import scope_job_statuses

    return job_activity(await scope_job_statuses(user_id, scope), since)

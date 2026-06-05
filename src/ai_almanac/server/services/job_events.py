"""Per-job event broker for live status and log streaming over WebSocket.

The in-process runner publishes events (status changes, log lines, completion)
into a per-job channel; WebSocket connections subscribe to that channel and
fan-out events to any browsers watching the job. This replaces HTTP polling.

The runner thread calls `publish(...)`; WebSocket handlers call
`subscribe(job_id)` to obtain an `asyncio.Queue` they drain until the job
terminates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any


@dataclass
class JobEvent:
    """Event payload streamed to subscribers. Serializes as JSON over the wire."""

    type: str  # "status" | "log" | "metric" | "done"
    payload: dict[str, Any] = field(default_factory=dict)


class JobEventBroker:
    """In-memory pub/sub for job events. Process-wide singleton via `get_broker()`.

    Threading note: runner code runs in a worker thread; FastAPI handlers run
    on the main event loop. `publish_threadsafe()` lets the runner push events
    without owning a loop reference at each call site.
    """

    def __init__(self) -> None:
        self._channels: dict[str, list[asyncio.Queue[JobEvent]]] = {}
        self._lock = asyncio.Lock()

    async def subscribe(self, job_id: str) -> asyncio.Queue[JobEvent]:
        async with self._lock:
            queue: asyncio.Queue[JobEvent] = asyncio.Queue(maxsize=1024)
            self._channels.setdefault(job_id, []).append(queue)
            return queue

    async def unsubscribe(self, job_id: str, queue: asyncio.Queue[JobEvent]) -> None:
        async with self._lock:
            subs = self._channels.get(job_id)
            if subs and queue in subs:
                subs.remove(queue)
            if subs is not None and not subs:
                self._channels.pop(job_id, None)

    async def publish(self, job_id: str, event: JobEvent) -> None:
        async with self._lock:
            subs = list(self._channels.get(job_id, ()))
        for q in subs:
            # Drop the oldest events if a slow subscriber falls behind — better
            # than blocking the runner thread on backpressure.
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(event)

    def publish_threadsafe(
        self,
        job_id: str,
        event: JobEvent,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Publish from a non-asyncio thread (the runner). Fire-and-forget."""
        asyncio.run_coroutine_threadsafe(self.publish(job_id, event), loop)


_broker: JobEventBroker | None = None


def get_broker() -> JobEventBroker:
    global _broker
    if _broker is None:
        _broker = JobEventBroker()
    return _broker

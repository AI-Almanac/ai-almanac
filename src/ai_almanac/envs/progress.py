"""Progress events emitted while preparing the managed runtime environments."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

EventKind = Literal["phase_started", "line", "phase_finished", "phase_skipped", "phase_failed"]


@dataclass(frozen=True, slots=True)
class EnvProgressEvent:
    kind: EventKind
    phase: str
    line: str | None = None
    detail: str | None = None


ProgressCallback = Callable[[EnvProgressEvent], None]

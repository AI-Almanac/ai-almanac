"""Provider-neutral job execution contract.

Defines how the application submits, inspects, and cancels a job's execution
without knowing how or where it runs. The first implementation is the local
detached-process supervisor (`LocalProcessRunner`); remote backends (Modal,
Slurm, batch services) can implement the same Protocol later. Handles are
opaque to the application: persisted and passed back, never interpreted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from ai_almanac.server.services.dataset_resolver import ResolvedDataset

JSONValue = str | int | float | bool | None | list[Any] | dict[str, Any]


@dataclass(frozen=True)
class ResourceRequest:
    gpus: int = 1


@dataclass(frozen=True)
class RunnerCapabilities:
    cancel: bool = True
    streaming_logs: bool = True


@dataclass(frozen=True)
class ExecutionRequest:
    job_id: str
    workspace: Path
    bundle_path: Path
    inputs: tuple[ResolvedDataset, ...] = ()
    resources: ResourceRequest = ResourceRequest()


@dataclass(frozen=True)
class RunnerHandle:
    """Opaque reference to a submitted execution. Persisted on the job row."""

    runner: str
    external_id: str
    metadata: Mapping[str, JSONValue] = field(default_factory=dict)

    def as_dict(self) -> dict[str, JSONValue]:
        return {
            "runner": self.runner,
            "external_id": self.external_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunnerHandle:
        return cls(
            runner=data["runner"],
            external_id=data["external_id"],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ExecutionSnapshot:
    status: str  # queued|starting|running|canceling|complete|failed|canceled|unknown
    exit_code: int | None = None


class JobRunner(Protocol):
    name: str
    capabilities: RunnerCapabilities

    async def submit(self, request: ExecutionRequest) -> RunnerHandle: ...

    async def inspect(self, handle: RunnerHandle) -> ExecutionSnapshot: ...

    async def cancel(self, handle: RunnerHandle) -> None: ...

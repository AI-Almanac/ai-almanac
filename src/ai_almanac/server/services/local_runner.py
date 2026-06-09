"""Local detached-process job runner.

Formalizes the existing supervisor (`job_manager`) behind the JobRunner
contract. Submission launches a detached supervisor process; status is
reconciled from the durable job row (DB is the source of truth — there is no
separate workspace state file); cancellation flags the job for the supervisor
to terminate. The runner never writes business rows, authorizes users,
interprets results, or decides retention.
"""

from __future__ import annotations

from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services.execution import (
    ExecutionRequest,
    ExecutionSnapshot,
    RunnerCapabilities,
    RunnerHandle,
)
from ai_almanac.server.services.job_manager import launch_job, signal_cancel


class LocalProcessRunner:
    name = "local"
    capabilities = RunnerCapabilities(cancel=True, streaming_logs=True)

    async def submit(self, request: ExecutionRequest) -> RunnerHandle:
        await launch_job(request.job_id)
        return RunnerHandle(runner=self.name, external_id=request.job_id, metadata={})

    async def inspect(self, handle: RunnerHandle) -> ExecutionSnapshot:
        async with get_db() as conn:
            row = (
                (
                    await conn.execute(
                        text("SELECT status, exit_code FROM jobs WHERE id = :id"),
                        {"id": handle.external_id},
                    )
                )
                .mappings()
                .fetchone()
            )
        if not row:
            return ExecutionSnapshot(status="unknown")
        return ExecutionSnapshot(status=row["status"], exit_code=row["exit_code"])

    async def cancel(self, handle: RunnerHandle) -> None:
        await signal_cancel(handle.external_id)


def get_job_runner() -> LocalProcessRunner:
    return LocalProcessRunner()

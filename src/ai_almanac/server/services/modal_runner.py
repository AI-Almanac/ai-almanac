"""Modal job runner — submits the benchmark workflow to a deployed Modal app.

Implements the `JobRunner` Protocol against Modal's spawn/poll API. `submit`
spawns the `run_benchmark` function and records the Modal call id in the runner
handle; `inspect` and `cancel` rehydrate the call from that id, so a stateless
Cloud Run instance can reconcile a job it did not itself submit. Modal jobs have
no local supervisor, so status is written by the reconciler polling `inspect`.

The Modal app (the `run_benchmark` function) is deployed separately; this is the
client side. Job config is read from the durable `jobs` row, mirroring how the
local workload resolves it.
"""

from __future__ import annotations

import json

import sqlalchemy as sa

from ai_almanac.server.db import get_db
from ai_almanac.server.services.execution import (
    ExecutionRequest,
    ExecutionSnapshot,
    RunnerCapabilities,
    RunnerHandle,
)
from ai_almanac.server.tables import jobs
from ai_almanac.settings import settings

# Providers whose obs data is read remotely by the compute, so the obs path is
# not required to be a gs:// URI (mirrors the production runner).
_REMOTE_OBS_PROVIDERS = frozenset({"earth2studio", "arco"})


class ModalPreflightError(Exception):
    """The job config cannot run on Modal (e.g. local input paths)."""


def _preflight_error(config: dict, outputs_bucket: str) -> str | None:
    """Return why this config can't run on Modal, or None if it can.

    Modal compute reads inputs from and writes outputs to GCS, so the obs/model
    paths must be gs:// URIs and an outputs bucket must be configured.
    """
    if not outputs_bucket:
        return "job_runner=modal requires gcs_outputs_bucket to be set."

    dataset_config = config.get("dataset_config") or {}
    if dataset_config.get("provider") not in _REMOTE_OBS_PROVIDERS:
        obs_dir = config.get("obs_dir", "")
        if not str(obs_dir).startswith("gs://"):
            return f"job_runner=modal requires obs_dir to be a gs:// URI; got {obs_dir!r}."

    model_dir = config.get("model_dir", "")
    if not str(model_dir).startswith("gs://"):
        return f"job_runner=modal requires model_dir to be a gs:// URI; got {model_dir!r}."

    return None


async def _job_config(job_id: str) -> dict:
    async with get_db() as conn:
        row = (
            await conn.execute(sa.select(jobs.c.config_json).where(jobs.c.id == job_id))
        ).fetchone()
    if not row:
        raise ModalPreflightError(f"job not found: {job_id}")
    return json.loads(row[0] or "{}")


class ModalRunner:
    name = "modal"
    capabilities = RunnerCapabilities(cancel=True, streaming_logs=False)

    def __init__(self, app_name: str, function_name: str, outputs_bucket: str) -> None:
        self._app_name = app_name
        self._function_name = function_name
        self._outputs_bucket = outputs_bucket

    async def submit(self, request: ExecutionRequest) -> RunnerHandle:
        config = await _job_config(request.job_id)
        error = _preflight_error(config, self._outputs_bucket)
        if error:
            raise ModalPreflightError(error)

        import modal

        function = modal.Function.from_name(self._app_name, self._function_name)
        call = function.spawn(request.job_id, config, self._outputs_bucket)
        return RunnerHandle(runner=self.name, external_id=call.object_id, metadata={})

    async def inspect(self, handle: RunnerHandle) -> ExecutionSnapshot:
        import modal

        call = modal.FunctionCall.from_id(handle.external_id)
        try:
            call.get(timeout=0)
        except TimeoutError:
            return ExecutionSnapshot(status="running")
        except Exception:  # noqa: BLE001 — any other error means the call failed
            return ExecutionSnapshot(status="failed", exit_code=1)
        return ExecutionSnapshot(status="complete", exit_code=0)

    async def cancel(self, handle: RunnerHandle) -> None:
        import modal

        modal.FunctionCall.from_id(handle.external_id).cancel()


def get_modal_runner() -> ModalRunner:
    return ModalRunner(
        app_name=settings.modal_app_name,
        function_name=settings.modal_function_name,
        outputs_bucket=settings.gcs_outputs_bucket,
    )

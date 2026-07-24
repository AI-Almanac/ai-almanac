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

import asyncio
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
_REMOTE_OBS_PROVIDERS = frozenset({"earth2studio", "era5_arco"})


class ModalPreflightError(Exception):
    """The job config cannot run on Modal (e.g. local input paths)."""


def _is_stageable(uri: object) -> bool:
    """Whether a Modal run can fetch this input URI.

    gs:// is the cloud-storage backend; an absolute path covers a mounted Modal
    volume (the local-mode backend). A relative or empty ref is unstageable.
    """
    text = str(uri)
    return text.startswith("gs://") or text.startswith("/")


def _preflight_error(config: dict, outputs_bucket: str) -> str | None:
    """Return why this config can't run on Modal, or None if it can.

    Modal compute reads inputs from and writes outputs to GCS, so the obs/model
    paths must be gs:// URIs and an outputs bucket must be configured.
    """
    if not outputs_bucket:
        return "job_runner=modal requires gcs_outputs_bucket to be set."

    # Forecast jobs run live inference against GFS directly — there's no
    # obs_dir/model_dir/model_files on the forecast job's own config to check.
    # The live-scoring step does stage historical data, but from the parent
    # blend's frozen config snapshot, so validate that instead.
    staging_config = config
    if config.get("job_type") == "forecast":
        staging_config = config.get("blend_config_snapshot") or {}

    dataset_config = staging_config.get("dataset_config") or {}
    if dataset_config.get("provider") not in _REMOTE_OBS_PROVIDERS:
        obs_dir = staging_config.get("obs_dir", "")
        if not str(obs_dir).startswith("gs://"):
            return f"job_runner=modal requires obs_dir to be a gs:// URI; got {obs_dir!r}."

    # Blend jobs (and forecast jobs' blend snapshot) carry per-model {year}.nc
    # staging URIs the server pre-resolved on the active backend; benchmark
    # jobs stage a single model dir.
    if "model_files" in staging_config:
        model_files = staging_config.get("model_files") or {}
        if not model_files:
            return "job_runner=modal blend requires at least one model."
        for name, uris in model_files.items():
            if not uris:
                return f"job_runner=modal blend has no files to stage for model {name!r}."
            for uri in uris:
                if not _is_stageable(uri):
                    return (
                        "job_runner=modal requires blend inputs to be gs:// URIs "
                        f"or absolute mount paths; got {uri!r}."
                    )
        return None

    model_dir = staging_config.get("model_dir", "")
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

        # The Modal SDK calls below block on the network, so keep them off the
        # event loop.
        call_id = await asyncio.to_thread(self._spawn, request.job_id, config)
        return RunnerHandle(runner=self.name, external_id=call_id, metadata={})

    async def inspect(self, handle: RunnerHandle) -> ExecutionSnapshot:
        return await asyncio.to_thread(self._inspect, handle.external_id)

    async def cancel(self, handle: RunnerHandle) -> None:
        await asyncio.to_thread(self._cancel, handle.external_id)

    def _spawn(self, job_id: str, config: dict) -> str:
        import modal

        # Job config selects the Modal app + function (e.g. blend jobs run
        # "run_blend" in a separate app); all share the (job_id, config,
        # outputs_bucket) signature.
        app_name = config.get("modal_app") or self._app_name
        function_name = config.get("modal_function") or self._function_name
        function = modal.Function.from_name(app_name, function_name)
        call = function.spawn(job_id, config, self._outputs_bucket)
        return call.object_id

    def _inspect(self, call_id: str) -> ExecutionSnapshot:
        import modal

        call = modal.FunctionCall.from_id(call_id)
        try:
            call.get(timeout=0)
        except TimeoutError:
            return ExecutionSnapshot(status="running")
        except Exception:  # noqa: BLE001 — any other error means the call failed
            return ExecutionSnapshot(status="failed", exit_code=1)
        return ExecutionSnapshot(status="complete", exit_code=0)

    def _cancel(self, call_id: str) -> None:
        import modal

        modal.FunctionCall.from_id(call_id).cancel()


def get_modal_runner() -> ModalRunner:
    return ModalRunner(
        app_name=settings.modal_app_name,
        function_name=settings.modal_function_name,
        outputs_bucket=settings.gcs_outputs_bucket,
    )

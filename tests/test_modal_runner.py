"""ModalRunner — preflight validation, spawn/inspect/cancel, and selection.

A fake `modal` module stands in for the SDK so submit/inspect/cancel are tested
without Modal or the network; `_job_config` is stubbed so no DB row is needed.
Errors raised by the fake are the real SDK's, so classification is tested
against the types Modal actually raises.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import modal.exception as modal_exc
import pytest

from ai_almanac.server.services import modal_runner as mr
from ai_almanac.server.services.execution import ExecutionRequest, RunnerHandle

_GCS_CONFIG = {
    "obs_dir": "gs://data/obs",
    "model_dir": "gs://data/models/fuxi",
    "dataset_config": {"provider": "local"},
}

_BLEND_CONFIG = {
    "obs_dir": "gs://data/obs",
    "model_files": {
        "gencast": [
            "gs://data/models/gencast/2019.nc",
            "gs://data/models/gencast/2020.nc",
        ],
        "aifs": ["gs://data/models/aifs/2019.nc", "gs://data/models/aifs/2020.nc"],
    },
    "modal_app": "almanac-blending",
    "modal_function": "run_blend",
    "dataset_config": {"provider": "local"},
}


_FORECAST_CONFIG = {
    "job_type": "forecast",
    "modal_app": "almanac-forecasts",
    "modal_function": "run_forecast",
    "blend_config_snapshot": _BLEND_CONFIG,
    "forecast_model_ids": ["aifs"],
}


def _request(job_id: str = "job1") -> ExecutionRequest:
    return ExecutionRequest(job_id=job_id, workspace=Path("."), bundle_path=Path("."))


def _install_fake_modal(monkeypatch, *, get_behavior="complete", record=None):
    modal = types.ModuleType("modal")

    class FakeFunction:
        @staticmethod
        def from_name(app_name, function_name):
            if record is not None:
                record["lookup"] = (app_name, function_name)
            return FakeFunction()

        def spawn(self, *args):
            if record is not None:
                record["spawn_args"] = args
            return types.SimpleNamespace(object_id="fc-123")

    class FakeFunctionCall:
        @staticmethod
        def from_id(call_id):
            inst = FakeFunctionCall()
            inst.call_id = call_id
            return inst

        def get(self, timeout=0):
            if isinstance(get_behavior, BaseException):
                raise get_behavior
            if get_behavior == "running":
                raise TimeoutError
            if get_behavior == "failed":
                raise RuntimeError("modal boom")
            return None

        def cancel(self):
            if record is not None:
                record["canceled"] = self.call_id

    modal.Function = FakeFunction
    modal.FunctionCall = FakeFunctionCall
    modal.exception = modal_exc
    monkeypatch.setitem(sys.modules, "modal", modal)
    monkeypatch.setitem(sys.modules, "modal.exception", modal_exc)


def _runner() -> mr.ModalRunner:
    return mr.ModalRunner("almanac-romp", "run_benchmark", "out-bucket")


# --- preflight ---------------------------------------------------------------


def test_preflight_requires_outputs_bucket() -> None:
    assert "gcs_outputs_bucket" in mr._preflight_error(_GCS_CONFIG, "")


@pytest.mark.parametrize(
    "config",
    [
        pytest.param(_GCS_CONFIG, id="all-gcs-paths"),
        pytest.param(
            {
                "obs_dir": "",
                "model_dir": "gs://data/models/fuxi",
                "dataset_config": {"provider": "earth2studio"},
            },
            id="remote-obs-provider-needs-no-obs-dir",
        ),
        pytest.param(_BLEND_CONFIG, id="blend-model-files"),
        # An absolute path is a mounted Modal volume, which is stageable.
        pytest.param(
            {**_BLEND_CONFIG, "model_files": {"aifs": ["/vol/forecasts/aifs/2019.nc"]}},
            id="blend-volume-mount",
        ),
        # A forecast's own config has no obs_dir/model_files — only its
        # blend_config_snapshot does (live inference reads GFS directly).
        pytest.param(_FORECAST_CONFIG, id="forecast-checks-its-blend-snapshot"),
    ],
)
def test_preflight_accepts_configs_modal_can_stage(config: dict) -> None:
    assert mr._preflight_error(config, "out-bucket") is None


@pytest.mark.parametrize(
    ("config", "names"),
    [
        pytest.param({**_GCS_CONFIG, "obs_dir": "/mnt/obs"}, "obs_dir", id="local-obs-dir"),
        pytest.param(
            {**_GCS_CONFIG, "model_dir": "/mnt/models"}, "model_dir", id="local-model-dir"
        ),
        pytest.param(
            {**_BLEND_CONFIG, "model_files": {"aifs": ["models/aifs/2019.nc"]}},
            "models/aifs/2019.nc",
            id="relative-blend-uri",
        ),
        pytest.param(
            {**_BLEND_CONFIG, "model_files": {"aifs": []}}, "aifs", id="blend-model-without-files"
        ),
        pytest.param(
            {**_BLEND_CONFIG, "model_files": {}}, "at least one model", id="no-blend-models"
        ),
        pytest.param(
            {**_FORECAST_CONFIG, "blend_config_snapshot": {**_BLEND_CONFIG, "obs_dir": "/local"}},
            "obs_dir",
            id="forecast-snapshot-with-local-obs-dir",
        ),
    ],
)
def test_preflight_names_what_modal_cannot_stage(config: dict, names: str) -> None:
    error = mr._preflight_error(config, "out-bucket")
    assert error and names in error


# --- submit / inspect / cancel ----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("config", "lookup"),
    [
        pytest.param(_GCS_CONFIG, ("almanac-romp", "run_benchmark"), id="benchmark"),
        # A blend config selects its own app + function over the runner's default.
        pytest.param(_BLEND_CONFIG, ("almanac-blending", "run_blend"), id="blend"),
    ],
)
async def test_submit_spawns_the_configured_function_and_records_its_call_id(
    monkeypatch: pytest.MonkeyPatch, config: dict, lookup: tuple[str, str]
) -> None:
    record: dict = {}
    _install_fake_modal(monkeypatch, record=record)

    async def fake_config(job_id):
        return config

    monkeypatch.setattr(mr, "_job_config", fake_config)

    handle = await _runner().submit(_request("job-xyz"))

    assert handle.runner == "modal"
    assert handle.external_id == "fc-123"
    assert record["lookup"] == lookup
    assert record["spawn_args"] == ("job-xyz", config, "out-bucket")


@pytest.mark.asyncio
async def test_submit_rejects_bad_config_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_modal(monkeypatch)

    async def fake_config(job_id):
        return {**_GCS_CONFIG, "model_dir": "/local/path"}

    monkeypatch.setattr(mr, "_job_config", fake_config)

    with pytest.raises(mr.ModalPreflightError):
        await _runner().submit(_request())


@pytest.mark.asyncio
async def test_inspect_maps_modal_states(monkeypatch: pytest.MonkeyPatch) -> None:
    handle = RunnerHandle(runner="modal", external_id="fc-123")

    _install_fake_modal(monkeypatch, get_behavior="running")
    assert (await _runner().inspect(handle)).status == "running"

    _install_fake_modal(monkeypatch, get_behavior="complete")
    assert (await _runner().inspect(handle)).status == "complete"

    _install_fake_modal(monkeypatch, get_behavior="failed")
    assert (await _runner().inspect(handle)).status == "failed"


@pytest.mark.parametrize(
    "error",
    [
        modal_exc.ConnectionError("Could not connect to the Modal server."),
        modal_exc.ServiceError("UNAVAILABLE"),
        modal_exc.InternalError("INTERNAL"),
        modal_exc.ResourceExhaustedError("rate limited"),
    ],
    ids=lambda error: type(error).__name__,
)
@pytest.mark.asyncio
async def test_inspect_leaves_the_job_active_when_modal_is_unreachable(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _install_fake_modal(monkeypatch, get_behavior=error)

    snapshot = await _runner().inspect(RunnerHandle(runner="modal", external_id="fc-123"))

    assert snapshot.status == "unknown"
    assert type(error).__name__ in snapshot.detail


@pytest.mark.parametrize(
    "error",
    [
        ValueError("raised by the function"),
        modal_exc.FunctionTimeoutError("exceeded its timeout"),
        modal_exc.RemoteError("terminated"),
        modal_exc.InternalFailure("input lost"),
        modal_exc.OutputExpiredError(),
        # Unknown or expired call ids: retrying would never succeed.
        modal_exc.NotFoundError("call not found"),
        # A function's own network error is re-raised as-is; it is the call's
        # outcome, not a transient poll failure.
        OSError("GCS connection reset"),
    ],
    ids=lambda error: type(error).__name__,
)
@pytest.mark.asyncio
async def test_inspect_fails_the_job_when_the_call_itself_failed(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    _install_fake_modal(monkeypatch, get_behavior=error)

    snapshot = await _runner().inspect(RunnerHandle(runner="modal", external_id="fc-123"))

    assert snapshot.status == "failed"


@pytest.mark.asyncio
async def test_cancel_rehydrates_call_from_id(monkeypatch: pytest.MonkeyPatch) -> None:
    record: dict = {}
    _install_fake_modal(monkeypatch, record=record)
    await _runner().cancel(RunnerHandle(runner="modal", external_id="fc-999"))
    assert record["canceled"] == "fc-999"

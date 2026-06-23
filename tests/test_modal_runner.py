"""ModalRunner — preflight validation, spawn/inspect/cancel, and selection.

A fake `modal` module stands in for the SDK so submit/inspect/cancel are tested
without Modal or the network; `_job_config` is stubbed so no DB row is needed.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

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
    "model_dirs": ["gs://data/models/gencast", "gs://data/models/aifs"],
    "modal_app": "almanac-blending",
    "modal_function": "run_blend",
    "dataset_config": {"provider": "local"},
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
    monkeypatch.setitem(sys.modules, "modal", modal)


def _runner() -> mr.ModalRunner:
    return mr.ModalRunner("almanac-romp", "run_benchmark", "out-bucket")


# --- preflight ---------------------------------------------------------------


def test_preflight_requires_outputs_bucket() -> None:
    assert "gcs_outputs_bucket" in mr._preflight_error(_GCS_CONFIG, "")


def test_preflight_rejects_local_obs_dir() -> None:
    config = {**_GCS_CONFIG, "obs_dir": "/mnt/obs"}
    error = mr._preflight_error(config, "out-bucket")
    assert error and "obs_dir" in error


def test_preflight_rejects_local_model_dir() -> None:
    config = {**_GCS_CONFIG, "model_dir": "/mnt/models"}
    error = mr._preflight_error(config, "out-bucket")
    assert error and "model_dir" in error


def test_preflight_exempts_remote_obs_provider() -> None:
    config = {
        "obs_dir": "",
        "model_dir": "gs://data/models/fuxi",
        "dataset_config": {"provider": "earth2studio"},
    }
    assert mr._preflight_error(config, "out-bucket") is None


def test_preflight_passes_for_all_gcs_paths() -> None:
    assert mr._preflight_error(_GCS_CONFIG, "out-bucket") is None


def test_preflight_passes_for_blend_model_dirs() -> None:
    assert mr._preflight_error(_BLEND_CONFIG, "out-bucket") is None


def test_preflight_rejects_local_blend_model_dir() -> None:
    config = {**_BLEND_CONFIG, "model_dirs": ["gs://data/ok", "/mnt/local"]}
    error = mr._preflight_error(config, "out-bucket")
    assert error and "/mnt/local" in error


def test_preflight_rejects_empty_blend_model_dirs() -> None:
    config = {**_BLEND_CONFIG, "model_dirs": []}
    error = mr._preflight_error(config, "out-bucket")
    assert error and "model dir" in error


# --- submit / inspect / cancel ----------------------------------------------


@pytest.mark.asyncio
async def test_submit_spawns_and_records_call_id(monkeypatch: pytest.MonkeyPatch) -> None:
    record: dict = {}
    _install_fake_modal(monkeypatch, record=record)

    async def fake_config(job_id):
        return _GCS_CONFIG

    monkeypatch.setattr(mr, "_job_config", fake_config)

    handle = await _runner().submit(_request("job-xyz"))

    assert handle.runner == "modal"
    assert handle.external_id == "fc-123"
    assert record["lookup"] == ("almanac-romp", "run_benchmark")
    assert record["spawn_args"] == ("job-xyz", _GCS_CONFIG, "out-bucket")


@pytest.mark.asyncio
async def test_submit_routes_blend_app_and_function(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    record: dict = {}
    _install_fake_modal(monkeypatch, record=record)

    async def fake_config(job_id):
        return _BLEND_CONFIG

    monkeypatch.setattr(mr, "_job_config", fake_config)

    handle = await _runner().submit(_request("blend-1"))

    assert handle.external_id == "fc-123"
    # Config selects a different app + function than the runner's benchmark default.
    assert record["lookup"] == ("almanac-blending", "run_blend")
    assert record["spawn_args"] == ("blend-1", _BLEND_CONFIG, "out-bucket")


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


@pytest.mark.asyncio
async def test_cancel_rehydrates_call_from_id(monkeypatch: pytest.MonkeyPatch) -> None:
    record: dict = {}
    _install_fake_modal(monkeypatch, record=record)
    await _runner().cancel(RunnerHandle(runner="modal", external_id="fc-999"))
    assert record["canceled"] == "fc-999"


# --- selection ---------------------------------------------------------------


def test_registry_selects_runner_by_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from ai_almanac.server.services import runner_registry
    from ai_almanac.server.services.local_runner import LocalProcessRunner

    monkeypatch.setattr("ai_almanac.settings.settings.job_runner", "modal")
    assert runner_registry.get_job_runner().name == "modal"

    monkeypatch.setattr("ai_almanac.settings.settings.job_runner", "local")
    assert isinstance(runner_registry.get_job_runner(), LocalProcessRunner)

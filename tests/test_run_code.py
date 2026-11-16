"""Running the assistant's code in a workspace: it is offered only where a sandbox can run
it, outputs are stripped of foreign history and recorded as unverified, and a Modal sandbox
seals the image it ran in."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from ai_almanac.paths import data_root
from ai_almanac.server.services import (
    code_sandbox,
    ledger,
    modal_code_sandbox,
    workspaces,
)
from ai_almanac.server.services.primitives import code_harness
from ai_almanac.settings import settings
from tests.helpers import ledger_reads
from tests.helpers.files import write_nc
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import (
    register_file,
    workspace_with_session,
)


def _run_python_unsandboxed(script, *, env, readable, writable, timeout, allowed_domains=()):
    """Isolation is covered by test_code_sandbox; this keeps provenance tests host-independent."""
    import os
    import subprocess
    import sys

    proc = subprocess.run(
        [sys.executable, "-I", str(script)],
        env={"PATH": os.environ.get("PATH", ""), "MPLBACKEND": "Agg", **env},
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return code_sandbox.SandboxedRun(proc.returncode, proc.stdout, proc.stderr)


@pytest.mark.asyncio
async def test_run_code_outputs_are_stripped_and_labelled_derived(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "code_sandbox", "srt")
    monkeypatch.setattr(code_sandbox, "run_python", _run_python_unsandboxed)
    monkeypatch.setattr(code_sandbox, "analysis_env_digest", lambda: "sha256:analysis-lock")
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    await register_file(user_id, wid, write_nc(tmp_path / "obs.nc"), "obs")
    code = """
import os, xarray as xr
def compute(inputs):
    ds = xr.open_dataset(inputs["obs"], engine="h5netcdf")
    out = (ds * 2).assign_attrs(ai_almanac_history="[]")
    out.to_netcdf(os.path.join(ARTIFACT_DIR, "mean.nc"), engine="h5netcdf")
    try:
        open(inputs["obs"], "ab").close()
        writable = True
    except PermissionError:
        writable = False
    return {"mean": float(ds["far"].mean()), "input_writable": writable}
"""
    result = await code_harness.run_code(
        tool_ctx(user_id, session["id"], wid), code=code, inputs=["obs"]
    )
    assert result.get("ok"), result
    assert result["result"] == {"mean": 1.0, "input_writable": False}
    labels = {a["label"]: a for a in result["workspace_artifacts"]}
    assert "derived_mean" in labels and any(label.startswith("code_") for label in labels)

    derived = await workspaces.resolve(wid, "derived_mean")
    assert ledger_reads.read_history(ledger.local_path(derived)) is None, "script history stripped"
    described = await workspaces.describe(wid, derived)
    assert "unverified computation" in described["lineage"]
    [step] = [s for s in described["steps"] if s["primitive"] == "code.run_code"]
    assert step["runtime"]["sandbox"] == "srt"
    assert step["runtime"]["env_digest"] == "sha256:analysis-lock"
    assert [i["role"] for i in step["inputs"]] == ["obs"]
    assert "inputs" not in step["params"]
    [code_file] = [
        a for a in described["artifacts"] if a["id"] in step["outputs"] and a["format"] == "python"
    ]
    assert code_file["label"].startswith("code_"), "the history points at the code that was run"


def _modal_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "code_sandbox", "modal")
    monkeypatch.setattr(code_sandbox, "sandbox_unavailable_reason", lambda: "no srt on Cloud Run")


@pytest.mark.asyncio
async def test_on_modal_run_code_runs_there_and_seals_the_sandbox_image(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _modal_deployment(monkeypatch)

    async def fake_run_harness(script, staged, out_dir, *, timeout):
        """Stands in for the Sandbox: runs the harness here under the same env contract."""
        harness = tmp_path / "harness.py"
        harness.write_text(script)
        (tmp_path / "scratch").mkdir()
        env = {
            "ARTIFACT_DIR": str(out_dir),
            "SCRATCH_DIR": str(tmp_path / "scratch"),
            "WORKSPACE_INPUTS": json.dumps(staged),
        }
        run = _run_python_unsandboxed(harness, env=env, readable=[], writable=[], timeout=timeout)
        return modal_code_sandbox.ModalRun(run, "im-sandbox-1")

    monkeypatch.setattr(modal_code_sandbox, "run_harness", fake_run_harness)
    assert code_harness.code_execution_available()
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    await register_file(user_id, wid, write_nc(tmp_path / "obs.nc"), "obs")
    code = """
import os, xarray as xr
def compute(inputs):
    (xr.open_dataset(inputs["obs"], engine="h5netcdf") * 2).to_netcdf(
        os.path.join(ARTIFACT_DIR, "double.nc"), engine="h5netcdf"
    )
    return {"done": True}
"""
    result = await code_harness.run_code(
        tool_ctx(user_id, session["id"], wid), code=code, inputs=["obs"]
    )
    assert result.get("ok"), result

    derived = await workspaces.resolve(wid, "derived_double")
    described = await workspaces.describe(wid, derived, verify=True)
    [step] = [s for s in described["steps"] if s["primitive"] == "code.run_code"]
    assert step["runtime"]["env_digest"] == "im-sandbox-1"
    assert step["runtime"]["sandbox"] == "modal"
    assert step["verification"] == "verified"


@pytest.mark.asyncio
async def test_a_modal_outage_reaches_the_model_as_a_plain_error(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    _modal_deployment(monkeypatch)

    async def unreachable(*args, **kwargs):
        raise ConnectionError("modal.com unreachable")

    monkeypatch.setattr(modal_code_sandbox, "run_harness", unreachable)
    workspace, session = await workspace_with_session(client, as_new_user)
    result = await code_harness.run_code(
        tool_ctx(user_id, session["id"], workspace["id"]), code="def compute(i): return {}"
    )
    assert result == {"ok": False, "error": "Code execution is unavailable right now"}
    assert await workspaces.list_artifacts(workspace["id"]) == []


@pytest.mark.parametrize(
    ("sandbox", "srt_reason", "available"),
    [
        ("off", None, False),
        ("srt", None, True),
        ("srt", "srt is not installed", False),
        ("modal", "srt is not installed", True),
    ],
)
def test_run_code_is_offered_only_where_the_configured_sandbox_can_run(
    monkeypatch: pytest.MonkeyPatch, sandbox: str, srt_reason: str | None, available: bool
) -> None:
    monkeypatch.setattr(settings, "enable_run_code", True)
    monkeypatch.setattr(settings, "code_sandbox", sandbox)
    monkeypatch.setattr(code_sandbox, "sandbox_unavailable_reason", lambda: srt_reason)
    assert code_harness.code_execution_available() is available


def test_shared_mode_keeps_workspace_code_but_not_page_chat_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_almanac.server.services import benchmark_domain

    monkeypatch.setattr(settings, "job_runner", "modal")
    monkeypatch.setattr(settings, "code_sandbox", "modal")
    monkeypatch.setattr(settings, "enable_run_code", False)  # what shared mode forces
    assert code_harness.code_execution_available() is True
    assert benchmark_domain.is_tool_available("run_code") is False
    monkeypatch.setattr(settings, "code_sandbox", "off")
    assert code_harness.code_execution_available() is False


@pytest.mark.asyncio
@pytest.mark.skipif(
    code_sandbox.sandbox_unavailable_reason() is not None,
    reason=str(code_sandbox.sandbox_unavailable_reason()),
)
async def test_run_code_harness_reads_netcdf_and_writes_figures_in_the_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "code_sandbox", "srt")
    source = data_root() / "workspaces" / "obs.nc"
    source.parent.mkdir(parents=True, exist_ok=True)
    xr.Dataset({"far": (("lat", "lon"), np.ones((2, 2)))}).to_netcdf(source, engine="h5netcdf")
    out_dir = data_root() / "workspaces" / "run"
    out_dir.mkdir()
    code = """
import os, xarray as xr, matplotlib.pyplot as plt
def compute(inputs):
    ds = xr.open_dataset(inputs["obs"], engine="h5netcdf")
    (ds * 2).to_netcdf(os.path.join(ARTIFACT_DIR, "double.nc"), engine="h5netcdf")
    fig, ax = plt.subplots()
    ds["far"].plot(ax=ax)
    save_figure(fig, "far.png")
    return {"mean": float(ds["far"].mean())}
"""
    ran = await code_harness.run_in_harness(code, {"obs": source}, out_dir)

    assert ran.outcome == {"ok": True, "result": {"mean": 1.0}}
    assert {p.name for p in ran.outputs} == {"double.nc", "far.png"}
    assert {p.name for p in out_dir.iterdir()} == {"double.nc", "far.png"}


@pytest.mark.asyncio
async def test_the_assistant_is_told_run_code_cannot_run_without_a_sandbox(
    client, as_new_user, user_id, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ai_almanac.server.services.primitives import inspection

    monkeypatch.setattr(settings, "code_sandbox", "off")
    workspace, session = await workspace_with_session(client, as_new_user)

    listed = await inspection.list_capabilities(tool_ctx(user_id, session["id"], workspace["id"]))

    available = {c["name"]: c["available"] for c in listed["capabilities"]}
    assert available["code.run_code"] is False

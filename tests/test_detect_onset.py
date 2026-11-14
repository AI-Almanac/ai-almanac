"""Onset read from a raw season forecast by an onset definition, in the code sandbox.

ROMP lives only in the sandbox's analysis environment, so these tests run the shipped
script with a stand-in for ROMP's rule contract: a cell's onset is the first lead day
over the wet-day threshold. The script's agreement with ROMP's own forecast scoring is
checked against the real analysis environment, outside this suite.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
import xarray as xr

from ai_almanac.server.services import boundaries, code_sandbox, ledger, workspaces
from ai_almanac.server.services.primitives import analysis
from ai_almanac.settings import settings
from tests.helpers.tool_calls import tool_ctx
from tests.helpers.workspaces import workspace_with_session

STAND_IN_ROMP = """
import numpy as np

class _FirstWetDay:
    lookahead = 0
    def label(self):
        return "first wet day"
    def __call__(self, rain, thresh, *, dim):
        return rain > thresh

def resolve_rule(onset_rule=None, onset_rule_params=None, **cfg):
    return _FirstWetDay()

def first_onset_index(mask, dim):
    cond = mask.fillna(False).astype(bool)
    return cond.argmax(dim).where(cond.any(dim), -1).astype(int)
"""


@pytest.fixture
def sandbox_with_stand_in_romp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    rules = tmp_path / "romp" / "momp" / "stats"
    rules.mkdir(parents=True)
    for package in (rules.parent, rules):
        (package / "__init__.py").write_text("")
    (rules / "onset_rule.py").write_text(STAND_IN_ROMP)
    romp_root = str(tmp_path / "romp")

    def run(script, *, env, readable, writable, timeout, allowed_domains=()):
        launcher = (
            f"import runpy, sys; sys.path.insert(0, {romp_root!r}); runpy.run_path({str(script)!r})"
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-c", launcher],
            env={"PATH": os.environ.get("PATH", ""), **env},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return code_sandbox.SandboxedRun(proc.returncode, proc.stdout, proc.stderr)

    monkeypatch.setattr(settings, "code_sandbox", "srt")
    monkeypatch.setattr(code_sandbox, "run_python", run)
    monkeypatch.setattr(code_sandbox, "analysis_env_digest", lambda: "sha256:analysis-lock")


def _season(
    path: Path,
    first_wet_day: dict[tuple[int, int], int],
    units: str | None = None,
    issue_dim: str = "time",
    source: str | None = None,
) -> Path:
    """Two issue dates over a 2x2 grid, dry except from each cell's first wet lead day."""
    days = np.arange(0, 34)
    tp = np.zeros((2, days.size, 2, 2))
    for (i, j), day in first_wet_day.items():
        tp[:, day:, i, j] = 30.0
    ds = xr.Dataset(
        {"tp": ((issue_dim, "day", "lat", "lon"), tp, {"units": units} if units else {})},
        coords={
            issue_dim: np.array(["2026-06-01", "2026-06-04"], dtype="datetime64[ns]"),
            "day": days,
            "lat": [9.0, 9.25],
            "lon": [38.0, 38.25],
        },
    )
    if source:
        ds.attrs["source"] = source
    ds.to_netcdf(path, engine="h5netcdf")
    return path


async def _add_season(user_id: str, wid: str, path: Path) -> None:
    _, [artifact] = await ledger.write_step(
        ledger.StepDraft(primitive="fetch.test", version="1.0.0", params={}),
        ledger.Attribution.by_user(user_id),
        [ledger.OutputFile(path=path, label=path.stem, kind="dataset")],
    )
    await workspaces.add_artifacts(wid, [(artifact, path.stem)])


@pytest.fixture
def one_zone_over_the_grid(monkeypatch: pytest.MonkeyPatch) -> None:
    ring = [[37.8, 8.8], [38.4, 8.8], [38.4, 9.4], [37.8, 9.4], [37.8, 8.8]]
    zone = {"type": "Feature", "properties": {"shapeName": "North Shewa"}}
    zone["geometry"] = {"type": "Polygon", "coordinates": [ring]}

    async def fake_load(iso: str, level: str) -> dict:
        return {"metadata": {}, "geojson": {"type": "FeatureCollection", "features": [zone]}}

    monkeypatch.setattr(boundaries, "load_boundary", fake_load)


@pytest.mark.asyncio
async def test_onset_days_and_a_window_table_come_back_from_the_sandbox(
    client, as_new_user, user_id, tmp_path: Path, sandbox_with_stand_in_romp, one_zone_over_the_grid
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    # One cell turns wet on lead day 5, one on day 20, one never within 28 days.
    season = _season(
        tmp_path / "fuxi_season_forecast.nc", {(0, 0): 5, (0, 1): 20, (1, 0): 31, (1, 1): 5}
    )
    await _add_season(user_id, wid, season)
    ctx = tool_ctx(user_id, session["id"], wid)

    found = await analysis.detect_onset(ctx, forecast="fuxi_season_forecast")

    assert found.get("error") is None, found
    assert found["single_run"] is True
    assert found["onset"]["latest_share_with_onset"] == 0.75
    labels = {a["label"]: a for a in found["workspace_artifacts"]}
    assert set(labels) == {"fuxi_onset_days", "fuxi_onset_windows"}
    days = xr.open_dataset(ledger.local_path(await workspaces.resolve(wid, "fuxi_onset_days")))
    assert np.array_equal(
        days["onset_day"].isel(time=0).values, [[5, 20], [np.nan, 5]], equal_nan=True
    )

    [step] = [
        s
        for s in (await workspaces.describe(wid, await workspaces.resolve(wid, "fuxi_onset_days")))[
            "steps"
        ]
        if s["primitive"] == "forecast.detect_onset"
    ]
    assert step["params"]["onset_rule_params"]["wet_spell"] == 3
    assert step["params"]["wet_threshold"] == 20.0
    assert step["runtime"]["env_digest"] == "sha256:analysis-lock"

    by_area = await analysis.summarize_by_area(
        ctx, forecast="fuxi_onset_windows", region_id="ethiopia", within_days=[14]
    )
    assert by_area.get("error") is None, by_area
    assert "not a probability" in by_area["values"]
    [zone] = by_area["latest"]
    assert zone["p_within_14d"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_an_opened_archive_is_read_by_its_issue_dates_and_named_by_its_model(
    client, as_new_user, user_id, tmp_path: Path, sandbox_with_stand_in_romp
) -> None:
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    archive = _season(tmp_path / "subset.nc", {(0, 0): 3}, issue_dim="init_time", source="../FuXi")
    await _add_season(user_id, wid, archive)

    found = await analysis.detect_onset(tool_ctx(user_id, session["id"], wid), forecast="subset")

    assert found.get("error") is None, found
    assert {a["label"] for a in found["workspace_artifacts"]} == {
        "FuXi_onset_days",
        "FuXi_onset_windows",
    }


@pytest.mark.asyncio
async def test_a_definition_the_forecast_is_too_short_for_is_refused_before_the_sandbox(
    client, as_new_user, user_id, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def never(*args, **kwargs):
        raise AssertionError("the sandbox should not run")

    monkeypatch.setattr(code_sandbox, "run_python", never)
    workspace, session = await workspace_with_session(client, as_new_user)
    wid = workspace["id"]
    await _add_season(user_id, wid, _season(tmp_path / "fuxi_season_forecast.nc", {}))
    await _add_season(user_id, wid, _season(tmp_path / "native_season_forecast.nc", {}, units="m"))
    ctx = tool_ctx(user_id, session["id"], wid)

    too_long = await analysis.detect_onset(
        ctx,
        forecast="fuxi_season_forecast",
        onset_rule="two_stage",
        onset_rule_params={"stage1_days": 5, "stage2_days": 10},
    )
    unknown = await analysis.detect_onset(
        ctx, forecast="fuxi_season_forecast", onset_rule_params={"wet_days": 3}
    )
    metres = await analysis.detect_onset(ctx, forecast="native_season_forecast")

    assert "fuxi forecasts run 33 days" in too_long["error"]
    assert "unknown parameter" in unknown["error"]
    assert "metres" in metres["error"]

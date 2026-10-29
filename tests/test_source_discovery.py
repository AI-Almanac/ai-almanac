"""Finding data sources under a directory tree (services.source_discovery)."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import numpy as np
import pandas as pd
import pytest
import xarray as xr
from typer.testing import CliRunner

from ai_almanac.server.services.source_discovery import infer_file_pattern

_TESTDATA = Path(__file__).parents[1] / "testdata" / "ethiopia"
runner = CliRunner()


def _copy_years(source: Path, target: Path, name: str = "{}.nc") -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for file in sorted(source.glob("*.nc")):
        shutil.copy(file, target / name.format(file.stem))
    return target


def _write_obs(path: Path, *, lat: tuple[float, float], lon: tuple[float, float]) -> None:
    """A one-year daily observation file on a small grid at the given extent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    times = pd.date_range("2001-01-01", periods=4, freq="D")
    lats = np.linspace(*lat, 3)
    lons = np.linspace(*lon, 3)
    xr.Dataset(
        {
            "precip": (("time", "lat", "lon"), np.zeros((4, 3, 3), dtype="float32")),
            "station_count": ((), 3),
        },
        coords={"time": times, "lat": lats, "lon": lons},
    ).to_netcdf(path)


def _by_path(discovery: dict) -> dict[str, dict]:
    return {Path(source["path"]).name: source for source in discovery["sources"]}


@pytest.mark.parametrize(
    ("names", "pattern"),
    [
        (["1998.nc", "1999.nc"], "{}.nc"),
        (["data_1901.nc", "data_1902.nc", "mask.nc"], "data_{}.nc"),
        (["aifs_2p0_2001_v2025.nc", "aifs_2p0_2002_v2025.nc"], "aifs_2p0_{}_v2025.nc"),
        (["a_2001.nc", "a_2002.nc", "b_2001.nc"], "a_{}.nc"),
        (["mask.nc", "grid.nc"], None),
    ],
    ids=["year-only", "prefixed", "year-before-a-version", "majority-wins", "no-year"],
)
def test_the_file_pattern_is_the_year_in_the_filenames(names, pattern) -> None:
    assert infer_file_pattern(names) == pattern


@pytest.mark.parametrize(
    ("folder", "grid_step", "suggested"),
    [
        ("AIFS_v2", 0.25, "aifs2"),
        ("AIFS_ENS_v2", 0.25, "aifs2ens"),
        ("GraphCast_Operational", 0.25, "graphcast_operational"),
        # GraphCast Small runs at 1°, so a 0.25° `graphcast` archive isn't it.
        ("graphcast", 0.25, None),
        ("graphcast", 1.0, "graphcast"),
        ("AIFS_v2", 1.0, None),
        ("aurora1p5", 0.25, "aurora1p5"),
        # The original Aurora is another model: no live link to Aurora 1.5.
        ("aurora", 0.25, None),
        ("AIFS_v2", None, None),
    ],
)
def test_a_live_model_is_suggested_only_for_an_exact_name_and_grid_match(
    folder, grid_step, suggested
) -> None:
    from ai_almanac.server.services.source_discovery import _suggest_forecast_model

    assert _suggest_forecast_model(folder, grid_step) == suggested


@pytest.mark.asyncio
async def test_a_scan_proposes_each_netcdf_directory_ready_to_register(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    _copy_years(_TESTDATA / "obs", tmp_path / "ethiopia" / "obs", "data_{}.nc")
    _copy_years(_TESTDATA / "fuxi", tmp_path / "ethiopia" / "models" / "fuxi")

    response = await client.post("/data-sources/discover", json={"root": str(tmp_path)})

    assert response.status_code == 200, response.text
    found = _by_path(response.json())
    assert set(found) == {"obs", "fuxi"}
    obs, model = found["obs"], found["fuxi"]
    assert (obs["kind"], obs["name"], obs["region"], obs["status"]) == (
        "obs",
        "Ethiopia obs",
        "ethiopia",
        "ready",
    )
    assert obs["metadata"]["obs_file_pattern"] == "data_{}.nc"
    assert obs["metadata"]["obs_var"] == "RAINFALL"
    assert (obs["metadata"]["start_year"], obs["metadata"]["end_year"]) == (1998, 2000)
    assert (model["kind"], model["name"], model["region"], model["status"]) == (
        "model",
        "Ethiopia fuxi",
        "ethiopia",
        "ready",
    )
    assert model["metadata"]["model_var"] == "tp"
    assert model["metadata"]["file_pattern"] == "{}.nc"
    # The folder names a live model that runs on this archive's grid.
    assert model["metadata"]["forecast_model_id"] == "fuxi"


@pytest.mark.asyncio
async def test_a_proposed_source_registers_as_is_and_is_then_marked_registered(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    _copy_years(_TESTDATA / "obs", tmp_path / "ethiopia" / "obs", "data_{}.nc")
    proposed = (await client.post("/data-sources/discover", json={"root": str(tmp_path)})).json()
    source = proposed["sources"][0]
    assert source["registered_as"] is None

    created = await client.post(
        "/data-sources",
        json={key: source[key] for key in ("kind", "name", "path", "region", "metadata")},
    )

    assert created.status_code == 201, created.text
    assert created.json()["status"] == "ready"
    rescanned = (await client.post("/data-sources/discover", json={"root": str(tmp_path)})).json()
    assert rescanned["sources"][0]["registered_as"] == "Ethiopia obs"


@pytest.mark.asyncio
async def test_without_a_region_in_the_path_bounds_suggest_one_only_when_they_match(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    # Same box as the packaged Ethiopia region.
    _write_obs(tmp_path / "east-africa" / "2001.nc", lat=(3.0, 15.0), lon=(33.0, 48.0))
    # A small box inside India's: overlapping is not enough to take its name.
    _write_obs(tmp_path / "kerala" / "2001.nc", lat=(8.0, 12.0), lon=(75.0, 77.0))
    _write_obs(tmp_path / "alaska" / "2001.nc", lat=(60.0, 70.0), lon=(-160.0, -140.0))

    found = _by_path(
        (await client.post("/data-sources/discover", json={"root": str(tmp_path)})).json()
    )

    assert found["east-africa"]["region"] == "ethiopia"
    assert found["kerala"]["region"] is None
    assert found["alaska"]["region"] is None


@pytest.mark.asyncio
async def test_the_gridded_variable_is_chosen_whatever_it_is_called(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    _write_obs(tmp_path / "ethiopia" / "gauges" / "2001.nc", lat=(3.0, 15.0), lon=(33.0, 48.0))

    found = _by_path(
        (await client.post("/data-sources/discover", json={"root": str(tmp_path)})).json()
    )

    # `station_count` is a scalar, so `precip` is the only variable on the grid.
    assert found["gauges"]["kind"] == "obs"
    assert found["gauges"]["metadata"]["obs_var"] == "precip"


@pytest.mark.asyncio
async def test_unusable_directories_are_reported_and_hidden_or_linked_ones_not_walked(
    client: httpx.AsyncClient, tmp_path: Path
) -> None:
    masks = tmp_path / "masks"
    masks.mkdir()
    shutil.copy(next((_TESTDATA / "obs").glob("*.nc")), masks / "land.nc")
    _copy_years(_TESTDATA / "obs", tmp_path / ".cache" / "obs")
    (tmp_path / "linked").symlink_to(_TESTDATA / "obs", target_is_directory=True)

    discovery = (await client.post("/data-sources/discover", json={"root": str(tmp_path)})).json()

    assert discovery["sources"] == []
    assert [(Path(item["path"]).name, item["reason"]) for item in discovery["skipped"]] == [
        ("masks", "no year (19xx or 20xx) in any filename")
    ]


@pytest.mark.asyncio
async def test_a_scan_defaults_to_and_stays_within_the_mount_roots(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ai_almanac.settings import settings

    mounted = tmp_path / "mounted"
    _copy_years(_TESTDATA / "obs", mounted / "ethiopia" / "obs")
    outside = _copy_years(_TESTDATA / "obs", tmp_path / "outside" / "obs")
    monkeypatch.setattr(settings, "dataset_mount_roots", str(mounted))

    default = await client.post("/data-sources/discover", json={})
    escaped = await client.post("/data-sources/discover", json={"root": str(outside)})

    assert default.status_code == 200
    assert default.json()["roots"] == [str(mounted.resolve())]
    assert [source["name"] for source in default.json()["sources"]] == ["Ethiopia obs"]
    assert escaped.status_code == 400
    assert "mount roots" in escaped.json()["detail"]


@pytest.mark.asyncio
async def test_a_scan_needs_a_root_and_an_admin(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from ai_almanac.settings import settings

    no_root = await client.post("/data-sources/discover", json={})
    monkeypatch.setattr(settings, "enable_fs_browser", False)
    browser_off = await client.post("/data-sources/discover", json={"root": str(tmp_path)})
    monkeypatch.setattr(settings, "enable_fs_browser", True)
    monkeypatch.setattr(settings, "auth_mode", "proxy")
    monkeypatch.setattr(settings, "admin_subjects", "root")
    non_admin = await client.post(
        "/data-sources/discover",
        json={"root": str(tmp_path)},
        headers={"X-Forwarded-User": "alice"},
    )

    assert no_root.status_code == 400
    assert browser_off.status_code == 403
    assert non_admin.status_code == 403


def test_the_cli_registers_ready_sources_once(tmp_path: Path) -> None:
    from ai_almanac.cli import app

    _copy_years(_TESTDATA / "obs", tmp_path / "ethiopia" / "obs", "data_{}.nc")
    _copy_years(_TESTDATA / "fuxi", tmp_path / "ethiopia" / "models" / "fuxi")

    preview = runner.invoke(app, ["sources", "discover", str(tmp_path)])
    first = runner.invoke(app, ["sources", "discover", str(tmp_path), "--register"])
    second = runner.invoke(app, ["sources", "discover", str(tmp_path), "--register"])

    assert preview.exit_code == 0, preview.output
    assert "Ethiopia obs" in preview.output and "Ethiopia fuxi" in preview.output
    assert "re-run with --register" in preview.output
    assert first.exit_code == 0, first.output
    assert "registered 2 source(s)" in first.output
    assert second.exit_code == 0, second.output
    assert "already registered as 'Ethiopia obs'" in second.output
    assert "registered 0 source(s)" in second.output


def test_the_cli_registers_sources_under_the_names_file(tmp_path: Path) -> None:
    import asyncio

    from ai_almanac.cli import app
    from ai_almanac.server.services import data_sources

    data = tmp_path / "datasets"
    _copy_years(_TESTDATA / "obs", data / "ethiopia" / "obs", "data_{}.nc")
    _copy_years(_TESTDATA / "fuxi", data / "ethiopia" / "models" / "fuxi")
    names = tmp_path / "names.yaml"
    names.write_text(
        "sources:\n"
        "  ethiopia/obs:\n"
        "    name: Chirps (IMERG)\n"
        "    obs_date_marks: start\n"
        "  ethiopia/models/fuxi:\n"
        "    name: FuXi\n"
        "    forecast_model_id: fuxi_live\n"
        "  india/obs:\n"
        "    name: IMD\n"
    )

    result = runner.invoke(
        app, ["sources", "discover", str(data), "--register", "--names", str(names)]
    )

    assert result.exit_code == 0, result.output
    assert "named but not found: india/obs" in result.output
    rows = {row["name"]: row for row in asyncio.run(data_sources.list_sources())}
    assert set(rows) == {"Chirps (IMERG)", "FuXi"}
    assert rows["FuXi"]["metadata"]["forecast_model_id"] == "fuxi_live"
    assert rows["Chirps (IMERG)"]["region"] == "ethiopia"
    assert rows["Chirps (IMERG)"]["metadata"]["obs_date_marks"] == "start"


@pytest.mark.parametrize(
    "body",
    [
        "sources:\n  ethiopia/obs:\n    region: india\n",
        "sources:\n  ethiopia/obs:\n    name: ''\n",
        "sources:\n  ethiopia/obs: IMD\n",
    ],
)
def test_a_names_file_entry_needs_a_name_and_nothing_else(tmp_path: Path, body: str) -> None:
    from ai_almanac.cli import app

    names = tmp_path / "names.yaml"
    names.write_text(body)

    result = runner.invoke(app, ["sources", "discover", str(tmp_path), "--names", str(names)])

    assert result.exit_code == 1
    assert "needs a name" in result.output


def test_the_spark_names_file_names_every_dataset_once() -> None:
    import yaml

    from ai_almanac.cli import _load_source_names

    path = Path(__file__).parents[1] / "deploy" / "spark-datasets.yaml"
    names = _load_source_names(path)
    assert names
    assert yaml.safe_load(path.read_text())["sources"].keys() == names.keys()

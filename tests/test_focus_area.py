from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
import xarray as xr
from pydantic import ValidationError

from ai_almanac.server.services import boundaries
from ai_almanac.server.services.focus_area import (
    FocusBox,
    FocusUnits,
    cells_inside,
    materialize_focus_mask,
    parse_focus_area,
    season_mask,
    write_focus_mask,
)

BOX = {"lat_min": 8.13, "lat_max": 12.4, "lon_min": 38.0, "lon_max": 40.7}


def _square(name: str, lat0: float, lat1: float, lon0: float, lon1: float) -> dict:
    ring = [[lon0, lat0], [lon1, lat0], [lon1, lat1], [lon0, lat1], [lon0, lat0]]
    return {
        "type": "Feature",
        "properties": {"shapeName": name},
        "geometry": {"type": "Polygon", "coordinates": [ring]},
    }


def _write_obs(path: Path) -> None:
    lat = np.arange(3.0, 15.01, 0.25)
    lon = np.arange(33.0, 48.01, 0.25)
    xr.DataArray(
        np.zeros((lat.size, lon.size), dtype="f4"),
        dims=("LATITUDE", "LONGITUDE"),
        coords={"LATITUDE": lat, "LONGITUDE": lon},
        name="RAINFALL",
    ).to_netcdf(path)


def _romp_apply(mask_path: str, ds: xr.Dataset) -> xr.DataArray:
    """Mirror ROMP's apply_nc_mask: nearest-select, then align exactly."""
    mask = xr.open_dataset(mask_path)["mask"].sel(lat=ds.lat, lon=ds.lon, method="nearest")
    mask_bool, _ = xr.align(mask == 1, ds, join="exact")
    return mask_bool


def test_parse_tells_a_box_from_picked_units() -> None:
    assert isinstance(parse_focus_area(BOX), FocusBox)
    units = parse_focus_area({"level": "adm2", "units": [" South Wollo ", "South Wollo", ""]})
    assert isinstance(units, FocusUnits)
    assert units.units == ["South Wollo"]


def test_focus_area_rejects_reversed_bounds_and_empty_picks() -> None:
    with pytest.raises(ValidationError):
        FocusBox(lat_min=12, lat_max=8, lon_min=38, lon_max=40)
    with pytest.raises(ValidationError):
        FocusUnits(level="adm2", units=["  "])


def test_box_mask_sits_on_the_obs_grid_and_keeps_only_the_box(tmp_path: Path) -> None:
    obs_dir = tmp_path / "obs"
    obs_dir.mkdir()
    _write_obs(obs_dir / "1998.nc")
    out = materialize_focus_mask(
        {"obs_dir": str(obs_dir), "romp_params": {"focus_area": BOX}}, tmp_path
    )
    with xr.open_dataset(obs_dir / "1998.nc") as obs:
        region = obs.rename(LATITUDE="lat", LONGITUDE="lon").sel(
            lat=slice(5, 14), lon=slice(35, 45)
        )
        kept = _romp_apply(out["romp_params"]["nc_mask"], region)
    assert bool(kept.sel(lat=10.0, lon=39.0))
    assert not bool(kept.sel(lat=8.0, lon=39.0))
    assert not bool(kept.sel(lat=10.0, lon=41.0))


def test_unit_outlines_rasterise_by_cell_centre_with_holes_cut_out() -> None:
    lat = np.arange(0.0, 5.0, 1.0)
    lon = np.arange(0.0, 5.0, 1.0)
    outer = [[0.5, 0.5], [3.5, 0.5], [3.5, 3.5], [0.5, 3.5], [0.5, 0.5]]
    hole = [[1.5, 1.5], [2.5, 1.5], [2.5, 2.5], [1.5, 2.5], [1.5, 1.5]]
    geometry = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Polygon", "coordinates": [outer, hole]},
            }
        ],
    }
    inside = cells_inside(geometry, lat, lon)
    assert inside[1, 1] and inside[3, 3]
    assert not inside[2, 2], "the hole is cut out"
    assert not inside[0, 0] and not inside[4, 4]


def test_units_mask_needs_attached_outlines(tmp_path: Path) -> None:
    grid = np.arange(0.0, 3.0, 1.0)
    with pytest.raises(ValueError, match="not attached"):
        write_focus_mask(FocusUnits(level="adm2", units=["X"]), tmp_path / "m.nc", grid, grid)


def test_select_units_matches_names_loosely_and_rejects_unknown_ones() -> None:
    geojson = {
        "type": "FeatureCollection",
        "features": [_square("South Wollo", 10, 12, 38, 40), _square("Awi", 10, 11, 36, 37)],
    }
    picked = boundaries.select_units(geojson, ["south  wollo"])
    assert [f["properties"]["name"] for f in picked["features"]] == ["South Wollo"]
    with pytest.raises(ValueError, match="Nowhere"):
        boundaries.select_units(geojson, ["Awi", "Nowhere"])


def test_materialize_is_a_no_op_without_a_focus_area(tmp_path: Path) -> None:
    assert materialize_focus_mask({"romp_params": {}}, tmp_path) == {"romp_params": {}}


def _season_kept(lat: float, lon: float) -> bool:
    """The packaged Ethiopia season mask's own value for a cell."""
    with xr.open_dataset(season_mask("ethiopia")) as ds:
        return bool(ds["precip"].sel(latitude=lat, longitude=lon))


def _ethiopia_job(tmp_path: Path, **romp_params) -> dict:
    obs_dir = tmp_path / "obs"
    obs_dir.mkdir(exist_ok=True)
    _write_obs(obs_dir / "1998.nc")
    return {"obs_dir": str(obs_dir), "region_id": "ethiopia", "romp_params": romp_params}


def test_ethiopia_benchmarks_score_only_its_june_september_rain_areas(tmp_path: Path) -> None:
    out = materialize_focus_mask(_ethiopia_job(tmp_path), tmp_path)

    with xr.open_dataset(out["romp_params"]["nc_mask"]) as mask:
        kept = mask["mask"] == 1
        # The northern highlands keep their cells; the Somali lowlands do not.
        assert _season_kept(13.0, 38.0) and bool(kept.sel(lat=13.0, lon=38.0))
        assert not _season_kept(5.0, 44.0) and not bool(kept.sel(lat=5.0, lon=44.0))
        with xr.open_dataset(season_mask("ethiopia")) as season:
            assert int(kept.sum()) == int(season["precip"].sum())


def test_the_season_mask_narrows_an_area_of_interest(tmp_path: Path) -> None:
    out = materialize_focus_mask(_ethiopia_job(tmp_path, focus_area=BOX), tmp_path)

    with xr.open_dataset(out["romp_params"]["nc_mask"]) as mask:
        kept = mask["mask"] == 1
        assert not bool(kept.sel(lat=13.0, lon=38.0)), "outside the box"
        inside_box = [(lat, 39.0) for lat in np.arange(8.25, 12.4, 0.25)]
        assert [bool(kept.sel(lat=a, lon=o)) for a, o in inside_box] == [
            _season_kept(a, o) for a, o in inside_box
        ]


def test_the_season_mask_can_be_turned_off_and_a_mask_file_replaces_it(tmp_path: Path) -> None:
    off = _ethiopia_job(tmp_path, season_mask=False)
    custom = _ethiopia_job(tmp_path, nc_mask="/data/masks/mine.nc")

    assert materialize_focus_mask(off, tmp_path) == off
    assert materialize_focus_mask(custom, tmp_path) == custom
    assert season_mask("india") is None


@pytest.mark.asyncio
async def test_an_area_of_interest_and_a_custom_mask_cannot_both_be_set(
    client, as_new_user: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    root = Path(__file__).parents[1] / "testdata" / "ethiopia"
    obs = await client.post(
        "/data-sources",
        json={
            "kind": "obs",
            "name": "Ethiopia observations",
            "path": str(root / "obs"),
            "region": "ethiopia",
            "metadata": {"obs_file_pattern": "{}.nc", "obs_var": "RAINFALL"},
        },
    )
    model = await client.post(
        "/data-sources",
        json={
            "kind": "model",
            "name": "Ethiopia forecast",
            "path": str(root / "fuxi"),
            "region": "ethiopia",
            "metadata": {"file_pattern": "{}.nc", "model_var": "tp", "model_type": "AIWP"},
        },
    )

    async def fake_launch(job_id: str) -> None:
        return None

    monkeypatch.setattr("ai_almanac.server.services.local_runner.launch_job", fake_launch)
    response = await client.post(
        "/jobs",
        headers=as_new_user,
        json={
            "dataset_id": obs.json()["id"],
            "model_name": model.json()["id"],
            "params": {"region": "ethiopia", "focus_area": BOX, "nc_mask": str(root / "obs")},
        },
    )

    assert response.status_code == 400
    assert "mask" in response.json()["detail"]


@pytest.mark.parametrize(
    ("level", "pickable"),
    [("adm1", True), ("adm2", True), ("adm3", False)],
)
def test_states_and_districts_can_be_picked_but_subdistricts_cannot(
    level: str, pickable: bool
) -> None:
    area = {"level": level, "units": ["Kerala"]}
    if not pickable:
        with pytest.raises(ValidationError):
            parse_focus_area(area)
        return
    picked = parse_focus_area(area)
    assert isinstance(picked, FocusUnits)
    assert picked.level == level

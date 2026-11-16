import json
from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from ai_almanac_tools import harness
from ai_almanac_tools.sources import FetchRequest, era5_arco, plan_fetch
from pydantic import ValidationError


def _request(**overrides) -> FetchRequest:
    fields = {
        "source": "era5_arco",
        "variables": ["precip"],
        "lat_bounds": (12, 15),
        "lon_bounds": (-17, -14),
        "start": date(2024, 7, 1),
        "end": date(2024, 7, 10),
    }
    return FetchRequest(**(fields | overrides))


def test_plan_estimates_float32_bytes_from_the_catalog_grid():
    plan = plan_fetch(_request())

    assert plan.grid_points == 13 * 13
    assert plan.estimated_bytes == 10 * 13 * 13 * 4


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"end": date(2024, 6, 1)}, "before start"),
        ({"lat_bounds": (80, 95)}, "must lie within"),
        ({"variables": []}, "at least 1 item"),
    ],
)
def test_malformed_requests_do_not_parse(overrides, message):
    with pytest.raises(ValidationError, match=message):
        _request(**overrides)


def test_plan_names_the_variables_a_source_offers():
    with pytest.raises(ValueError, match="no variable t2m; it offers precip"):
        plan_fetch(_request(variables=["t2m"]))


def _fake_store(monkeypatch, start: str, hours: int) -> None:
    lat = np.arange(20.0, 9.75, -0.25)  # descending, like ARCO
    lon = np.arange(340.0, 350.25, 0.25)
    hourly = xr.DataArray(
        np.full((hours, lat.size, lon.size), 0.001, dtype="float32"),
        coords={"time": pd.date_range(start, periods=hours, freq="h"), "lat": lat, "lon": lon},
        dims=("time", "lat", "lon"),
    )
    monkeypatch.setattr(era5_arco, "open_hourly", lambda: hourly)


def _run_harness(tmp_path, request: FetchRequest, capsys) -> tuple[int, dict]:
    request_file = tmp_path / "request.json"
    request_file.write_text(request.model_dump_json())
    (tmp_path / "out").mkdir()
    (tmp_path / "scratch").mkdir()
    code = harness.main(
        ["fetch", str(request_file), str(tmp_path / "out"), str(tmp_path / "scratch")]
    )
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_the_harness_writes_one_dataset_in_the_callers_longitudes(tmp_path, monkeypatch, capsys):
    _fake_store(monkeypatch, "2024-06-30", 24 * 3)
    request = _request(start=date(2024, 6, 30), end=date(2024, 7, 1))

    code, result = _run_harness(tmp_path, request, capsys)

    assert code == 0, result
    assert result["source_version"] == "record through 2024-07-02"
    ds = xr.open_dataset(tmp_path / "out" / result["file"])
    assert ds["precip"].attrs["units"] == "mm day-1"
    assert ds.sizes["time"] == 2
    assert float(ds.lon.min()) == -17.0 and float(ds.lon.max()) == -14.0
    np.testing.assert_allclose(ds["precip"].values, 24.0)
    assert list((tmp_path / "scratch").iterdir()) == []


def test_the_harness_reports_a_request_past_the_record_as_an_error(tmp_path, monkeypatch, capsys):
    _fake_store(monkeypatch, "2024-06-30", 24)

    code, result = _run_harness(tmp_path, _request(start=date(2024, 6, 30)), capsys)

    assert code == 1
    assert result == {
        "ok": False,
        "error": "ValueError: ERA5 ARCO covers 2024-06-30 to 2024-06-30; "
        "requested 2024-06-30 to 2024-07-10",
    }

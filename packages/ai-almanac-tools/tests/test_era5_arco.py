from datetime import date

import numpy as np
import pandas as pd
import pytest
import xarray as xr
from ai_almanac_tools.bbox import BBox
from ai_almanac_tools.contract import rename_to_canonical
from ai_almanac_tools.sources import era5_arco


def _hourly_one_mm_per_hour(start: str, hours: int) -> xr.DataArray:
    # ARCO stores latitude descending; the loader must still select and sort it.
    return xr.DataArray(
        np.full((hours, 3, 3), 0.001, dtype="float32"),
        coords={
            "time": pd.date_range(start, periods=hours, freq="h"),
            "lat": [10.0, 5.0, 0.0],
            "lon": [0.0, 5.0, 10.0],
        },
        dims=("time", "lat", "lon"),
    )


def test_daily_totals_split_by_month_within_the_bounds(tmp_path):
    files = era5_arco.write_daily_months(
        _hourly_one_mm_per_hour("2024-01-31", 48),
        date(2024, 1, 31),
        date(2024, 2, 1),
        tmp_path,
        bbox=BBox.parse(lat_bounds=[0, 5], lon_bounds=[10, 5]),
        name="RAINFALL",
    )

    assert list(files) == [2024]
    january, february = (xr.open_dataset(path)["RAINFALL"].load() for path in files[2024])
    assert [str(t)[:10] for t in january.time.values] == ["2024-01-31"]
    assert [str(t)[:10] for t in february.time.values] == ["2024-02-01"]
    assert january.lat.values.tolist() == [0.0, 5.0]
    assert january.lon.values.tolist() == [5.0, 10.0]
    np.testing.assert_allclose(january.values, 24.0)


def test_month_spans_cover_the_range_inclusively():
    assert era5_arco.month_spans(date(2023, 12, 30), date(2024, 2, 2)) == [
        (date(2023, 12, 30), date(2023, 12, 31)),
        (date(2024, 1, 1), date(2024, 1, 31)),
        (date(2024, 2, 1), date(2024, 2, 2)),
    ]


def test_synonym_dimensions_are_renamed_to_the_contract():
    ds = xr.Dataset(coords={"latitude": [0.0], "LONGITUDE": [1.0], "number": [0], "time": [0]})

    assert set(rename_to_canonical(ds).dims) == {"lat", "lon", "member", "time"}


def test_days_past_the_store_are_refused_before_anything_is_fetched(tmp_path):
    ends_midday_jan_2 = _hourly_one_mm_per_hour("2024-01-01", 36)

    with pytest.raises(ValueError, match="covers 2024-01-01 to 2024-01-01"):
        era5_arco.write_daily_months(
            ends_midday_jan_2, date(2024, 1, 1), date(2024, 1, 2), tmp_path
        )
    assert list(tmp_path.iterdir()) == []

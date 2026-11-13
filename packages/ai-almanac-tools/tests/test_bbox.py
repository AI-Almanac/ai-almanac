import numpy as np
import pytest
import xarray as xr
from ai_almanac_tools.bbox import BBox


def _global_0_360() -> xr.DataArray:
    # Each value is its own 0..360 longitude, so remapped selections can be checked.
    lon = np.arange(0.0, 360.0, 5.0)
    return xr.DataArray(
        np.tile(lon, (3, 1)), coords={"lat": [10.0, 5.0, 0.0], "lon": lon}, dims=("lat", "lon")
    )


def test_negative_longitudes_select_across_the_prime_meridian_of_0_360_data():
    selected = BBox.parse(lon_bounds=[5, -10]).select(_global_0_360())

    assert selected.lon.values.tolist() == [-10.0, -5.0, 0.0, 5.0]
    assert selected.isel(lat=0).values.tolist() == [350.0, 355.0, 0.0, 5.0]


def test_0_360_longitudes_come_back_unchanged():
    selected = BBox.parse(lat_bounds=[0, 5], lon_bounds=[340, 350]).select(_global_0_360())

    assert selected.lon.values.tolist() == [340.0, 345.0, 350.0]
    assert selected.lat.values.tolist() == [5.0, 0.0]


def test_0_360_boxes_select_from_negative_longitude_data():
    data = _global_0_360()
    data = data.assign_coords(lon=((data.lon + 180) % 360) - 180).sortby("lon")

    selected = BBox.parse(lon_bounds=[350, 355]).select(data)

    assert selected.lon.values.tolist() == [350.0, 355.0]


def test_a_box_outside_the_grid_is_an_error_not_an_empty_selection():
    with pytest.raises(ValueError, match="selects no grid points along lat"):
        BBox.parse(lat_bounds=[40, 50]).select(_global_0_360())


@pytest.mark.parametrize(
    ("lat", "lon"), [([80, 95], None), (None, [-190, 0]), (None, [0, 370]), (None, [-10, 300])]
)
def test_bounds_off_the_globe_are_rejected(lat, lon):
    with pytest.raises(ValueError, match="must lie within"):
        BBox.parse(lat_bounds=lat, lon_bounds=lon)

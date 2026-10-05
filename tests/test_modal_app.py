"""Modal app smoke test — guards the runner <-> app contract.

The Modal app (`modal/app.py`) is deployed separately and runs on Modal, so it
can't be exercised locally. This only verifies it loads and that the app/function
names the ModalRunner spawns actually exist, so a rename on either side fails
here instead of at deploy time.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ai_almanac.settings import settings

_APP_PATH = Path(__file__).parents[1] / "modal" / "app.py"


def _load_modal_app():
    spec = importlib.util.spec_from_file_location("almanac_modal_app", _APP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_modal_app_matches_runner_configuration() -> None:
    module = _load_modal_app()
    assert module.app.name == settings.modal_app_name
    # The function the ModalRunner spawns must be defined on the app.
    assert hasattr(module, settings.modal_function_name)


def test_area_of_interest_reaches_romp_as_a_mask_on_the_staged_obs_grid(
    tmp_path: Path, monkeypatch
) -> None:
    import sys

    import numpy as np
    import xarray as xr

    from ai_almanac.server.services import focus_area

    monkeypatch.setitem(sys.modules, "focus_area", focus_area)
    module = _load_modal_app()
    local_obs, local_model, local_out, local_fig = module._stage_paths(tmp_path)
    lat, lon = np.arange(3.0, 15.01, 0.25), np.arange(33.0, 48.01, 0.25)
    xr.DataArray(
        np.zeros((lat.size, lon.size), dtype="f4"),
        dims=("lat", "lon"),
        coords={"lat": lat, "lon": lon},
        name="RAINFALL",
    ).to_netcdf(local_obs / "obs_2020.nc")
    box = {"lat_min": 8.0, "lat_max": 12.0, "lon_min": 38.0, "lon_max": 40.0}
    config = {"model_name": "m", "romp_params": {"focus_area": box}}

    masked = module._with_focus_mask(config, local_obs, tmp_path)
    env = module._romp_env(masked, local_obs, local_model, local_out, local_fig)

    mask = xr.open_dataset(env["ROMP_NC_MASK"])["mask"]
    assert int(mask.sum()) == 17 * 9

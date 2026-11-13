"""A finished benchmark over two Amhara zones, with skill maps for AIFS and climatology."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import xarray as xr

from ai_almanac.server.services import boundaries, workspaces
from ai_almanac.server.services.storage import get_storage
from tests.helpers.places import ZONES
from tests.helpers.records import insert_job
from tests.helpers.workspaces import workspace_with_session

# Rows are lat 10.25, 10.75 and 12.25; columns lon 36.25, 37.25 and 37.75. East Gojjam
# holds the four cells 2, 3, 5 and 6; U-shaped North Gondar holds 7 and 9, the cell
# between them lying in its notch.
RUN_LAT = [10.25, 10.75, 12.25]
RUN_LON = [36.25, 37.25, 37.75]
RUN_ERROR = np.arange(1.0, 10.0).reshape(3, 3)
AMHARA_ZONES = ["East Gojjam", "North Gondar"]


def run_config(**romp_params: object) -> dict:
    return {
        "model_name": "aifs",
        "model_display_name": "AIFS",
        "region_id": "ethiopia",
        "region_name": "Ethiopia",
        "romp_params": {"start_date": "1998-05-02", "end_date": "2024-07-29", **romp_params},
    }


def zones_config() -> dict:
    outlines = boundaries.select_units(ZONES, AMHARA_ZONES)
    return run_config(focus_area={"level": "adm2", "units": AMHARA_ZONES, "geometry": outlines})


def write_skill(job_id: str, model: str, scale: float) -> None:
    output, _ = get_storage().job_output_uri(job_id)
    error = RUN_ERROR * scale
    xr.Dataset(
        {
            "mean_mae": (("lat", "lon"), error),
            "false_alarm_rate": (("lat", "lon"), error / 10),
            "miss_rate": (("lat", "lon"), error / 20),
            "mae_2024": (("lat", "lon"), np.full((3, 3), 99.0)),
        },
        coords={"lat": RUN_LAT, "lon": RUN_LON},
        attrs={"model": model, "verification_window": "1,15"},
    ).to_netcdf(Path(output) / f"spatial_metrics_{model}_1-15.nc", engine="h5netcdf")


async def workspace_with_zone_run(client, headers, user_id) -> tuple[str, str]:
    """A workspace holding the zone run: (workspace id, job id)."""
    workspace, _ = await workspace_with_session(client, headers)
    job_id = insert_job(user_id, zones_config())
    write_skill(job_id, "aifs", 1.0)
    write_skill(job_id, "climatology", 2.0)
    await workspaces.link_job(workspace["id"], job_id)
    return workspace["id"], job_id

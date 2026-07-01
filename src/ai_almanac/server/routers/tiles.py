"""Map tile serving for forecast COGs (Cloud-Optimized GeoTIFFs).

Wraps a TiTiler TilerFactory so the frontend can request slippy-map tiles
directly from a forecast job's rendered rasters, without the backend having
to pre-tile anything itself. Access is scoped per-job the same way every
other job result is: `job_id` + `path` (not a raw URL) go through the same
job_access ownership check `/jobs/{id}` endpoints use, then get resolved to
a real local path or gs:// URI via storage.result_file_uri.
"""

from __future__ import annotations

import warnings
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from rio_tiler.colormap import cmap as rio_cmap
from titiler.core.dependencies import create_colormap_dependency
from titiler.core.errors import DEFAULT_STATUS_CODES
from titiler.core.errors import add_exception_handlers as _add_exception_handlers
from titiler.core.factory import TilerFactory

from ai_almanac.server.auth import CurrentUser
from ai_almanac.server.services import job_access
from ai_almanac.server.services.storage import get_storage

# numpy.ma raises this when casting float fill values (e.g. 1e20) to uint8
# during rio_tiler's float32->uint8 colormap pipeline. The mask is correct;
# the warning is noise.
warnings.filterwarnings(
    "ignore",
    message="invalid value encountered in cast",
    category=RuntimeWarning,
    module=r"numpy\.ma",
)


def _build_almanac_colormap() -> dict[int, tuple[int, int, int, int]]:
    """Interpolate the 6-stop almanac palette to a 256-entry RGBA dict."""
    stops = [
        (40, 44, 98),
        (43, 127, 207),
        (87, 197, 173),
        (226, 222, 93),
        (232, 132, 54),
        (116, 35, 38),
    ]
    out: dict[int, tuple[int, int, int, int]] = {}
    for i in range(256):
        t = i / 255.0
        scaled = t * (len(stops) - 1)
        idx = min(len(stops) - 2, int(scaled))
        local = scaled - idx
        a, b = stops[idx], stops[idx + 1]
        out[i] = (
            int(round(a[0] + (b[0] - a[0]) * local)),
            int(round(a[1] + (b[1] - a[1]) * local)),
            int(round(a[2] + (b[2] - a[2]) * local)),
            220,
        )
    return out


rio_cmap.data["almanac"] = _build_almanac_colormap()


async def _validated_cog_path(
    job_id: Annotated[str, Query(description="Job that produced this COG")],
    path: Annotated[
        str, Query(description="Relative path under the job's output dir, e.g. aifs/rasters/t2m/24.tif")
    ],
    user: CurrentUser,
) -> str:
    job = await job_access.fetch_job(job_id)
    if not job or not job_access.can_read(job, user):
        raise HTTPException(status_code=404, detail="Job not found")
    try:
        return get_storage().result_file_uri(job_id, "output", path)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="COG access denied") from exc


cog_tiler = TilerFactory(
    path_dependency=_validated_cog_path,
    colormap_dependency=create_colormap_dependency(rio_cmap),
)
router = cog_tiler.router


def add_exception_handlers(app: FastAPI) -> None:
    """Map rio-tiler's exceptions (e.g. tile out of bounds) to HTTP status codes."""
    _add_exception_handlers(app, DEFAULT_STATUS_CODES)

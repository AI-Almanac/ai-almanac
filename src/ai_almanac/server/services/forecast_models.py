"""AI weather model registry for live, on-demand forecast generation.

Static packaged config (server/config/forecast_models.yaml), unlike
`services.registry`'s DB-backed benchmark/blend model sources — there is
nothing per-user or per-region to register here, just which earth2studio
models are available and how to run them. A model data source links to one
of these by id (`metadata.forecast_model_id`), chosen at registration.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from ai_almanac.server.services.forecast_pipeline import INIT_SOURCES
from ai_almanac.settings import forecast_model_by_id, get_packaged_forecast_models

_INTERNAL_FIELDS = ("earth2studio_class", "gpu", "env", "ensemble", "nensemble")

LiveForecastStatus = Literal["ready", "grid_mismatch", "unavailable"]


@dataclass(frozen=True)
class LiveForecastCompatibility:
    """Whether an archived model source can be extended into a live season."""

    status: LiveForecastStatus
    detail: str | None = None
    model_id: str | None = None


def archive_grid_step(source: dict) -> float | None:
    """The grid step recorded for a model source at registration, if any."""
    return (source.get("metadata") or {}).get("grid_step_deg")


def linked_forecast_model_id(source: dict) -> str | None:
    """The live forecast model chosen for a model archive at registration.

    Remote-provider sources skip inspection and keep metadata verbatim, so a
    non-string value is treated as no link rather than trusted.
    """
    model_id = (source.get("metadata") or {}).get("forecast_model_id")
    return model_id if isinstance(model_id, str) else None


def live_forecast_compatibility(
    forecast_model_id: str | None, archive_step: float | None, registry: dict | None = None
) -> LiveForecastCompatibility:
    """Check that an archive's linked live forecast model can extend it, grid included.

    A live season is scored with coefficients fit on the archive, so the live
    model must run on the archive's grid. A coarser or finer model would not
    fail downstream; it would quietly produce miscalibrated onset probabilities.
    Archives registered before the grid step was recorded pass this check until
    they are revalidated.
    """
    if forecast_model_id is None:
        return LiveForecastCompatibility(
            "unavailable", "This data isn't linked to a live forecast model."
        )
    registry = registry if registry is not None else get_packaged_forecast_models()
    entry = forecast_model_by_id(registry, forecast_model_id)
    if entry is None:
        return LiveForecastCompatibility(
            "unavailable", f"{forecast_model_id!r} is not an available live forecast model."
        )
    live_step = entry.get("resolution_deg")
    # A source whose inspection failed keeps its user-supplied metadata verbatim,
    # so the archive step is only trusted when it is actually a number.
    grids_known = isinstance(live_step, int | float) and isinstance(archive_step, int | float)
    if grids_known and not math.isclose(live_step, archive_step, abs_tol=1e-6):
        return LiveForecastCompatibility(
            "grid_mismatch",
            f"{entry['display_name']} runs live forecasts on a {live_step:g}° grid, "
            f"but this archive is on a {archive_step:g}° grid, so weights trained "
            "on it don't apply to live forecasts.",
            entry["id"],
        )
    return LiveForecastCompatibility("ready", None, entry["id"])


async def load_forecast_model_registry() -> list[dict]:
    """Public model list: registry entries with Modal-only fields stripped."""
    registry = get_packaged_forecast_models()
    return [
        {k: v for k, v in model.items() if k not in _INTERNAL_FIELDS}
        for model in registry.get("models") or []
    ]


def load_init_sources() -> list[dict]:
    """Selectable initialization data sources for the forecast run form,
    ordered alphabetically by display name so the list is easy to scan."""
    return sorted(
        (
            {"id": source_id, "display_name": entry["display_name"]}
            for source_id, entry in INIT_SOURCES.items()
        ),
        key=lambda source: source["display_name"].lower(),
    )

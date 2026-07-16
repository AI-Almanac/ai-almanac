"""AI weather model registry for live, on-demand forecast generation.

Static packaged config (server/config/forecast_models.yaml), unlike
`services.registry`'s DB-backed benchmark/blend model sources — there is
nothing per-user or per-region to register here, just which earth2studio
models are available and how to run them.
"""

from __future__ import annotations

from ai_almanac.server.services.forecast_pipeline import INIT_SOURCES
from ai_almanac.settings import get_packaged_forecast_models

_INTERNAL_FIELDS = ("earth2studio_class", "gpu")


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

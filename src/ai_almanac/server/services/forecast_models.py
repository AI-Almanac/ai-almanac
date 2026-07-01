"""AI weather model registry for live, on-demand forecast generation.

Static packaged config (server/config/forecast_models.yaml), unlike
`services.registry`'s DB-backed benchmark/blend model sources — there is
nothing per-user or per-region to register here, just which earth2studio
models are available and how to run them.
"""

from __future__ import annotations

from ai_almanac.settings import get_packaged_forecast_models

_INTERNAL_FIELDS = ("earth2studio_class", "gpu")


async def load_forecast_model_registry() -> list[dict]:
    """Public model list: registry entries with Modal-only fields stripped."""
    registry = get_packaged_forecast_models()
    return [
        {k: v for k, v in model.items() if k not in _INTERNAL_FIELDS}
        for model in registry.get("models") or []
    ]

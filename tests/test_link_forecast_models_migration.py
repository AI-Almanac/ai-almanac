from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_MIGRATION = (
    Path(__file__).parents[1]
    / "src/ai_almanac/server/alembic/versions/0027_link_forecast_models.py"
)


def _migration():
    spec = importlib.util.spec_from_file_location("link_forecast_models", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("name", "metadata", "expected"),
    [
        ("AIFS Single v2", {"grid_step_deg": 0.25}, "aifs2"),
        ("GraphCast Small", {}, "graphcast"),
        ("FuXi", {"grid_step_deg": 0.25}, "fuxi"),
        # A name match on the wrong grid could not forecast before either.
        ("GraphCast", {"grid_step_deg": 0.25}, None),
        ("AIFS v2 India 0.25", {"grid_step_deg": 0.25}, None),
    ],
)
def test_backfill_links_only_sources_the_old_name_match_could_forecast(
    name: str, metadata: dict, expected: str | None
) -> None:
    assert _migration()._linked_model_id(name, metadata) == expected

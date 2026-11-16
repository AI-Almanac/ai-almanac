"""A blend is trained on a country's sub-districts only where the pipeline has them."""

from __future__ import annotations

import pytest

from ai_almanac.server.services.blend_units import on_sub_districts
from ai_almanac.server.services.focus_area import FocusBox, FocusUnits, country_of

ETHIOPIA = {"id": "ethiopia", "boundary_iso": "ETH"}
BOX = FocusBox(lat_min=8, lat_max=12, lon_min=36, lon_max=40)


def test_a_blends_own_place_says_where_it_is_whatever_its_data_is_filed_under() -> None:
    turkana = FocusUnits(level="adm1", units=["Turkana"], iso="KEN")

    assert country_of(turkana, ETHIOPIA) == "KEN"


@pytest.mark.parametrize("area", [None, BOX, FocusUnits(level="adm2", units=["Awi"])])
def test_a_blend_that_names_no_country_is_where_its_observations_are_filed(area) -> None:
    assert country_of(area, ETHIOPIA) == "ETH"


def test_a_blend_with_no_place_and_no_region_is_nowhere_in_particular() -> None:
    assert country_of(BOX, None) is None
    assert country_of(None, {"id": "drawn-box", "boundary_iso": None}) is None


@pytest.mark.parametrize(("country", "expected"), [("ETH", True), ("KEN", False), (None, False)])
def test_only_countries_the_pipeline_has_sub_districts_for_are_blended_on_them(
    country: str | None, expected: bool
) -> None:
    assert on_sub_districts(country) is expected

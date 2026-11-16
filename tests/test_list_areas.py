"""The assistant names a region's administrative areas from its boundary data."""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text

from ai_almanac.server.db import get_db
from ai_almanac.server.services import boundaries, chat_tools
from ai_almanac.server.services.focus_area import points_inside
from tests.helpers.places import REGIONS, U_ZONE, ZONES, serve_boundaries


def test_a_concave_zone_is_placed_by_a_point_inside_it_not_by_its_middle() -> None:
    lon, lat = boundaries.interior_point(U_ZONE["geometry"])

    assert points_inside(U_ZONE["geometry"], np.array([lon]), np.array([lat])).all()
    assert not points_inside(U_ZONE["geometry"], np.array([37.0]), np.array([13.0])).any()


def test_each_zone_is_named_with_the_region_that_holds_it() -> None:
    areas = boundaries.name_areas(ZONES, REGIONS)

    assert areas == [
        {"name": "East Gojjam", "parent": "Amhara"},
        {"name": "North Gondar", "parent": "Amhara"},
        {"name": "Borena", "parent": "Oromia"},
    ]


def test_areas_within_a_parent_match_its_name_loosely_and_an_unknown_parent_names_the_known_ones() -> (
    None
):
    areas = boundaries.name_areas(ZONES, REGIONS)

    assert [area["name"] for area in boundaries.areas_within(areas, "amhara ")] == [
        "East Gojjam",
        "North Gondar",
    ]
    with pytest.raises(ValueError, match="Known: Amhara, Oromia"):
        boundaries.areas_within(areas, "Tigray")


@pytest.fixture
def ethiopia_boundaries(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    return serve_boundaries(monkeypatch, {"adm1": REGIONS, "adm2": ZONES})


@pytest.mark.asyncio
async def test_the_zones_in_amhara_come_from_the_regions_boundaries(
    client, ethiopia_boundaries
) -> None:
    result = await chat_tools.list_areas("ethiopia", "adm2", "Amhara")

    assert [area["name"] for area in result["areas"]] == ["East Gojjam", "North Gondar"]
    assert ethiopia_boundaries == [("ETH", "adm2"), ("ETH", "adm1")]


@pytest.mark.asyncio
async def test_states_are_listed_without_a_parent(client, ethiopia_boundaries) -> None:
    result = await chat_tools.list_areas("ethiopia", "adm1", None)
    nested = await chat_tools.list_areas("ethiopia", "adm1", "Amhara")

    assert result["areas"] == [{"name": "Amhara"}, {"name": "Oromia"}]
    assert "leave out within" in nested["error"]


async def _region_drawn_before_regions_stopped_being_created(region_id: str) -> None:
    """A box with no country behind it, as the catalogue may still hold."""
    async with get_db() as conn:
        await conn.execute(
            text(
                "INSERT INTO regions (id, display_name, description, lat_min, lat_max, lon_min, "
                "lon_max, land_only, shp_only, is_builtin, created_at, updated_at) "
                "VALUES (:id, 'Drawn Box', '', 0, 5, 0, 5, FALSE, FALSE, FALSE, :now, :now)"
            ),
            {"id": region_id, "now": "2026-01-01T00:00:00+00:00"},
        )


@pytest.mark.asyncio
async def test_a_region_without_boundaries_or_an_unknown_one_is_refused(
    client, ethiopia_boundaries
) -> None:
    await _region_drawn_before_regions_stopped_being_created("drawn-box")

    no_boundaries = await chat_tools.list_areas("drawn-box", "adm1", None)
    unknown = await chat_tools.list_areas("atlantis", "adm1", None)

    assert "no administrative boundaries" in no_boundaries["error"]
    assert "list_regions" in unknown["error"]
    assert ethiopia_boundaries == []


COUNTRIES = [
    {"iso": "COD", "name": "Democratic Republic of the Congo"},
    {"iso": "COG", "name": "Republic of the Congo"},
    {"iso": "KEN", "name": "Kenya"},
]


@pytest.fixture
def known_countries(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(boundaries, "_COUNTRIES", list(COUNTRIES))


@pytest.mark.parametrize(
    ("asked", "found"),
    [
        ("Kenya", ["KEN"]),
        (" ken ", ["KEN"]),
        ("congo", ["COD", "COG"]),
        ("Atlantis", []),
        ("", []),
    ],
)
def test_a_country_is_found_by_its_name_its_code_or_part_of_its_name(
    asked: str, found: list[str]
) -> None:
    assert [c["iso"] for c in boundaries.match_countries(COUNTRIES, asked)] == found


@pytest.mark.asyncio
async def test_the_assistant_finds_the_code_a_country_is_picked_by(known_countries) -> None:
    kenya = await chat_tools.find_country("kenya")
    nowhere = await chat_tools.find_country("Atlantis")

    assert kenya == {"countries": [{"iso": "KEN", "name": "Kenya"}], "count": 1}
    assert "Atlantis" in nowhere["error"]


@pytest.mark.asyncio
async def test_the_areas_of_any_country_are_named_by_its_code_alone(
    client, ethiopia_boundaries, known_countries
) -> None:
    result = await chat_tools.list_areas(None, "adm1", None, "ken")

    assert (result["region"], result["country"]) == (None, "KEN")
    assert [area["name"] for area in result["areas"]] == ["Amhara", "Oromia"]
    assert ethiopia_boundaries == [("KEN", "adm1")]


@pytest.mark.asyncio
async def test_a_regions_areas_come_with_the_country_code_to_pick_them_by(
    client, ethiopia_boundaries
) -> None:
    result = await chat_tools.list_areas("ethiopia", "adm1", None)

    assert (result["region"], result["country"]) == ("ethiopia", "ETH")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("country", "says"), [("Kenya", "three-letter code"), ("ZZZ", "No country has the code ZZZ")]
)
async def test_a_country_that_cannot_be_placed_points_to_find_country(
    client, ethiopia_boundaries, known_countries, country: str, says: str
) -> None:
    result = await chat_tools.list_areas(None, "adm1", None, country)

    assert says in result["error"]
    assert "find_country" in result["error"]
    assert ethiopia_boundaries == []


@pytest.mark.asyncio
async def test_areas_cannot_be_named_for_nowhere(client, ethiopia_boundaries) -> None:
    result = await chat_tools.list_areas(None, "adm1", None)

    assert "three-letter code" in result["error"]

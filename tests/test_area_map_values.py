"""Which columns of an area table can be mapped, and how a missing value is drawn."""

from __future__ import annotations

import pytest

from ai_almanac.server.services.primitives.figures import _area_values

TABLE = (
    "area,parent,issue_date,p_within_14d,most_likely,coverage\n"
    "Awi,Amhara,2026-06-01,0.4,week 2,0.9\n"
    "Bale,Oromia,2026-06-01,0.7,week 1,\n"
)


def test_a_numeric_column_maps_each_area_and_leaves_an_empty_one_out() -> None:
    values, shown = _area_values(TABLE, "coverage", None)

    assert shown == "2026-06-01"
    assert values == {"Awi": 0.9}


@pytest.mark.parametrize("column", ["most_likely", "parent", "missing"])
def test_a_column_of_words_or_none_is_refused_with_the_ones_that_can_be_mapped(column) -> None:
    with pytest.raises(ValueError, match="cannot be mapped; choose one of p_within_14d, coverage"):
        _area_values(TABLE, column, None)


def test_a_column_no_area_has_a_value_in_is_refused_in_words() -> None:
    unknown_everywhere = (
        "area,issue_date,p_within_14d,coverage\nAwi,2026-06-01,0.4,\nBale,2026-06-01,0.7,\n"
    )

    with pytest.raises(ValueError, match="No area has a coverage value on 2026-06-01"):
        _area_values(unknown_everywhere, "coverage", None)

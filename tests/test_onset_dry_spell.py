"""The onset definition's knobs: the dry-spell check (off by default in benchmarks and
blends, one switch turns it on) and the blend's 3- or 5-day trigger window."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_almanac.envs import blend_recipe
from ai_almanac.server.services.job_submission import BlendParams
from ai_almanac.server.services.romp import render_romp_config


def _romp(romp_params: dict) -> dict:
    config = {
        "model_name": "fuxi",
        "obs_dir": "/data/obs",
        "model_dir": "/data/fuxi",
        "model_config": {"start_date": "2019-05-01", "end_date": "2024-07-31"},
        "romp_params": romp_params,
    }
    namespace: dict = {}
    exec(render_romp_config(config, Path("/tmp/out"), Path("/tmp/fig")), {}, namespace)
    return namespace


def test_benchmarks_check_no_dry_spell_unless_switched_on() -> None:
    off = _romp({})
    on = _romp({"dry_spell_check": True})
    by_hand = _romp({"dry_spell_check": True, "dry_spell": 5, "dry_extent": 15})

    assert off["dry_extent"] == 0
    # A dry run of 7+ days within the onset day and the 21 days after it.
    assert (on["dry_spell"], on["dry_extent"]) == (7, 22)
    assert (by_hand["dry_spell"], by_hand["dry_extent"]) == (5, 15)


def _onset(region: str = "ethiopia", **params) -> dict:
    return blend_recipe.onset(params, region)


def test_icpac_onsets_need_20_mm_over_5_days_with_3_rain_days_and_no_dry_spell_check() -> None:
    definition = _onset()["definition"]

    assert definition == {
        "window": 5,
        "trigger_rule": "first_day_wet",
        "min_wet_days": 3,
        "wet_day_min_mm": 1.0,
        "wet_day_comparison": "gte",
        "threshold_comparison": "gte",
        "follow_days": 0,
    }
    assert _onset()["threshold"] == {"mm": 20.0}


def test_the_switch_turns_on_icpacs_dry_spell_check() -> None:
    definition = _onset(dry_spell_check=True)["definition"]

    assert definition["follow_days"] == 21
    assert definition["followup_anchor"] == "onset_day"
    assert definition["dry_spell"] == {
        "mode": "consecutive_dry",
        "min_dry_days": 7,
        "dry_day_min_mm": 1.0,
        "contained": True,
    }
    tuned = _onset(dry_spell_check=True, dry_spell_days=5, dry_spell_window_days=15)["definition"]
    assert (tuned["dry_spell"]["min_dry_days"], tuned["follow_days"]) == (5, 15)


def test_the_switch_turns_on_moron_robertsons_own_dry_spell_check() -> None:
    assert _onset("india")["definition"]["follow_days"] == 0
    definition = _onset("india", dry_spell_check=True)["definition"]

    assert (definition["follow_days"], definition["followup_anchor"]) == (30, "after_trigger")
    assert definition["dry_spell"] == {"mode": "window_sum", "sum_window": 10, "sum_min_mm": 5.0}


def test_dry_spell_numbers_need_the_switch_and_must_fit() -> None:
    years = {"training_years": "2008:2020", "cv_holdout_years": "2021"}
    with pytest.raises(ValidationError, match="need dry_spell_check"):
        BlendParams(**years, dry_spell_days=5)
    with pytest.raises(ValidationError, match="cannot fit"):
        BlendParams(**years, dry_spell_check=True, dry_spell_days=10, dry_spell_window_days=7)
    assert BlendParams(**years, dry_spell_check=True, dry_spell_days=5).dry_spell_days == 5


def test_a_3_day_window_is_icpacs_3_wet_days_in_a_row_as_benchmarks_count_it() -> None:
    definition = _onset(onset_window_days=3)["definition"]

    assert (definition["window"], definition["min_wet_days"]) == (3, 3)
    assert definition["trigger_rule"] == "first_day_wet"
    assert _onset(onset_window_days=3)["threshold"] == {"mm": 20.0}
    # Choosing the definition's own window changes nothing.
    assert _onset(onset_window_days=5) == _onset()


def test_moron_robertsons_cell_amount_totals_the_chosen_window() -> None:
    onset = _onset("india", onset_window_days=3)

    assert onset["definition"]["window"] == 3
    assert onset["threshold"]["climatology"]["days"] == 3
    assert onset["threshold"]["climatology"]["years"] == [1991, 2020]
    assert _onset("india", onset_window_days=3, threshold_mm=30)["threshold"] == {"mm": 30.0}


def test_the_window_is_3_or_5_days() -> None:
    years = {"training_years": "2008:2020", "cv_holdout_years": "2021"}
    assert BlendParams(**years, onset_window_days=3).onset_window_days == 3
    with pytest.raises(ValidationError):
        BlendParams(**years, onset_window_days=4)


def test_ethiopia_blends_keep_the_woredas_mostly_inside_its_season_mask(tmp_path: Path) -> None:
    import csv

    import xarray as xr

    from ai_almanac.server.services import focus_area

    assert blend_recipe.SEASON_MASKS == focus_area.SEASON_MASKS
    with xr.open_dataset(focus_area.season_mask("ethiopia")) as ds:
        kept = ds["precip"]
        wet, dry = (13.0, 38.0), (5.0, 44.0)
        assert bool(kept.sel(latitude=wet[0], longitude=wet[1]))
        assert not bool(kept.sel(latitude=dry[0], longitude=dry[1]))
    weights = tmp_path / "weights.csv"
    with open(weights, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["latitude", "longitude", "adm3_name", "weight"])
        writer.writerows(
            [
                [*wet, "Highland", 1.0],
                [*dry, "Lowland", 1.0],
                # Mostly outside: 0.4 of its area in the mask.
                [*wet, "Edge", 0.4],
                [*dry, "Edge", 0.6],
            ]
        )

    units = blend_recipe.season_units("ethiopia", {}, weights, tmp_path / "recipe")

    with open(units) as handle:
        assert [row["adm3_name"] for row in csv.DictReader(handle)] == ["Highland"]
    assert blend_recipe.season_units("ethiopia", {"season_mask": False}, weights, tmp_path) is None
    assert blend_recipe.season_units("kenya", {}, weights, tmp_path) is None


def test_the_recipe_with_a_dry_spell_check_is_a_valid_blend_config(tmp_path: Path) -> None:
    onset_blending = pytest.importorskip("onset_blending")
    job = {
        "obs_dir": "/obs",
        "model_names": ["m"],
        "model_files": {"m": ["/m/2008.nc"]},
        "region_id": "kenya",
        "blend_params": {
            "training_years": "2008:2020",
            "cv_holdout_years": "2021",
            "dry_spell_check": True,
            "onset_window_days": 3,
        },
    }
    config = onset_blending.BlendConfig.from_dict(
        blend_recipe.blend_config(job, work_dir=tmp_path, blending_root=tmp_path)
    )
    assert config.onset.definition.dry_spell.contained
    assert config.onset.definition.window == 3

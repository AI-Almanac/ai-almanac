"""The dry-spell check: off by default in benchmarks and blends, one switch turns it on."""

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


def test_icpac_onsets_start_on_a_wet_day_and_check_no_dry_spell_by_default() -> None:
    definition = _onset()["definition"]

    assert definition == {
        "window": 3,
        "trigger_rule": "first_day_wet",
        "wet_day_min_mm": 1.0,
        "wet_day_comparison": "gte",
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
        },
    }
    config = onset_blending.BlendConfig.from_dict(
        blend_recipe.blend_config(job, work_dir=tmp_path, blending_root=tmp_path)
    )
    assert config.onset.definition.dry_spell.contained

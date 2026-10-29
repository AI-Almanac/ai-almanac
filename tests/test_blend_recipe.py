"""A blend job's onset_blending recipe: the workflow's blend, from the job config."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_almanac.envs import blend_recipe

LAYOUTS = {
    "aifs_ensv2": {
        "forecast_dims": {
            "lat": "lat",
            "lon": "lon",
            "init_time": "time",
            "step": "prediction_timedelta_daily",
            "member": "number",
        },
        "unit_cvt": 1000.0,
    },
    "aifsv2": {
        "forecast_dims": {"lat": "lat", "lon": "lon", "step": "prediction_timedelta_daily"},
        "unit_cvt": 1000.0,
    },
}


def _job(**changes) -> dict:
    job = {
        "blend_name": "Kiremt blend",
        "obs_dir": "/datasets/ethiopia/obs",
        "model_names": ["aifs_ensv2", "aifsv2"],
        "model_files": {
            "aifs_ensv2": ["/datasets/m/ens/2008.nc", "/datasets/m/ens/2009.nc"],
            "aifsv2": ["/datasets/m/det/2008.nc"],
        },
        "model_layouts": LAYOUTS,
        "region_id": "india",
        "blend_params": {
            "training_years": "2008:2022",
            "cv_holdout_years": "2023,2024",
            "true_holdout_years": "2025:2025",
            "obs_value_col": "total_precipitation_24hr",
        },
    }
    job.update(changes)
    return job


def test_a_job_becomes_the_workflows_blend(tmp_path: Path) -> None:
    recipe = blend_recipe.blend_config(_job(), work_dir=tmp_path)

    assert recipe["name"] == "Kiremt blend"
    assert recipe["domain"] == {"grid_ids": {"decimal_digits": 2, "format": "fixed"}}
    assert recipe["observations"] == {
        "files": {"folder": "/datasets/ethiopia/obs", "pattern": r"\.nc$"},
        "variable": "total_precipitation_24hr",
        "date_marks": "end",
    }
    ens, det = recipe["models"]
    assert ens == {
        "name": "aifs_ensv2",
        "files": {"paths": ["/datasets/m/ens/2008.nc", "/datasets/m/ens/2009.nc"]},
        "variable": "tp",
        "dims": {
            "lat": "lat",
            "lon": "lon",
            "time": "time",
            "prediction_timedelta_daily": "day",
            "number": "number",
        },
        "unit_scale": 1000.0,
        "rain_predictors": [{"agg": "diff", "window": "trigger"}],
    }
    assert det["dims"] == {"lat": "lat", "lon": "lon", "prediction_timedelta_daily": "day"}
    assert recipe["onset"] == {
        "threshold": {"mm": 20.0},
        "season_start": "05-01",
        "reference_onset": {"month_day": "06-01"},
        "definition": blend_recipe.ONSET_DEFINITION,
    }
    assert recipe["climatology"] == {
        "forecast_window": 45,
        "cv_by_year": False,
        "min_onset_years": 10,
    }
    assert recipe["evaluation"] == {
        "years": {
            "training": list(range(2008, 2023)),
            "cv_holdout": [2023, 2024],
            "true_holdout": [2025],
        },
        "formulas": {
            "blended_model": "outcome ~ prob_clim_qx * diff_aifs_ensv2_qx * diff_aifsv2_qx"
        },
    }


def test_the_users_onset_rule_and_formula_carry_over(tmp_path: Path) -> None:
    params = {
        **_job()["blend_params"],
        "threshold_mm": 25,
        "cutoff_month_day": "06-01",
        "mok_month_day": "06-15",  # the pre-rename spelling of ref_onset_month_day
        "min_onset_years": 5,
        "formula_text": "outcome ~ prob_clim_mr_qx * diff_aifsv2_qx",
    }
    recipe = blend_recipe.blend_config(_job(blend_params=params), work_dir=tmp_path)

    assert recipe["onset"]["threshold"] == {"mm": 25.0}
    assert recipe["onset"]["season_start"] == "06-01"
    assert recipe["onset"]["reference_onset"] == {"month_day": "06-15"}
    assert recipe["climatology"]["min_onset_years"] == 5
    # Stored formulas name the workflow's climatology features.
    assert recipe["evaluation"]["formulas"]["blended_model"] == (
        "outcome ~ prob_clim_qx * diff_aifsv2_qx"
    )


def test_observations_that_mark_the_day_itself_say_so(tmp_path: Path) -> None:
    recipe = blend_recipe.blend_config(_job(obs_date_marks="start"), work_dir=tmp_path)

    assert recipe["observations"]["date_marks"] == "start"


def test_adm3_regions_regrid_with_the_checkouts_district_weights(tmp_path: Path) -> None:
    recipe = blend_recipe.blend_config(
        _job(region_id="Ethiopia"), work_dir=tmp_path, blending_root=Path("/opt/blending")
    )

    assert recipe["domain"]["regrid"] == {
        "weights": "/opt/blending/Monsoon_Data/grid_to_district_mapping.csv"
    }
    with pytest.raises(ValueError, match="ADM3 weights"):
        blend_recipe.blend_config(_job(region_id="ethiopia"), work_dir=tmp_path)


def test_a_focus_box_limits_the_blend(tmp_path: Path) -> None:
    box = {"lat_min": 5, "lat_max": 10, "lon_min": 35, "lon_max": 40}
    params = {**_job()["blend_params"], "focus_area": box}

    recipe = blend_recipe.blend_config(_job(blend_params=params), work_dir=tmp_path)

    assert recipe["domain"]["area_of_interest"] == {
        "bbox": {"lat_min": 5.0, "lat_max": 10.0, "lon_min": 35.0, "lon_max": 40.0}
    }


def test_picked_units_limit_the_blend_to_their_outlines_located_by_centroids(
    tmp_path: Path,
) -> None:
    root = tmp_path / "blending"
    weights = root / blend_recipe.ADM3_WEIGHTS
    weights.parent.mkdir(parents=True)
    weights.write_text(
        "latitude,longitude,adm3_name,weight\n10.0,40.0,A,1\n12.0,42.0,A,3\n8.0,36.0,B,2\n"
    )
    outlines = {"type": "FeatureCollection", "features": []}
    params = {
        **_job()["blend_params"],
        "focus_area": {"level": "adm1", "units": ["Afar"], "geometry": outlines},
    }

    recipe = blend_recipe.blend_config(
        _job(region_id="ethiopia", blend_params=params),
        work_dir=tmp_path / "work",
        blending_root=root,
    )

    area = recipe["domain"]["area_of_interest"]
    assert json.loads(Path(area["geometry"]).read_text()) == outlines
    centroids = Path(recipe["domain"]["centroids"]["file"]).read_text().splitlines()
    # Each district at its weight-averaged grid point, as the workflow places it.
    assert centroids == ["id,lat,lon", "A,11.5,41.5", "B,8.0,36.0"]


def test_a_job_without_models_or_their_files_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="no models"):
        blend_recipe.blend_config(_job(model_names=[]), work_dir=tmp_path)
    with pytest.raises(ValueError, match="No forecast files"):
        blend_recipe.blend_config(_job(model_files={"aifs_ensv2": []}), work_dir=tmp_path)


def test_the_recipe_is_a_valid_blend_config(tmp_path: Path) -> None:
    onset_blending = pytest.importorskip("onset_blending")

    config = onset_blending.BlendConfig.from_dict(
        blend_recipe.blend_config(_job(), work_dir=tmp_path)
    )

    assert config.model_names == ("aifs_ensv2", "aifsv2")

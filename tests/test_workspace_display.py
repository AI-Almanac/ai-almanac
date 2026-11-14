"""How a workspace names its files, describes its steps and shows a run's settings."""

from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from ai_almanac.server.services import (
    workspace_display,
)
from tests.helpers.records import BENCHMARK_CONFIG


def _artifact(storage_key: str, kind: str = "metrics", fmt: str = "netcdf") -> dict:
    return {"filename": PurePosixPath(storage_key).name, "kind": kind, "format": fmt}


_AIFS_STEP = {"params": {"model_name": "aifs", "model_display_name": "AIFS"}}


@pytest.mark.parametrize(
    ("storage_key", "kind", "fmt", "title", "group"),
    [
        (
            "job:j/output/spatial_metrics_aifs_1-15.nc",
            "metrics",
            "netcdf",
            "AIFS · Spatial metrics · days 1–15",
            "Spatial metrics",
        ),
        (
            "job:j/output/spatial_metrics_climatology_16-30.nc",
            "metrics",
            "netcdf",
            "Climatology · Spatial metrics · days 16–30",
            "Climatology",
        ),
        (
            "job:j/output/climatology_onset_doy_1998-2024.nc",
            "metrics",
            "netcdf",
            "Climatology · onset day of year · 1998–2024",
            "Climatology",
        ),
        (
            "job:j/output/e2s_spatial_metrics_aifs_all.nc",
            "metrics",
            "netcdf",
            "AIFS · Spatial metrics · all lead days",
            "Spatial metrics",
        ),
        (
            "job:j/output/binned_skill_scores_aifs_1-15.csv",
            "table",
            "csv",
            "AIFS · Binned skill scores · days 1–15",
            "Tables",
        ),
        ("job:j/figure/portrait_aifs.png", "figure", "png", "AIFS · Portrait plot", "Figures"),
        (
            "job:j/output/summary_models_fixed_cutoff_2008_2024.csv",
            "table",
            "csv",
            "Summary models fixed cutoff · 2008–2024",
            "Tables",
        ),
        ("documents/ab12cd34_run_code.py", "document", "python", "Ab12cd34 run code", "Code"),
    ],
)
def test_artifacts_get_a_human_title_and_family(storage_key, kind, fmt, title, group) -> None:
    artifact = _artifact(storage_key, kind, fmt)
    assert workspace_display.artifact_title(artifact, _AIFS_STEP) == title
    assert workspace_display.artifact_group(artifact) == group


def test_a_model_the_step_does_not_name_keeps_its_filename_token() -> None:
    artifact = _artifact("job:j/output/spatial_metrics_FuXi_1-15.nc")
    assert workspace_display.artifact_title(artifact, None) == "FuXi · Spatial metrics · days 1–15"


@pytest.mark.parametrize(
    ("step", "sentence"),
    [
        (
            {"primitive": "benchmark.job", "actor_kind": "user", "params": BENCHMARK_CONFIG},
            "Benchmark run of AIFS against CHIRPS Ethiopia, 1998–2024, days 1–30.",
        ),
        (
            {
                "primitive": "acquire.open_source",
                "params": {
                    "source_name": "CHIRPS Ethiopia",
                    "area_name": "Borena",
                    "start": "1998-01-01",
                    "end": "2024-12-31",
                },
            },
            "Opened CHIRPS Ethiopia for Borena, 1 Jan 1998 to 31 Dec 2024.",
        ),
        (
            {"primitive": "figure.plot_area_map", "params": {"title": "Onset within 14 days"}},
            "Drew the map “Onset within 14 days”.",
        ),
        (
            {"primitive": "code.run_code", "actor_kind": "assistant", "params": {}},
            "Ran code the assistant wrote. The platform does not check its results.",
        ),
        (
            {"primitive": "fetch.new_thing", "actor_kind": "assistant", "params": {}},
            "Ran the new thing step.",
        ),
    ],
    ids=["benchmark", "opened source", "map", "code", "unknown"],
)
def test_each_step_is_described_in_a_sentence(step: dict, sentence: str) -> None:
    assert workspace_display.describe_step(step) == sentence


def test_a_blend_is_named_by_the_name_it_was_given() -> None:
    blend = {"blend_name": "GraphCast-FuXi Ethiopia Blend", "model_names": ["graphcast", "fuxi"]}

    assert workspace_display.job_name(blend) == "GraphCast-FuXi Ethiopia Blend"
    assert workspace_display.job_name(BENCHMARK_CONFIG) == "AIFS"


def test_display_params_hide_paths_and_storage_keys() -> None:
    shown = workspace_display.display_params(BENCHMARK_CONFIG)
    assert "obs_dir" not in shown and "storage_key" not in shown and "model_files" not in shown
    assert "model_dir" not in shown["model_config"]
    assert shown["model_config"] == {"start_year": 1998, "end_year": 2024}
    assert shown["dataset_config"] == {"source_name": "CHIRPS Ethiopia"}
    assert shown["romp_params"]["max_forecast_day"] == 30


@pytest.mark.parametrize(
    ("filename", "internal"),
    [
        ("coefs_blended_model_global_fixed_cutoff_2019_2024_year2019.pkl", True),
        ("combined_wide.pkl", True),
        ("training_spec.yml", True),
        ("manifest.json", True),
        ("formula_sample_support_fixed_cutoff_2019_2024.json", True),
        ("summary_models_pooled_fixed_cutoff_2019_2024.csv", False),
        ("coefs_blended_model_global_final.csv", False),
        ("spatial_metrics_AIFS_1-15.nc", False),
        ("blended_forecast_probabilities.csv", False),
    ],
)
def test_a_blends_training_internals_are_supporting_files(filename: str, internal: bool) -> None:
    assert workspace_display.is_training_internal(filename) is internal

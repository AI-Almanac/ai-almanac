from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_almanac.envs.blend_entrypoint import _load_workflow


def test_blending_workflow_loads_with_local_function_wrappers(monkeypatch) -> None:
    monkeypatch.setenv("ALMANAC_BLENDING_ROOT", "/tmp/onset-blending")
    workflow = _load_workflow()

    assert Path("/tmp/onset-blending") == workflow.BLENDING_ROOT
    assert hasattr(workflow.build_lat_lon_intermediates_bundle, "local")


class _FakeTraining:
    """onset_blending's train(), recording the recipe and what was saved."""

    def __init__(self, files: dict[str, bytes]):
        self.files, self.recipes, self.saved, self.resources = files, [], [], []

    def modules(self) -> dict:
        fake = self

        class BlendConfig:
            @staticmethod
            def from_dict(recipe):
                fake.recipes.append(recipe)
                return SimpleNamespace(recipe=recipe)

        class Result:
            inputs = SimpleNamespace(timings={"evaluation and final fit": 1.0})

            @staticmethod
            def save(directory, *, intermediates):
                fake.saved.append(intermediates)
                Path(directory).mkdir(parents=True, exist_ok=True)
                for name, data in fake.files.items():
                    (Path(directory) / name).write_bytes(data)

        def train(config, *, resources):
            fake.resources.append(resources)
            return Result()

        training = SimpleNamespace(
            Resources=SimpleNamespace(default=lambda *, cache_dir=None: {"cache_dir": cache_dir}),
            train=train,
        )
        return {
            "onset_blending": SimpleNamespace(BlendConfig=BlendConfig),
            "onset_blending.training": training,
        }


@pytest.mark.parametrize("day_level", [False, True])
def test_a_local_blend_trains_with_onset_blending_from_its_recipe(
    tmp_path: Path, monkeypatch, capsys, day_level: bool
) -> None:
    from ai_almanac.envs import blend_entrypoint

    fake = _FakeTraining(
        {
            "summary_models_pooled_fixed_cutoff_2022.csv": b"model,rps\nblended_model,0.4\n",
            "summary_models_fixed_cutoff_2022.csv": b"id,rps\nA,0.4\nALL,0.4\n",
            "trained.blend": b"blend",
        }
    )
    for name, module in fake.modules().items():
        monkeypatch.setitem(sys.modules, name, module)
    centroided = []
    workflow = SimpleNamespace(
        _with_area_centroids=lambda path: centroided.append(path.name) or b"with centroids"
    )
    monkeypatch.setattr(blend_entrypoint, "_load_workflow", lambda: workflow)
    output_dir = tmp_path / "job" / "output"
    config = {
        "blend_name": "test",
        "obs_dir": str(tmp_path / "obs"),
        "model_names": ["aifs"],
        "model_files": {"aifs": [str(tmp_path / "aifs" / "2023.nc")]},
        "blend_params": {"training_years": "2020,2021", "cv_holdout_years": "2022"},
        "train_day_level_blend": day_level,
        "cache_dir": str(tmp_path / "cache"),
    }

    blend_entrypoint.run(config, output_dir)

    # Computed onset thresholds are kept in the app's cache.
    assert fake.resources == [{"cache_dir": str(tmp_path / "cache")}]

    [recipe] = fake.recipes
    assert recipe["evaluation"]["years"] == {"training": [2020, 2021], "cv_holdout": [2022]}
    assert recipe["models"][0]["files"] == {"paths": [str(tmp_path / "aifs" / "2023.nc")]}
    # No wide intermediate tables are published.
    assert fake.saved == [()]
    assert (output_dir / "trained.blend").read_bytes() == b"blend"
    # Per-point summaries get unit centroids; the pooled summary is left alone.
    assert centroided == ["summary_models_fixed_cutoff_2022.csv"]
    assert (output_dir / "summary_models_fixed_cutoff_2022.csv").read_bytes() == b"with centroids"
    # The day-level blend is onset_blending's random forest, trained with the rest.
    assert ("random_forest" in recipe["evaluation"]) is day_level
    assert "Model blending complete" in capsys.readouterr().out


def test_intermediate_prep_kwargs_reads_legacy_mok_month_day() -> None:
    intermediate_prep_kwargs = _load_workflow()._intermediate_prep_kwargs

    # Pre-rename job configs still carry mok_month_day.
    assert intermediate_prep_kwargs({"mok_month_day": "06-05", "threshold_mm": 25.0}) == {
        "threshold_mm": 25.0,
        "ref_onset_month_day": "06-05",
    }
    # The new name wins when both are present; unrelated keys are not forwarded.
    assert intermediate_prep_kwargs(
        {"mok_month_day": "06-05", "ref_onset_month_day": "06-10", "formula_text": "x"}
    ) == {"ref_onset_month_day": "06-10"}
    assert intermediate_prep_kwargs({"training_years": "2020"}) == {}


def test_blend_intermediates_run_with_the_given_resources_and_blend_params(
    monkeypatch,
) -> None:
    workflow = _load_workflow()
    calls: list[dict] = []
    monkeypatch.setattr(
        workflow, "build_intermediates_from_dirs", lambda *args, **kwargs: calls.append(kwargs)
    )

    workflow.build_blend_intermediates(
        Path("obs"),
        {"aifs": Path("aifs")},
        {"mok_month_day": "06-05", "training_years": "2020"},
        region_id="india",
        cache_dir="gs://bucket/blend-intermediates",
        resources=workflow.IntermediateResources(workers=6, file_memory_budget_bytes=123),
        climatology_test_year_min=2000,
    )

    built = calls[0]
    assert (built["file_workers"], built["climatology_workers"]) == (6, 6)
    assert built["file_memory_budget_bytes"] == 123
    assert built["ref_onset_month_day"] == "06-05"
    assert built["region_id"] == "india"
    assert built["climatology_test_year_min"] == 2000
    assert "training_years" not in built


def test_live_scoring_builds_intermediates_with_the_resources_it_is_given(monkeypatch) -> None:
    workflow = _load_workflow()
    builds: list[dict] = []

    def build(*_args, **kwargs):  # noqa: ANN002, ANN003
        builds.append(kwargs)
        return {"output_dir": "/tmp/intermediates"}

    monkeypatch.setattr(workflow, "_extract_bundle", lambda bundle: Path("extracted"))
    monkeypatch.setattr(workflow, "build_blend_intermediates", build)
    monkeypatch.setattr(
        workflow,
        "apply_blend_coefs_bundle",
        SimpleNamespace(local=lambda *_a, **_k: workflow.LiveScores(weekly_csv=b"weekly")),
    )

    workflow.score_live_forecast.local(
        b"obs",
        {"aifs": b"live"},
        ["aifs"],
        {"training_years": "2020", "cv_holdout_years": "2021"},
        2026,
        resources=workflow.MODAL_INTERMEDIATE_RESOURCES,
        coef_pkl=b"coefs",
        climatology_test_year_min=2000,
    )

    assert builds[0]["resources"] == workflow.MODAL_INTERMEDIATE_RESOURCES
    assert builds[0]["climatology_test_year_min"] == 2000


def _score_live_season(monkeypatch, blend_config: dict, artifacts: dict[str, bytes]):
    """score_live_season with recorded scoring; returns (score kwargs, loaded models)."""
    workflow = _load_workflow()
    scored: list[dict] = []
    loaded: list[str] = []

    def score(obs, forecasts, model_names, params, live_year, **kwargs):  # noqa: ANN001, ANN003
        scored.append({"forecasts": forecasts, "params": params, **kwargs})
        return workflow.LiveScores(weekly_csv=b"weekly")

    def load_historical(name: str) -> bytes:
        loaded.append(name)
        return b"historical"

    monkeypatch.setattr(workflow, "score_live_forecast", SimpleNamespace(local=score))
    monkeypatch.setattr(
        workflow, "_merge_forecast_bundle", lambda historical, live: historical + b"+" + live
    )
    workflow.score_live_season(
        {"model_names": ["aifs"], "region_id": "india", **blend_config},
        {"aifs": b"live"},
        2026,
        obs_bundle=b"obs",
        load_historical=load_historical,
        read_artifact=artifacts.get,
        cache_dir=None,
        resources=workflow.MODAL_INTERMEDIATE_RESOURCES,
    )
    return scored[0], loaded


def test_live_scoring_with_trained_coefficients_reads_only_the_live_season(monkeypatch) -> None:
    coef_name = _load_workflow().FINAL_COEF_FILENAME
    scored, loaded = _score_live_season(
        monkeypatch, {"forecast_years": [2003, 2000, 2010]}, {coef_name: b"coefs"}
    )

    assert loaded == []
    assert scored["forecasts"] == {"aifs": b"live"}
    assert scored["coef_pkl"] == b"coefs"
    # The climatology keeps the window the blend trained with: obs before 2000.
    assert scored["climatology_test_year_min"] == 2000
    assert scored["params"]["region_id"] == "india"


@pytest.mark.parametrize(
    ("blend_config", "has_coefs"),
    [
        ({"forecast_years": [2000, 2001]}, False),
        # A config that predates recording its years cannot pin the climatology.
        ({}, True),
    ],
)
def test_live_scoring_merges_historical_forecasts_when_it_cannot_score_from_the_live_season(
    monkeypatch, blend_config: dict, has_coefs: bool
) -> None:
    artifacts = {_load_workflow().FINAL_COEF_FILENAME: b"coefs"} if has_coefs else {}
    scored, loaded = _score_live_season(monkeypatch, blend_config, artifacts)

    assert loaded == ["aifs"]
    assert scored["forecasts"] == {"aifs": b"historical+live"}


def test_live_scoring_rejects_a_missing_live_forecast() -> None:
    workflow = _load_workflow()
    with pytest.raises(ValueError, match="No live forecast bundle"):
        workflow.score_live_season(
            {"model_names": ["aifs", "graphcast"]},
            {"aifs": b"live"},
            2026,
            obs_bundle=b"obs",
            load_historical=lambda name: b"",
            read_artifact=lambda filename: None,
            cache_dir=None,
            resources=workflow.MODAL_INTERMEDIATE_RESOURCES,
        )


def _local_forecast_scoring(tmp_path: Path, monkeypatch, blend_files: dict[str, bytes]):
    """Run the local forecast scorer against a locally trained blend whose
    output directory holds `blend_files`; returns (output_dir, score calls)."""
    from ai_almanac.envs import forecast_entrypoint

    obs_dir = tmp_path / "obs"
    obs_dir.mkdir()
    (obs_dir / "2023.nc").write_bytes(b"obs")
    historical = tmp_path / "aifs-2023.nc"
    historical.write_bytes(b"forecast")
    live = tmp_path / "aifs.nc"
    live.write_bytes(b"live")
    blend_output = tmp_path / "blend-output"
    blend_output.mkdir()
    for name, data in blend_files.items():
        (blend_output / name).write_bytes(data)

    workflow = _load_workflow()
    calls: list[dict] = []

    def score_live_forecast(*_args, forest_pkl=None, **kwargs):  # noqa: ANN002, ANN003
        calls.append({"forest_pkl": forest_pkl, **kwargs})
        daily = b"daily" if forest_pkl is not None else None
        return workflow.LiveScores(weekly_csv=b"weekly", daily_csv=daily)

    monkeypatch.setattr(workflow, "_bundle_files", lambda files: b"bundle")
    monkeypatch.setattr(workflow, "_merge_forecast_bundle", lambda historical, live: b"merged")
    monkeypatch.setattr(workflow, "score_live_forecast", SimpleNamespace(local=score_live_forecast))
    monkeypatch.setattr(forecast_entrypoint, "_load_workflow", lambda: workflow)

    output_dir = tmp_path / "output"
    forecast_entrypoint._score_live(
        {
            "blend_config_snapshot": {
                "obs_dir": str(obs_dir),
                "model_names": ["aifs"],
                "model_files": {"aifs": [str(historical)]},
                "blend_output_uri": str(blend_output),
            }
        },
        2026,
        {"aifs": live},
        output_dir,
    )
    return workflow, output_dir, calls


@pytest.mark.parametrize("has_day_level_model", [True, False])
def test_local_forecast_writes_daily_scores_only_when_the_blend_has_a_day_level_model(
    tmp_path: Path, monkeypatch, has_day_level_model: bool
) -> None:
    probe = _load_workflow()
    blend_files = {probe.FINAL_COEF_FILENAME: b"coefs"}
    if has_day_level_model:
        blend_files[probe.FOREST_MODEL_FILENAME] = b"forest"
    workflow, output_dir, calls = _local_forecast_scoring(tmp_path, monkeypatch, blend_files)

    assert calls[0]["coef_pkl"] == b"coefs"
    assert calls[0]["forest_pkl"] == (b"forest" if has_day_level_model else None)
    assert (output_dir / workflow.WEEKLY_FORECAST_FILENAME).read_bytes() == b"weekly"
    daily = output_dir / workflow.DAILY_FORECAST_FILENAME
    assert daily.exists() is has_day_level_model


@pytest.mark.parametrize("day_level", [False, True])
def test_a_blend_onset_blending_trained_forecasts_with_its_trained_blend(
    tmp_path: Path, monkeypatch, day_level: bool
) -> None:
    import pandas as pd

    from ai_almanac.envs import forecast_entrypoint

    blend_output = tmp_path / "blend-output"
    blend_output.mkdir()
    (blend_output / "trained.blend").write_bytes(b"blend")
    applied = []

    class TrainedBlend:
        @staticmethod
        def load(path):
            assert path == blend_output / "trained.blend"
            return TrainedBlend()

        def forecast(self, forecasts):
            applied.append(forecasts)
            weekly = pd.DataFrame(
                {"id": ["10.00_40.00"], "time": ["2026-06-01"], "cv_week1": [0.5]}
            )
            daily = None
            if day_level:
                daily = weekly.assign(p_day_1=0.25, p_later=0.75)
            return SimpleNamespace(weekly=weekly, daily=daily)

    monkeypatch.setitem(sys.modules, "onset_blending", SimpleNamespace(TrainedBlend=TrainedBlend))
    workflow = _load_workflow()
    monkeypatch.setattr(forecast_entrypoint, "_load_workflow", lambda: workflow)
    live = tmp_path / "aifs.nc"
    output_dir = tmp_path / "output"

    forecast_entrypoint._score_live(
        {"blend_config_snapshot": {"blend_output_uri": str(blend_output), "region_id": "india"}},
        2026,
        {"aifs": live},
        output_dir,
    )

    # Only the live files come from the forecast job; the blend holds the rest.
    assert applied == [{"aifs": live}]
    weekly = (output_dir / workflow.WEEKLY_FORECAST_FILENAME).read_text()
    assert weekly.splitlines() == ["id,time,cv_week1", "10.00_40.00,2026-06-01,0.5"]
    # The day-level blend's probabilities by lead day, when the blend has one.
    daily = output_dir / workflow.DAILY_FORECAST_FILENAME
    assert daily.exists() is day_level
    if day_level:
        assert daily.read_text().splitlines() == [
            "id,time,cv_week1,p_day_1,p_later",
            "10.00_40.00,2026-06-01,0.5,0.25,0.75",
        ]


def test_forecast_workloads_see_the_package_and_nothing_else_beside_it(tmp_path) -> None:
    """Installed, the package's parent is the server's site-packages; on a
    forecast env's path its numpy, built for the server's Python, shadowed the
    env's own and the first local forecast failed to import it."""
    import ai_almanac
    from ai_almanac.server.services.job_workload import _package_path

    path = _package_path(tmp_path / "package-path")

    assert [entry.name for entry in path.iterdir()] == ["ai_almanac"]
    assert (path / "ai_almanac").resolve() == Path(ai_almanac.__file__).parent.resolve()
    assert _package_path(path) == path  # a second phase reuses it


def test_the_package_imports_where_it_is_not_installed(tmp_path) -> None:
    """A workload env has the package on its path without its metadata."""
    import subprocess

    import ai_almanac
    from ai_almanac.server.services.job_workload import _package_path

    path = _package_path(tmp_path / "package-path")
    # -S: no site-packages, so no installed ai-almanac metadata either.
    result = subprocess.run(
        [sys.executable, "-S", "-c", "import ai_almanac; print(ai_almanac.__version__)"],
        env={"PYTHONPATH": str(path)},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0+unknown"
    assert ai_almanac.__version__ != "0+unknown"

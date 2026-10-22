from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai_almanac.envs.blend_entrypoint import _load_workflow, run


def _archive(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _built_intermediates(root: Path, combined: bytes) -> dict:
    """What build_intermediates_from_dirs returns in-process: its output dir."""
    output_dir = root / "intermediates"
    output_dir.mkdir()
    (output_dir / "combined_wide.pkl").write_bytes(combined)
    return {"manifest": {}, "outputs_tar": None, "output_dir": str(output_dir)}


class _LocalFunction:
    def __init__(self, result: dict):
        self.result = result
        self.calls: list[tuple[tuple, dict]] = []

    def local(self, *args, **kwargs):  # noqa: ANN002, ANN003
        self.calls.append((args, kwargs))
        return self.result

    __call__ = local


def test_blending_workflow_loads_with_local_function_wrappers(monkeypatch) -> None:
    monkeypatch.setenv("ALMANAC_BLENDING_ROOT", "/tmp/onset-blending")
    workflow = _load_workflow()

    assert Path("/tmp/onset-blending") == workflow.BLENDING_ROOT
    assert hasattr(workflow.build_lat_lon_intermediates_bundle, "local")


@pytest.mark.parametrize("day_level", [False, True])
def test_local_blend_stages_inputs_trains_and_publishes_artifacts(
    tmp_path: Path, day_level: bool
) -> None:
    obs_dir = tmp_path / "obs"
    model_dir = tmp_path / "aifs"
    obs_dir.mkdir()
    model_dir.mkdir()
    (obs_dir / "2023.nc").write_bytes(b"obs")
    forecast_path = model_dir / "2023.nc"
    forecast_path.write_bytes(b"forecast")
    blending_root = tmp_path / "onset-blending"
    source_marker = blending_root / "python" / "prepare_data" / "nc_utils.py"
    source_marker.parent.mkdir(parents=True)
    source_marker.write_text("")

    combined = b"combined-data"
    built = _built_intermediates(tmp_path, combined)
    prepare = _LocalFunction(built)
    train = _LocalFunction(
        {
            "manifest": {"ok": True},
            "outputs_tar": _archive(
                {
                    "manifest.json": json.dumps({"ok": True}).encode(),
                    "weights.pkl": b"weights",
                }
            ),
        }
    )
    day_level_calls = []

    def train_day_level_bundle(combined_wide_path, model_names, **kwargs):
        day_level_calls.append((combined_wide_path, model_names, kwargs))
        out = tmp_path / "day-level-out"
        out.mkdir()
        (out / "forest.pkl").write_bytes(b"forest")
        return {"manifest": {}, "output_dir": str(out)}

    workflow = SimpleNamespace(
        BLENDING_ROOT=blending_root,
        LOCAL_INTERMEDIATE_RESOURCES=_load_workflow().LOCAL_INTERMEDIATE_RESOURCES,
        _bundle_files=lambda files: [path.name for path in files],
        _extract_bundle=lambda bundle: bundle,
        _read_tar_member_bytes=lambda data, name: combined,
        _parse_years=lambda value: [int(year) for year in value.split(",") if year],
        build_blend_intermediates=prepare,
        train_blending_model_bundle=train,
        train_day_level_bundle=train_day_level_bundle,
    )
    layouts = {
        "aifs": {"forecast_dims": {"step": "prediction_timedelta_daily"}, "unit_cvt": 1000.0}
    }
    output_dir = tmp_path / "output"
    config = {
        "obs_dir": str(obs_dir),
        "model_names": ["aifs"],
        "model_files": {"aifs": [str(forecast_path)]},
        "model_layouts": layouts,
        "region_id": "ethiopia",
        "blend_params": {
            "training_years": "2020,2021",
            "cv_holdout_years": "2022",
            "threshold_mm": 25.0,
        },
        "cache_dir": str(tmp_path / "blend-intermediates"),
        "train_day_level_blend": day_level,
    }

    run(config, output_dir, workflow)

    prep_args, prep_kwargs = prepare.calls[0]
    assert prep_args == (["2023.nc"], {"aifs": ["2023.nc"]}, config["blend_params"])
    assert prep_kwargs["cache_dir"] == str(tmp_path / "blend-intermediates")
    assert prep_kwargs["model_layouts"] == layouts
    assert prep_kwargs["region_id"] == "ethiopia"
    _, train_kwargs = train.calls[0]
    # Training reads the combined table from the build's output dir, not bytes.
    assert train_kwargs["combined_wide_path"] == str(
        Path(built["output_dir"]) / "combined_wide.pkl"
    )
    assert train_kwargs["model_names"] == ["aifs"]
    assert train_kwargs["training_years"] == [2020, 2021]
    assert train_kwargs["cv_holdout_years"] == [2022]
    assert (output_dir / "combined_wide.pkl").read_bytes() == combined
    assert (output_dir / "weights.pkl").read_bytes() == b"weights"
    # The day-level blend trains from the same combined table, after the weekly one.
    assert (output_dir / "forest.pkl").exists() is day_level
    if day_level:
        [(combined_wide_path, model_names, kwargs)] = day_level_calls
        assert combined_wide_path == str(output_dir / "combined_wide.pkl")
        assert model_names == ["aifs"]
        assert kwargs["cv_holdout_years"] == [2022]
        assert kwargs["cache_dir"] == str(tmp_path / "blend-intermediates")
    else:
        assert day_level_calls == []


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


def test_a_failed_local_day_level_blend_leaves_the_weekly_blend_published(
    tmp_path: Path, capsys
) -> None:
    from ai_almanac.envs.blend_entrypoint import _train_day_level

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "weights.pkl").write_bytes(b"weights")

    def failing_bundle(*args, **kwargs):
        raise RuntimeError("forest exploded")

    workflow = SimpleNamespace(train_day_level_bundle=failing_bundle)

    _train_day_level(workflow, output_dir / "combined_wide.pkl", ["aifs"], {}, {}, output_dir)

    assert sorted(path.name for path in output_dir.iterdir()) == ["weights.pkl"]
    assert "forest exploded" in capsys.readouterr().err

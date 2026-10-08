from __future__ import annotations

import io
import json
import os
import tarfile
from pathlib import Path
from types import SimpleNamespace

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


def test_blending_workflow_loads_with_local_function_wrappers(monkeypatch) -> None:
    monkeypatch.setenv("ALMANAC_BLENDING_ROOT", "/tmp/onset-blending")
    workflow = _load_workflow()

    assert Path("/tmp/onset-blending") == workflow.BLENDING_ROOT
    assert hasattr(workflow.build_lat_lon_intermediates_bundle, "local")


def test_local_blend_stages_inputs_trains_and_publishes_artifacts(tmp_path: Path) -> None:
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
    workflow = SimpleNamespace(
        BLENDING_ROOT=blending_root,
        _bundle_files=lambda files: [path.name for path in files],
        _read_tar_member_bytes=lambda data, name: combined,
        _parse_years=lambda value: [int(year) for year in value.split(",") if year],
        build_lat_lon_intermediates_bundle=prepare,
        train_blending_model_bundle=train,
    )
    output_dir = tmp_path / "output"
    config = {
        "obs_dir": str(obs_dir),
        "model_names": ["aifs"],
        "model_files": {"aifs": [str(forecast_path)]},
        "blend_params": {
            "training_years": "2020,2021",
            "cv_holdout_years": "2022",
            "threshold_mm": 25.0,
        },
        "cache_dir": str(tmp_path / "blend-intermediates"),
    }

    run(config, output_dir, workflow)

    prep_args, prep_kwargs = prepare.calls[0]
    assert prep_args == (["2023.nc"], {"aifs": ["2023.nc"]})
    assert prep_kwargs == {
        "return_outputs": False,
        "threshold_mm": 25.0,
        "cache_dir": str(tmp_path / "blend-intermediates"),
        "model_layouts": None,
    }
    train_args, train_kwargs = train.calls[0]
    # Training reads the combined table from the build's output dir, not bytes.
    assert train_args == (None,)
    assert train_kwargs["combined_wide_path"] == str(
        Path(built["output_dir"]) / "combined_wide.pkl"
    )
    assert train_kwargs["model_names"] == ["aifs"]
    assert train_kwargs["training_years"] == [2020, 2021]
    assert train_kwargs["cv_holdout_years"] == [2022]
    assert train_kwargs["cores"] == (os.cpu_count() or 1)
    assert (output_dir / "combined_wide.pkl").read_bytes() == combined
    assert (output_dir / "weights.pkl").read_bytes() == b"weights"


def test_local_blend_passes_job_inputs_to_intermediate_builder(tmp_path: Path) -> None:
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
    prepare = _LocalFunction(_built_intermediates(tmp_path, combined))
    train = _LocalFunction({"manifest": {"ok": True}, "outputs_tar": _archive({})})
    workflow = SimpleNamespace(
        BLENDING_ROOT=blending_root,
        _bundle_files=lambda files: [path.name for path in files],
        _read_tar_member_bytes=lambda data, name: combined,
        _parse_years=lambda value: [int(year) for year in value.split(",") if year],
        build_lat_lon_intermediates_bundle=prepare,
        train_blending_model_bundle=train,
    )
    layouts = {
        "aifs": {"forecast_dims": {"step": "prediction_timedelta_daily"}, "unit_cvt": 1000.0}
    }

    run(
        {
            "obs_dir": str(obs_dir),
            "model_names": ["aifs"],
            "model_files": {"aifs": [str(forecast_path)]},
            "model_layouts": layouts,
            "region_id": "ethiopia",
            "blend_params": {"training_years": "2020", "cv_holdout_years": "2021"},
        },
        tmp_path / "output",
        workflow,
    )

    _, prep_kwargs = prepare.calls[0]
    assert prep_kwargs["region_id"] == "ethiopia"
    assert prep_kwargs["model_layouts"] == layouts


def test_intermediate_prep_kwargs_reads_legacy_mok_month_day() -> None:
    from ai_almanac.envs.blend_entrypoint import intermediate_prep_kwargs

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
        {"aifs": live},
        output_dir,
    )
    return workflow, output_dir, calls


def test_local_forecast_writes_daily_scores_when_the_blend_has_a_day_level_model(
    tmp_path: Path, monkeypatch
) -> None:
    probe = _load_workflow()
    workflow, output_dir, calls = _local_forecast_scoring(
        tmp_path,
        monkeypatch,
        {probe.FINAL_COEF_FILENAME: b"coefs", probe.FOREST_MODEL_FILENAME: b"forest"},
    )

    assert calls[0]["coef_pkl"] == b"coefs"
    assert calls[0]["forest_pkl"] == b"forest"
    assert (output_dir / workflow.WEEKLY_FORECAST_FILENAME).read_bytes() == b"weekly"
    assert (output_dir / workflow.DAILY_FORECAST_FILENAME).read_bytes() == b"daily"


def test_local_forecast_from_an_older_blend_writes_weekly_scores_only(
    tmp_path: Path, monkeypatch
) -> None:
    probe = _load_workflow()
    workflow, output_dir, calls = _local_forecast_scoring(
        tmp_path, monkeypatch, {probe.FINAL_COEF_FILENAME: b"coefs"}
    )

    assert calls[0]["forest_pkl"] is None
    assert (output_dir / workflow.WEEKLY_FORECAST_FILENAME).read_bytes() == b"weekly"
    assert not (output_dir / workflow.DAILY_FORECAST_FILENAME).exists()

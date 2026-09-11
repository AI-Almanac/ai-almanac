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
    prepare = _LocalFunction({"outputs_tar": _archive({"combined_wide.pkl": combined})})
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
        "return_outputs": True,
        "threshold_mm": 25.0,
        "cache_dir": str(tmp_path / "blend-intermediates"),
    }
    _, train_kwargs = train.calls[0]
    assert train_kwargs["model_names"] == ["aifs"]
    assert train_kwargs["training_years"] == [2020, 2021]
    assert train_kwargs["cv_holdout_years"] == [2022]
    assert train_kwargs["cores"] == (os.cpu_count() or 1)
    assert (output_dir / "combined_wide.pkl").read_bytes() == combined
    assert (output_dir / "weights.pkl").read_bytes() == b"weights"


def test_local_blend_passes_region_to_intermediate_builder(tmp_path: Path) -> None:
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
    prepare = _LocalFunction({"outputs_tar": _archive({"combined_wide.pkl": combined})})
    train = _LocalFunction({"manifest": {"ok": True}, "outputs_tar": _archive({})})
    workflow = SimpleNamespace(
        BLENDING_ROOT=blending_root,
        _bundle_files=lambda files: [path.name for path in files],
        _read_tar_member_bytes=lambda data, name: combined,
        _parse_years=lambda value: [int(year) for year in value.split(",") if year],
        build_lat_lon_intermediates_bundle=prepare,
        train_blending_model_bundle=train,
    )

    run(
        {
            "obs_dir": str(obs_dir),
            "model_names": ["aifs"],
            "model_files": {"aifs": [str(forecast_path)]},
            "region_id": "ethiopia",
            "blend_params": {"training_years": "2020", "cv_holdout_years": "2021"},
        },
        tmp_path / "output",
        workflow,
    )

    _, prep_kwargs = prepare.calls[0]
    assert prep_kwargs["region_id"] == "ethiopia"

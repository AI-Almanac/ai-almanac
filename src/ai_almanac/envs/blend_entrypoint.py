"""Run a blend inside its managed Pixi environment.

Training goes through onset_blending (`run`): the job config becomes a
BlendConfig (envs/blend_recipe.py), and every stage stays in memory as
arrays, with no wide intermediate tables; the day-level blend
(train_day_level_blend) is its random forest, trained and scored with the
rest. The Modal workflow (modal/blending_app.py) is still loaded, with a local
stand-in for modal, for what has not moved: unit centroids, and scoring blends
trained before onset_blending.
"""

from __future__ import annotations

import argparse
import atexit
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from types import ModuleType, SimpleNamespace

try:  # imported as ai_almanac.envs.blend_entrypoint
    from . import blend_recipe
except ImportError:  # run as a script inside the blending env: its directory is on sys.path
    import blend_recipe


class _LocalFunction:
    def __init__(self, function):
        self._function = function

    def local(self, *args, **kwargs):
        return self._function(*args, **kwargs)


class _LocalApp:
    def __init__(self, name: str):
        self.name = name

    def function(self, **_options):
        return lambda function: _LocalFunction(function)

    def local_entrypoint(self):
        return lambda function: function


class _NoopBuilder:
    def __getattr__(self, _name):
        return lambda *args, **kwargs: self


def _local_modal_module() -> ModuleType:
    module = ModuleType("modal")
    module.App = _LocalApp
    module.Image = SimpleNamespace(debian_slim=lambda **_kwargs: _NoopBuilder())
    module.Secret = SimpleNamespace(from_name=lambda _name: object())
    return module


def _workflow_path() -> Path:
    packaged = Path(__file__).with_name("blending_app.py")
    if packaged.exists():
        return packaged
    source_checkout = Path(__file__).parents[3] / "modal" / "blending_app.py"
    if source_checkout.exists():
        return source_checkout
    raise FileNotFoundError("The bundled blending workflow could not be found")


WORKFLOW_MODULE = "ai_almanac_blending_workflow"

# Stands on sys.path under the workflow's module name, so the workers its
# process pools spawn can re-import it by name, as they do on Modal.
_WORKFLOW_LOADER = '''"""The blending workflow under this module's name; see blend_entrypoint._load_workflow."""
import importlib.util as _util
import sys as _sys

_spec = _util.spec_from_file_location("_ai_almanac_blend_entrypoint", {entrypoint!r})
_entry = _util.module_from_spec(_spec)
_spec.loader.exec_module(_entry)
_entry._exec_workflow(_sys.modules[__name__])
'''
_loader_dir: Path | None = None


def _exec_workflow(module: ModuleType) -> None:
    """Run the workflow's source in `module`, with the local stand-in for modal."""
    path = _workflow_path()
    previous_modal = sys.modules.get("modal")
    sys.modules["modal"] = _local_modal_module()
    try:
        module.__file__ = str(path)
        exec(compile(path.read_text(), str(path), "exec"), module.__dict__)
    finally:
        if previous_modal is None:
            sys.modules.pop("modal", None)
        else:
            sys.modules["modal"] = previous_modal


def _workflow_loader_dir() -> Path:
    """A directory holding just the workflow's loader, on sys.path (which spawned
    workers inherit); created once per process."""
    global _loader_dir
    if _loader_dir is None:
        _loader_dir = Path(tempfile.mkdtemp(prefix="blend-workflow-"))
        loader = _WORKFLOW_LOADER.format(entrypoint=str(Path(__file__).resolve()))
        (_loader_dir / f"{WORKFLOW_MODULE}.py").write_text(loader)
        atexit.register(shutil.rmtree, _loader_dir, ignore_errors=True)
        sys.path.append(str(_loader_dir))
    return _loader_dir


def _load_workflow() -> ModuleType:
    """A fresh copy of the workflow module, importable by name.

    Its process pools spawn workers that re-import the module by name: the
    loader on sys.path lets them, where a module loaded only from a path would
    leave them unable to (and the pools to run serially).
    """
    loader = _workflow_loader_dir() / f"{WORKFLOW_MODULE}.py"
    spec = importlib.util.spec_from_file_location(WORKFLOW_MODULE, loader)
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load the blending workflow")
    module = importlib.util.module_from_spec(spec)
    sys.modules[WORKFLOW_MODULE] = module
    _exec_workflow(module)
    return module


def _netcdf_files(directory: str) -> list[Path]:
    root = Path(directory).expanduser()
    if not root.is_dir():
        raise FileNotFoundError(f"NetCDF input directory does not exist: {root}")
    files = sorted(root.glob("*.nc"))
    if not files:
        raise FileNotFoundError(f"No .nc files found in {root}")
    return files


def _forecast_files(config: dict, model_name: str) -> list[Path]:
    values = (config.get("model_files") or {}).get(model_name) or []
    files = [Path(value).expanduser() for value in values]
    missing = [str(path) for path in files if not path.is_file()]
    if not files:
        raise ValueError(f"No forecast files configured for model {model_name!r}")
    if missing:
        raise FileNotFoundError("Forecast files do not exist: " + ", ".join(missing))
    return files


TRAINED_BLEND = "trained.blend"


def blending_root() -> Path:
    """The pinned blending checkout: the ADM3 district weights, and the code the
    workflow's remaining steps run."""
    return Path(os.environ.get("ALMANAC_BLENDING_ROOT", "/opt/onset_blending"))


def run(config: dict, output_dir: Path) -> None:
    """Train a blend with onset_blending: its evaluation files (the summaries
    the app reads, the day-level blend's rows among them), the trained blend
    live forecasts apply (TRAINED_BLEND) and its recipe (config.yml), into
    output_dir."""
    from onset_blending import BlendConfig
    from onset_blending.training import Resources, train

    # The recipe's own inputs (area outlines, unit centroids) live with the job,
    # not among its published outputs.
    recipe_dir = Path(output_dir).parent / "blend-recipe"
    recipe = blend_recipe.blend_config(config, work_dir=recipe_dir, blending_root=blending_root())
    print("==> Training the blend", flush=True)
    # The app's cache keeps what outlives a run: thresholds computed from the
    # observations (Moron-Robertson's), reused while their inputs are unchanged.
    resources = Resources.default(cache_dir=config.get("cache_dir"))
    result = train(BlendConfig.from_dict(recipe), resources=resources)
    timings = ", ".join(
        f"{stage} {seconds:.0f}s" for stage, seconds in result.inputs.timings.items()
    )
    print(f"==> Trained: {timings}", flush=True)
    result.save(output_dir, intermediates=())
    _attach_unit_centroids(output_dir)
    print("==> Model blending complete", flush=True)


def _attach_unit_centroids(output_dir: Path) -> None:
    """Named units (ADM3 districts) in the per-point summaries get a centroid,
    so the skill map can place them, as the workflow publishes them."""
    workflow = None
    for path in sorted(Path(output_dir).glob("summary_models_*.csv")):
        if path.name.startswith("summary_models_pooled"):
            continue
        workflow = workflow or _load_workflow()
        path.write_bytes(workflow._with_area_centroids(path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    run(config, args.output_dir)


if __name__ == "__main__":
    main()

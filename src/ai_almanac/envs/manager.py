"""Pixi-managed benchmark environment.

The benchmark environment carries the heavy ML deps (torch+CUDA, earth2studio,
ROMP, NetCDF/HDF5/GDAL) separately from the ai-almanac web server's own
environment. This keeps `pip install ai-almanac` small and stops a torch
upgrade from breaking FastAPI's deps.

The env lives at `$AI_ALMANAC_DATA_DIR/benchmark-env/` and is prepared on
first use via `ai-almanac env prepare`.
"""

from __future__ import annotations

import shutil
import subprocess
from importlib.resources import files
from pathlib import Path

from ai_almanac.paths import benchmark_env_dir


def _pixi_spec() -> Path:
    """Return the packaged `benchmark.pixi.toml` path."""
    spec = files("ai_almanac.envs").joinpath("benchmark.pixi.toml")
    return Path(str(spec))


def _require_pixi() -> str:
    pixi = shutil.which("pixi")
    if not pixi:
        raise RuntimeError(
            "pixi is not installed. Install it from https://pixi.sh and re-run."
        )
    return pixi


def ensure_env() -> Path:
    """Idempotently prepare the benchmark env. Returns the env directory."""
    pixi = _require_pixi()
    env_dir = benchmark_env_dir()
    env_dir.mkdir(parents=True, exist_ok=True)

    # If pixi.toml doesn't exist in the env dir, copy the packaged spec in so
    # users can edit it for local overrides if they want to.
    target_spec = env_dir / "pixi.toml"
    if not target_spec.exists():
        target_spec.write_text(_pixi_spec().read_text())

    subprocess.run(
        [pixi, "install", "--manifest-path", str(target_spec)],
        check=True,
    )
    return env_dir


def run(cmd: list[str], env: dict[str, str] | None = None) -> subprocess.Popen:
    """Spawn a process inside the benchmark env. Streams stdout/stderr."""
    pixi = _require_pixi()
    env_dir = benchmark_env_dir()
    full_cmd = [pixi, "run", "--manifest-path", str(env_dir / "pixi.toml"), "--", *cmd]
    return subprocess.Popen(
        full_cmd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )


def env_versions() -> dict[str, str]:
    """Report installed versions of the key packages in the benchmark env."""
    pixi = _require_pixi()
    env_dir = benchmark_env_dir()
    if not (env_dir / "pixi.toml").exists():
        return {"<env>": "not prepared — run `ai-almanac env prepare`"}

    proc = subprocess.run(
        [
            pixi,
            "run",
            "--manifest-path",
            str(env_dir / "pixi.toml"),
            "--",
            "python",
            "-c",
            "import importlib.metadata as m, sys;"
            "names=['torch','earth2studio','ROMP','xarray','netcdf4','cdsapi'];"
            "[print(n, m.version(n) if m.distribution else '?') "
            "for n in names]",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    versions: dict[str, str] = {}
    for line in (proc.stdout or "").splitlines():
        if " " in line:
            name, ver = line.split(None, 1)
            versions[name] = ver
    if not versions:
        versions["<env>"] = "prepared, but no packages found"
    return versions

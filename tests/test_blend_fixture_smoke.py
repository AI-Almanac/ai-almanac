"""Opt-in smoke run of the local blend builder on the checked-in Ethiopia fixtures.

Runs inside the real managed blending environment, so it is skipped unless
AI_ALMANAC_BLENDING_SMOKE_ENV names that environment's directory, e.g.

    AI_ALMANAC_BLENDING_SMOKE_ENV="$HOME/Library/Application Support/ai-almanac/blending-env" \\
        .pixi/envs/dev/bin/python -m pytest tests/test_blend_fixture_smoke.py

The fixtures cover three years, so the run stops short of training: the
climatology has no finite values on so short a record. Getting a combined
table out of them is what keeps the builder's local path exercised.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parents[1]
FIXTURES = REPO_ROOT / "testdata" / "ethiopia"
BLENDING_ENV = os.environ.get("AI_ALMANAC_BLENDING_SMOKE_ENV")

pytestmark = pytest.mark.skipif(
    not BLENDING_ENV or shutil.which("pixi") is None,
    reason="set AI_ALMANAC_BLENDING_SMOKE_ENV to the managed blending env and put pixi on PATH",
)

# Prints the combined table's row count and cell count as JSON.
_DRIVER = """
import json, pickle, sys
sys.path.insert(0, "src/ai_almanac/envs")
import blend_entrypoint
config = json.loads(sys.argv[1])
path = blend_entrypoint.build_combined_table(config, blend_entrypoint._load_workflow())
table = pickle.loads(path.read_bytes())
print(json.dumps({"rows": len(table), "cells": int(table["id"].nunique())}))
"""


def _fixture_config(cache_dir: Path) -> dict:
    return {
        "obs_dir": str(FIXTURES / "obs"),
        "model_names": ["fuxi"],
        "model_files": {"fuxi": [str(path) for path in sorted((FIXTURES / "fuxi").glob("*.nc"))]},
        "blend_params": {
            "min_onset_years": 1,
            "training_years": "1998,1999",
            "cv_holdout_years": "2000",
        },
        "cache_dir": str(cache_dir),
    }


def test_the_ethiopia_fixtures_build_a_combined_table(tmp_path: Path) -> None:
    env_dir = Path(str(BLENDING_ENV))
    result = subprocess.run(
        [
            "pixi",
            "run",
            "--manifest-path",
            str(env_dir / "pixi.toml"),
            "--",
            "python",
            "-c",
            _DRIVER,
            json.dumps(_fixture_config(tmp_path / "cache")),
        ],
        cwd=REPO_ROOT,
        env={**os.environ, "ALMANAC_BLENDING_ROOT": str(env_dir / "onset-blending")},
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )

    assert result.returncode == 0, result.stderr[-4000:]
    summary = json.loads(result.stdout.strip().splitlines()[-1])
    assert summary["rows"] > 0
    assert summary["cells"] == 25

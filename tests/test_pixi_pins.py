"""The container image ships the pixi that installs the workload environments.

It must be the version `pixi_bootstrap` downloads for native installs, so both
run the env manifests with the same pixi (newer releases reject syntax they use).
"""

from __future__ import annotations

import re
from pathlib import Path

from ai_almanac.envs.pixi_bootstrap import PIXI_VERSION

_DOCKERFILE = Path(__file__).resolve().parents[1] / "Dockerfile"


def test_image_runtime_pixi_matches_bootstrap_pin() -> None:
    match = re.search(r"^ARG PIXI_RUNTIME_VERSION=(\S+)$", _DOCKERFILE.read_text(), re.MULTILINE)
    assert match, f"PIXI_RUNTIME_VERSION not found in {_DOCKERFILE}"
    assert match.group(1) == PIXI_VERSION

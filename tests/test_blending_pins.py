"""The onset_blending checkout is pinned in two places — the local blend env
(`ai_almanac.envs.manager`) and the Modal image (`modal/blending_app.py`). They
drifted once (a99a5034 vs 8ba308eb), so local and cloud blends silently ran
different code. Keep them identical.

The packages are pinned once each: the local env's onset-blending (whose own
random forest is its day-level blend) in `envs/blending.pixi.toml`, and the
Modal workflow's onset-forest in `modal/blending_app.py`.

`modal/blending_app.py` imports `modal` at module scope, which the test env
need not have, so the Modal side is read from source text.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from ai_almanac.envs import manager

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BLENDING_APP = _REPO_ROOT / "modal" / "blending_app.py"


def _modal_constant(name: str) -> str:
    match = re.search(rf'^{name} = "([^"]+)"$', _BLENDING_APP.read_text(), re.MULTILINE)
    assert match, f"{name} not found in {_BLENDING_APP}"
    return match.group(1)


def test_blending_repo_pins_match() -> None:
    assert _modal_constant("DEFAULT_REPO_REF") == manager.BLENDING_REPO_REF
    assert _modal_constant("DEFAULT_REPO_URL") == manager.BLENDING_REPO_URL
    # Full SHAs keep `git fetch --depth 1 origin <ref>` deterministic; a branch
    # name or short SHA would make the env and image drift over time.
    assert re.fullmatch(r"[0-9a-f]{40}", manager.BLENDING_REPO_REF)


def test_the_local_blend_env_pins_onset_blending_alone_by_commit() -> None:
    manifest = tomllib.loads((_REPO_ROOT / "src/ai_almanac/envs/blending.pixi.toml").read_text())
    packages = manifest["pypi-dependencies"]
    assert "onset-forest" not in packages
    assert re.fullmatch(r"[0-9a-f]{40}", packages["onset-blending"]["rev"])


def test_the_modal_forest_pin_is_a_commit() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", _modal_constant("FOREST_REPO_REF"))

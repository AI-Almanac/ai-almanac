"""The onset_blending checkout and the onset-forest package are each pinned in
two places — the local blend env (`ai_almanac.envs.manager` and
`envs/blending.pixi.toml` respectively) and the Modal image
(`modal/blending_app.py`). They drifted once (a99a5034 vs 8ba308eb), so local
and cloud blends silently ran different code. Keep them identical.

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


def test_blending_repo_ref_is_a_full_sha() -> None:
    # Full SHAs keep `git fetch --depth 1 origin <ref>` deterministic; a branch
    # name or short SHA would make the env and image drift over time.
    assert re.fullmatch(r"[0-9a-f]{40}", manager.BLENDING_REPO_REF)


def _blending_env_forest_pin() -> tuple[str, str]:
    manifest = tomllib.loads((_REPO_ROOT / "src/ai_almanac/envs/blending.pixi.toml").read_text())
    forest = manifest["pypi-dependencies"]["onset-forest"]
    return forest["git"], forest["rev"]


def test_forest_pins_match() -> None:
    url, rev = _blending_env_forest_pin()
    assert _modal_constant("FOREST_REPO_URL") == url
    assert _modal_constant("FOREST_REPO_REF") == rev


def test_forest_ref_is_a_full_sha() -> None:
    assert re.fullmatch(r"[0-9a-f]{40}", _blending_env_forest_pin()[1])

from __future__ import annotations

from pathlib import Path

import pytest

from ai_almanac.envs.blend_entrypoint import _load_workflow


@pytest.fixture()
def workflow(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("ALMANAC_BLENDING_ROOT", str(tmp_path / "onset-blending"))
    return _load_workflow()


def _cache_files(cache_root: Path) -> list[Path]:
    return [path for path in cache_root.rglob("*.pkl") if path.is_file()]


def test_read_through_computes_once_then_hits(workflow, tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    calls = []

    def compute():
        calls.append(1)
        return {"rows": [1, 2, 3]}

    key = {"file_sha256": "abc", "threshold_mm": 20.0}
    first, first_cached = workflow._cached_pickle(str(cache_root), "obs", key, compute)
    second, second_cached = workflow._cached_pickle(str(cache_root), "obs", key, compute)

    assert (first, first_cached) == ({"rows": [1, 2, 3]}, False)
    assert (second, second_cached) == ({"rows": [1, 2, 3]}, True)
    assert len(calls) == 1
    assert len(_cache_files(cache_root)) == 1
    assert not [path for path in cache_root.rglob("*") if path.name.endswith(".tmp")]


@pytest.mark.parametrize(
    ("scope", "overrides"),
    [
        ("fc", {}),
        ("obs", {"threshold_mm": 25.0}),
        ("obs", {"file_sha256": "def"}),
    ],
)
def test_key_separation_by_scope_and_params(
    workflow, tmp_path: Path, scope: str, overrides: dict
) -> None:
    cache_root = str(tmp_path / "cache")
    key = {"file_sha256": "abc", "threshold_mm": 20.0}
    workflow._cached_pickle(cache_root, "obs", key, lambda: "original")

    _, was_cached = workflow._cached_pickle(cache_root, scope, {**key, **overrides}, lambda: "x")

    assert was_cached is False
    assert workflow._cached_pickle(cache_root, "obs", key, lambda: "recomputed") == (
        "original",
        True,
    )


@pytest.mark.parametrize(
    ("attribute", "bumped"),
    [("BLEND_INTERMEDIATES_CACHE_VERSION", 999), ("_blending_repo_ref", lambda: "f" * 40)],
)
def test_a_schema_or_science_code_bump_misses_the_cache(
    workflow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, attribute: str, bumped
) -> None:
    cache_root = str(tmp_path / "cache")
    key = {"file_sha256": "abc"}
    workflow._cached_pickle(cache_root, "obs", key, lambda: "old")

    monkeypatch.setattr(workflow, attribute, bumped)

    assert workflow._cached_pickle(cache_root, "obs", key, lambda: "new") == ("new", False)


def test_disabled_cache_always_computes(workflow, tmp_path: Path) -> None:
    calls = []

    def compute():
        calls.append(1)
        return "x"

    for _ in range(2):
        obj, was_cached = workflow._cached_pickle(None, "obs", {"k": 1}, compute)
        assert (obj, was_cached) == ("x", False)
    assert len(calls) == 2
    assert not list(tmp_path.rglob("*.pkl"))


def test_repo_ref_falls_back_without_git_checkout(workflow) -> None:
    ref = workflow._blending_repo_ref()
    assert ref == workflow.DEFAULT_REPO_REF


_ONSET_BASE = {
    "id_precision": 2,
    "threshold_mm": 20.0,
    "min_day": 1,
    "max_day": 45,
    "cutoff_month_day": "05-01",
    "ref_onset_month_day": "06-01",
    "adm3_domain": False,
    "focus_area": None,
}


def test_parts_are_not_reused_across_onset_definitions(workflow, tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    computed = []

    def build(**overrides) -> bool:
        key = workflow._intermediates_cache_params(**{**_ONSET_BASE, **overrides})
        _, was_cached = workflow._cached_pickle(
            str(cache_root), "obs", key, lambda: computed.append(overrides) or "part"
        )
        return was_cached

    assert build() is False
    assert build() is True
    assert build(threshold_mm=25.0) is False
    assert build(cutoff_month_day="05-15") is False
    assert build(ref_onset_month_day="06-10") is False
    assert len(computed) == 4


def test_a_change_to_the_fixed_onset_rule_misses_the_cache(
    workflow, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache_root = str(tmp_path / "cache")

    def was_cached() -> bool:
        key = workflow._intermediates_cache_params(**_ONSET_BASE)
        return workflow._cached_pickle(cache_root, "obs", key, lambda: "part")[1]

    assert was_cached() is False
    monkeypatch.setattr(workflow, "ONSET_RULE", {**workflow.ONSET_RULE, "changed": True})
    assert was_cached() is False

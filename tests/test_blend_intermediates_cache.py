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
    entry = _cache_files(cache_root)
    assert len(entry) == 1
    ref = workflow._blending_repo_ref()[:12]
    assert entry[0].relative_to(cache_root).parts[:3] == (
        f"v{workflow.BLEND_INTERMEDIATES_CACHE_VERSION}",
        ref,
        "obs",
    )


def test_key_separation_by_scope_and_params(workflow, tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    key = {"file_sha256": "abc", "threshold_mm": 20.0}

    workflow._cached_pickle(str(cache_root), "obs", key, lambda: "a")
    workflow._cached_pickle(str(cache_root), "fc", key, lambda: "b")
    workflow._cached_pickle(str(cache_root), "obs", {**key, "threshold_mm": 25.0}, lambda: "c")
    workflow._cached_pickle(str(cache_root), "obs", {**key, "file_sha256": "def"}, lambda: "d")

    assert len(_cache_files(cache_root)) == 4
    hit, was_cached = workflow._cached_pickle(str(cache_root), "obs", key, lambda: "recomputed")
    assert (hit, was_cached) == ("a", True)


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


def test_no_tmp_files_left_behind(workflow, tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    workflow._cached_pickle(str(cache_root), "clim", {"k": 1}, lambda: "x")
    assert not [path for path in cache_root.rglob("*") if path.name.endswith(".tmp")]


def test_file_sha256_matches_content(workflow, tmp_path: Path) -> None:
    import hashlib

    path = tmp_path / "2024.nc"
    path.write_bytes(b"netcdf-bytes")
    assert workflow._file_sha256(path) == hashlib.sha256(b"netcdf-bytes").hexdigest()


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
    "subdistrict_mapping_sha256": None,
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


def test_cache_key_records_the_fixed_onset_rule(workflow) -> None:
    key = workflow._intermediates_cache_params(**_ONSET_BASE)
    assert key["onset_rule"] == workflow.ONSET_RULE


def test_subdistrict_mapping_keys_parts_without_changing_grid_keys(workflow) -> None:
    grid = workflow._intermediates_cache_params(**_ONSET_BASE)
    # Grid keys match the ones cached before subdistricts were configurable.
    assert grid["adm3_domain"] is False
    assert "subdistrict_mapping_sha256" not in grid

    first = workflow._intermediates_cache_params(
        **{**_ONSET_BASE, "subdistrict_mapping_sha256": "aaa"}
    )
    second = workflow._intermediates_cache_params(
        **{**_ONSET_BASE, "subdistrict_mapping_sha256": "bbb"}
    )
    assert first["adm3_domain"] is True
    assert first != second


def test_subdistrict_files_resolve_inside_the_blending_checkout(workflow) -> None:
    mapping = workflow.BLENDING_ROOT / "Monsoon_Data" / "map.csv"
    mapping.parent.mkdir(parents=True)
    mapping.write_text("adm3_name,lat,lon,weight\n")

    assert workflow._subdistrict_mapping({"grid_mapping": "Monsoon_Data/map.csv"}) == mapping
    assert workflow._subdistrict_mapping(None) is None
    with pytest.raises(FileNotFoundError, match="missing from the blending checkout"):
        workflow._subdistrict_files({"grid_mapping": "Monsoon_Data/map.csv", "cells": "nope.csv"})

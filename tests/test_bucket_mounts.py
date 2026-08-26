"""Tests for bucket_mounts — mount-path ↔ gs:// translation at Modal dispatch."""

from __future__ import annotations

import pytest

from ai_almanac.server.services import bucket_mounts as bm


@pytest.fixture(autouse=True)
def _clear_mounts(monkeypatch: pytest.MonkeyPatch):
    """Start each test with no bucket_mounts and no shared_cache_dir."""
    monkeypatch.setattr("ai_almanac.settings.settings.bucket_mounts", {})
    monkeypatch.setattr("ai_almanac.settings.settings.shared_cache_dir", "")
    monkeypatch.setattr("ai_almanac.settings.settings.job_outputs_dir", "")


def _set_mounts(monkeypatch: pytest.MonkeyPatch, mounts: dict[str, str]) -> None:
    monkeypatch.setattr("ai_almanac.settings.settings.bucket_mounts", mounts)


# ---------------------------------------------------------------------------
# to_gs_uri — basic translation
# ---------------------------------------------------------------------------


def test_empty_string_returns_none() -> None:
    assert bm.to_gs_uri("") is None


def test_gs_uri_passes_through() -> None:
    assert bm.to_gs_uri("gs://bucket/path/file.nc") == "gs://bucket/path/file.nc"


def test_unmapped_path_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    assert bm.to_gs_uri("/other/path/file.nc") is None


def test_exact_mount_translates_to_bare_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    assert bm.to_gs_uri("/mnt/data") == "gs://data-bucket"


def test_subpath_appended_to_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    assert bm.to_gs_uri("/mnt/data/obs/2020.nc") == "gs://data-bucket/obs/2020.nc"


def test_trailing_slash_stripped_from_gs_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket/"})
    assert bm.to_gs_uri("/mnt/data/file.nc") == "gs://data-bucket/file.nc"


def test_longest_prefix_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(
        monkeypatch,
        {
            "/mnt/data": "gs://data-bucket",
            "/mnt/data/models": "gs://models-bucket",
        },
    )
    assert bm.to_gs_uri("/mnt/data/models/fuxi/2020.nc") == "gs://models-bucket/fuxi/2020.nc"


def test_shorter_mount_still_matches_other_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(
        monkeypatch,
        {
            "/mnt/data": "gs://data-bucket",
            "/mnt/data/models": "gs://models-bucket",
        },
    )
    assert bm.to_gs_uri("/mnt/data/obs/file.nc") == "gs://data-bucket/obs/file.nc"


# ---------------------------------------------------------------------------
# outputs_bucket_name
# ---------------------------------------------------------------------------


def test_outputs_bucket_name_no_mapping_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("ai_almanac.settings.settings.job_outputs_dir", "/mnt/outputs")
    assert bm.outputs_bucket_name() is None


def test_outputs_bucket_name_bare_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/outputs": "gs://outputs-bucket"})
    monkeypatch.setattr("ai_almanac.settings.settings.job_outputs_dir", "/mnt/outputs")
    assert bm.outputs_bucket_name() == "outputs-bucket"


def test_outputs_bucket_name_with_key_prefix_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A gs:// URI with a key prefix violates the bare-bucket invariant.
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    monkeypatch.setattr("ai_almanac.settings.settings.job_outputs_dir", "/mnt/data/outputs")
    assert bm.outputs_bucket_name() is None


# ---------------------------------------------------------------------------
# translate_job_config — benchmark job
# ---------------------------------------------------------------------------


def test_translate_benchmark_config(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    config = {
        "obs_dir": "/mnt/data/obs",
        "model_dir": "/mnt/data/models/fuxi",
    }
    result = bm.translate_job_config(config)
    assert result["obs_dir"] == "gs://data-bucket/obs"
    assert result["model_dir"] == "gs://data-bucket/models/fuxi"


def test_translate_does_not_mutate_original(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    original = {"obs_dir": "/mnt/data/obs", "model_dir": "/mnt/data/models/fuxi"}
    bm.translate_job_config(original)
    assert original["obs_dir"] == "/mnt/data/obs"
    assert original["model_dir"] == "/mnt/data/models/fuxi"


def test_translate_passes_through_existing_gs_uris(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    config = {"obs_dir": "gs://already/here", "model_dir": "gs://models/fuxi"}
    result = bm.translate_job_config(config)
    assert result["obs_dir"] == "gs://already/here"
    assert result["model_dir"] == "gs://models/fuxi"


def test_translate_unmapped_path_left_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    # Unmapped paths pass through; preflight catches them at dispatch time.
    config = {"obs_dir": "/local/obs", "model_dir": "gs://data/models"}
    result = bm.translate_job_config(config)
    assert result["obs_dir"] == "/local/obs"


# ---------------------------------------------------------------------------
# translate_job_config — blend job (model_files)
# ---------------------------------------------------------------------------


def test_translate_blend_model_files(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    config = {
        "obs_dir": "/mnt/data/obs",
        "model_files": {
            "aifs": ["/mnt/data/models/aifs/2019.nc", "/mnt/data/models/aifs/2020.nc"],
            "gencast": ["gs://other-bucket/gencast/2019.nc"],
        },
    }
    result = bm.translate_job_config(config)
    assert result["model_files"]["aifs"] == [
        "gs://data-bucket/models/aifs/2019.nc",
        "gs://data-bucket/models/aifs/2020.nc",
    ]
    assert result["model_files"]["gencast"] == ["gs://other-bucket/gencast/2019.nc"]


# ---------------------------------------------------------------------------
# translate_job_config — forecast job (blend_config_snapshot)
# ---------------------------------------------------------------------------


def test_translate_forecast_blend_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    config = {
        "job_type": "forecast",
        "blend_config_snapshot": {
            "obs_dir": "/mnt/data/obs",
            "model_files": {"aifs": ["/mnt/data/models/aifs/2019.nc"]},
        },
    }
    result = bm.translate_job_config(config)
    snapshot = result["blend_config_snapshot"]
    assert snapshot["obs_dir"] == "gs://data-bucket/obs"
    assert snapshot["model_files"]["aifs"] == ["gs://data-bucket/models/aifs/2019.nc"]


# ---------------------------------------------------------------------------
# cache URI injection
# ---------------------------------------------------------------------------


def test_no_cache_injection_without_shared_cache_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    config = {"job_type": "blend", "obs_dir": "/mnt/data/obs"}
    result = bm.translate_job_config(config)
    assert "cache_uri" not in result


def test_blend_cache_uri_injected_when_shared_cache_dir_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    monkeypatch.setattr("ai_almanac.settings.settings.shared_cache_dir", "/mnt/data/cache")
    config = {"job_type": "blend", "obs_dir": "/mnt/data/obs"}
    result = bm.translate_job_config(config)
    assert result.get("cache_uri") == "gs://data-bucket/cache/blend-intermediates"


def test_forecast_trajectory_cache_uri_injected(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    monkeypatch.setattr("ai_almanac.settings.settings.shared_cache_dir", "/mnt/data/cache")
    config = {
        "job_type": "forecast",
        "blend_config_snapshot": {"obs_dir": "/mnt/data/obs"},
    }
    result = bm.translate_job_config(config)
    assert result.get("trajectory_cache_uri") == "gs://data-bucket/cache/season-forecasts"


def test_blend_snapshot_gets_cache_uri(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    monkeypatch.setattr("ai_almanac.settings.settings.shared_cache_dir", "/mnt/data/cache")
    config = {
        "job_type": "forecast",
        "blend_config_snapshot": {
            "job_type": "blend",
            "obs_dir": "/mnt/data/obs",
        },
    }
    result = bm.translate_job_config(config)
    assert (
        result["blend_config_snapshot"].get("cache_uri")
        == "gs://data-bucket/cache/blend-intermediates"
    )


def test_cache_injection_skipped_when_shared_dir_unmapped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # shared_cache_dir set but NOT in bucket_mounts — no injection (to_gs_uri returns None).
    _set_mounts(monkeypatch, {"/mnt/data": "gs://data-bucket"})
    monkeypatch.setattr("ai_almanac.settings.settings.shared_cache_dir", "/other/cache")
    config = {"job_type": "blend"}
    result = bm.translate_job_config(config)
    assert "cache_uri" not in result

"""Mount-path ↔ gs:// URI translation for the Modal dispatch boundary.

Job configs are stored and passed through the system in mount-path form
(absolute paths that work for FUSE-mounted buckets in Cloud Run and plain
local paths in personal installs). At Modal dispatch time translate_job_config
converts those paths to gs:// URIs so Modal workers can reach them via the GCS
API. The translation is deterministic: re-applied on every dispatch from the
current bucket_mounts setting, so stored configs are always in canonical form.
"""

from __future__ import annotations

import copy
from pathlib import Path


def parsed_mounts() -> list[tuple[Path, str]]:
    """Sorted (mount_path, gs:// prefix) pairs, longest path first.

    Returns resolved Path objects so comparisons are canonical. The gs://
    prefix has no trailing slash; a bare-bucket mapping looks like 'gs://bucket'.
    """
    from ai_almanac.settings import settings

    mounts: dict[str, str] = settings.bucket_mounts or {}
    result: list[tuple[Path, str]] = []
    for raw_path, gs_uri in mounts.items():
        mount = Path(raw_path).expanduser().resolve()
        prefix = gs_uri.rstrip("/")
        result.append((mount, prefix))
    result.sort(key=lambda t: len(str(t[0])), reverse=True)
    return result


def to_gs_uri(value: str) -> str | None:
    """Translate a mount-path value to a gs:// URI, or None if unmapped.

    Already-gs:// values are returned unchanged. Absolute paths under a
    configured mount are translated; paths under no mount return None (preflight
    will surface them as an error at dispatch time).
    """
    if not value:
        return None
    if value.startswith("gs://"):
        return value
    path = Path(value).resolve()
    for mount, gs_prefix in parsed_mounts():
        if path == mount:
            return gs_prefix
        if path.is_relative_to(mount):
            rel = path.relative_to(mount)
            return f"{gs_prefix}/{rel.as_posix()}"
    return None


def outputs_bucket_name() -> str | None:
    """Bare bucket name for Modal dispatch, derived from job_outputs_dir.

    Returns None when the outputs dir is not mapped by bucket_mounts, or when
    it maps to a gs:// URI that includes a key prefix. The bare-bucket
    constraint exists because Modal workers join {job_id}/... directly onto the
    bucket root — a prefix would mis-route every write.
    """
    from ai_almanac.settings import settings

    gs_uri = to_gs_uri(settings.job_outputs_dir)
    if gs_uri is None:
        return None
    remainder = gs_uri.removeprefix("gs://")
    if "/" in remainder:
        return None  # has a key prefix — violates the bare-bucket invariant
    return remainder


def _translate_value(value: object) -> object:
    """Translate a single string value: pass through gs://, translate paths."""
    if not isinstance(value, str):
        return value
    translated = to_gs_uri(value)
    return translated if translated is not None else value


def _translate_config_inplace(config: dict) -> None:
    """Translate data-location keys in a config dict in place."""
    for key in ("obs_dir", "model_dir", "blend_output_uri"):
        if key in config:
            config[key] = _translate_value(config[key])
    if "model_files" in config and isinstance(config["model_files"], dict):
        config["model_files"] = {
            name: [_translate_value(uri) for uri in uris] if isinstance(uris, list) else uris
            for name, uris in config["model_files"].items()
        }


def _inject_cache_uris(config: dict, job_type: str) -> None:
    """Inject cache_uri / trajectory_cache_uri when shared_cache_dir is mapped."""
    from ai_almanac.settings import settings

    cache_dir = (settings.shared_cache_dir or "").strip()
    if not cache_dir:
        return
    if job_type == "blend":
        cache_uri = to_gs_uri(f"{cache_dir}/blend-intermediates")
        if cache_uri:
            config["cache_uri"] = cache_uri
    elif job_type == "forecast":
        traj_cache_uri = to_gs_uri(f"{cache_dir}/season-forecasts")
        if traj_cache_uri:
            config["trajectory_cache_uri"] = traj_cache_uri


def translate_job_config(config: dict) -> dict:
    """Return a translated deep copy of config for Modal dispatch.

    Translates obs_dir, model_dir, blend_output_uri, and model_files URIs from
    mount paths to gs:// URIs using the current bucket_mounts setting. Applies
    the same translation recursively into blend_config_snapshot. Injects
    cache_uri and trajectory_cache_uri when shared_cache_dir is configured.

    Already-gs:// values pass through unchanged. Values under no mount pass
    through unchanged and are caught by Modal preflight. The original config
    dict is never mutated.
    """
    out = copy.deepcopy(config)
    job_type = out.get("job_type", "")

    _translate_config_inplace(out)

    if isinstance(out.get("blend_config_snapshot"), dict):
        _translate_config_inplace(out["blend_config_snapshot"])
        _inject_cache_uris(out["blend_config_snapshot"], "blend")

    _inject_cache_uris(out, job_type)
    return out

"""Provenance primitives: hashing, chain sealing, and stamping downloads.

Pure functions over files and plain dicts; the database side lives in
``services/ledger.py``. Files are stored exactly as produced, so a content
hash is plain sha256 of the stored bytes. The chain is written only into the
copy a user downloads (``stamp_history``). The runtime stamps provenance,
never the model (P1), so anything a model-authored script wrote under the
history keys is stripped by ``strip_history`` before a file is registered.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any, BinaryIO

HISTORY_ATTR = "ai_almanac_history"
FOREIGN_HISTORY_ATTRS = ("weather_skills_history",)
_HISTORY_KEYS = (HISTORY_ATTR, *FOREIGN_HISTORY_ATTRS)
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_PNG_TEXT_CHUNKS = {b"tEXt", b"zTXt", b"iTXt"}
_CHUNK = 1 << 20

FORMAT_BY_SUFFIX = {
    ".nc": "netcdf",
    ".zarr": "zarr",
    ".png": "png",
    ".webp": "webp",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".csv": "csv",
    ".json": "json",
    ".geojson": "geojson",
    ".py": "python",
    ".txt": "text",
    ".md": "text",
}
KIND_BY_FORMAT = {
    "netcdf": "dataset",
    "zarr": "dataset",
    "png": "figure",
    "webp": "figure",
    "jpeg": "figure",
    "csv": "table",
    "json": "document",
    "geojson": "document",
    "python": "document",
    "text": "document",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def params_hash(params: dict) -> str:
    return sha256_text(canonical_json(params))


def step_hash(
    primitive: str,
    version: str,
    params_digest: str,
    inputs: Iterable[tuple[str, str]],
    env_digest: str | None,
    parent_hashes: list[str],
) -> str:
    """Seal a step. ``inputs`` are ``(role, content_hash)`` pairs, so swapping two
    inputs between roles changes the seal (D-R11)."""
    return sha256_text(
        canonical_json(
            [
                primitive,
                version,
                params_digest,
                sorted([role, digest] for role, digest in inputs),
                env_digest or "",
                sorted(parent_hashes),
            ]
        )
    )


def runtime_digest(runtime: dict) -> str | None:
    """The environment a step ran in: the analysis env or the job image."""
    return runtime.get("env_digest") or runtime.get("image_digest")


def format_of(path: Path | str) -> str:
    return FORMAT_BY_SUFFIX.get(Path(path).suffix.lower(), "binary")


def kind_of(path: Path | str) -> str:
    return KIND_BY_FORMAT.get(format_of(path), "document")


# --- content hashing ----------------------------------------------------------


def hash_stream(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := handle.read(_CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def content_hash(path: Path) -> str:
    with path.open("rb") as handle:
        return hash_stream(handle)


# --- PNG chunks ---------------------------------------------------------------


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(_PNG_SIGNATURE):
        raise ValueError("not a PNG file")
    chunks = []
    offset = len(_PNG_SIGNATURE)
    while offset + 8 <= len(data):
        (length,) = struct.unpack(">I", data[offset : offset + 4])
        chunk_type = data[offset + 4 : offset + 8]
        body = data[offset + 8 : offset + 8 + length]
        chunks.append((chunk_type, body))
        offset += 12 + length
    return chunks


def _png_bytes(chunks: list[tuple[bytes, bytes]]) -> bytes:
    out = bytearray(_PNG_SIGNATURE)
    for chunk_type, body in chunks:
        out += struct.pack(">I", len(body))
        out += chunk_type
        out += body
        out += struct.pack(">I", zlib.crc32(chunk_type + body) & 0xFFFFFFFF)
    return bytes(out)


def _is_history_text_chunk(chunk: tuple[bytes, bytes]) -> bool:
    chunk_type, body = chunk
    if chunk_type not in _PNG_TEXT_CHUNKS:
        return False
    keyword = body.split(b"\x00", 1)[0].decode("latin-1", "replace")
    return keyword in _HISTORY_KEYS


# --- stamping (download copies only) --------------------------------------------


def stamp_history(path: Path, history: list[dict]) -> bool:
    """Write the chain into ``path``, a download copy; False for formats we cannot stamp.

    ponytail: NetCDF and PNG only; JPEG EXIF and HTML <meta> when those formats appear.
    """
    fmt = format_of(path)
    payload = json.dumps(history, default=str)
    if fmt == "netcdf":
        import h5netcdf

        with h5netcdf.File(path, "a") as handle:
            handle.attrs[HISTORY_ATTR] = payload
        return True
    if fmt == "png":
        chunks = [c for c in _png_chunks(path.read_bytes()) if not _is_history_text_chunk(c)]
        text_chunk = (
            b"tEXt",
            HISTORY_ATTR.encode() + b"\x00" + payload.encode("latin-1", "replace"),
        )
        chunks.insert(len(chunks) - 1, text_chunk)
        path.write_bytes(_png_bytes(chunks))
        return True
    return False


def strip_history(path: Path) -> bool:
    """Remove any history attribute or chunk a script may have written (P1)."""
    fmt = format_of(path)
    if fmt == "netcdf":
        import h5netcdf

        removed = False
        with h5netcdf.File(path, "a") as handle:
            for key in _HISTORY_KEYS:
                if key in handle.attrs:
                    del handle.attrs[key]
                    removed = True
        return removed
    if fmt == "png":
        chunks = _png_chunks(path.read_bytes())
        kept = [c for c in chunks if not _is_history_text_chunk(c)]
        if len(kept) != len(chunks):
            path.write_bytes(_png_bytes(kept))
            return True
    return False


# --- summaries ----------------------------------------------------------------


def summarize(path: Path) -> dict:
    """Shape-level description a model can reason about without seeing data."""
    fmt = format_of(path)
    if fmt == "netcdf":
        return _summarize_netcdf(path)
    if fmt in ("png", "webp", "jpeg"):
        return _summarize_image(path)
    return {"size_bytes": path.stat().st_size}


def _without_history(attrs: dict) -> dict:
    return {key: value for key, value in attrs.items() if key not in _HISTORY_KEYS}


def _summarize_netcdf(path: Path) -> dict:
    from ai_almanac.server.services import netcdf

    with netcdf.open_dataset(path, engine="h5netcdf") as ds:
        coords = {}
        for name, coord in ds.coords.items():
            if coord.ndim != 1 or coord.size == 0:
                continue
            values = coord.values
            coords[name] = {
                "size": int(coord.size),
                "min": str(values.min()),
                "max": str(values.max()),
            }
        return {
            "dims": {name: int(size) for name, size in ds.sizes.items()},
            "coords": coords,
            "variables": {
                name: {
                    "dims": list(var.dims),
                    "dtype": str(var.dtype),
                    "units": var.attrs.get("units"),
                    "long_name": var.attrs.get("long_name"),
                }
                for name, var in ds.data_vars.items()
            },
            "attrs": {k: str(v)[:200] for k, v in _without_history(dict(ds.attrs)).items()},
        }


def _summarize_image(path: Path) -> dict:
    from PIL import Image

    with Image.open(path) as image:
        return {"width": image.width, "height": image.height, "mode": image.mode}


# --- rendering ----------------------------------------------------------------


def history_entry(step: dict) -> dict:
    """The per-step record stamped into a downloaded file (D3). Holds no labels."""
    return {
        "step": step["id"],
        "primitive": step["primitive"],
        "version": step["primitive_version"],
        "params": step["params"],
        "inputs": [{"role": item["role"], "hash": item["content_hash"]} for item in step["inputs"]],
        "step_hash": step["step_hash"],
    }


def render_lineage(
    artifact: dict,
    steps_by_id: dict[str, dict],
    artifacts_by_id: dict[str, dict],
    names: dict[str, str],
    status: dict[str, str],
) -> str:
    """Tree rendering of an artifact's chain, inputs indented under their consumers.

    ``names`` maps artifact ids to the handle shown for them (a workspace label,
    else the filename). Each file is traced through the step that first produced
    it; other producers of the same bytes are listed on the file's line (D-R6).
    """
    lines: list[str] = []

    def name(art: dict) -> str:
        return names.get(art["id"], art["filename"])

    def describe_step(step: dict) -> str:
        params = ", ".join(
            f"{k}={v}" for k, v in sorted(step["params"].items()) if not isinstance(v, (dict, list))
        )
        badge = status.get(step["id"], "")
        tag = " (unverified computation)" if step["primitive"] == "code.run_code" else ""
        turn = f"  [turn {step['turn_id'][:8]}]" if step.get("turn_id") else ""
        return f"{step['primitive']} v{step['primitive_version']}{tag} — {params}{turn} {badge}".rstrip()

    def also_by(art: dict) -> str:
        others = [
            s["primitive"]
            for s in steps_by_id.values()
            if art["id"] in s["outputs"] and s["id"] != art["provenance_step_id"]
        ]
        return f"  [also produced by {', '.join(others)}]" if others else ""

    def inputs_of(step: dict) -> list[dict]:
        return [
            artifacts_by_id[i["artifact_id"]]
            for i in step["inputs"]
            if i["artifact_id"] in artifacts_by_id
        ]

    def walk(art: dict, prefix: str, is_last: bool) -> None:
        connector = "└─ " if is_last else "├─ "
        lines.append(f"{prefix}{connector}{name(art)}  ({art['kind']}){also_by(art)}")
        step = steps_by_id.get(art["provenance_step_id"])
        if step is None:
            return
        child_prefix = prefix + ("   " if is_last else "│  ")
        lines.append(f"{child_prefix}← {describe_step(step)}")
        parents = inputs_of(step)
        for index, parent in enumerate(parents):
            walk(parent, child_prefix, index == len(parents) - 1)

    lines.append(
        f"{name(artifact)}  ({artifact['kind']}, "
        f"{status.get(artifact['provenance_step_id'], 'unverified')}){also_by(artifact)}"
    )
    step = steps_by_id.get(artifact["provenance_step_id"])
    if step is not None:
        lines.append(f"← {describe_step(step)}")
        parents = inputs_of(step)
        for index, parent in enumerate(parents):
            walk(parent, "", index == len(parents) - 1)
    return "\n".join(lines)

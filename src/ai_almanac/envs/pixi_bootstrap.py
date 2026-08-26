"""Auto-bootstrap pixi from a pinned GitHub release.

Pins live here as module constants so a version bump is a single self-reviewing
diff. The update procedure:

    V=v0.NEW.VER
    for a in pixi-x86_64-unknown-linux-musl.tar.gz \\
             pixi-aarch64-unknown-linux-musl.tar.gz \\
             pixi-aarch64-apple-darwin.tar.gz; do
      echo -n "$a: "
      curl -fsSL "https://github.com/prefix-dev/pixi/releases/download/$V/$a.sha256"
    done

Bump PIXI_VERSION and replace all three hashes in the same commit.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import tarfile
import time
from pathlib import Path

import httpx

PIXI_VERSION = "0.67.2"

# Map pixi platform → (release asset name, sha256 of the tarball)
_PIXI_ASSETS: dict[str, tuple[str, str]] = {
    "linux-64": (
        "pixi-x86_64-unknown-linux-musl.tar.gz",
        "a2e8d1dc18351e71a67cee22c8ff1635636d625c7d2dcf068ca035cda1dd133b",
    ),
    "linux-aarch64": (
        "pixi-aarch64-unknown-linux-musl.tar.gz",
        "0f1ca00409f9324ed6c74cd6190dcb86d378bd49636c486d8f76231afab3352f",
    ),
    "osx-arm64": (
        "pixi-aarch64-apple-darwin.tar.gz",
        "c440c760169ced6969e50612f173f7b68f3e171377f1c8967416c5a6ccbd7155",
    ),
}

_GITHUB_BASE = "https://github.com/prefix-dev/pixi/releases/download"
_RETRY_DELAYS = (0, 1, 4)


def _current_pixi_platform() -> str:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Linux":
        return "linux-aarch64" if machine in ("aarch64", "arm64") else "linux-64"
    if system == "Darwin":
        return "osx-arm64" if machine == "arm64" else "osx-64"
    if system == "Windows":
        return "win-64"
    return f"{system.lower()}-{machine}"


def _verify_sha256(data: bytes, expected: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise RuntimeError(
            f"sha256 mismatch for pixi tarball: expected {expected}, got {actual}. "
            "The pin in pixi_bootstrap.py may be stale — update PIXI_VERSION and hashes."
        )


def _extract_pixi(tarball: bytes, dest: Path) -> None:
    with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tf:
        member = next((m for m in tf.getmembers() if m.name == "pixi"), None)
        if member is None:
            raise RuntimeError("pixi binary not found in tarball (expected member named 'pixi')")
        if member.name != "pixi" or "/" in member.name:
            raise RuntimeError(f"path traversal guard: unexpected member name {member.name!r}")
        f = tf.extractfile(member)
        if f is None:
            raise RuntimeError("could not extract pixi member from tarball")
        dest.write_bytes(f.read())
    dest.chmod(0o755)


def _download(url: str, progress_lines: list[str]) -> bytes:
    with httpx.Client(
        follow_redirects=True,
        timeout=httpx.Timeout(30.0, read=120.0),
    ) as client:
        chunks: list[bytes] = []
        downloaded = 0
        last_reported = 0
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            for chunk in resp.iter_bytes(chunk_size=65536):
                chunks.append(chunk)
                downloaded += len(chunk)
                if downloaded - last_reported >= 8 * 1024 * 1024:
                    msg = f"downloading pixi: {downloaded // (1024 * 1024)} MB"
                    progress_lines.append(msg)
                    last_reported = downloaded
    return b"".join(chunks)


def ensure_pixi(progress=None) -> str:  # noqa: ANN001
    """Resolve or download the pinned pixi binary; return its absolute path.

    Resolution order:
      1. PATH (shutil.which) — developers and CI always win.
      2. Cached $DATA_DIR/bin/pixi with a matching version stamp.
      3. Download from GitHub, verify sha256, cache.

    `progress` is an optional ProgressCallback (from manager.py). When a
    download actually happens, emits phase_started / line / phase_finished
    events under phase="pixi-bootstrap".
    """
    # 1. PATH wins — never download when pixi is already installed.
    system_pixi = shutil.which("pixi")
    if system_pixi:
        return system_pixi

    from ai_almanac.paths import data_root

    bin_dir = data_root() / "bin"
    cached = bin_dir / "pixi"
    stamp = bin_dir / "pixi.version"

    # 2. Valid cache hit.
    if (
        cached.exists()
        and os.access(cached, os.X_OK)
        and stamp.exists()
        and stamp.read_text().strip() == PIXI_VERSION
    ):
        return str(cached)

    # 3. Download.
    plat = _current_pixi_platform()
    if plat not in _PIXI_ASSETS:
        raise RuntimeError(
            f"Unsupported platform {plat!r} for pixi auto-bootstrap. "
            "Install pixi manually from https://pixi.sh and re-run."
        )

    asset_name, expected_sha = _PIXI_ASSETS[plat]
    url = f"{_GITHUB_BASE}/v{PIXI_VERSION}/{asset_name}"

    if progress is not None:
        from ai_almanac.envs.manager import EnvProgressEvent

        progress(EnvProgressEvent(kind="phase_started", phase="pixi-bootstrap", detail=url))

    progress_lines: list[str] = []
    tarball = _download_with_retry(url, progress_lines)

    if progress is not None:
        from ai_almanac.envs.manager import EnvProgressEvent

        for line in progress_lines:
            progress(EnvProgressEvent(kind="line", phase="pixi-bootstrap", line=line))

    _verify_sha256(tarball, expected_sha)

    bin_dir.mkdir(parents=True, exist_ok=True)
    tmp = bin_dir / f".pixi.tmp-{os.getpid()}"
    try:
        _extract_pixi(tarball, tmp)
        tmp.rename(cached)
        stamp.write_text(PIXI_VERSION + "\n")
    except Exception:
        tmp.unlink(missing_ok=True)
        raise

    if progress is not None:
        from ai_almanac.envs.manager import EnvProgressEvent

        progress(
            EnvProgressEvent(kind="phase_finished", phase="pixi-bootstrap", detail=str(cached))
        )

    return str(cached)


def _download_with_retry(url: str, progress_lines: list[str]) -> bytes:
    last_exc: Exception | None = None
    for delay in _RETRY_DELAYS:
        if delay:
            time.sleep(delay)
        try:
            return _download(url, progress_lines)
        except httpx.TransportError as exc:
            last_exc = exc
            continue
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise RuntimeError(
                    f"pixi release asset not found at {url} — the pin in "
                    "pixi_bootstrap.py may be incorrect. Update PIXI_VERSION and hashes."
                ) from exc
            if exc.response.status_code >= 500:
                last_exc = exc
                continue
            raise

    from ai_almanac.paths import data_root

    raise RuntimeError(
        f"Could not download pixi after {len(_RETRY_DELAYS)} attempts ({last_exc}). "
        "To fix, try one of:\n"
        "  1. Install pixi from https://pixi.sh\n"
        f"  2. Place a pixi binary at {data_root() / 'bin' / 'pixi'} "
        f"and write '{PIXI_VERSION}' to {data_root() / 'bin' / 'pixi.version'}\n"
        "  3. Restore network connectivity and re-run `ai-almanac env prepare`"
    )

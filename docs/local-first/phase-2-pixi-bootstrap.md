# Phase 2 spec — pixi auto-bootstrap + env-prepare progress callback

**One PR.** Implements Phase 2 of `docs/local-first-single-process.md`, minus
the DGX-Spark hardware spike (Phase 0 item, tracked separately).

> **Canonical contract note:** the `EnvProgressEvent` shape defined here is
> the single source of truth for env-prepare progress events. Phase 4's SSE
> layer (`docs/local-first/phase-4-onboarding-wizard.md`) wraps these events
> with `seq`/`state`/`done` framing; it does not define its own event type.

## Current state (verified)

- `src/ai_almanac/envs/manager.py` — `_require_pixi()` (line 65) raises if
  `pixi` is not on PATH. Called from 7 places: `_install`,
  `ensure_forecast_env`, `run`, `run_blending`, `run_forecast`,
  `env_versions`. `_install` and the git clone/fetch/checkout in
  `ensure_blending_env` run `subprocess.run(..., check=True)` inheriting
  stdout; the per-environment forecast solves do the same.
- `src/ai_almanac/cli.py` — `env_prepare` (line 104) has its own
  `shutil.which("pixi")` pre-check duplicating `_require_pixi`; remove it.
  `env_info` calls `env_versions()`.
- `src/ai_almanac/server/services/job_workload.py` — imports
  `run`/`run_blending`/`run_forecast` and streams their piped stdout itself
  via `_stream_process`. It never calls `ensure_env()`, so the callback
  refactor cannot regress job execution. `tests/test_romp_runner.py` and
  `tests/test_blend_workload.py` monkeypatch at the `job_workload` level.
- No test file covers `envs/manager.py` directly today. `conftest.py` sets
  `AI_ALMANAC_DATA_DIR` to a session tmpdir, so `data_root()/bin` is safely
  writable in tests.
- `httpx>=0.28.1` is already a core dependency — no new dependency needed.

## Design decisions

1. **Pins live as module constants** in a new
   `src/ai_almanac/envs/pixi_bootstrap.py`: a `PIXI_VERSION` string plus a
   per-platform dict mapping pixi platform → `(release_asset_name,
   sha256_of_asset)`. Pins change rarely, the diff is self-reviewing, no
   data-file plumbing.
2. **Download source**: GitHub releases,
   `https://github.com/prefix-dev/pixi/releases/download/v{PIXI_VERSION}/{asset}`
   with assets `pixi-x86_64-unknown-linux-musl.tar.gz` (linux-64),
   `pixi-aarch64-unknown-linux-musl.tar.gz` (linux-aarch64),
   `pixi-aarch64-apple-darwin.tar.gz` (osx-arm64). Each has an upstream
   `.sha256` sibling, so pins are byte-identical to upstream's published
   checksums. Verify the **tarball** sha256, then extract the single `pixi`
   member with stdlib `tarfile` (extract only the member named `pixi`; guard
   path traversal).
3. **Cache validity via version stamp**: write the verified binary to
   `$AI_ALMANAC_DATA_DIR/bin/pixi` (mode 0755) plus sibling
   `bin/pixi.version` containing `PIXI_VERSION`. Reuse iff the stamp matches
   and the file is executable; a version bump triggers exactly one
   re-download. Write to `bin/.pixi.tmp-{pid}` and `os.replace()` for
   atomicity (concurrent bootstraps are safe).
4. **Resolution order**: `shutil.which("pixi")` → cached `$DATA_DIR/bin/pixi`
   (stamp-valid) → download. PATH always wins so developers/CI never hit the
   network.
5. **Retry/timeout**: 3 attempts, backoff 1 s / 4 s,
   `httpx.Client(follow_redirects=True, timeout=httpx.Timeout(30.0,
   read=120.0))`, streamed to disk in chunks (~30–50 MB assets). Retry only
   on `httpx.TransportError` and 5xx; 404 (bad pin) fails immediately with
   the pin-update hint.
6. **Offline / failure error**: `RuntimeError` naming what was tried and
   three fixes: install pixi from https://pixi.sh, place a binary at
   `$DATA_DIR/bin/pixi` (plus `PIXI_VERSION` in `pixi.version`), or restore
   network and re-run `ai-almanac env prepare`. Unsupported platforms
   (osx-64, win-64): distinct immediate error pointing at pixi.sh.
7. **Progress event shape** (SSE-ready; lives in `manager.py`):

   ```python
   EventKind = Literal["phase_started", "line", "phase_finished",
                       "phase_skipped", "phase_failed"]

   @dataclass(frozen=True, slots=True)
   class EnvProgressEvent:
       kind: EventKind
       phase: str            # "pixi-bootstrap" | "benchmark" | "blending"
                             # | "blending-source" | "forecast:base"
                             # | "forecast:aifs2" | "forecast:aifs2ens"
       line: str | None = None    # kind == "line" (stdout+stderr merged)
       detail: str | None = None  # started: label / finished: env path /
                                  # skipped: reason / failed: last ~30 lines

   ProgressCallback = Callable[[EnvProgressEvent], None]
   ```

   Flat, `dataclasses.asdict()`-JSON-serializable; the Phase 4 SSE endpoint
   is `json.dumps(asdict(e))` per event with no adapter.
8. **Default callback preserves CLI UX**: `ensure_env(progress=None)` falls
   back to a module-level printer (`==> {phase}` banners, raw lines,
   ready-path lines on `phase_finished`). Known cosmetic change: pixi runs
   with piped stdout so it prints plain progress lines instead of animated
   bars. Note in PR description.

## Ordered implementation steps

**Step 1 — pin the version.** Pick the current pixi release and record
checksums (run on a dev machine; document verbatim in a comment block above
the pins as the update procedure):

```bash
V=v0.XX.Y   # target release tag
for a in pixi-x86_64-unknown-linux-musl.tar.gz \
         pixi-aarch64-unknown-linux-musl.tar.gz \
         pixi-aarch64-apple-darwin.tar.gz; do
  curl -fsSL "https://github.com/prefix-dev/pixi/releases/download/$V/$a.sha256"
done
```

Confirm the three asset names exist on the release page first.

**Step 2 — new `src/ai_almanac/envs/pixi_bootstrap.py`** (~130 lines):
`PIXI_VERSION`, `_PIXI_ASSETS`, pin-update comment;
`ensure_pixi(progress=None) -> str` implementing decisions 2–6, emitting
`phase="pixi-bootstrap"` events (`phase_started` only when a download
actually happens; byte-progress `line` events every ~8 MB; `phase_finished`
with the resolved path). Internal seams for tests: `_download`,
`_verify_sha256`, `_extract_pixi`. Move `_current_pixi_platform()` here from
`manager.py` and re-export (bootstrap must not import manager — cycle).

**Step 3 — `src/ai_almanac/paths.py`**: add `bin_dir() -> Path` returning
`data_root() / "bin"` (not in `ensure_layout()`; bootstrap mkdirs lazily).

**Step 4 — refactor `src/ai_almanac/envs/manager.py`:**

- Delete `_require_pixi`; add `_ensure_pixi(progress=None)` wrapping
  `pixi_bootstrap.ensure_pixi`. All call sites switch.
  `run`/`run_blending`/`run_forecast` call it with no progress arg (silent
  bootstrap at job launch; signatures unchanged — zero churn in
  `job_workload.py`).
- Add `EnvProgressEvent`, `ProgressCallback`, `_default_progress`, and
  `_run_streaming(cmd, phase, progress, *, cwd=None)`: `Popen(stdout=PIPE,
  stderr=STDOUT, text=True, bufsize=1)`, emit a `line` event per line,
  retain a `deque(maxlen=30)` tail; on nonzero exit emit
  `phase_failed(detail=tail)` and raise `CalledProcessError(rc, cmd,
  output=tail_str)`.
- `_install(spec, env_dir, phase, progress, environments=None)`: wraps each
  `pixi install` in `phase_started`/`_run_streaming`/`phase_finished`.
  `ensure_forecast_env` collapses onto it with
  `environments=FORECAST_ENVIRONMENTS` and phases `forecast:{env}`; its
  platform-skip `print` becomes `phase_skipped`.
- `ensure_blending_env(progress=None)`: pixi solve under phase `blending`;
  git clone/fetch/checkout route through `_run_streaming` under
  `blending-source` (`rev-parse` stays `capture_output` — probe, not
  progress).
- `ensure_env(progress: ProgressCallback | None = None)`: resolve
  `progress = progress or _default_progress` once, thread through
  `_ensure_pixi` and all three `ensure_*`. Return type unchanged.
- `env_versions()`: check `(benchmark_env_dir()/"pixi.toml").exists()`
  **before** `_ensure_pixi()` so `env info` on a fresh machine reports
  "not prepared" instead of triggering a 40 MB download.

**Step 5 — `src/ai_almanac/cli.py`:** delete the `shutil.which("pixi")` gate
in `env_prepare` (and the now-unused top-level `import shutil`).
`env_prepare` becomes the `ensure_env()` call plus existing "ready at"
echoes, wrapped in `try/except RuntimeError` → red message + `Exit(1)` so
the offline error stays clean.

**Step 6 — docs:** pin-update procedure lives in the `pixi_bootstrap.py`
comment ("bump `PIXI_VERSION`, replace all three hashes in the same
commit").

**Step 7 — format + gates** (see Verification).

## New tests — `tests/test_env_manager.py`

No network anywhere: monkeypatch `pixi_bootstrap._download` (or
`httpx.MockTransport`) and the pin dict.

1. `test_ensure_pixi_prefers_path` — `which` → `/usr/bin/pixi`; `_download`
   monkeypatched to `pytest.fail`.
2. `test_ensure_pixi_uses_cached_binary_when_stamp_matches`.
3. `test_ensure_pixi_redownloads_on_version_bump` — stale stamp; fake
   tarball built in-test; assert new content, 0755, stamp updated.
4. `test_ensure_pixi_downloads_verifies_and_marks_executable` — plus
   `phase_started`→`phase_finished` `pixi-bootstrap` events recorded.
5. `test_ensure_pixi_rejects_sha256_mismatch` — error mentions `sha256`;
   no `bin/pixi` or tmp files remain.
6. `test_ensure_pixi_unsupported_platform_message` — mentions `pixi.sh`.
7. `test_ensure_pixi_offline_error_is_actionable` — `httpx.ConnectError`
   through all retries (backoff → 0); error names `$DATA_DIR/bin/pixi` and
   `https://pixi.sh`.
8. `test_run_streaming_emits_lines_and_phase_events`.
9. `test_run_streaming_failure_includes_tail` — `CalledProcessError` with
   rc 3 and `"boom"` in output + `phase_failed` detail.
10. `test_install_event_sequence` — `_ensure_pixi` → `/bin/echo`.
11. `test_ensure_env_default_progress_prints` (capsys).
12. `test_env_versions_without_env_does_not_bootstrap`.
13. `test_cli_env_prepare_no_longer_requires_path_pixi` — `CliRunner`,
    `ensure_env` monkeypatched, `which` → None, exit 0.

Regression watch: `tests/test_romp_runner.py`, `tests/test_blend_workload.py`,
`tests/test_job_manager.py` must pass untouched.

## Verification commands

```bash
pixi run format-python
pixi run check
pixi run test-python
pixi run test
# manual smoke (network):
AI_ALMANAC_DATA_DIR=$(mktemp -d) PATH=/usr/bin:/bin ai-almanac env prepare  # bootstrap path
ai-almanac env prepare                                                       # PATH-pixi fast path
ai-almanac env info                                                          # no download on fresh dir
```

## Open questions

1. **Asset existence check** (blocking Step 1): confirm the three `.tar.gz`
   assets and `.sha256` siblings exist under those exact names on the chosen
   release.
2. **Bare-binary alternative**: pinning the binary hash directly would allow
   re-verifying the cached file on every use, at the cost of diverging from
   upstream's published checksums. Tarball + stamp chosen.
3. Should `run()`/`run_forecast()` bootstrap silently at job launch, or fail
   with "run `ai-almanac env prepare` first"? Plan: bootstrap silently.
4. Event timestamps: omitted; the Phase-4 SSE layer stamps `seq` on
   emission. Add `ts: float` now if replay-after-reconnect wants wall time.
5. Phase 3 introduces `AI_ALMANAC_ENV_ROOT`; the bootstrapped binary stays
   under the **data dir** (`bin/`) — per-instance, not shared. Confirmed
   intended.

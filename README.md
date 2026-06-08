# ai-almanac

Local-first benchmarking platform for AI weather and climate models.

Pick a region and an event type (e.g. monsoon onset), select one or more models,
submit a benchmark, and see per-grid-point skill maps (MAE, FAR, miss rate, RMSE,
ACC, bias) rendered in your browser. The whole thing — web UI, API, benchmark
runner, and database — is a single Python process you launch on your own GPU
workstation (NVIDIA DGX Spark, a lab box, your laptop for the LLM-driven UI).

```bash
pipx install ai-almanac      # or `uv tool install ai-almanac`
ai-almanac serve             # opens http://localhost:8765 in your browser
```

That's it.

---

## What you get

| Layer | Where it lives |
|---|---|
| Web UI | SvelteKit SPA, bundled into the wheel |
| API | FastAPI, served on the same port as the UI |
| Database | SQLite under `~/.local/share/ai-almanac/almanac.db` (auto-migrated) |
| Storage | Filesystem under the same data directory |
| Benchmark runner | In-process, shells out to ROMP / earth2studio in a pixi-managed env |
| Auth | None in the app. Bind 127.0.0.1 for local use, or put oauth2-proxy in front for public deployments |

One Python package. One process. One port. One data directory.

---

## Install

```bash
# Recommended — uv tool install gives you a managed venv per CLI
uv tool install ai-almanac

# Or pipx
pipx install ai-almanac

# Or pip (less isolated)
pip install ai-almanac
```

Once a homebrew tap / .deb is published, `brew install ai-almanac` and
`apt install ai-almanac` will be available too. The PyPI package is the
source of truth — those channels are thin wrappers.

### Prerequisites for running real benchmarks

The web UI works out of the box, but actual benchmark execution needs the
heavy ML stack (torch + CUDA, earth2studio, ROMP, NetCDF/HDF5). That stack
lives in a separate pixi-managed environment to keep the core install small:

```bash
# Install pixi (one-time): https://pixi.sh
curl -fsSL https://pixi.sh/install.sh | bash

# Materialize the benchmark env (takes a few minutes the first time)
ai-almanac env prepare
```

Subsequent `ai-almanac serve` runs reuse the cached env. `ai-almanac env info`
prints the installed versions.

---

## Usage

```bash
ai-almanac serve                       # default: 127.0.0.1:8765, opens browser
ai-almanac serve --port 9000           # alternate port
ai-almanac serve --no-open             # don't auto-launch a browser tab
ai-almanac serve --reload              # dev mode (uvicorn auto-reload)
ai-almanac serve --bind 0.0.0.0 --allow-public
                                       # bind to all interfaces — REQUIRED
                                       # behind a reverse proxy doing auth

ai-almanac env prepare                 # install / update the benchmark env
ai-almanac env info                    # show installed package versions

ai-almanac reset --confirm             # wipe ~/.local/share/ai-almanac/
ai-almanac version
```

### Where data lives

Everything goes under `$AI_ALMANAC_DATA_DIR`, defaulting to:

- Linux: `~/.local/share/ai-almanac/`
- macOS: `~/Library/Application Support/ai-almanac/`
- Windows: `%LOCALAPPDATA%\ai-almanac\`

```
$AI_ALMANAC_DATA_DIR/
├── almanac.db          ← SQLite (WAL mode)
├── uploads/            ← user-uploaded obs datasets
├── jobs/<job_id>/      ← run logs, NetCDF outputs, figures
├── benchmark-env/      ← pixi env (torch + earth2studio + ROMP + NetCDF)
└── cache/              ← weight cache (HuggingFace), ARCO chunks
```

Override with `AI_ALMANAC_DATA_DIR=/some/path ai-almanac serve` — useful for
sharing a data directory across multiple researchers on one box (each runs
their own `ai-almanac serve` on their own port; the FS perms decide what's
shared).

---

## Public deployment

ai-almanac has no built-in authentication. To host a publicly-reachable
instance, run it bound to localhost and put a reverse proxy in front that
handles auth. See [`DEPLOY_PUBLIC.md`](./DEPLOY_PUBLIC.md) for Caddy +
oauth2-proxy + Globus OIDC examples.

The app reads `X-Forwarded-User` (header name configurable via
`SUBMITTED_BY_HEADER`) and records it on jobs/datasets as `submitted_by`
for attribution. It does not enforce anything — the proxy is the trust
boundary.

---

## Adding a model

1. Add an entry to `src/ai_almanac/server/config/models.yaml` with
   `id`, `display_name`, `region`, etc.
2. Set the env var `{REGION}_{ID}_MODEL_DIR` to where the model's NetCDF
   files live on disk (or `gs://...` for an ARCO-style remote zarr).
3. Restart `ai-almanac serve`. Models whose env var is unset get filtered
   out of the registry automatically.

---

## Development

See [`DEVELOPMENT.md`](./DEVELOPMENT.md) for the no-Docker workflow:
`uv sync && cd web && npm install && npm run dev` in one terminal,
`uv run ai-almanac serve --reload` in another.

---

## License

MIT.

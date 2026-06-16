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
| Benchmark runner | Detached local supervisor, with ROMP in a Pixi-managed environment |
| Access model | Local, single-user application bound to the loopback interface |

One Python package. One process. One port. One data directory.

Benchmark jobs run in detached local supervisor processes. Closing or restarting
the web server does not stop active work. Restarting `ai-almanac serve`
reconciles queued and running jobs from SQLite, and active jobs can be canceled
from the benchmark UI.

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

Actual benchmark execution needs ROMP and its scientific Python stack. Those
dependencies live in a separate Pixi-managed environment to keep the core
install small:

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
├── benchmark-env/      ← Pixi environment (ROMP + scientific/geo dependencies)
└── cache/              ← weight cache (HuggingFace), ARCO chunks
```

Override with `AI_ALMANAC_DATA_DIR=/some/path ai-almanac serve` to move one
instance's private state. Do not point multiple running instances at the same
application data directory. Researchers may register the same read-only input
dataset directories from separate AI Almanac instances.

---

## Adding data

Open **Data** in the web UI and register local observation or model-output
directories. AI Almanac checks the file pattern and configured NetCDF variable
before making a source available to benchmark workflows.

---

## Development

Install [Pixi](https://pixi.sh/), then start the complete development stack:

```bash
pixi run dev
```

This runs FastAPI with Python auto-reload on `http://localhost:8765` and the
SvelteKit Vite server with hot module replacement on `http://localhost:5173`.
See [`DEVELOPMENT.md`](./DEVELOPMENT.md) for the full task list.

---

## License

MIT.

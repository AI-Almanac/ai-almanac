# ai-almanac

Assess AI weather models, blend them, and forecast the weather events people
plan around — starting with the onset of the rainy season.

Pick a region, compare how well AI and conventional weather models have
predicted the rainy season onset there, combine the best of them into a blended
forecast, and run that blend live for the current season. A built-in assistant
can set up each step for you from a plain-language request.

> **Try it without installing:** [ai-almanac.org](https://ai-almanac.org) is
> the hosted version. Browse example results without an account, or sign in to
> run your own.

To run it on your own machine, with your own data (see
[Run it on your own hardware](#run-it-on-your-own-hardware) for keeping
everything in-house):

```bash
uv tool install ai-almanac   # or `pipx install ai-almanac`
ai-almanac serve             # opens http://localhost:8765 in your browser
```

---

## What you can do

- **Benchmarks** — score one or more models against observations for a region
  and season. Results are per-grid-point skill maps (false alarm rate, miss
  rate, and onset error in days), lead-time skill curves, and, for ensemble
  models, probabilistic scores (Brier, RPS, AUC, reliability). Limit scoring to
  an area of interest by drawing a box or picking administrative areas.
- **Blends** — train a weighted combination of several models on past seasons,
  check its skill on held-out years, and download the trained weights and
  scores.
- **Forecasts** — run a trained blend's AI models forward from today's GFS or
  IFS analysis and map the blended probability of onset across the region.
- **Assistant** — describe what you want ("which models best predict Kiremt
  onset in Ethiopia?") and the assistant fills in the benchmark or blend setup,
  explains results, and digs into failed runs. Nothing is submitted without
  your approval.
- **Almanac** — reference pages on AI weather model families, architectures,
  and observation datasets, plus a glossary.

Ethiopia (Kiremt) and India (monsoon onset) are built-in regions; you can add
your own.

---

## Install

```bash
uv tool install ai-almanac   # recommended: an isolated, managed environment
pipx install ai-almanac      # equivalent
pip install ai-almanac       # into the current environment
```

AI Almanac supports Linux and Apple Silicon macOS, with Python 3.12 or newer.
It is in early development, so releases are published as pre-releases
(`0.1.0a1`, …); the commands above install the latest one.

### Workload environments

Benchmarks, blends, and forecasts each need a scientific Python stack that is
kept apart from the app. [Pixi](https://pixi.sh) installs and manages them:

```bash
curl -fsSL https://pixi.sh/install.sh | bash   # one-time
ai-almanac env prepare                         # a few minutes the first time
```

| Workflow   | Runs on                                                              |
| ---------- | -------------------------------------------------------------------- |
| Benchmarks | Linux or Apple Silicon macOS, CPU only                               |
| Blends     | Linux or Apple Silicon macOS, CPU only                               |
| Forecasts  | Linux with an NVIDIA GPU (CUDA); model weights download on first run |

On macOS, `env prepare` skips the forecast environment. Re-run it after
upgrading AI Almanac to pick up new pinned versions.

---

## Getting started

1. **Add data.** Open **Data** and register an observation dataset and one or
   more model forecast datasets. Each is a pointer to a local directory or a
   `gs://` prefix of NetCDF files. AI Almanac checks the files and variable
   before saving and never modifies them. A fresh install has no datasets.
2. **Run a benchmark.** Open **Benchmarks**, describe it to the assistant or
   choose **Manual configuration**, and submit.
3. **Train a blend** from the models you benchmarked, then **run a forecast**
   from it.

Runs continue in the background: closing or restarting `ai-almanac serve` does
not stop them, and the app reconnects to them when it starts again. Cancel a
run from its results page.

### Setting up the assistant

The assistant needs an LLM. Point it at any OpenAI-compatible endpoint with
environment variables:

```bash
export LLM_BASE_URL=https://api.openai.com/v1
export LLM_MODEL=gpt-5
export LLM_API_KEY=sk-...
ai-almanac serve
```

Local servers (Ollama, vLLM, LM Studio) work the same way; leave `LLM_API_KEY`
unset if the server doesn't check it. The same keys, lowercased
(`llm_base_url`, …), can live in `config.yaml` in the data directory instead.
Without an LLM, every workflow still works through manual configuration.

---

## Usage

```bash
ai-almanac serve                 # 127.0.0.1:8765, opens a browser tab
ai-almanac serve --port 9000     # alternate port
ai-almanac serve --no-open       # don't open a browser tab
ai-almanac env prepare           # install or update workload environments
ai-almanac env info              # show benchmark package versions
ai-almanac db upgrade            # apply database migrations (serve does this too)
ai-almanac reset --confirm       # delete everything in the data directory
ai-almanac version
```

`ai-almanac serve` only listens on the loopback interface: it is a single-user
app with no sign-in. To share an installation with a team, see
[Running for a team](#running-for-a-team).

### Where data lives

Everything goes under `$AI_ALMANAC_DATA_DIR`, defaulting to
`~/.local/share/ai-almanac/` on Linux and
`~/Library/Application Support/ai-almanac/` on macOS.

```
$AI_ALMANAC_DATA_DIR/
├── almanac.db       ← SQLite database
├── config.yaml      ← optional settings file
├── jobs/<job_id>/   ← run logs, NetCDF outputs, figures
├── cache/           ← blend intermediates, cached forecast rollouts
├── benchmark-env/   ← workload environments (from `env prepare`)
├── blending-env/
└── forecast-env/    ← Linux only
```

Registered datasets stay where they are; only pointers to them are stored.
Set `AI_ALMANAC_DATA_DIR=/some/path` to keep a separate instance. Don't point
two running instances at the same data directory.

---

## Run it on your own hardware

AI Almanac can run entirely on machines you control, so observations, model
data, and results stay inside your organization and your country. The
installed app is the same software as [ai-almanac.org](https://ai-almanac.org),
not a reduced version.

- **Your own machine is the default.** `ai-almanac serve` runs as a single
  process with its own database, and benchmarks, blends, and forecasts run on
  the same machine. It listens only on `127.0.0.1` and needs no account.
  Registered datasets are read where they already are and are never uploaded;
  everything the app produces goes to the
  [data directory](#where-data-lives).
- **The assistant can use a model on your own network.** Point it at any LLM
  server with an OpenAI-compatible API, such as Ollama, vLLM, or LM Studio:

  ```bash
  export LLM_BASE_URL=http://localhost:11434/v1   # Ollama's default address
  export LLM_MODEL=<a model you have pulled>
  ai-almanac serve
  ```

  The assistant sets up runs by calling tools, so choose a model that supports
  tool calling. Without an LLM, every workflow still works through manual
  configuration.

- **Forecasts need an NVIDIA GPU on Linux** (x86-64 or ARM64, with CUDA). The
  hosted version runs each forecast model on an 80 GB NVIDIA A100; a GPU with
  less memory may not fit every model. Benchmarks and blends need only a CPU
  and also run on Apple Silicon Macs.

### What needs an internet connection

| Task                                                   | Needs internet?                                                                                                         |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------- |
| Installing or upgrading, and `ai-almanac env prepare`  | Yes: downloads the app, its workload environments, and the scoring and blending code                                    |
| Benchmarks and blends on datasets in local directories | No                                                                                                                      |
| The first forecast with each AI model                  | Yes: downloads the model's weights                                                                                      |
| Live forecasts                                         | Yes: downloads the latest public GFS (NOAA) or IFS (ECMWF) analysis as starting conditions; none of your data is sent   |
| The assistant                                          | Only if `LLM_BASE_URL` points outside your network                                                                      |
| Choosing administrative areas by name                  | Yes: outlines come from [geoBoundaries](https://www.geoboundaries.org). Drawing a box works offline                     |
| Maps in the browser                                    | Yes: the background map loads from public tile servers (CARTO, OpenFreeMap), which see the area you view, not your data |

On a machine with no internet access, runs still complete and save their run
logs, NetCDF outputs, and figures under `jobs/` in the data directory, but the in-app maps cannot be drawn
without their background map.

---

## Running for a team

The installed `ai-almanac serve` is single-user; never expose it to untrusted
users. To give a team its own installation, run AI Almanac in shared mode:
multiple signed-in users, PostgreSQL, per-user ownership, admin roles, and
optional GCS storage and Modal-hosted runs.

Shared mode runs as Docker containers built from this repository, so it starts
from a clone (with Docker and [Pixi](https://pixi.sh)) rather than the PyPI
package:

```bash
git clone https://github.com/AI-Almanac/ai-almanac.git && cd ai-almanac
pixi run self-host-local   # try the full shared stack locally
```

The [deployment guide](./docs/deployment.md) covers configuration and running
it on a real host.

---

## Development

Install [Pixi](https://pixi.sh/) and run:

```bash
pixi run dev
```

This starts the API with auto-reload on `http://localhost:8765` and the web UI
with hot reload on `http://localhost:5173`. See
[`DEVELOPMENT.md`](./DEVELOPMENT.md) and [`CONTRIBUTING.md`](./CONTRIBUTING.md).

---

## License

[MIT](./LICENSE)

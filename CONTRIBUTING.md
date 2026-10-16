# Contributing

## Add a region, event definition, or model

You don't need to be a software developer to extend AI Almanac. Most additions
are a few lines of YAML, or a dataset registered in the app.

| To add                                              | Where it lives                                                                                                                                                                                                                                                                                                                                            |
| --------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A region                                            | [`regions.yaml`](src/ai_almanac/server/config/regions.yaml): an id, name, bounding box, and optionally the ISO country code used to look up administrative areas. On your own installation you can also add one from the **Regions** page, with no code change.                                                                                           |
| An onset definition                                 | The rainfall threshold and wet- and dry-spell lengths that define onset default to the `defaults` in [`romp.yaml`](src/ai_almanac/server/config/romp.yaml), and every benchmark and blend can override them in its settings. The event scored today is rainy-season onset; a new kind of event needs changes to the scoring code, so open an issue first. |
| A model's past forecasts, for benchmarks and blends | Register the dataset from the **Data** page: a local directory or `gs://` prefix of NetCDF files. No code change. Shared datasets on ai-almanac.org are registered by an administrator, so open an issue saying where the data lives and under what licence.                                                                                              |
| An AI model for live forecasts                      | [`forecast_models.yaml`](src/ai_almanac/server/config/forecast_models.yaml). Forecasts run through [Earth2Studio](https://github.com/NVIDIA/earth2studio), so the model must be available there. Record training years only from the model's publication: the assistant uses them to flag overlap with evaluation years.                                  |
| Guidance for the assistant                          | [`rulesets/builtin.yaml`](src/ai_almanac/server/config/rulesets/builtin.yaml): the domain knowledge and result-interpretation caveats the assistant follows.                                                                                                                                                                                              |

Each file opens with comments describing its fields.

Packaged regions are added to each installation when it starts, but an entry
that has already shipped is never overwritten. If your change edits an existing
region rather than adding one, say so in the PR so a maintainer can update
installations that already have it.

### How a contribution ships

1. **Open an issue or a pull request.** An issue is enough: describe the
   region, definition, or model and link the paper or data behind it. If you
   are comfortable with Git, open a PR against `develop` with the change.
2. **CI checks the change.** Every PR runs formatting, lint, type checks, and
   the full test suite.
3. **A maintainer reviews it**, including the scientific basis.
4. **It ships in the next release.** Merged changes go live on
   staging.ai-almanac.org right away, reach ai-almanac.org with the next
   production release, and reach local installations with the next PyPI
   release (`uv tool upgrade ai-almanac`).

## Setup

Install [pixi](https://pixi.sh), then:

```bash
git clone git@github.com:AI-Almanac/ai-almanac.git
cd ai-almanac
pixi run dev
```

That starts the SvelteKit frontend (Vite HMR, http://localhost:5173) and the
FastAPI backend (http://localhost:8765). See `DEVELOPMENT.md` for the
architecture walkthrough, project layout, and how to add models or
dependencies.

## Before you push

```bash
pixi run check   # ruff + svelte-check
pixi run test    # pytest + vitest
```

If you changed any backend route or Pydantic model, regenerate the TS API
types — CI fails when they're stale:

```bash
pixi run generate-api-types
```

## Branch flow

- `develop` is the default branch. Changes land via PR; CI must pass. Every
  merge to `develop` deploys **staging** (staging.ai-almanac.org).
- Merging `develop` → `main` deploys **production** (ai-almanac.org).
- Release wheels are published to PyPI by tagging `v*` (see
  [Releasing](#releasing)).

## Releasing

The PyPI package (`pip install ai-almanac`) is built and published by
`.github/workflows/release.yml` through PyPI Trusted Publishing — there is no
API token to manage.

1. Bump `version` in `pyproject.toml` (the only place it lives) in a PR to
   `develop`, then merge `develop` → `main` as usual. While the project is in rapid development, release alphas
   (`0.1.0a2`, `0.1.0a3`, …): PyPI treats any PEP 440 pre-release version as a
   pre-release. Installers still pick one when no stable release exists, and
   stop doing so once one does.
2. Optional dry run: run the `release` workflow manually from the Actions tab.
   It builds, smoke-tests the installed wheel, and publishes to TestPyPI.
3. Tag the release commit on `main` and push the tag:

   ```bash
   git tag v0.1.0a2 && git push origin v0.1.0a2
   ```

The workflow refuses a tag that doesn't match the `pyproject.toml` version.
PyPI never accepts a re-upload of a published version, so a broken release is
fixed by publishing the next version.

## Infrastructure

GCP/OpenTofu configuration lives in `terraform/` — see `terraform/README.md`.
Deploys authenticate via Workload Identity Federation; no credentials to set
up for CI. For your own `tofu plan` you need access to the `ai-almanac` GCP
project — ask an existing maintainer.

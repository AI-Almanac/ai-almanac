# Contributing

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

1. Bump `version` in `pyproject.toml` (the only place it lives) and merge to
   `main`. While the project is in rapid development, release alphas
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

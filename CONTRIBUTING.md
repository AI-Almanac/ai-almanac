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
  `.github/workflows/release.yml`).

## Infrastructure

GCP/OpenTofu configuration lives in `terraform/` — see `terraform/README.md`.
Deploys authenticate via Workload Identity Federation; no credentials to set
up for CI. For your own `tofu plan` you need access to the `ai-almanac` GCP
project — ask an existing maintainer.

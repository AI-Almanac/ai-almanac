# CLAUDE.md

Repository guidance for Claude Code.

ai-almanac is a local-first benchmarking platform for AI weather and climate
models: pick a region and event type, select models, submit a benchmark, and
view per-grid-point skill maps in the browser. One Python package serves the
web UI, API, SQLite database, and detached benchmark supervisor from a single
process. The same codebase also deploys to GCP Cloud Run (prod and staging).

## Git workflow

- `main` is the production branch. Pushes to `main` deploy prod.
- `develop` is the default branch and the base for all work. Pushes to
  `develop` deploy staging.
- Start new work on a fresh branch off `develop`, after pulling the latest:

  ```bash
  git checkout develop && git pull
  git checkout -b <branch-name>
  ```

- Before committing, always run the linters, type checks, and test suites:

  ```bash
  pixi run check
  pixi run test
  ```

- Git hooks enforce this locally: `pixi run install-hooks` (one-time per
  clone) enables a fast staged-file format/lint check on commit and the full
  `check` + `test` gate on push. Bypass only in emergencies (`--no-verify`);
  CI enforces the same gates regardless.

## Agents in a sandboxed shell (Cowork)

Sandboxes mount this repo with delete protection: files can be created and
modified but never unlinked. Git's locking protocol is create-then-unlink, so
native mutating git commands strand `.lock` files that block git for everyone.
The scripts below handle that; use them instead of working around it by hand.

### Need a worktree?

Ask the human to run this on the host — not from the sandbox, which would bake
sandbox paths into git's worktree metadata:

```bash
scripts/agent-worktree.sh <name>          # off develop; --base <ref> to change
```

It creates `../ai-almanac-<name>`, copies `.env`, and assigns the worktree its own
ports and `AI_ALMANAC_DATA_DIR` so parallel worktrees don't collide. It prints the
path to add as a Cowork folder — the one manual step. `--repair <path>` fixes a
worktree that was created from a sandbox; `--list` and `--remove <name>` do what
they say.

To run the dev server in a worktree:

```bash
set -a && . ./.env.agent && set +a && pixi run dev
```

### Commit

```bash
scripts/cowork-git.sh add <paths...>
scripts/cowork-git.sh commit -m "<message>"
scripts/cowork-git.sh status | diff | log | show | branch
```

Works in the main checkout and in worktrees. Everything else — `checkout`,
`merge`, `rebase`, `pull`, `push` — must run natively on the host; `push` also
needs network and SSH keys the sandbox lacks.

### Verify

```bash
scripts/agent-verify.sh                   # --python / --web / --quiet
```

Runs the real `pixi` gate when pixi works, and otherwise installs what it needs
and runs everything reachable. It exits non-zero if a check that ran failed, and
lists what it could not run — so `pytest` and `generate-api-types` needing a host
run is visible rather than assumed. Do not conclude that verification is
impossible in a sandbox; it mostly isn't.

### Hand off

Pushes and PRs are host-side. Leave a runnable script and a PR body rather than
prose instructions.

## Security and agent conventions

- Never read, copy, echo, or commit secrets: `.env`, `web/.env`, anything in
  `~/.config/gcloud/`, service-account JSONs, or GCP Secret Manager values.
  Shared `.claude/settings.json` denies these; do not work around it.
- `web/src/lib/api-types.gen.ts` is generated. Never edit it directly; run
  `pixi run generate-api-types` (a PreToolUse hook enforces this).
- Lockfiles (`pixi.lock`, `uv.lock`, `web/package-lock.json`) are tool-owned;
  change them only via `pixi`/`uv`/`npm`.
- Database migrations must be backward-compatible one version: deploys run
  migrations before routing traffic, so a rollback runs old code on the new
  schema. Additive changes first; destructive changes in a later release.
- Before pushing changes that touch `src/ai_almanac/server/`, `terraform/`,
  `.github/`, `modal/`, or `deploy/`, run `/security-review` and address or
  explicitly justify each finding.
- GitHub Actions in workflows are pinned to commit SHAs with a `# vX` comment.
  When bumping an action, update both the SHA and the comment.
- Edited files are auto-formatted by a PostToolUse hook (ruff/prettier); run
  `pixi run format` for tree-wide formatting. CI enforces format checks via
  `pixi run check`.

## Development

Pixi is the project environment and task manager. Do not use `uv` for this
repository.

```bash
pixi run dev
```

This starts:

- SvelteKit with Vite HMR at `http://localhost:5173`
- FastAPI with Uvicorn reload at `http://localhost:8765`

Useful tasks:

```bash
pixi run test
pixi run check
pixi run build
pixi run backend
pixi run frontend
```

Use `pixi add --pypi <package>` for Python runtime dependencies and
`pixi add --pypi --feature dev <package>` for development dependencies.
Use npm from `web/` for frontend dependencies.

After adding or changing backend API routes, regenerate the frontend API
types from the OpenAPI schema:

```bash
pixi run generate-api-types
```

This rewrites `web/src/lib/api-types.gen.ts`; commit the result.

## Architecture

- `src/ai_almanac/`: local-first Python package and CLI
- `src/ai_almanac/server/`: FastAPI API, SQLite persistence, job supervision
- `src/ai_almanac/server/config/`: packaged model, dataset, region, and ROMP defaults
- `src/ai_almanac/envs/`: separately managed benchmark runtime
- `web/`: SvelteKit frontend
- `modal/`: Modal apps (forecast generation, blending)
- `terraform/`: OpenTofu config for the GCP deployment
- `deploy/`, `compose*.yaml`: container deployment and local compose variants
- `testdata/`: compact NetCDF fixtures
- `docs/`, `DEVELOPMENT.md`, `CONTRIBUTING.md`: further documentation

The production wheel includes the static frontend build. Development uses the
Vite server directly; `VITE_API_URL` points it at FastAPI.

## Testing

Run focused tests while working, then use:

```bash
pixi run test
pixi run check
```

Python tests use a temporary SQLite data directory. Frontend tests use Vitest.

## Data Sources

Datasets are pointers registered through the data-sources API/UI: a row in
`data_sources` naming a local directory or `gs://` prefix, validated at
registration. Admin-registered rows are shared built-ins; user-registered
rows are private. There is no YAML or env-var dataset seeding; regions are
still seeded from packaged `regions.yaml`.

Model initialization weekdays use Python weekday numbering: Monday is `0` and
Sunday is `6`.

## Infrastructure (Terraform / OpenTofu)

`terraform/` holds the GCP deployment for project `ai-almanac`. Use the `tofu`
CLI, not `terraform`. See `terraform/README.md` for the full picture.

- `envs.tf` instantiates `modules/almanac-env` twice (prod and staging):
  Cloud Run service + migrate job, per-env buckets, service account, IAM.
  Per-env values live in `local.env_config`.
- Shared resources sit at the root: `database.tf` (SQL instance),
  `storage.tf` (shared data bucket), `secrets.tf`, `load_balancer.tf`,
  `artifact_registry.tf`, `batch.tf` (ROMP batch worker), `wif.tf`
  (GitHub Actions keyless auth).
- State lives in the `ai-almanac-tf-state` GCS bucket
  (`tofu init -backend-config=backend.hcl`).
- CI owns image rollouts (develop → staging, main → prod via
  `.github/workflows/deploy-*.yml`); `tofu apply` never rolls a revision
  unless the config itself changed.
- Secret values and DNS are managed manually, outside Terraform.

## Cloud buckets (gcloud CLI)

All buckets are in GCP project `ai-almanac`
(`gcloud config set project ai-almanac`):

- `almanac-data-ai-almanac` — shared datasets, registered in the app as
  `gs://` data-source pointers
- `almanac-uploads-ai-almanac` / `almanac-job-outputs-ai-almanac` — prod
- `almanac-uploads-staging-ai-almanac` /
  `almanac-job-outputs-staging-ai-almanac` — staging
- `ai-almanac-tf-state` — OpenTofu state

Use `gcloud storage` to inspect and move data:

```bash
gcloud storage ls gs://almanac-data-ai-almanac/
gcloud storage ls -l gs://almanac-job-outputs-ai-almanac/<job-id>/
gcloud storage cat gs://almanac-data-ai-almanac/<path>
gcloud storage cp <local> gs://almanac-uploads-staging-ai-almanac/<path>
```

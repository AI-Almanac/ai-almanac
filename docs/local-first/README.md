# Local-first implementation — execution guide

This directory holds the per-PR implementation specs for the local-first
single-process plan (`docs/local-first-single-process.md`). It is written
for an agent (Claude Code) executing the work in a **sandboxed shell**
against this repo.

## Specs and execution order

| Order | Spec | Branch suggestion | Depends on |
|---|---|---|---|
| 1 | `phase-1-storage-convergence.md` | `local-first/storage-convergence` | Phase 0 FUSE spike result |
| 2 | `phase-2-pixi-bootstrap.md` | `local-first/pixi-bootstrap` | — |
| 3 | `phase-3-serve-ux.md` | `local-first/serve-ux` | — (touches `envs/manager.py`; rebase if Phase 2 lands first) |
| 4a | `phase-4-onboarding-wizard.md` (backend half) | `local-first/setup-backend` | Phases 2 + 3 merged |
| 4b | `phase-4-onboarding-wizard.md` (frontend + CLI half) | `local-first/setup-frontend` | 4a merged |

Phases 2 and 3 have no dependency on Phase 1 and can be executed first or
in parallel. Each phase is one branch off `develop`, one PR against
`develop`.

## Locked decisions — do not re-derive

- Local installs are **single-user personal mode**, loopback-only. No
  password auth, no local shared mode. Multi-user stays cloud-only.
- Two blessed configs: personal (SQLite/local runner/local paths) and
  managed cloud (Postgres/Modal/proxy/FUSE-mounted buckets). CI targets
  these two, not the combinatorial matrix.
- `EnvProgressEvent` in `phase-2-pixi-bootstrap.md` is the **canonical**
  progress-event contract. Phase 4's SSE layer wraps it with `seq`
  framing; nothing defines a second event type.
- Job configs persist in **mount-path form**; gs:// translation happens
  only in `modal_runner.submit` (Phase 1, decision 2).
- The Modal outputs bucket is **derived** from `bucket_mounts`
  (bare-bucket invariant at startup), not separately configured.
- SQLite stays the system DB. DuckDB rejected. Detached job supervisor
  stays.

## Sandbox rules (from CLAUDE.md — read it first)

- The repo mount has delete protection; native mutating git strands
  `.lock` files. Use `scripts/cowork-git.sh add|commit|status|diff|log`
  for all commits. `checkout`, `merge`, `rebase`, `pull`, `push` must run
  on the host — do not attempt them.
- If a worktree is needed, ask the human to run
  `scripts/agent-worktree.sh <name>` on the host.
- Verify with `scripts/agent-verify.sh` (falls back gracefully when pixi
  is unavailable in the sandbox). The full gates are `pixi run check` and
  `pixi run test`; anything the sandbox cannot run gets listed — surface
  that list in the handoff, do not silently skip.
- Never edit `web/src/lib/api-types.gen.ts` by hand — `pixi run
  generate-api-types` (Phases 1 and 4a change routes; commit the result).
  If the sandbox can't run it, flag it as a host-side step.
- Lockfiles (`pixi.lock`, `uv.lock`, `web/package-lock.json`) only via
  their tools. Phase 1 changes deps → `pixi install` regenerates
  `pixi.lock`; if that can't run in the sandbox, flag it.
- Never read/copy/echo `.env`, `web/.env`, gcloud config, or secrets.
- Migrations must be additive/backward-compatible one version (none of
  these phases should need one; Phase 4 uses `app_config` rows).

## Per-phase completion checklist

1. All spec steps done; spec's "Open questions" either resolved in-PR (say
   how) or restated in the PR body for Hayden.
2. New + existing tests pass: `pixi run check && pixi run test` (or
   `agent-verify.sh` with the could-not-run list surfaced).
3. `pixi run generate-api-types && git diff --exit-code
   web/src/lib/api-types.gen.ts` clean (Phases 1, 4a; expect no-op for 2,
   3, 4b).
4. Phases touching `src/ai_almanac/server/`, `terraform/`, `.github/`:
   run `/security-review` and address or justify each finding in the PR
   body (Phases 1, 3, 4a, 4b).
5. Hand off host-side steps as a runnable script + PR body, not prose:
   push, PR creation, `pixi install` if skipped, Modal app redeploy
   (Phase 1 only — Modal apps deploy BEFORE the server revision), staging
   verification.

## Open questions needing Hayden's call (collected from the specs)

- P1: mount the uploads bucket or leave it dead? Tighten admin data-source
  registration to mount roots too?
- P2: pixi version + three sha256 pins (needs network; fill via the
  documented curl procedure). Silent pixi bootstrap at job launch, or
  fail-fast?
- P3: `SERVE_ACCESS_TOKEN` vs `AI_ALMANAC_`-prefixed naming; token cookie
  lifetime (30 d proposed); refuse-vs-warn on network-FS SQLite.
- P4: grandfathering rule for existing installs; `/api/setup/*` in the
  public OpenAPI schema or `include_in_schema=False`.

Defaults are stated in each spec; proceed with the spec's recommendation
unless Hayden overrides, and record the choice in the PR body.

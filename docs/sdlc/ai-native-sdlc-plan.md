# AI-Native SDLC Plan for ai-almanac

Adapting Anthropic's AI-native SDLC practices ([securing the AI-native SDLC](https://claude.com/blog/how-anthropic-secures-its-ai-native-software-development-lifecycle), [running an AI-native engineering org](https://claude.com/blog/running-an-ai-native-engineering-org)) to a solo-maintained, public, budget-constrained project.

**Companion design docs:** `design-pr-review-pipeline.html` (gate architecture) and `design-deploy-verification.html` (post-deploy monitoring).

## Guiding principles (borrowed from Anthropic, scaled down)

1. **Separate the four jobs** — creating a change, checking it, authorizing it, and deploying it should not be owned by one identity. Solo adaptation: you + Claude create; CI + independent AI reviewers check; you alone authorize (merge); a scoped GitHub Actions identity deploys.
2. **Deterministic checks for machine-provable facts** — lint, types, tests, secret scanning, dependency audit. Never spend AI tokens on what a linter can prove.
3. **AI reviewers for contextual reasoning, with separate context windows** — a general code reviewer and a security reviewer run as independent agents so one compromised/anchored context can't wave through both gates.
4. **Humans own high-impact decisions** — merges to `develop` and especially `main` remain yours.
5. **Staged rollout with post-deploy verification** — `develop` → staging soak → `main` is the admin-paced-rollout analog; every deploy is followed by automated verification.
6. **Codify knowledge where agents read it** — CLAUDE.md and committed `.claude/` config are the org-knowledge index, scaled to one repo.

## Current state (audited 2026-07-23)

| Area | Today |
|---|---|
| CI | `ci.yml`: lint (ruff) + typecheck (svelte-check) + API-type freshness + tests. No format checks, no Python type checker. |
| Security scanning | **None.** No CodeQL/SAST, secret scanning, dependency audit, or container scanning. |
| Dependency automation | None, despite three lockfiles (`pixi.lock`, `uv.lock`, `web/package-lock.json`). |
| PR automation | None. No CODEOWNERS, templates, or AI review. |
| Branch protection | UI-managed only; `check-and-test` implied required. |
| Deploy | Push-to-branch → Cloud Run via WIF OIDC (good: keyless). Migrations run before deploy. |
| Monitoring | **None.** stdlib logging → default Cloud Run logs. No alerting, uptime, or error tracking. |
| AI config | Good root CLAUDE.md; no shared `.claude/settings.json`, hooks, or agents. |
| Hygiene notes | `.env` is a credential-free template but is git-tracked while claiming to be gitignored — untrack it (`git rm --cached .env`, keep `.env.example`). Actions not SHA-pinned. |

## Phase 0 — Free deterministic baseline (~half day)

The repo is public, so GitHub's security tier is free.

1. **Repo settings (one-time, UI):** enable secret scanning **with push protection**, Dependabot alerts + security updates, and CodeQL default setup (Python + JavaScript/TypeScript).
2. **`.github/dependabot.yml`:** version updates for `pip` (root `pyproject.toml`), `npm` (`/web`), `github-actions`, and `docker` (`deploy/`). Weekly, grouped minor/patch. Limitation: Dependabot doesn't understand `pixi.lock` — it updates `pyproject.toml` constraints; refresh the lockfile with `pixi update` when merging.
3. **Branch protection → rulesets (both branches):**
   - `develop`: require PR, require `check-and-test` + (later) AI review checks, block force pushes. No human-approval count (solo).
   - `main`: require PR, require checks, restrict who can push, block force pushes and deletions.
4. **Close CI gaps:** add `ruff format --check` and `prettier --check` (web `npm run lint`) to `pixi run check`; SHA-pin all third-party actions; add top-level `permissions: contents: read` to workflows.
5. **Hygiene:** untrack `.env`; confirm no other tracked secrets (push protection covers the future).

Cost: $0. This alone gets you most of the deterministic gate Anthropic describes.

## Phase 1 — Coding-time security (~half day)

Make the local Claude Code loop safe and consistent — Anthropic's "least privilege for agents" applied at your desk.

1. **Committed `.claude/settings.json`** (shared, not `settings.local.json`): deny reads of `~/.config/gcloud/**`, real credential paths, and Terraform state; allow the pixi/npm/gcloud-readonly commands the project needs.
2. **Hooks:** PostToolUse hook running `ruff format` / `prettier --write` on edited files (agents ship formatted code by construction); PreToolUse guard blocking writes to credential paths and `web/src/lib/api-types.gen.ts` (must come from the generator).
3. **Sandboxed bash:** adopt `/sandbox` as the default posture for agent shell use.
4. **Local `/security-review`** before pushing changes touching `src/ai_almanac/server/`, `terraform/`, or auth code — uses your seat, no API cost.
5. **CLAUDE.md security section:** secrets handling rules, the api-types regeneration rule, migration-editing rules, dependency-addition policy (`pixi add --pypi`, never hand-edit lockfiles).
6. Optional: `.pre-commit-config.yaml` mirroring format+lint for human-typed commits.

## Phase 2 — Automated PR review (~1 day)

Two independent AI review gates via `anthropics/claude-code-action`, authenticated with `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` (works on Team/Enterprise seats). See `design-pr-review-pipeline.html` for the full architecture and prompt sketches.

1. **Token:** `claude setup-token` → repo secret `CLAUDE_CODE_OAUTH_TOKEN`. Caveats: draws from *your personal seat quota* — the quota guardrails below matter; long-lived but rotate on a calendar reminder; if quota contention bites, revisit hosted Claude Code Review (admin-enabled, subscription-billed) or a small API key.
2. **`claude-review.yml` — general reviewer:** PRs to `develop`, non-draft, skipping docs-only paths. Custom prompt encoding project-specific review checklist (API-type regeneration, Python-weekday convention, SQLite migration discipline, data-source auth boundaries, ROMP/blending config correctness). Sticky comment, updated per push.
3. **`claude-security-review.yml` — security reviewer, separate context:** triggers only on risky paths (`src/ai_almanac/server/**`, `terraform/**`, `.github/**`, `modal/**`, `deploy/**`). Security-focused prompt (injection, authz on data-source ownership, SSRF in `gs://`/local-path validation, secrets, workflow injection). Note: the dedicated `claude-code-security-review` action is API-key-primary as of mid-2026, so this runs as a second `claude-code-action` job with a security prompt until OAuth support lands.
4. **Blocking policy:** report-only for the first few weeks; then make "review completed" a required check; optionally later fail on high-severity findings only.
5. **Quota guardrails:** skip drafts and docs-only diffs, `concurrency` cancel on new pushes, cap agent turns, path-filter the security job. `@claude` mentions for on-demand follow-ups.

## Phase 3 — Deployment verification (~1 day)

CI-based, cloud-portable post-deploy monitoring. See `design-deploy-verification.html`.

1. **`verify` job** appended to both deploy workflows: poll the new Cloud Run revision to readiness → smoke-test `/health` (exists at `server/app.py`) plus 2–3 read-only API endpoints → scan the first ~10 minutes of logs for ERROR-severity entries → fail the workflow (and notify) on regression.
2. **Portability:** all cloud-specific commands live in `scripts/post_deploy_check.sh`; an AWS migration swaps one script, not the workflows.
3. **Optional Claude triage:** on verification failure, a `claude-code-action` step summarizes the diff + error logs into the job summary — first-pass incident triage, Anthropic-style, for the cost of one agent run per *failed* deploy.
4. **Rollback runbook** (`docs/sdlc/rollback.md`): `gcloud run services update-traffic <svc> --to-revisions <prev>=100`, plus the migration-rollback caveat (migrations run pre-deploy, so document backward-compatible-migration expectations).

## Phase 4 — Deferred / trigger-based

| Item | Trigger to adopt |
|---|---|
| Hosted Claude Code Review (admin-enabled, subscription-billed, zero-maintenance) | You get Owner access / it exits research preview / token quota becomes painful |
| Sentry + external uptime checks (both cloud-agnostic) | First real users, or first production incident you learn about late |
| OpenTelemetry instrumentation | After the AWS migration decision settles |
| CODEOWNERS + required human approval | Second regular contributor |
| Python type checker (pyright) in `check` | Any time; cheap win that strengthens the deterministic gate |
| Container image scanning (Trivy) in deploy workflows | Before AWS migration or first security-sensitive user data |

## Cost summary

Everything in Phases 0–3 is $0 in tooling: GitHub security features (public repo), Actions minutes (public repo = free), and Claude usage billed to your existing seat. The only real budget is your seat quota consumed by PR reviews — guardrails in Phase 2 keep that to roughly one or two agent runs per PR.

## Suggested sequencing

Phase 0 first (an afternoon, pure wins). Phase 1 and Phase 3 are independent of each other. Phase 2 last, since its checks slot into the branch-protection rules Phase 0 creates. Each phase is one PR against `develop`.

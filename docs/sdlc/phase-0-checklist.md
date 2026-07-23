# Phase 0 — one-time repo settings checklist

The Phase 0 PR adds everything that lives in the repo (Dependabot config,
CodeQL workflow, format gates, SHA-pinned actions). The items below are
GitHub *settings* and need a repo admin in the UI or `gh` CLI. ~10 minutes.

## 1. Secret scanning + push protection

Settings → Security → Code security → "Secret scanning": enable
**Secret scanning** and **Push protection**.

```bash
gh api -X PATCH repos/AI-Almanac/ai-almanac \
  -f 'security_and_analysis[secret_scanning][status]=enabled' \
  -f 'security_and_analysis[secret_scanning_push_protection][status]=enabled'
```

## 2. Dependabot alerts + security updates

Settings → Security → Code security: enable **Dependabot alerts** and
**Dependabot security updates**. (Version updates come from the committed
`.github/dependabot.yml` automatically.)

```bash
gh api -X PUT repos/AI-Almanac/ai-almanac/vulnerability-alerts
gh api -X PUT repos/AI-Almanac/ai-almanac/automated-security-fixes
```

## 3. CodeQL

Nothing to enable: the committed `.github/workflows/codeql.yml` is an
*advanced setup*. Do **not** also turn on "default setup" under Code
security → Code scanning (the two conflict). Findings appear under
Security → Code scanning after the first run on `develop`.

## 4. Branch rulesets

Import `docs/sdlc/rulesets/develop-ruleset.json` and `main-ruleset.json`:
Settings → Rules → Rulesets → New ruleset → Import a ruleset.

```bash
gh api -X POST repos/AI-Almanac/ai-almanac/rulesets \
  --input docs/sdlc/rulesets/develop-ruleset.json
gh api -X POST repos/AI-Almanac/ai-almanac/rulesets \
  --input docs/sdlc/rulesets/main-ruleset.json
```

What they enforce (solo-tuned): PRs required on both branches with the
`check-and-test` status check; no force pushes or deletion; no
human-approval count (you self-merge); `main` additionally requires
branches to be up to date before merging and allows merge commits only.
When AI PR reviews land (Phase 2), their check contexts get added to the
`develop` ruleset. If a legacy classic branch-protection rule exists on
either branch, delete it after importing — rulesets replace it.

## 5. Blame hygiene (local, each clone)

The one-time reformat commit is listed in `.git-blame-ignore-revs`.
GitHub's blame view respects it automatically; locally run:

```bash
git config blame.ignoreRevsFile .git-blame-ignore-revs
```

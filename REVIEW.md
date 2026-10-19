# REVIEW.md

Review rules for ai-almanac. The CI reviewers (`claude-review`,
`claude-security-review`) follow this file, and so should a local
`/code-review` or a human reviewer. CLAUDE.md describes the project; this file
says what a review of it should look for and how to report it.

## What failure looks like here

Look for these first. Each one has shipped or nearly shipped before.

- **Wrong numbers presented as right.** A skill map, score, onset date, or
  lead-time bin that is silently wrong is worse than a crash. Check unit
  conversions, grid alignment, date/lead binning, NaN handling, and joins
  that drop or duplicate rows without saying so.
- **A deploy that breaks one version back.** Anything persisted or deployed
  separately from the Cloud Run image must work with both the previous and
  the new code, because rollouts are not atomic and a rollback runs old code
  against new state:
  - database migrations (deploys migrate before routing traffic)
  - job-config and other JSON stored in the database or read by in-flight
    jobs
  - Terraform/OpenTofu changes, which are applied by hand, separately from
    the image
  - the container `ENTRYPOINT`, command, and required environment variables
  - Modal apps, which deploy separately from the backend
  - DB settings overlay keys and feature flags
- **A user seeing another user's data.** User-registered data sources and
  jobs are private; admin-registered rows are shared; example jobs are
  visible to everyone. Every new query or route on user-scoped resources
  needs an authorization check.
- **Bad input surfacing as a 500.** Input is parsed at the boundary. A bare
  `ValueError` or `ValidationError` escaping a router is a bug; it should be
  a 422 with a clear message.
- **SQLite-only correctness.** CI runs SQLite; prod runs Postgres. Flag
  integer widths, type affinity, case sensitivity, and SQL that only one of
  them accepts.
- **Untested behavior changes**, especially logic in `modal/`, which tests
  cannot import. Name the case that should be tested.
- **Frontend state that goes stale.** Data not reloaded when a route param
  changes, SSR/hydration flashes, fetches with no error or retry path, and
  one-shot URL flags that replay on Back.
- **The same fact maintained in two layers**, such as a client constant that
  mirrors server config. Prefer exposing it from the API.
- **Data lifecycle.** Objects referenced long-term (example outputs, shared
  datasets) must not live under a bucket prefix with an expiry rule.
- **Widened lint or format scope.** Newly covered paths bring their
  pre-existing violations; flag anything that will fail CI.

## Security review

The security reviewer covers only these, in priority order:

1. Injection: SQL built from strings, shell/subprocess calls, unsafe
   deserialization.
2. AuthN/AuthZ: the data-ownership rules above, Globus auth flows, and any
   endpoint on user-scoped resources without authorization.
3. Path and URL validation: local-directory and `gs://` data-source pointers
   (traversal, SSRF), and file upload/download handling.
4. Secrets: anything logged, echoed, committed, or baked into images; Secret
   Manager values leaving the boundary.
5. GitHub Actions: untrusted event data (PR titles, branch names, comment
   bodies) interpolated into `run:` blocks; overly broad permissions;
   actions not pinned to a commit SHA.
6. Terraform/IAM: privilege widening, and public exposure of buckets or
   services.

Report only what is exploitable, with the attack path.

## Skip

- Generated and tool-owned files: `web/src/lib/api-types.gen.ts`,
  `pixi.lock`, `uv.lock`, `web/package-lock.json`.
- Fixtures under `testdata/`.
- Anything CI already enforces: formatting, lint, type checks, and whether
  the generated API types are current. Do not mention these, even to confirm
  they pass.
- Style preferences a formatter or linter could own.

## Evidence

- Verify every finding against the code before reporting it. Do not
  speculate about code you have not read.
- Cite `file:line` for each finding, and say what goes wrong: the input or
  state, and the wrong result.
- Give a concrete fix.

## Severity

- **Critical**: data loss, a security hole, a broken deploy or rollback, or
  wrong results shown as correct.
- **Major**: a bug in changed behavior that users or deploys will hit, or a
  risky change with no test.
- **Minor**: an edge case, missing error handling, or a maintainability cost
  you can name.
- **Nit**: anything else. Report at most 3, after everything else.

Tag a finding **Pre-existing** when the problem is in code this change did
not introduce. It is worth knowing but does not block this change.

## Reporting

- Start with the finding count by severity, e.g.
  `2 findings: 1 Major, 1 Minor`, or `No findings.`
- Then list findings by severity, each with its `file:line`.
- If there are no findings, stop after two sentences on what you checked.
- Post each Critical or Major finding that is not Pre-existing as an inline
  comment on its line as well, so it has to be resolved before merge.

## Re-reviews

When a change was reviewed before, review only what changed since that
review, with the previous review as context.

- Do not repeat findings from the previous review. Report only new findings,
  and previous findings the new commits did not fix or made worse.
- Say in one line which previous findings the new commits fixed.
- After three previous reviews, report only Critical and Major findings. If
  any remain, suggest reconsidering the approach rather than patching it
  further.

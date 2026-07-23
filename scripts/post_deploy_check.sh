#!/usr/bin/env bash
# Post-deploy verification (SDLC Phase 3).
#
# Usage: post_deploy_check.sh <service> <region> <base_url> [soak_seconds]
#
# 1. Waits for the service's latest revision to be Ready and serving.
# 2. Smoke-tests /health, /ready (real dependency checks), /config.js, and /.
# 3. Soaks, then scans revision logs for ERROR-severity entries and 5xx
#    responses; any hit fails the check.
#
# All cloud-specific commands live HERE, not in the workflows: an AWS
# migration rewrites this script and nothing else. Captured errors go to
# post-deploy-errors.txt for the AI triage step.

set -euo pipefail

SERVICE=${1:?service name required}
REGION=${2:?region required}
BASE_URL=${3:?base url required}
SOAK=${4:-300}
ERRFILE="post-deploy-errors.txt"
START_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)

fail() {
  echo "post-deploy-check: FAIL — $*" >&2
  exit 1
}

# --- 1. Revision readiness -------------------------------------------------
echo "Waiting for latest revision of $SERVICE to be ready..."
REVISION=""
for _ in $(seq 1 60); do
  read -r created ready <<<"$(gcloud run services describe "$SERVICE" \
    --region "$REGION" \
    --format 'value(status.latestCreatedRevisionName, status.latestReadyRevisionName)')"
  if [[ -n "$created" && "$created" == "$ready" ]]; then
    REVISION=$created
    break
  fi
  sleep 5
done
[[ -n "$REVISION" ]] || fail "revision not ready after 300s"
echo "Revision ready: $REVISION"

# --- 2. Smoke tests (cloud-agnostic) ----------------------------------------
smoke() {
  local path=$1 expect=${2:-200} code
  code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 --retry 3 \
    --retry-delay 5 "$BASE_URL$path") || fail "curl error on $path"
  [[ "$code" == "$expect" ]] || fail "$path returned $code (expected $expect)"
  echo "smoke ok: $path -> $code"
}

smoke /health
smoke /ready       # 503 here means a dependency (db/storage/runner/auth) is down
smoke /config.js
smoke /

# --- 3. Soak, then scan logs -----------------------------------------------
echo "Soaking ${SOAK}s before log scan..."
sleep "$SOAK"

FILTER="resource.type=cloud_run_revision \
AND resource.labels.service_name=$SERVICE \
AND resource.labels.revision_name=$REVISION \
AND timestamp>=\"$START_TS\" \
AND (severity>=ERROR OR httpRequest.status>=500)"

gcloud logging read "$FILTER" \
  --format 'value(timestamp, severity, httpRequest.status, textPayload, jsonPayload.message)' \
  --limit 200 >"$ERRFILE" || fail "log query failed"

ERRORS=$(grep -c . "$ERRFILE" || true)
if [[ "$ERRORS" -gt 0 ]]; then
  echo "--- captured errors ($ERRORS lines) ---"
  head -50 "$ERRFILE"
  fail "$ERRORS error/5xx log entries for $REVISION since $START_TS"
fi

echo "post-deploy-check: PASS — $REVISION healthy, no errors in ${SOAK}s soak"

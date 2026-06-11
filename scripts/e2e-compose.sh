#!/usr/bin/env sh
set -eu

project="${COMPOSE_PROJECT_NAME:-ai-almanac-e2e}"
compose="docker compose -p $project -f compose.e2e.yaml"
base_url="${AI_ALMANAC_E2E_URL:-http://localhost:18080}"
owner_header="X-E2E-User: owner"
reader_header="X-E2E-User: reader"

cleanup() {
  if [ "${KEEP_E2E_STACK:-0}" != "1" ]; then
    $compose down --volumes --remove-orphans
  fi
}
trap cleanup EXIT

request() {
  method="$1"
  path="$2"
  identity="$3"
  body="${4:-}"
  if [ -n "$body" ]; then
    curl --fail --silent --show-error \
      --request "$method" \
      --header "$identity" \
      --header "Content-Type: application/json" \
      --data "$body" \
      "$base_url$path"
  else
    curl --fail --silent --show-error \
      --request "$method" \
      --header "$identity" \
      "$base_url$path"
  fi
}

wait_for_job() {
  job_id="$1"
  attempts=0
  while [ "$attempts" -lt 90 ]; do
    job="$(request GET "/jobs/$job_id" "$owner_header")"
    status="$(printf '%s' "$job" | jq -r '.status')"
    case "$status" in
      complete)
        printf '%s' "$job"
        return 0
        ;;
      failed|canceled)
        printf 'job ended in %s:\n%s\n' "$status" "$job" >&2
        request GET "/jobs/$job_id/logs" "$owner_header" >&2 || true
        return 1
        ;;
    esac
    attempts=$((attempts + 1))
    sleep 1
  done
  printf 'job %s did not complete before timeout\n' "$job_id" >&2
  return 1
}

$compose up --build --detach --wait --wait-timeout 180

me="$(request GET /auth/me "$owner_header")"
test "$(printf '%s' "$me" | jq -r '.role')" = "admin"
test "$(printf '%s' "$me" | jq -r '.deployment_mode')" = "shared"

obs="$(request POST /data-sources "$owner_header" '{
  "kind": "obs",
  "name": "E2E observations",
  "path": "/datasets/ethiopia/obs",
  "region": "ethiopia",
  "metadata": {
    "obs_file_pattern": "{}.nc",
    "obs_var": "RAINFALL"
  }
}')"
obs_id="$(printf '%s' "$obs" | jq -er '.id')"
test "$(printf '%s' "$obs" | jq -r '.status')" = "ready"

model="$(request POST /data-sources "$owner_header" '{
  "kind": "model",
  "name": "E2E FuXi",
  "path": "/datasets/ethiopia/fuxi",
  "region": "ethiopia",
  "metadata": {
    "file_pattern": "{}.nc",
    "model_var": "tp",
    "model_type": "AIWP"
  }
}')"
model_id="$(printf '%s' "$model" | jq -er '.id')"
test "$(printf '%s' "$model" | jq -r '.status')" = "ready"

job="$(request POST /jobs "$owner_header" "{
  \"dataset_id\": \"$obs_id\",
  \"model_name\": \"$model_id\",
  \"run_id\": \"e2e-run\",
  \"params\": {\"region\": \"ethiopia\"}
}")"
job_id="$(printf '%s' "$job" | jq -er '.id')"
wait_for_job "$job_id" >/dev/null

attempts=0
while [ "$attempts" -lt 15 ]; do
  artifacts="$(request GET "/jobs/$job_id/artifacts" "$owner_header")"
  if [ "$(printf '%s' "$artifacts" | jq 'length')" -ge 6 ]; then
    break
  fi
  attempts=$((attempts + 1))
  sleep 1
done
test "$(printf '%s' "$artifacts" | jq 'length')" -ge 6

metrics="$(request GET "/jobs/$job_id/metrics" "$owner_header")"
test "$(printf '%s' "$metrics" | jq '.windows | length')" -ge 1

request POST "/jobs/$job_id/share" "$owner_header" >/dev/null
shared="$(request GET "/jobs/$job_id" "$reader_header")"
test "$(printf '%s' "$shared" | jq -r '.visibility')" = "shared"
test "$(printf '%s' "$shared" | jq -r '.is_owner')" = "false"

$compose restart app
attempts=0
until request GET /ready "$owner_header" >/dev/null 2>&1; do
  attempts=$((attempts + 1))
  if [ "$attempts" -ge 30 ]; then
    printf 'application did not become ready after restart\n' >&2
    exit 1
  fi
  sleep 1
done
request GET "/jobs/$job_id" "$owner_header" >/dev/null

request DELETE "/jobs/$job_id" "$owner_header" >/dev/null
status_code="$(
  curl --silent --output /dev/null --write-out '%{http_code}' \
    --header "$owner_header" "$base_url/jobs/$job_id"
)"
test "$status_code" = "404"

printf 'Compose end-to-end benchmark flow passed for job %s\n' "$job_id"

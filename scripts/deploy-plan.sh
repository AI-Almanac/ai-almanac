#!/usr/bin/env bash
# deploy-plan — decide which parts of a release a deploy run has to ship: the
# image (Cloud Run), the Modal apps, and the database migrations.
#
# A run that follows a successful run ships only what changed since that run's
# commit. It ships everything when it can't trust what is deployed: on a manual
# dispatch, after a failed or cancelled run (it may have shipped part of its
# commit), when that commit isn't in the clone, and when traffic is pinned to
# one revision by scripts/rollback.sh (the backend and Modal have to come back
# to a single release).
#
# Usage:
#   scripts/deploy-plan.sh <workflow file> <service> <region>
#
# Prints image=, modal= and migrations= lines (true/false) for $GITHUB_OUTPUT;
# reasons go to stderr. DEPLOY_EVERYTHING=true ships everything.
#
# Needs: git with full history, gh (actions: read), gcloud, jq. GITHUB_RUN_ID
# identifies the current run so its predecessor can be found.

set -euo pipefail

log() { printf 'deploy-plan: %s\n' "$*" >&2; }

# --- what a changed path ships (pure: one repo-relative path as $1) -----------
# Mirrors the Dockerfile's COPY lines; tests and docs never reach the image.
# The wheel also packages modal/blending_app.py, but only local job runners
# execute that copy: Cloud Run runs jobs on Modal, so it ships with Modal.
ships_in_image() {
	case $1 in
	*.md | web/tests/* | *.test.ts) return 1 ;;
	src/* | web/* | Dockerfile | pyproject.toml | pixi.lock) return 0 ;;
	esac
	return 1
}

# The Modal apps plus the repo files they bundle with add_local_file; pixi.lock
# pins the Modal client that deploys them.
ships_to_modal() {
	case $1 in
	modal/* | pixi.lock) return 0 ;;
	src/ai_almanac/server/config/forecast_models.yaml) return 0 ;;
	src/ai_almanac/server/services/forecast_pipeline.py) return 0 ;;
	src/ai_almanac/server/services/e2s.py) return 0 ;;
	src/ai_almanac/server/services/focus_area.py) return 0 ;;
	src/ai_almanac/server/services/romp.py) return 0 ;;
	esac
	return 1
}

changes_schema() {
	case $1 in
	src/ai_almanac/server/alembic/* | src/ai_almanac/server/alembic.ini) return 0 ;;
	esac
	return 1
}

print_plan() { printf 'image=%s\nmodal=%s\nmigrations=%s\n' "$1" "$2" "$3"; }
full_plan() { print_plan true true true; }

# Reads changed paths, one per line, on stdin.
plan_for_paths() {
	local image=false modal=false migrations=false path
	while IFS= read -r path; do
		if ships_in_image "$path"; then image=true; fi
		if ships_to_modal "$path"; then modal=true; fi
		# The migrate job runs from the new image, so a schema change needs one.
		if changes_schema "$path"; then migrations=true image=true; fi
	done
	print_plan "$image" "$modal" "$migrations"
}

# Reads `gh run list --json databaseId,status,conclusion,headSha` on stdin and
# prints "<conclusion>\t<sha>" of the newest finished run older than $1.
previous_run() {
	jq -r --argjson current "$1" '
		[.[] | select(.status == "completed" and .databaseId < $current)]
		| first // empty | [.conclusion, .headSha] | @tsv'
}

# --- queries -------------------------------------------------------------------
recent_runs() {
	gh run list --workflow "$1" --limit 30 --json databaseId,status,conclusion,headSha
}

traffic_pinned() {
	local service
	service=$(gcloud run services describe "$1" --region "$2" --format=json)
	jq -e 'any(.status.traffic[]; .latestRevision == true and .percent == 100) | not' \
		<<<"$service" >/dev/null
}

main() {
	local workflow=$1 service=$2 region=$3 previous conclusion base
	if [[ ${DEPLOY_EVERYTHING:-false} == true ]]; then
		log "shipping everything: manual run"
		full_plan
		return
	fi
	if traffic_pinned "$service" "$region"; then
		log "shipping everything: $service traffic is pinned to one revision (rollback)"
		full_plan
		return
	fi
	previous=$(recent_runs "$workflow" | previous_run "$GITHUB_RUN_ID")
	IFS=$'\t' read -r conclusion base <<<"$previous"
	if [[ ${conclusion:-} != success ]]; then
		log "shipping everything: the previous run ended ${conclusion:-nowhere (none found)}"
		full_plan
		return
	fi
	if ! git cat-file -e "$base^{commit}" 2>/dev/null; then
		log "shipping everything: the previous run's commit $base is not in this clone"
		full_plan
		return
	fi
	log "shipping what changed since $base"
	git diff --name-only --no-renames "$base" HEAD | plan_for_paths
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
	main "$@"
fi

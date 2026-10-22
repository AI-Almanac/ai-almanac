#!/usr/bin/env bash
# rollback — move an environment back to an earlier release in one command.
#
# Rolls Cloud Run traffic AND the Modal apps back to the same commit, so the
# backend never calls Modal code from a different release. The database schema
# is left as is; see "The migration caveat" in docs/sdlc/rollback.md.
#
# Usage:
#   scripts/rollback.sh [--dry-run] [--yes] [--skip-deploy-check] <staging|prod> [<sha>]
#
#   <sha>      roll to the newest ready revision built from this commit.
#              Without it: the newest ready revision older than the one now
#              serving traffic that was built from a different commit.
#   --dry-run  resolve the target, then print the commands that would change
#              anything instead of running them.
#   --yes      skip the confirmation prompt.
#   --skip-deploy-check
#              don't ask gh whether a deploy run is in progress (check by hand).
#
# Needs: gcloud (authenticated for the project), jq, git, pixi, curl, gh, and a
# Modal profile with deploy access to the target Modal environment.

set -euo pipefail

REGION=us-central1
PROJECT=${GCP_PROJECT:-ai-almanac}
MODAL_APPS=(app blending_app forecasts_app)

die() { printf 'rollback: %s\n' "$*" >&2; exit 1; }
log() { printf 'rollback: %s\n' "$*" >&2; }
usage() { sed -n '8,18s/^# \{0,1\}//p' "$0"; }

# --- command runners: everything that changes state goes through these --------
quote() { printf '%q ' "$@"; }

run() {
	if $DRY_RUN; then
		printf '+ %s\n' "$(quote "$@")"
	else
		"$@"
	fi
}

run_in() {
	local dir=$1
	shift
	if $DRY_RUN; then
		printf '+ (cd %q && %s)\n' "$dir" "$(quote "$@")"
	else
		(cd "$dir" && "$@")
	fi
}

# --- revision selection (pure: TSV of "<revision>\t<commit sha>" on stdin) ------
previous_revision() {
	awk -F'\t' -v serving="$1" '
		$1 == serving { found = 1; image = $2; next }
		found && $2 != image { print; exit }'
}

# Prefers the serving revision ($2) when it runs this sha, so a resync leaves
# traffic alone even if a config-only apply made a newer same-image revision.
revision_for_sha() {
	awk -F'\t' -v sha="$1" -v serving="${2:-}" '
		$2 != sha { next }
		$1 == serving { print; found = 1; exit }
		first == "" { first = $0 }
		END { if (!found && first != "") print first }'
}

# --- Cloud Run queries (read-only, run even under --dry-run) -----------------
serving_revision() {
	gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format=json |
		jq -r '[.status.traffic[] | select(.percent == 100) | .revisionName] | if length == 1 then .[0] else empty end'
}

# Cloud Run stores the image by digest, so the commit comes from the
# commit-sha label the deploy workflows set on each revision, then from the
# tag of the image as deployed (client.knative.dev/user-image).
ready_revisions_newest_first() {
	gcloud run revisions list --service "$SERVICE" --region "$REGION" --project "$PROJECT" --format=json |
		jq -r 'def commit_sha:
				.metadata.labels["commit-sha"]
				// (.metadata.annotations["client.knative.dev/user-image"] // "" | capture(":(?<sha>[0-9a-f]{40})$").sha)
				// "unknown";
			map(select(any(.status.conditions[]?; .type == "Ready" and .status == "True")))
			| sort_by(.metadata.creationTimestamp) | reverse | .[]
			| [.metadata.name, commit_sha] | @tsv'
}

fetch_origin() {
	git fetch --quiet origin || log "git fetch failed; using local refs"
}

resolve_commit() {
	git rev-parse --verify --quiet "$1^{commit}" || die "unknown commit: $1"
}

# --- steps -------------------------------------------------------------------
confirm() {
	if $DRY_RUN || $ASSUME_YES; then
		return 0
	fi
	[[ -t 0 ]] || die "no terminal to confirm on; pass --yes to proceed"
	local reply
	read -r -p "Roll $ENV_NAME to $TARGET_REVISION ($TARGET_SHA)? [y/N] " reply
	[[ $reply == [yY] ]] || die "aborted"
}

# The deploy job ends by routing traffic to latest, which would undo this.
# A run that is only soaking in its verify job is fine; one waiting for
# approval to start its deploy job is not.
ensure_no_deploy_running() {
	local workflow="deploy-$ENV_NAME.yml" run_ids id
	$SKIP_DEPLOY_CHECK && return 0
	run_ids=$(gh run list --workflow "$workflow" --limit 10 --json databaseId,status \
		--jq '.[] | select(.status != "completed") | .databaseId') ||
		die "could not ask gh about $workflow runs; check by hand that none is deploying, then pass --skip-deploy-check"
	for id in $run_ids; do
		deploy_job_finished "$id" ||
			die "$workflow run $id has not finished its deploy job (pending approval counts); wait for or cancel it, then rerun"
	done
}

deploy_job_finished() {
	local finished
	finished=$(gh run view "$1" --json jobs \
		--jq '[.jobs[] | select(.name == "deploy" and .status == "completed")] | length') ||
		die "could not ask gh about run $1; check it by hand, then pass --skip-deploy-check"
	[[ $finished != 0 ]]
}

WORKTREE=""
remove_worktree() {
	if [[ -n $WORKTREE ]]; then
		git worktree remove --force "$WORKTREE"
	fi
}
trap remove_worktree EXIT

prepare_release() {
	if $DRY_RUN; then
		RELEASE_DIR="<temp-worktree>"
	else
		RELEASE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/almanac-rollback.XXXXXX")
	fi
	if ! run git worktree add --detach "$RELEASE_DIR" "$TARGET_SHA"; then
		rmdir "$RELEASE_DIR"
		die "could not check out $TARGET_SHA"
	fi
	$DRY_RUN || WORKTREE=$RELEASE_DIR
	# pyproject force-includes web/build, so pixi cannot install the package
	# (and therefore the env) without it. Modal deploys don't need the SPA.
	run mkdir -p "$RELEASE_DIR/web/build"
	require_modal_apps
	run_in "$RELEASE_DIR" pixi install --locked
}

require_modal_apps() {
	$DRY_RUN && return 0
	local app
	for app in "${MODAL_APPS[@]}"; do
		[[ -f "$RELEASE_DIR/modal/$app.py" ]] || die "modal/$app.py does not exist at $TARGET_SHA; roll back by hand"
	done
}

deploy_modal_apps() {
	local app deployed=()
	for app in "${MODAL_APPS[@]}"; do
		run_in "$RELEASE_DIR" pixi run --locked python -m modal deploy --env "$MODAL_ENV" "modal/$app.py" ||
			die "modal deploy of $app failed after traffic moved (already redeployed: ${deployed[*]:-none}). Fix the cause, then rerun: scripts/rollback.sh $ENV_NAME $TARGET_SHA"
		deployed+=("$app")
	done
}

smoke_ready() {
	if $DRY_RUN; then
		printf '+ curl %q  # until 200, up to 60s\n' "$BASE_URL/ready"
		return 0
	fi
	local code=000
	for _ in $(seq 1 12); do
		code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "$BASE_URL/ready") || code=000
		if [[ $code == 200 ]]; then
			log "$BASE_URL/ready -> 200"
			return 0
		fi
		sleep 5
	done
	die "$BASE_URL/ready returned $code after 60s; check the 503 body for the failing dependency"
}

# --- main --------------------------------------------------------------------
parse_args() {
	DRY_RUN=false
	ASSUME_YES=false
	SKIP_DEPLOY_CHECK=false
	local positional=() arg
	for arg in "$@"; do
		case $arg in
		--dry-run) DRY_RUN=true ;;
		--yes | -y) ASSUME_YES=true ;;
		--skip-deploy-check) SKIP_DEPLOY_CHECK=true ;;
		-h | --help)
			usage
			exit 0
			;;
		-*) die "unknown flag: $arg" ;;
		*) positional+=("$arg") ;;
		esac
	done
	if ((${#positional[@]} < 1 || ${#positional[@]} > 2)); then
		usage >&2
		exit 2
	fi
	ENV_NAME=${positional[0]}
	REQUESTED_SHA=${positional[1]:-}
}

select_env() {
	case $1 in
	prod)
		SERVICE=almanac-backend
		MODAL_ENV=main
		BASE_URL=https://ai-almanac.org
		;;
	staging)
		SERVICE=almanac-backend-staging
		MODAL_ENV=staging
		BASE_URL=https://staging.ai-almanac.org
		;;
	*) die "environment must be staging or prod, got '$1'" ;;
	esac
}

# Prints "<revision>\t<sha>" for the revision to roll to.
resolve_target() {
	local serving=$1 revisions target sha
	# errexit is off inside the $(...) that calls this, so failures need explicit exits.
	revisions=$(ready_revisions_newest_first) || die "could not list revisions of $SERVICE"
	if [[ -n $REQUESTED_SHA ]]; then
		sha=$(resolve_commit "$REQUESTED_SHA") || exit 1
		target=$(revision_for_sha "$sha" "$serving" <<<"$revisions")
		[[ -n $target ]] || die "no ready $SERVICE revision was built from $sha"
	else
		target=$(previous_revision "$serving" <<<"$revisions")
		[[ -n $target ]] || die "no ready revision of $SERVICE older than $serving built from a different commit"
	fi
	printf '%s\n' "$target"
}

main() {
	parse_args "$@"
	select_env "$ENV_NAME"
	cd "$(git rev-parse --show-toplevel)"

	local serving target
	fetch_origin
	serving=$(serving_revision)
	[[ -n $serving ]] || die "$SERVICE is not sending 100% of traffic to one revision; resolve the split by hand"
	target=$(resolve_target "$serving")
	IFS=$'\t' read -r TARGET_REVISION TARGET_SHA <<<"$target"
	[[ $TARGET_SHA =~ ^[0-9a-f]{40}$ ]] ||
		die "can't tell which commit $TARGET_REVISION was built from (got '$TARGET_SHA'); pass <sha> explicitly"
	# Checked before traffic moves, so a stale clone can't strand Modal on the bad release.
	git cat-file -e "$TARGET_SHA^{commit}" 2>/dev/null ||
		die "commit $TARGET_SHA is not in this clone; fetch it, then rerun"
	ensure_no_deploy_running

	log "env:     $ENV_NAME ($SERVICE, Modal env $MODAL_ENV)"
	log "serving: $serving"
	log "target:  $TARGET_REVISION @ $TARGET_SHA"
	[[ $TARGET_REVISION != "$serving" ]] || log "target already serves traffic; resyncing Modal apps only"
	confirm

	# Everything that can fail before Modal deploys runs before traffic moves.
	log "1/4 preparing $TARGET_SHA in a temporary worktree"
	prepare_release
	# Again: a deploy could have been approved during the install.
	ensure_no_deploy_running

	log "2/4 routing 100% of traffic to $TARGET_REVISION"
	run gcloud run services update-traffic "$SERVICE" --region "$REGION" --project "$PROJECT" \
		--to-revisions "$TARGET_REVISION=100"

	log "3/4 redeploying Modal apps from $TARGET_SHA"
	deploy_modal_apps

	log "4/4 smoke-testing $BASE_URL/ready"
	smoke_ready

	log "done. Traffic stays pinned to $TARGET_REVISION until the next deploy moves it to latest: revert or fix the bad commit first."
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
	main "$@"
fi

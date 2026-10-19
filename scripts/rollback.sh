#!/usr/bin/env bash
# rollback — move an environment back to an earlier release in one command.
#
# Rolls Cloud Run traffic AND the Modal apps back to the same commit, so the
# backend never calls Modal code from a different release. The database schema
# is left as is; see "The migration caveat" in docs/sdlc/rollback.md.
#
# Usage:
#   scripts/rollback.sh [--dry-run] [--yes] <staging|prod> [<sha>]
#
#   <sha>      roll to the newest ready revision built from this commit.
#              Without it: the newest ready revision older than the one now
#              serving traffic that runs a different image.
#   --dry-run  resolve the target, then print the commands that would change
#              anything instead of running them.
#   --yes      skip the confirmation prompt.
#
# Needs: gcloud (authenticated for the project), jq, git, pixi, curl, and a
# Modal profile with deploy access to the target Modal environment.

set -euo pipefail

REGION=us-central1
PROJECT=${GCP_PROJECT:-ai-almanac}
MODAL_APPS=(app blending_app forecasts_app)

die() { printf 'rollback: %s\n' "$*" >&2; exit 1; }
log() { printf 'rollback: %s\n' "$*" >&2; }
usage() { sed -n '8,16s/^# \{0,1\}//p' "$0"; }

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

# --- revision selection (pure: TSV of "<revision>\t<image tag>" on stdin) ------
previous_revision() {
	awk -F'\t' -v serving="$1" '
		$1 == serving { found = 1; image = $2; next }
		found && $2 != image { print; exit }'
}

revision_for_sha() {
	awk -F'\t' -v sha="$1" '$2 == sha { print; exit }'
}

# --- Cloud Run queries (read-only, run even under --dry-run) -----------------
serving_revision() {
	gcloud run services describe "$SERVICE" --region "$REGION" --project "$PROJECT" --format=json |
		jq -r '[.status.traffic[] | select(.percent == 100) | .revisionName] | if length == 1 then .[0] else empty end'
}

ready_revisions_newest_first() {
	gcloud run revisions list --service "$SERVICE" --region "$REGION" --project "$PROJECT" --format=json |
		jq -r 'map(select(any(.status.conditions[]?; .type == "Ready" and .status == "True")))
			| sort_by(.metadata.creationTimestamp) | reverse | .[]
			| [.metadata.name, (.spec.containers[0].image | sub("^.*:"; ""))] | @tsv'
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

WORKTREE=""
remove_worktree() {
	if [[ -n $WORKTREE ]]; then
		git worktree remove --force "$WORKTREE"
	fi
}
trap remove_worktree EXIT

checkout_release() {
	if $DRY_RUN; then
		RELEASE_DIR="<temp-worktree>"
	else
		RELEASE_DIR=$(mktemp -d "${TMPDIR:-/tmp}/almanac-rollback.XXXXXX")
	fi
	run git worktree add --detach "$RELEASE_DIR" "$TARGET_SHA"
	$DRY_RUN || WORKTREE=$RELEASE_DIR
	# pyproject force-includes web/build, so pixi cannot install the package
	# (and therefore the env) without it. Modal deploys don't need the SPA.
	run mkdir -p "$RELEASE_DIR/web/build"
}

deploy_modal_apps() {
	local app
	for app in "${MODAL_APPS[@]}"; do
		run_in "$RELEASE_DIR" pixi run --locked python -m modal deploy --env "$MODAL_ENV" "modal/$app.py"
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
	local positional=() arg
	for arg in "$@"; do
		case $arg in
		--dry-run) DRY_RUN=true ;;
		--yes | -y) ASSUME_YES=true ;;
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
	revisions=$(ready_revisions_newest_first)
	if [[ -n $REQUESTED_SHA ]]; then
		sha=$(resolve_commit "$REQUESTED_SHA")
		target=$(revision_for_sha "$sha" <<<"$revisions")
		[[ -n $target ]] || die "no ready $SERVICE revision runs image tag $sha"
	else
		target=$(previous_revision "$serving" <<<"$revisions")
		[[ -n $target ]] || die "no ready revision of $SERVICE older than $serving with a different image"
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
		die "$TARGET_REVISION runs image tag '$TARGET_SHA', not a commit SHA; pass <sha> explicitly"
	# Checked before traffic moves, so a stale clone can't strand Modal on the bad release.
	git cat-file -e "$TARGET_SHA^{commit}" 2>/dev/null ||
		die "commit $TARGET_SHA is not in this clone; fetch it, then rerun"

	log "env:     $ENV_NAME ($SERVICE, Modal env $MODAL_ENV)"
	log "serving: $serving"
	log "target:  $TARGET_REVISION @ $TARGET_SHA"
	[[ $TARGET_REVISION != "$serving" ]] || log "target already serves traffic; resyncing Modal apps only"
	confirm

	log "1/3 routing 100% of traffic to $TARGET_REVISION"
	run gcloud run services update-traffic "$SERVICE" --region "$REGION" --project "$PROJECT" \
		--to-revisions "$TARGET_REVISION=100"

	log "2/3 redeploying Modal apps from $TARGET_SHA"
	checkout_release
	deploy_modal_apps

	log "3/3 smoke-testing $BASE_URL/ready"
	smoke_ready

	log "done. Traffic stays pinned to $TARGET_REVISION until the next deploy moves it to latest: revert or fix the bad commit first."
}

main "$@"

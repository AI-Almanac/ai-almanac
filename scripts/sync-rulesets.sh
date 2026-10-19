#!/usr/bin/env bash
# Make the repository's branch rulesets match docs/sdlc/rulesets/*.json,
# matching each file to a live ruleset by its "name". Idempotent: run it after
# editing the JSON. Requires an admin `gh auth login` and jq.
#
#   scripts/sync-rulesets.sh           # create or replace each ruleset
#   scripts/sync-rulesets.sh --check   # print drift and exit 1; change nothing
set -euo pipefail

repo="${GITHUB_REPOSITORY:-AI-Almanac/ai-almanac}"
rulesets_dir="$(git rev-parse --show-toplevel)/docs/sdlc/rulesets"
mode="apply"

case "${1:-}" in
	"") ;;
	--check) mode="check" ;;
	*)
		printf 'usage: %s [--check]\n' "$0" >&2
		exit 2
		;;
esac

ruleset_id() {
	gh api --paginate "repos/$repo/rulesets" |
		jq -s -r --arg name "$1" 'add | map(select(.name == $name)) | .[0].id // empty'
}

# Order-independent form of a ruleset, so reordering rules is not drift.
canonical() {
	jq -S '.rules |= sort_by(.type) | .bypass_actors |= sort_by(.actor_type, .actor_id)'
}

# The live ruleset restricted to the keys the file declares: GitHub adds
# defaults (e.g. dismissal_restriction) that the file does not need to pin.
live_as_declared() {
	local id="$1" declared="$2"
	gh api "repos/$repo/rulesets/$id" | canonical | jq -S --argjson want "$declared" '
		def project($w):
			if ($w | type) == "object" and type == "object" then
				with_entries(.key as $k | select($w | has($k)) | .value |= project($w[$k]))
			elif ($w | type) == "array" and type == "array" then
				[range(0; length) as $i | .[$i] | project($w[$i])]
			else . end;
		project($want)'
}

check_one() {
	local file="$1" name="$2" id="$3" declared
	if [[ -z "$id" ]]; then
		printf 'drift: %s does not exist\n' "$name"
		return 1
	fi
	declared="$(canonical <"$file")"
	if ! diff -u --label "$file" --label "live:$name" \
		<(printf '%s\n' "$declared") <(live_as_declared "$id" "$declared"); then
		return 1
	fi
	printf 'in sync: %s\n' "$name"
}

apply_one() {
	local file="$1" name="$2" id="$3"
	if [[ -z "$id" ]]; then
		gh api -X POST "repos/$repo/rulesets" --input "$file" --jq '"created: \(.name) (\(.id))"'
	else
		gh api -X PUT "repos/$repo/rulesets/$id" --input "$file" --jq '"updated: \(.name) (\(.id))"'
	fi
}

drifted=0
for file in "$rulesets_dir"/*.json; do
	name="$(jq -r .name "$file")"
	id="$(ruleset_id "$name")"
	if [[ "$mode" == "check" ]]; then
		check_one "$file" "$name" "$id" || drifted=1
	else
		apply_one "$file" "$name" "$id"
	fi
done
exit "$drifted"

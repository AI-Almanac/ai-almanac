#!/usr/bin/env bash
# prepare-review-inputs — write the files a Claude review run starts from, and
# decide whether this run is a first review, a re-review, or not needed.
#
# Usage: prepare-review-inputs.sh <pr-number> <marker> [pathspec...]
#
# Run from the pull_request merge checkout (HEAD^1 = base, HEAD^2 = PR head).
# Each posted review embeds `<!-- <marker>: <head-sha> -->`. The newest such
# comment by the review bot sets where this review starts, so a re-review
# covers only the commits pushed since. Pathspecs limit which of the PR's
# files count; with none, all of them do.
#
# Writes to .pr-review/:
#   stat.txt, pr.diff                the whole PR
#   new-stat.txt, new.diff           commits since the last review (re-review)
#   previous-review.md               the last review (when there is one)
# Writes to $GITHUB_OUTPUT: round, since (empty on a first review), skip.
set -euo pipefail

pr="${1:?usage: $0 <pr-number> <marker> [pathspec...]}"
marker="${2:?usage: $0 <pr-number> <marker> [pathspec...]}"
shift 2

# The REST login carries the [bot] suffix, which only the GitHub App can hold;
# anyone can comment on this public repo, so a bare "claude" could be forged.
review_bot="claude[bot]"
out=.pr-review
head=$(git rev-parse HEAD^2)

emit() { echo "$1=$2" >>"${GITHUB_OUTPUT:-/dev/stdout}"; }

mkdir -p "$out"
git diff --stat=200 HEAD^1 HEAD >"$out/stat.txt"
git diff HEAD^1 HEAD >"$out/pr.diff"

pattern="<!-- ${marker}: [0-9a-f]{40} -->"
gh api --paginate "repos/${GITHUB_REPOSITORY:?}/issues/${pr}/comments" \
  --jq ".[] | select(.user.login == \"${review_bot}\") | {body}" |
  jq -s --arg pattern "$pattern" '[.[].body | select(test($pattern))]' \
    >"$out/reviews.json"

previous_count=$(jq 'length' "$out/reviews.json")
emit round "$((previous_count + 1))"

if [ "$previous_count" -eq 0 ]; then
  emit since ""
  emit skip false
  exit 0
fi

jq -r '.[-1]' "$out/reviews.json" >"$out/previous-review.md"
since=$(jq -r --arg pattern "<!-- ${marker}: (?<sha>[0-9a-f]{40}) -->" \
  '.[-1] | capture($pattern).sha' "$out/reviews.json")

if [ "$since" = "$head" ]; then
  emit since "$since"
  emit skip true
  exit 0
fi

# A rebase or force-push leaves the last reviewed commit outside the branch;
# its range is meaningless, so review the whole PR again.
if ! git merge-base --is-ancestor "$since" "$head" 2>/dev/null; then
  emit since ""
  emit skip false
  exit 0
fi

pr_files=()
while IFS= read -r -d '' file; do
  pr_files+=("$file")
done < <(git diff -z --name-only HEAD^1 HEAD -- "$@")
if [ "${#pr_files[@]}" -gt 0 ]; then
  git diff --stat=200 "$since" "$head" -- "${pr_files[@]}" >"$out/new-stat.txt"
  git diff "$since" "$head" -- "${pr_files[@]}" >"$out/new.diff"
fi

emit since "$since"
if [ -s "$out/new.diff" ]; then
  emit skip false
else
  emit skip true
fi

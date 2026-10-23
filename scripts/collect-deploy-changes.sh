#!/usr/bin/env bash
# collect-deploy-changes — write the commits a deploy shipped, with their diff,
# for the AI triage step, which has no shell to run git itself.
#
# Usage: collect-deploy-changes.sh <previous-sha> <output-file>
#
# <previous-sha> is the push event's `before`. When it is empty
# (workflow_dispatch), all zeros, or missing from the clone, the triage covers
# only the last commit.
set -euo pipefail

previous="${1-}"
output="${2:?usage: $0 <previous-sha> <output-file>}"

if ! git cat-file -e "${previous}^{commit}" 2>/dev/null; then
  previous=HEAD~1
fi

{
  echo "# Commits deployed ($previous..HEAD)"
  git log --stat "$previous..HEAD"
  echo
  echo "# Diff"
  git diff "$previous" HEAD
} >"$output"

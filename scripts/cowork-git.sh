#!/usr/bin/env bash
# cowork-git — git add/commit/status that work inside Cowork's sandboxed shell.
#
# Cowork mounts the repo with delete protection: files can be created and
# written but not unlinked. Git's lockfile protocol (create foo.lock →
# rename/unlink) strands locks in .git/ on every mutating command, which then
# block git for everyone. This wrapper avoids every unlink on the mount:
#
#   - The index is shadowed in /tmp (GIT_INDEX_FILE), where unlink works;
#     the real .git/index is refreshed by plain copy (a write, which is fine).
#   - Commits go through plumbing (write-tree / commit-tree), so no
#     .git/index.lock is ever taken.
#   - The branch ref is advanced by writing the loose ref file directly:
#     update-ref locks HEAD when moving the checked-out branch and releases
#     that lock via unlink, stranding .git/HEAD.lock every time. A direct
#     write takes no locks. We check the ref hasn't moved first and verify
#     after; the sandbox has no concurrent git, so the lost lock-based race
#     protection is moot. Cost: wrapper commits don't appear in `git reflog`.
#   - Read commands run with GIT_OPTIONAL_LOCKS=0 so `status` doesn't take an
#     opportunistic index lock it can't release.
#
# Loose-object writes may occasionally strand .git/objects/*/tmp_obj_* files
# (git unlinks the temp when the object already exists). Harmless; a native
# `git gc` or `git prune` cleans them up.
#
# Usage:
#   cowork-git.sh add <paths...>
#   cowork-git.sh commit -m <message>        # commits the staged index
#   cowork-git.sh status | diff | log [...]  # lock-free pass-through
#
# Not supported: checkout, merge, rebase, pull — run those natively.

set -euo pipefail

die() { echo "cowork-git: $*" >&2; exit 1; }

GIT_DIR=$(git rev-parse --git-dir) || die "not a git repository"
GIT_DIR=$(cd "$GIT_DIR" && pwd)
SHADOW="/tmp/cowork-git-$(echo "$GIT_DIR" | cksum | cut -d' ' -f1).index"

# The shadow must start from the repo's real index so we never lose staged
# state created natively; the copy-back keeps native git in agreement with us.
sync_in()  { cp "$GIT_DIR/index" "$SHADOW"; }
sync_out() { cp "$SHADOW" "$GIT_DIR/index"; }

cmd=${1:-}; shift || true

case "$cmd" in
	add)
		[ $# -gt 0 ] || die "add: no paths given"
		sync_in
		GIT_INDEX_FILE="$SHADOW" git add "$@"
		sync_out
		;;

	commit)
		[ "${1:-}" = "-m" ] && [ -n "${2:-}" ] || die "commit: usage: commit -m <message>"
		msg=$2
		git config user.email >/dev/null 2>&1 ||
			die "no git identity; run: git config user.name '...' && git config user.email '...'"

		sync_in
		tree=$(GIT_INDEX_FILE="$SHADOW" git write-tree)
		branch=$(git symbolic-ref --short HEAD) || die "detached HEAD not supported"
		parent=$(git rev-parse HEAD)

		[ "$tree" != "$(git rev-parse "$parent^{tree}")" ] || die "nothing staged to commit"

		commit=$(GIT_INDEX_FILE="$SHADOW" git commit-tree "$tree" -p "$parent" -m "$msg")

		# Direct loose-ref write (see header). Re-check the ref, write, verify.
		[ "$(git rev-parse "refs/heads/$branch")" = "$parent" ] ||
			die "ref moved during commit; aborting"
		mkdir -p "$GIT_DIR/refs/heads/$(dirname "$branch" 2>/dev/null || true)" 2>/dev/null || true
		printf '%s\n' "$commit" > "$GIT_DIR/refs/heads/$branch"
		[ "$(git rev-parse HEAD)" = "$commit" ] || die "ref verification failed"
		echo "[$branch $(git rev-parse --short "$commit")] $msg"
		;;

	status|diff|log|show|branch)
		GIT_OPTIONAL_LOCKS=0 git "$cmd" "$@"
		;;

	*)
		die "unsupported command '${cmd:-}'; supported: add, commit -m, status, diff, log, show, branch"
		;;
esac

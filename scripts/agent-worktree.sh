#!/usr/bin/env bash
# agent-worktree — create a ready-to-use worktree for an agent (or a human).
#
# Run this from the host, never from a sandboxed shell: `git worktree add` records
# absolute paths in .git/worktrees/<name>/gitdir and <worktree>/.git, so a sandbox
# bakes in paths that don't exist on the host. The result is a worktree that is
# broken natively (`git worktree move` fails validation) and invisible to editors.
# `--repair` fixes one that was created that way.
#
# What you get, in one command:
#   - a worktree at a visible sibling path (~/code/ai-almanac-<name>), not a
#     dot-directory that Finder and editors hide
#   - .env copied across (gitignored, so it does not come with the worktree)
#   - web/build/ present, without which hatchling's force-include fails the
#     editable install and pixi cannot create the environment
#   - a unique port pair and a private AI_ALMANAC_DATA_DIR, so parallel worktrees
#     neither fight over 8765/5173 nor share one SQLite database
#
# Usage:
#   scripts/agent-worktree.sh <name> [--base <ref>]   # create (or re-report)
#   scripts/agent-worktree.sh --repair <path>         # fix sandbox-made paths
#   scripts/agent-worktree.sh --list
#   scripts/agent-worktree.sh --remove <name>

set -euo pipefail

die() { printf 'agent-worktree: %s\n' "$*" >&2; exit 1; }
note() { printf '  %s\n' "$*"; }

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || die "not a git repository"
REPO_ROOT=$(cd "$REPO_ROOT" && pwd)
# --git-common-dir so this behaves the same when invoked from inside a worktree.
COMMON_DIR=$(cd "$(git rev-parse --git-common-dir)" && pwd)
MAIN_ROOT=$(dirname "$COMMON_DIR")
PARENT=$(dirname "$MAIN_ROOT")
PREFIX=$(basename "$MAIN_ROOT")

# ---------------------------------------------------------------------------
# Port allocation
# ---------------------------------------------------------------------------

# Deterministic from the name so a worktree keeps its ports across invocations,
# then bumped past anything already listening or already claimed by a sibling.
port_in_use() {
	local port=$1
	if command -v lsof >/dev/null 2>&1; then
		lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1 && return 0
	fi
	grep -rhs "^ALMANAC_\(API\|WEB\)_PORT=$port\$" "$PARENT/$PREFIX"-*/.env.agent 2>/dev/null | grep -q . && return 0
	return 1
}

allocate_ports() {
	local name=$1
	local slot=$(( $(printf '%s' "$name" | cksum | cut -d' ' -f1) % 40 ))
	local api web attempt=0
	while [ "$attempt" -lt 60 ]; do
		api=$(( 8800 + slot * 2 ))
		web=$(( 5200 + slot * 2 ))
		if ! port_in_use "$api" && ! port_in_use "$web"; then
			printf '%s %s\n' "$api" "$web"
			return 0
		fi
		slot=$(( (slot + 1) % 40 ))
		attempt=$(( attempt + 1 ))
	done
	die "could not find a free port pair after 60 attempts"
}

# ---------------------------------------------------------------------------
# Repair
# ---------------------------------------------------------------------------

repair() {
	local wt=$1
	[ -d "$wt" ] || die "no such directory: $wt"
	wt=$(cd "$wt" && pwd)
	local name
	name=$(basename "$wt")

	# The admin directory is named after the worktree's basename at creation time.
	local admin="$COMMON_DIR/worktrees/$name"
	[ -d "$admin" ] || die "no worktree admin dir at $admin (checked basename '$name')"

	printf 'Repairing %s\n' "$wt"

	# Both pointer files hold absolute paths; a sandbox writes its own namespace
	# into them. Plain writes, which sandboxes are allowed to do.
	printf 'gitdir: %s\n' "$admin" > "$wt/.git"
	printf '%s\n' "$wt/.git" > "$admin/gitdir"
	note "rewrote gitdir pointers"

	# `git worktree add` creates 'locked' and removes it on success; a sandbox
	# cannot unlink, so it survives and blocks move/prune on its own.
	local removed=0
	for stale in HEAD.lock index.lock locked; do
		if [ -e "$admin/$stale" ]; then
			rm -f "$admin/$stale" && removed=$(( removed + 1 ))
		fi
	done
	note "removed $removed stale lock/lock-like files"

	git worktree repair "$wt" >/dev/null 2>&1 || true
	note "git worktree repair completed"
	printf '\nRepaired. Verify with: git worktree list\n'
}

# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------

create() {
	local name=$1 base=$2
	local path="$PARENT/$PREFIX-$name"
	local branch="$name"

	if [ -d "$path" ]; then
		printf 'Worktree already exists at %s\n' "$path"
	else
		git -C "$MAIN_ROOT" fetch --quiet origin "$base" 2>/dev/null ||
			note "could not fetch origin/$base; using the local ref"
		local start="origin/$base"
		git -C "$MAIN_ROOT" rev-parse --verify --quiet "$start" >/dev/null || start="$base"

		printf 'Creating worktree\n'
		if git -C "$MAIN_ROOT" rev-parse --verify --quiet "refs/heads/$branch" >/dev/null; then
			note "branch $branch already exists; checking it out"
			git -C "$MAIN_ROOT" worktree add "$path" "$branch"
		else
			git -C "$MAIN_ROOT" worktree add "$path" -b "$branch" "$start"
		fi
	fi

	# --- bootstrap the things git does not carry across -------------------
	if [ -f "$MAIN_ROOT/.env" ] && [ ! -f "$path/.env" ]; then
		cp "$MAIN_ROOT/.env" "$path/.env"
		note ".env copied (gitignored, so git does not bring it)"
	fi

	# pyproject force-includes web/build into the wheel, so hatchling fails the
	# editable install if it is missing — and pixi therefore cannot create the
	# environment that would have run `pixi run build-web`.
	mkdir -p "$path/web/build"

	# --- per-worktree isolation ------------------------------------------
	local data_dir="$path/.almanac-data"
	mkdir -p "$data_dir"
	if [ ! -f "$path/.env.agent" ]; then
		read -r api web <<<"$(allocate_ports "$name")"
		cat > "$path/.env.agent" <<EOF
# Written by scripts/agent-worktree.sh. Gitignored.
#
# Source this before \`pixi run dev\` so this worktree does not collide with
# another. Without it every checkout shares ports 8765/5173 and, worse, one
# SQLite database and one job_outputs/ tree.
#
#   set -a; . ./.env.agent; set +a
ALMANAC_API_PORT=$api
ALMANAC_WEB_PORT=$web
AI_ALMANAC_DATA_DIR=$data_dir
EOF
		note "allocated ports $api (api) / $web (web) and a private data dir"
	else
		note "reusing existing .env.agent"
	fi

	local api web
	api=$(sed -n 's/^ALMANAC_API_PORT=//p' "$path/.env.agent")
	web=$(sed -n 's/^ALMANAC_WEB_PORT=//p' "$path/.env.agent")

	cat <<EOF

Ready.

  path     $path
  branch   $branch
  api      http://localhost:$api
  web      http://localhost:$web
  data     $data_dir

Next:
  1. Add "$path" as a Cowork folder (one-time, so the agent can reach it).
  2. cd "$path" && set -a && . ./.env.agent && set +a && pixi run dev
EOF
}

# ---------------------------------------------------------------------------

case "${1:-}" in
	--repair)
		[ -n "${2:-}" ] || die "--repair needs a worktree path"
		repair "$2"
		;;
	--list)
		git -C "$MAIN_ROOT" worktree list
		;;
	--remove)
		[ -n "${2:-}" ] || die "--remove needs a name"
		target="$PARENT/$PREFIX-$2"
		[ -d "$target" ] || die "no worktree at $target"
		git -C "$MAIN_ROOT" worktree remove "$target"
		printf 'Removed %s (branch %s kept)\n' "$target" "$2"
		;;
	'' | -h | --help)
		sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
		;;
	-*)
		die "unknown option: $1"
		;;
	*)
		name=$1; shift
		base=develop
		while [ $# -gt 0 ]; do
			case "$1" in
				--base) base=${2:?--base needs a ref}; shift 2 ;;
				*) die "unknown argument: $1" ;;
			esac
		done
		case "$name" in
			*/*) die "name must not contain '/' (it becomes a directory suffix)" ;;
		esac
		create "$name" "$base"
		;;
esac

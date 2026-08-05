#!/usr/bin/env bash
# agent-verify — run as much of the CI gate as this machine can, and say plainly
# what it could not run.
#
# On the host this is just `pixi run check` + `pixi run test`. In a sandbox the
# pixi environment is unusable (it is solved for macOS arm64), which previously
# led to the wrong conclusion that nothing could be verified. In fact everything
# except pytest and the OpenAPI type generation is reachable, given two one-time
# installs this script performs itself.
#
# Always exits non-zero if a check that DID run failed. Steps that could not run
# are reported as SKIP and do not fail the script — but they are listed at the end
# so they never silently pass for "verified".
#
# Usage:
#   scripts/agent-verify.sh              # everything reachable
#   scripts/agent-verify.sh --python     # ruff only
#   scripts/agent-verify.sh --web        # svelte-check, prettier, vitest only
#   scripts/agent-verify.sh --quiet      # summary lines only

set -uo pipefail

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
	echo "agent-verify: not a git repository" >&2
	exit 1
}
cd "$REPO_ROOT"

WANT_PYTHON=1
WANT_WEB=1
QUIET=0
for arg in "$@"; do
	case "$arg" in
		--python) WANT_WEB=0 ;;
		--web) WANT_PYTHON=0 ;;
		--quiet) QUIET=1 ;;
		-h | --help) sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		*) echo "agent-verify: unknown argument: $arg" >&2; exit 1 ;;
	esac
done

FAILED=()
PASSED=()
SKIPPED=()

run() { # run <label> <command...>
	local label=$1; shift
	printf '\n=== %s\n' "$label"
	local out rc
	if [ "$QUIET" = 1 ]; then
		out=$("$@" 2>&1); rc=$?
		[ "$rc" -eq 0 ] || printf '%s\n' "$out" | tail -25
	else
		"$@" 2>&1 | sed 's/^/  /'
		rc=${PIPESTATUS[0]}
	fi
	if [ "$rc" -eq 0 ]; then
		PASSED+=("$label"); printf '  → pass\n'
	else
		FAILED+=("$label"); printf '  → FAIL\n'
	fi
}

skip() { SKIPPED+=("$1 — $2"); printf '\n=== %s\n  → skip (%s)\n' "$1" "$2"; }

# ---------------------------------------------------------------------------
# Is the pixi environment usable on this machine?
# ---------------------------------------------------------------------------

PIXI_OK=0
if command -v pixi >/dev/null 2>&1; then
	# Cheapest possible probe that actually executes something from the env.
	if pixi run --environment dev python -c '' >/dev/null 2>&1; then
		PIXI_OK=1
	fi
fi

if [ "$PIXI_OK" = 1 ]; then
	printf 'pixi environment is usable — running the real gate.\n'
else
	printf 'pixi environment is not usable here (it is platform-specific).\n'
	printf 'Falling back to the directly runnable subset.\n'
fi

# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------

if [ "$WANT_PYTHON" = 1 ]; then
	if [ "$PIXI_OK" = 1 ]; then
		run "ruff check"        pixi run lint-python
		run "ruff format"       pixi run format-python-check
		run "pytest"            pixi run test-python
	else
		# ruff ships prebuilt wheels for Linux, so this works in a sandbox.
		if ! python3 -m ruff --version >/dev/null 2>&1; then
			printf 'Installing ruff...\n'
			pip install --break-system-packages --quiet ruff >/dev/null 2>&1 ||
				pip install --quiet ruff >/dev/null 2>&1 || true
		fi
		if python3 -m ruff --version >/dev/null 2>&1; then
			run "ruff check"  python3 -m ruff check src tests modal scripts
			run "ruff format" python3 -m ruff format --check src tests modal scripts
		else
			skip "ruff" "could not install"
		fi
		skip "pytest" "needs the full dependency tree; run pixi run test-python on the host"
	fi
fi

# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

if [ "$WANT_WEB" = 1 ]; then
	if [ ! -d web/node_modules ]; then
		printf '\nInstalling frontend dependencies...\n'
		(cd web && npm install --no-audit --no-fund >/dev/null 2>&1) || true
	fi

	if [ ! -d web/node_modules ]; then
		skip "svelte-check" "web/node_modules missing"
		skip "prettier" "web/node_modules missing"
		skip "vitest" "web/node_modules missing"
	else
		# A node_modules installed on another platform lacks this platform's rollup
		# binary, without which Vite cannot load at all. Probe for the package
		# directory rather than `require('rollup')`: rollup is a transitive dep and
		# is not always hoisted, so requiring it reports a false negative.
		arch=$(uname -m); os=$(uname -s | tr '[:upper:]' '[:lower:]')
		case "$os-$arch" in
			linux-aarch64 | linux-arm64) pkg=@rollup/rollup-linux-arm64-gnu ;;
			linux-x86_64) pkg=@rollup/rollup-linux-x64-gnu ;;
			darwin-arm64) pkg=@rollup/rollup-darwin-arm64 ;;
			darwin-x86_64) pkg=@rollup/rollup-darwin-x64 ;;
			*) pkg="" ;;
		esac
		if [ -n "$pkg" ] && [ ! -d "web/node_modules/$pkg" ]; then
			printf 'Installing %s for this platform...\n' "$pkg"
			# --no-save: a local repair, not a dependency change. This can fail on a
			# delete-protected mount (npm renames existing dirs), but it usually
			# unpacks the target package first, so try vitest regardless.
			(cd web && npm install --no-save --no-audit --no-fund "$pkg" >/dev/null 2>&1) || true
		fi

		(cd web && npx svelte-kit sync >/dev/null 2>&1) || true

		run "svelte-check" bash -c 'cd web && npx svelte-check --tsconfig ./tsconfig.json --output human'
		run "prettier"     bash -c 'cd web && npx prettier --check .'
		# Run it and let the result speak: a genuine rollup problem surfaces as a
		# FAIL with the real error, which is more useful than a guessed skip.
		run "vitest"       bash -c 'cd web && npx vitest run'
	fi
fi

# ---------------------------------------------------------------------------
# Always host-only
# ---------------------------------------------------------------------------

if [ "$PIXI_OK" = 1 ]; then
	run "api types are current" bash -c \
		'pixi run generate-api-types >/dev/null && git diff --exit-code web/src/lib/api-types.gen.ts'
else
	skip "api types are current" "needs the app importable; run pixi run generate-api-types on the host"
fi

# ---------------------------------------------------------------------------

printf '\n%s\n' "----------------------------------------"
printf 'passed:  %s\n' "${#PASSED[@]}"
if [ "${#SKIPPED[@]}" -gt 0 ]; then
	printf 'skipped: %s\n' "${#SKIPPED[@]}"
	for s in "${SKIPPED[@]}"; do printf '  - %s\n' "$s"; done
fi
if [ "${#FAILED[@]}" -gt 0 ]; then
	printf 'FAILED:  %s\n' "${#FAILED[@]}"
	for f in "${FAILED[@]}"; do printf '  - %s\n' "$f"; done
	printf '\nNot verified. Fix the failures above.\n'
	exit 1
fi
printf '\nEverything runnable here passed.\n'
[ "${#SKIPPED[@]}" -gt 0 ] && printf 'Skipped steps still need a host run before this is merge-ready.\n'
exit 0

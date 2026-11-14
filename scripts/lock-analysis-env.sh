#!/usr/bin/env bash
# Regenerate src/ai_almanac/envs/analysis.pixi.lock from analysis.pixi.toml.
#
# pixi always writes pixi.lock next to the manifest, and the envs directory holds
# several manifests, so the solve runs in a scratch copy and only the lockfile is
# copied back.
set -euo pipefail

envs_dir="$(cd "$(dirname "$0")/../src/ai_almanac/envs" && pwd)"
scratch="$(mktemp -d)"
trap 'rm -rf "$scratch"' EXIT

cp "$envs_dir/analysis.pixi.toml" "$scratch/pixi.toml"
if [[ -f "$envs_dir/analysis.pixi.lock" ]]; then
  cp "$envs_dir/analysis.pixi.lock" "$scratch/pixi.lock"
fi
pixi lock --manifest-path "$scratch/pixi.toml"
cp "$scratch/pixi.lock" "$envs_dir/analysis.pixi.lock"

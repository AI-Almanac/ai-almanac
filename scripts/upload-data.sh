#!/usr/bin/env bash
# Upload benchmark data to the shared GCS data bucket.
#
# After uploading, register each dataset from the Data Sources admin page
# (or POST /data-sources) pointing at its gs:// prefix — no env vars, no
# terraform, no redeploy.
#
# Usage: ./scripts/upload-data.sh [--india-only | --ethiopia-only]
#
# Requires: gcloud CLI authenticated with sufficient permissions
#   - storage.objects.create on almanac-data-ai-almanac

set -euo pipefail

INDIA_DIR="${INDIA_DIR:-$HOME/code/ROMP/data/india}"
ETHIOPIA_DIR="${ETHIOPIA_DIR:-$HOME/code/ROMP/data/ethiopia}"
BUCKET="gs://almanac-data-ai-almanac"

UPLOAD_INDIA=true
UPLOAD_ETHIOPIA=true

for arg in "$@"; do
  case "$arg" in
    --india-only)    UPLOAD_ETHIOPIA=false ;;
    --ethiopia-only) UPLOAD_INDIA=false ;;
  esac
done

# ---------------------------------------------------------------------------
# India data
# ---------------------------------------------------------------------------

if $UPLOAD_INDIA; then
  if [[ ! -d "$INDIA_DIR" ]]; then
    echo "WARN: INDIA_DIR not found: $INDIA_DIR — skipping India upload"
    echo "      Override with: INDIA_DIR=/path/to/india ./scripts/upload-data.sh"
  else
    echo "==> Uploading India obs data"
    gcloud storage cp "$INDIA_DIR/imd_rainfall_data/2p0/"*.nc "$BUCKET/obs/imd-2p0/"

    echo "==> Uploading India model data"
    india_models=(aifs aifs_daily fuxi fuxi_s2s gencast graphcast ifs neuralgcm)
    for model in "${india_models[@]}"; do
      src="$INDIA_DIR/$model"
      if [[ -d "$src" ]]; then
        echo "  $model"
        gcloud storage cp "$src/"*.nc "$BUCKET/models/india/$model/"
      else
        echo "  SKIP $model (not found: $src)"
      fi
    done
  fi
fi

# ---------------------------------------------------------------------------
# Ethiopia data
# ---------------------------------------------------------------------------

if $UPLOAD_ETHIOPIA; then
  if [[ ! -d "$ETHIOPIA_DIR" ]]; then
    echo "WARN: ETHIOPIA_DIR not found: $ETHIOPIA_DIR — skipping Ethiopia upload"
    echo "      Override with: ETHIOPIA_DIR=/path/to/ethiopia ./scripts/upload-data.sh"
  else
    echo "==> Uploading Ethiopia obs data"
    gcloud storage cp "$ETHIOPIA_DIR/obs/"*.nc "$BUCKET/obs/ethiopia/"

    echo "==> Uploading Ethiopia model data"
    ethiopia_models=(aifs fuxi gencast graphcast)
    for model in "${ethiopia_models[@]}"; do
      src="$ETHIOPIA_DIR/$model"
      if [[ -d "$src" ]]; then
        echo "  $model"
        gcloud storage cp "$src/"*.nc "$BUCKET/models/ethiopia/$model/"
      else
        echo "  SKIP $model (not found: $src)"
      fi
    done
  fi
fi

echo ""
echo "==> Done. Register the uploaded prefixes on the Data Sources page:"
echo "    ${BUCKET}/obs/...    (observations)"
echo "    ${BUCKET}/models/... (model forecasts)"

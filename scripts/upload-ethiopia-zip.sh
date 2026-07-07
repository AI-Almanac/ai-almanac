#!/usr/bin/env bash
# Upload Ethiopia benchmark forecast data zips to the production GCS data bucket.
#
# By default this reads every zip file in ./data. Each archive should contain
# one or more model directories, and those directory names are preserved exactly
# in GCS:
#   AIFS-single-v2/*.nc
#   AIFS-ens-v2/*.nc
#   gencast/*.nc
#   nvidia_atlas/*.nc
#
# Production paths consumed by the app:
#   gs://almanac-data-ai-almanac/models/ethiopia/{zip-directory-name}/
#
# Usage:
#   ./scripts/upload-ethiopia-zip.sh
#   ./scripts/upload-ethiopia-zip.sh data/aifs-single-v2.zip data/gencast.zip
#   ./scripts/upload-ethiopia-zip.sh --dry-run ./data

set -euo pipefail
shopt -s nullglob

BUCKET="${BUCKET:-gs://almanac-data-ai-almanac}"
DATA_DIR="${DATA_DIR:-data}"
DRY_RUN=false
ZIP_PATHS=()

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
  echo "ERROR: $*" >&2
  exit 1
}

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    -*)
      die "unknown option: $arg"
      ;;
    *)
      ZIP_PATHS+=("$arg")
      ;;
  esac
done

if [[ ${#ZIP_PATHS[@]} -eq 0 ]]; then
  ZIP_PATHS=("$DATA_DIR"/*.zip)
elif [[ ${#ZIP_PATHS[@]} -eq 1 && -d "${ZIP_PATHS[0]}" ]]; then
  ZIP_PATHS=("${ZIP_PATHS[0]}"/*.zip)
fi

[[ ${#ZIP_PATHS[@]} -gt 0 ]] || die "no zip files found; pass zip paths or set DATA_DIR"
for zip_path in "${ZIP_PATHS[@]}"; do
  [[ -f "$zip_path" ]] || die "zip file does not exist: $zip_path"
done
command -v unzip >/dev/null 2>&1 || die "unzip is required"
if ! $DRY_RUN; then
  command -v gcloud >/dev/null 2>&1 || die "gcloud CLI is required"
fi

tmpdir="$(mktemp -d)"
cleanup() {
  rm -rf "$tmpdir"
}
trap cleanup EXIT

require_nc_files() {
  local dir="$1"
  local label="$2"
  local file
  [[ -d "$dir" ]] || die "missing $label directory: $dir"

  local files=("$dir"/*.nc)
  [[ -e "${files[0]}" ]] || die "no NetCDF files found in $label directory: $dir"
  for file in "${files[@]}"; do
    [[ -s "$file" ]] || die "empty NetCDF file found in $label directory: $file"
  done
}

nc_basenames() {
  local dir="$1"
  local file

  for file in "$dir"/*.nc; do
    [[ -e "$file" ]] || return 0
    basename "$file"
  done | sort
}

report_overlaps() {
  local source_dir="$1"
  local destination="$2"
  local local_names="$tmpdir/local-names.txt"
  local remote_names="$tmpdir/remote-names.txt"
  local overlaps="$tmpdir/overlaps.txt"

  nc_basenames "$source_dir" > "$local_names"
  gcloud storage ls "$destination/*.nc" 2>/dev/null \
    | while IFS= read -r object; do basename "$object"; done \
    | sort > "$remote_names" || true
  comm -12 "$local_names" "$remote_names" > "$overlaps"

  if [[ -s "$overlaps" ]]; then
    echo "  Replacing existing year files in $destination:"
    sed 's/^/    /' "$overlaps"
  else
    echo "  No existing .nc filenames overlap in $destination"
  fi
}

copy_nc_files() {
  local source_dir="$1"
  local destination="$2"

  if $DRY_RUN; then
    echo "DRY RUN: gcloud storage cp \"$source_dir/\"*.nc \"$destination/\""
  else
    report_overlaps "$source_dir" "$destination"
    gcloud storage cp "$source_dir/"*.nc "$destination/"
  fi
}

discover_model_dirs() {
  local data_root="$1"
  local dir
  local files

  files=("$data_root"/*.nc)
  if [[ "$(basename "$data_root")" != "obs" && -e "${files[0]}" ]]; then
    require_nc_files "$data_root" "$(basename "$data_root") model"
    model_dirs+=("$data_root")
    return
  fi

  for dir in "$data_root"/*; do
    [[ -d "$dir" ]] || continue
    [[ "$(basename "$dir")" == "obs" ]] && continue
    files=("$dir"/*.nc)
    if [[ -e "${files[0]}" ]]; then
      require_nc_files "$dir" "$(basename "$dir") model"
      model_dirs+=("$dir")
    else
      echo "WARN: skipping non-data directory without .nc files: $(basename "$dir")"
    fi
  done
}

data_root_for_zip() {
  local extract_dir="$1"
  local top_level_dirs=("$extract_dir"/*)

  if [[ ${#top_level_dirs[@]} -eq 1 && -d "${top_level_dirs[0]}" ]]; then
    echo "${top_level_dirs[0]}"
  else
    echo "$extract_dir"
  fi
}

model_dirs=()
extract_index=0
for zip_path in "${ZIP_PATHS[@]}"; do
  extract_index=$((extract_index + 1))
  extract_dir="$tmpdir/extract-$extract_index"
  mkdir -p "$extract_dir"

  echo "==> Extracting $zip_path"
  unzip -q "$zip_path" -d "$extract_dir"
  data_root="$(data_root_for_zip "$extract_dir")"

  if [[ -d "$data_root/obs" ]]; then
    echo "WARN: obs directory found in $zip_path; this script only uploads forecast data"
  fi
  discover_model_dirs "$data_root"
done
[[ ${#model_dirs[@]} -gt 0 ]] || die "no model directories with .nc files found in provided zips"

echo "==> Uploading Ethiopia model data"
for model_dir in "${model_dirs[@]}"; do
  model="$(basename "$model_dir")"
  echo "  $model"
  copy_nc_files "$model_dir" "$BUCKET/models/ethiopia/$model"
done

cat <<EOF

==> Done
Uploaded paths:
EOF

for model_dir in "${model_dirs[@]}"; do
  echo "  $BUCKET/models/ethiopia/$(basename "$model_dir")/"
done

cat <<EOF

Note: upload paths preserve the zip's model directory names. New model
directories also need corresponding platform data-source entries before users
can select them in the UI.
EOF

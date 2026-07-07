# ---------------------------------------------------------------------------
# Observation + model reforecast data — shared by prod and staging.
# Structured as:
#   obs/{dataset_id}/          — pre-loaded demo obs datasets
#   models/{model_name}/       — model reforecast NetCDF files
# Globus transfers land here. Model data can be purged after caching window.
# Per-env uploads/job-outputs buckets live in modules/almanac-env.
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "data" {
  name          = "almanac-data-${var.project_id}"
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  versioning {
    enabled = false
  }
}

output "data_bucket_name" {
  value = google_storage_bucket.data.name
}

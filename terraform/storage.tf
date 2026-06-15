locals {
  # Suffix buckets with project ID to ensure global uniqueness
  data_bucket    = "almanac-data-${var.project_id}"
  uploads_bucket = "almanac-uploads-${var.project_id}"
  outputs_bucket = "almanac-job-outputs-${var.project_id}"
}

# ---------------------------------------------------------------------------
# Observation + model reforecast data
# Structured as:
#   obs/{dataset_id}/          — pre-loaded demo obs datasets
#   models/{model_name}/       — model reforecast NetCDF files
# Globus transfers land here. Model data can be purged after caching window.
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "data" {
  name          = local.data_bucket
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  versioning {
    enabled = false
  }
}

# ---------------------------------------------------------------------------
# User-uploaded observation datasets
# Structured as: {user_id}/{dataset_id}/{filename}
# Separate bucket to scope service account permissions narrowly and apply
# independent lifecycle/retention policies.
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "uploads" {
  name          = local.uploads_bucket
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  # Delete incomplete multipart uploads after 7 days
  lifecycle_rule {
    condition {
      age = 7
      with_state = "ANY"
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }
}

# ---------------------------------------------------------------------------
# ROMP job outputs (NetCDF metrics + PNG figures)
# Structured as: {job_id}/output/  and  {job_id}/figure/
# Outputs are reproducible — expire after retention window.
# ---------------------------------------------------------------------------
resource "google_storage_bucket" "job_outputs" {
  name          = local.outputs_bucket
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = var.job_output_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

# ---------------------------------------------------------------------------
# Output variables — referenced in cloud_run.tf and iam.tf
# ---------------------------------------------------------------------------
output "data_bucket_name" {
  value = google_storage_bucket.data.name
}

output "uploads_bucket_name" {
  value = google_storage_bucket.uploads.name
}

output "job_outputs_bucket_name" {
  value = google_storage_bucket.job_outputs.name
}

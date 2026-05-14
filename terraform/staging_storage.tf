locals {
  staging_uploads_bucket = "almanac-uploads-staging-${var.project_id}"
  staging_outputs_bucket = "almanac-job-outputs-staging-${var.project_id}"
}

resource "google_storage_bucket" "uploads_staging" {
  name          = local.staging_uploads_bucket
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age        = var.staging_upload_retention_days
      with_state = "ANY"
    }
    action {
      type = "Delete"
    }
  }

  lifecycle_rule {
    condition {
      age        = 7
      with_state = "ANY"
    }
    action {
      type = "AbortIncompleteMultipartUpload"
    }
  }
}

resource "google_storage_bucket" "job_outputs_staging" {
  name          = local.staging_outputs_bucket
  location      = var.region
  storage_class = "STANDARD"

  uniform_bucket_level_access = true

  lifecycle_rule {
    condition {
      age = var.staging_job_output_retention_days
    }
    action {
      type = "Delete"
    }
  }
}

output "staging_uploads_bucket_name" {
  value = google_storage_bucket.uploads_staging.name
}

output "staging_job_outputs_bucket_name" {
  value = google_storage_bucket.job_outputs_staging.name
}

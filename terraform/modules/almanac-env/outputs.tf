output "service_name" {
  value = google_cloud_run_v2_service.backend.name
}

output "service_url" {
  value = google_cloud_run_v2_service.backend.uri
}

output "backend_sa_email" {
  value = google_service_account.backend.email
}

output "backend_sa_name" {
  value = google_service_account.backend.name
}

output "uploads_bucket_name" {
  value = google_storage_bucket.uploads.name
}

output "job_outputs_bucket_name" {
  value = google_storage_bucket.job_outputs.name
}

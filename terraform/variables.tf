variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for all resources"
  type        = string
  default     = "us-central1"
}

variable "ghcr_owner" {
  description = "GitHub user/org that owns the GHCR packages (e.g. hholb)"
  type        = string
}

# Derived image paths — resolved at plan time via locals in artifact_registry.tf
# Override these only if you push images to a different registry.
variable "frontend_image" {
  description = "Container image for the SvelteKit frontend"
  type        = string
  default     = ""
}

variable "backend_image" {
  description = "Container image for the FastAPI backend"
  type        = string
  default     = ""
}

variable "romp_image" {
  description = "ROMP worker image (used directly by Cloud Batch, not Cloud Run)"
  type        = string
  default     = ""
}

variable "db_tier" {
  description = "Cloud SQL machine tier (db-f1-micro for dev, db-g1-small for prod)"
  type        = string
  default     = "db-f1-micro"
}

# ---------------------------------------------------------------------------
# Cloud Run scaling
# Cloud SQL runs 24/7 regardless, so keeping one backend instance warm costs
# little on top of the fixed DB floor and removes the cold-start wait where the
# frontend loads before the backend is up.
# ---------------------------------------------------------------------------

variable "backend_min_instances" {
  description = "Minimum warm backend instances in production (1 avoids cold starts)."
  type        = number
  default     = 1
}

variable "backend_max_instances" {
  description = "Maximum backend instances in production (caps runaway autoscale cost)."
  type        = number
  default     = 4
}

variable "staging_backend_min_instances" {
  description = "Minimum warm staging backend instances (0 lets staging scale to zero)."
  type        = number
  default     = 0
}

variable "staging_backend_max_instances" {
  description = "Maximum staging backend instances."
  type        = number
  default     = 2
}

variable "db_password" {
  description = "Password for the almanac-backend Cloud SQL user"
  type        = string
  sensitive   = true
}

variable "custom_domain" {
  description = "Custom domain for the frontend (e.g. app.example.com or example.com). Leave empty to skip."
  type        = string
  default     = ""
}

variable "api_domain" {
  description = "Custom domain for the backend API (e.g. api.example.com). Leave empty to skip."
  type        = string
  default     = ""
}

variable "frontend_url" {
  description = "Frontend Cloud Run URL for CORS allowlist. Set after first deploy if unknown."
  type        = string
  default     = ""
}

# ---------------------------------------------------------------------------
# Auth (shared mode, auth_mode=globus)
# Identities are admitted by valid Globus token; these allow-lists grant admin.
# At least one of admin_emails / admin_subjects must be set or the backend
# refuses to start in shared mode.
# ---------------------------------------------------------------------------

variable "admin_emails" {
  description = "Comma-separated admin emails (Globus identity emails)."
  type        = string
  default     = ""
}

variable "admin_subjects" {
  description = "Comma-separated admin Globus subjects (OIDC sub)."
  type        = string
  default     = ""
}

variable "staging_custom_domain" {
  description = "Custom domain for the staging frontend. Leave empty to skip."
  type        = string
  default     = "staging.ai-almanac.org"
}

variable "staging_api_domain" {
  description = "Custom domain for the staging backend API. Leave empty to skip."
  type        = string
  default     = "api-staging.ai-almanac.org"
}

variable "staging_frontend_url" {
  description = "Staging frontend origin for the staging backend CORS allowlist."
  type        = string
  default     = "https://staging.ai-almanac.org"
}

variable "staging_db_password" {
  description = "Password for the staging Cloud SQL user. Defaults to db_password when empty."
  type        = string
  sensitive   = true
  default     = ""
}

variable "staging_job_output_retention_days" {
  description = "Days before staging job output files are automatically deleted from GCS"
  type        = number
  default     = 30
}

variable "staging_upload_retention_days" {
  description = "Days before staging uploads are automatically deleted from GCS"
  type        = number
  default     = 30
}

variable "llm_base_url" {
  description = "OpenAI-compatible base URL for the backend chat assistant. Leave empty to disable chat."
  type        = string
  default     = "https://openrouter.ai/api/v1/"
}

variable "llm_model" {
  description = "Model name sent to the configured LLM provider."
  type        = string
  default     = "anthropic/claude-haiku-4-5"
}

variable "enable_run_code" {
  description = "Whether the chat assistant may use the run_code tool."
  type        = bool
  default     = true
}

variable "enable_run_code_sandbox" {
  description = "Whether the chat assistant may use the run_code_sandbox tool."
  type        = bool
  default     = true
}

variable "job_output_retention_days" {
  description = "Days before job output files are automatically deleted from GCS"
  type        = number
  default     = 30
}

# ---------------------------------------------------------------------------
# Job runner / data config
# ---------------------------------------------------------------------------

variable "job_runner" {
  description = "Job runner backend: 'modal' (production) or 'docker' (local dev)"
  type        = string
  default     = "modal"
}

variable "ethiopia_obs_dir" {
  description = "GCS URI for the Ethiopia obs dataset (datasets.yaml id: ethiopia)"
  type        = string
  default     = ""
}
variable "imd_2p0_obs_dir" {
  description = "GCS URI for the IMD India 2-degree obs dataset (datasets.yaml id: imd-2p0)"
  type        = string
  default     = ""
}

variable "india_aifs_model_dir" {
  type    = string
  default = ""
}
variable "india_aifs_daily_model_dir" {
  type    = string
  default = ""
}
variable "india_fuxi_model_dir" {
  type    = string
  default = ""
}
variable "india_fuxi_s2s_model_dir" {
  type    = string
  default = ""
}
variable "india_gencast_model_dir" {
  type    = string
  default = ""
}
variable "india_graphcast_model_dir" {
  type    = string
  default = ""
}
variable "india_ifs_model_dir" {
  type    = string
  default = ""
}
variable "india_neuralgcm_model_dir" {
  type    = string
  default = ""
}
variable "ethiopia_aifs_model_dir" {
  type    = string
  default = ""
}
variable "ethiopia_fuxi_model_dir" {
  type    = string
  default = ""
}
variable "ethiopia_gencast_model_dir" {
  type    = string
  default = ""
}
variable "ethiopia_graphcast_model_dir" {
  type    = string
  default = ""
}

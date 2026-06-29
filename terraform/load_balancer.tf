# ---------------------------------------------------------------------------
# Shared HTTPS load balancer (prod + staging)
#
# One global external Application Load Balancer fronts both environments via
# host-based routing. It replaces Cloud Run *domain mappings*, which route
# through ghs.googlehosted.com and strip the `Authorization` header — so Globus
# bearer tokens never reached the backend and every authed route 401'd. A
# serverless NEG per Cloud Run service forwards Authorization intact.
#
# Routing (single-image services serve SPA + API same-origin):
#   default                 -> prod backend
#   staging_custom_domain   -> staging backend
#
# Cost: a single LB keeps both envs inside GCP's first-5-forwarding-rules
# bundle and shares one global IP, so moving prod off domain mappings adds
# effectively nothing to the LB line item.
#
# DNS is managed outside Terraform. The global IP below is the existing staging
# address (staging A records already point at it — leave them). To cut prod
# over, after `apply` point the prod domain's A record at this same IP (see the
# `lb_ip` output). Each managed cert provisions only once its domain resolves
# here (typically 15-60 min); during that window the freshly pointed domain is
# unavailable, so cut over in a maintenance window.
# ---------------------------------------------------------------------------

# Existing staging IP, now shared. Keep the resource name to avoid recreating
# the address (which would orphan staging's live A records).
resource "google_compute_global_address" "staging_lb" {
  name = "almanac-staging-lb-ip"
}

# --- Backends -------------------------------------------------------------

resource "google_compute_region_network_endpoint_group" "prod" {
  name                  = "almanac-prod-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.backend.name
  }
}

resource "google_compute_backend_service" "prod" {
  name                  = "almanac-prod-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group = google_compute_region_network_endpoint_group.prod.id
  }
}

resource "google_compute_region_network_endpoint_group" "staging" {
  name                  = "almanac-staging-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = google_cloud_run_v2_service.backend_staging.name
  }
}

resource "google_compute_backend_service" "staging" {
  name                  = "almanac-staging-backend"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  protocol              = "HTTPS"

  backend {
    group = google_compute_region_network_endpoint_group.staging.id
  }
}

# --- Routing --------------------------------------------------------------
# Prod is the default service; staging is reached by Host header. Both backend
# services always exist, so the default is valid even before prod DNS is set.
resource "google_compute_url_map" "shared" {
  name            = "almanac-urlmap"
  default_service = google_compute_backend_service.prod.id

  host_rule {
    hosts        = [var.staging_custom_domain]
    path_matcher = "staging"
  }

  path_matcher {
    name            = "staging"
    default_service = google_compute_backend_service.staging.id
  }
}

# --- Certs ----------------------------------------------------------------
# One managed cert per env on the same proxy, so a prod cutover never blocks
# staging's cert (a single multi-domain cert won't provision until every domain
# resolves to the IP).
resource "google_compute_managed_ssl_certificate" "staging" {
  name = "almanac-staging-cert"

  managed {
    domains = [var.staging_custom_domain]
  }
}

resource "google_compute_managed_ssl_certificate" "prod" {
  count = var.custom_domain != "" ? 1 : 0
  name  = "almanac-prod-cert"

  managed {
    domains = [var.custom_domain]
  }
}

resource "google_compute_target_https_proxy" "shared" {
  name    = "almanac-https-proxy"
  url_map = google_compute_url_map.shared.id
  ssl_certificates = concat(
    [google_compute_managed_ssl_certificate.staging.id],
    google_compute_managed_ssl_certificate.prod[*].id,
  )
}

resource "google_compute_global_forwarding_rule" "https" {
  name                  = "almanac-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.staging_lb.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.shared.id
}

# Redirect plain HTTP to HTTPS so bare domains still work.
resource "google_compute_url_map" "redirect" {
  name = "almanac-redirect"

  default_url_redirect {
    https_redirect = true
    strip_query    = false
  }
}

resource "google_compute_target_http_proxy" "redirect" {
  name    = "almanac-http-proxy"
  url_map = google_compute_url_map.redirect.id
}

resource "google_compute_global_forwarding_rule" "http" {
  name                  = "almanac-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.staging_lb.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.redirect.id
}

output "lb_ip" {
  description = "Shared LB IP. Staging A records already point here; point prod A records here too."
  value       = google_compute_global_address.staging_lb.address
}

# ---------------------------------------------------------------------------
# Staging HTTPS load balancer
#
# Replaces the Cloud Run *domain mappings* for staging. Domain mappings route
# through ghs.googlehosted.com, which strips the `Authorization` header — so
# Globus bearer tokens never reached the backend and every authed route 401'd.
# A global external Application Load Balancer with a serverless NEG forwards
# Authorization intact.
#
# DNS is managed outside Terraform. After `apply`, point both staging domains
# at the LB IP (see the `staging_lb_ip` output) with A records, replacing the
# old CNAMEs to ghs.googlehosted.com. The managed cert provisions only once DNS
# resolves to this IP (typically 15–60 min).
# ---------------------------------------------------------------------------

resource "google_compute_global_address" "staging_lb" {
  name = "almanac-staging-lb-ip"
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

resource "google_compute_url_map" "staging" {
  name            = "almanac-staging-urlmap"
  default_service = google_compute_backend_service.staging.id
}

resource "google_compute_managed_ssl_certificate" "staging" {
  name = "almanac-staging-cert"

  managed {
    domains = compact([var.staging_custom_domain, var.staging_api_domain])
  }
}

resource "google_compute_target_https_proxy" "staging" {
  name             = "almanac-staging-https-proxy"
  url_map          = google_compute_url_map.staging.id
  ssl_certificates = [google_compute_managed_ssl_certificate.staging.id]
}

resource "google_compute_global_forwarding_rule" "staging_https" {
  name                  = "almanac-staging-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.staging_lb.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.staging.id
}

# Redirect plain HTTP to HTTPS so the bare domain still works.
resource "google_compute_url_map" "staging_redirect" {
  name = "almanac-staging-redirect"

  default_url_redirect {
    https_redirect = true
    strip_query    = false
  }
}

resource "google_compute_target_http_proxy" "staging" {
  name    = "almanac-staging-http-proxy"
  url_map = google_compute_url_map.staging_redirect.id
}

resource "google_compute_global_forwarding_rule" "staging_http" {
  name                  = "almanac-staging-http"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.staging_lb.id
  port_range            = "80"
  target                = google_compute_target_http_proxy.staging.id
}

output "staging_lb_ip" {
  description = "Point staging A records here, replacing the ghs.googlehosted.com CNAMEs."
  value       = google_compute_global_address.staging_lb.address
}

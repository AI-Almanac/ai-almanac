# ---------------------------------------------------------------------------
# Shared HTTPS load balancer (prod + staging)
#
# One global external Application Load Balancer fronts both environments via
# host-based routing. It replaces Cloud Run *domain mappings*, which route
# through ghs.googlehosted.com and strip the `Authorization` header — so Globus
# bearer tokens never reached the backend and every authed route 401'd. A
# serverless NEG per Cloud Run service forwards Authorization intact.
#
# This started as the staging-only LB and was extended in place to also serve
# prod, so the front-end resources (address, url map, proxies, forwarding
# rules, staging cert) keep their original `almanac-staging-*` names to avoid
# destroying/recreating live infra. The IP is shared; staging A records already
# point at it.
#
# Routing (single-image services serve SPA + API same-origin):
#   default                 -> staging backend
#   custom_domain (prod)    -> prod backend
#
# Cost: a single LB keeps both envs inside GCP's first-5-forwarding-rules
# bundle and shares one global IP, so moving prod off domain mappings adds
# effectively nothing to the LB line item.
#
# DNS is managed outside Terraform. To cut prod over, point the prod domain's A
# record at the LB IP (see the `lb_ip` output). The prod managed cert provisions
# only once its domain resolves here (15-60 min); during that window the domain
# is unavailable, so cut over in a maintenance window.
# ---------------------------------------------------------------------------

resource "google_compute_global_address" "staging_lb" {
  name = "almanac-staging-lb-ip"
}

# --- Backends -------------------------------------------------------------

resource "google_compute_region_network_endpoint_group" "prod" {
  name                  = "almanac-prod-neg"
  region                = var.region
  network_endpoint_type = "SERVERLESS"

  cloud_run {
    service = module.env["prod"].service_name
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
    service = module.env["staging"].service_name
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
# Staging stays the default; prod is reached by Host header. The prod host_rule
# only appears once custom_domain is set, so a domainless install still routes.
resource "google_compute_url_map" "staging" {
  name            = "almanac-staging-urlmap"
  default_service = google_compute_backend_service.staging.id

  dynamic "host_rule" {
    for_each = var.custom_domain != "" ? [var.custom_domain] : []
    content {
      hosts        = [host_rule.value]
      path_matcher = "prod"
    }
  }

  dynamic "path_matcher" {
    for_each = var.custom_domain != "" ? [1] : []
    content {
      name            = "prod"
      default_service = google_compute_backend_service.prod.id
    }
  }
}

# --- Certs ----------------------------------------------------------------
# One managed cert per env on the same proxy, so a prod cutover never blocks
# staging's cert (a single multi-domain cert won't provision until every domain
# resolves to the IP).
resource "google_compute_managed_ssl_certificate" "staging" {
  name = "almanac-staging-cert"

  managed {
    # api-staging is a harmless SAN that resolves to the LB (served by the
    # default staging backend); kept here only so this cert isn't replaced —
    # changing a managed cert's domains forces a 15-60 min reprovision.
    domains = [var.staging_custom_domain, "api-staging.ai-almanac.org"]
  }
}

resource "google_compute_managed_ssl_certificate" "prod" {
  count = var.custom_domain != "" ? 1 : 0
  name  = "almanac-prod-cert"

  managed {
    domains = [var.custom_domain]
  }
}

resource "google_compute_target_https_proxy" "staging" {
  name    = "almanac-staging-https-proxy"
  url_map = google_compute_url_map.staging.id
  ssl_certificates = concat(
    [google_compute_managed_ssl_certificate.staging.id],
    google_compute_managed_ssl_certificate.prod[*].id,
  )
}

resource "google_compute_global_forwarding_rule" "staging_https" {
  name                  = "almanac-staging-https"
  load_balancing_scheme = "EXTERNAL_MANAGED"
  ip_address            = google_compute_global_address.staging_lb.id
  port_range            = "443"
  target                = google_compute_target_https_proxy.staging.id
}

# Redirect plain HTTP to HTTPS so bare domains still work.
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

output "lb_ip" {
  description = "Shared LB IP. Staging A records already point here; point prod A records here too."
  value       = google_compute_global_address.staging_lb.address
}

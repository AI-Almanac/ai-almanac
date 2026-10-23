# ---------------------------------------------------------------------------
# Monitoring — uptime checks and alert policies, emailed to var.alert_email.
# Covers a dead backend (uptime on /ready, which checks DB, storage, runner,
# and auth), a bad deploy (Cloud Run 5xx ratio), and a Cloud SQL outage
# (database/up, plus metric absence: a zonal outage can stop the metric
# rather than report 0). Runbook: docs/sdlc/rollback.md.
# ---------------------------------------------------------------------------

locals {
  # Envs with a public hostname get an uptime check; drop an env here to stop
  # alerting on it.
  monitored_hosts = {
    for env, host in { prod = var.custom_domain, staging = var.staging_custom_domain } :
    env => host if host != ""
  }

  runbook = "Runbook: https://github.com/AI-Almanac/ai-almanac/blob/develop/docs/sdlc/rollback.md"
}

resource "google_monitoring_notification_channel" "email" {
  display_name = "almanac alerts (email)"
  type         = "email"
  labels = {
    email_address = var.alert_email
  }
}

resource "google_monitoring_uptime_check_config" "ready" {
  for_each = local.monitored_hosts

  display_name = "almanac-${each.key}-ready"
  timeout      = "10s"
  period       = "60s"

  http_check {
    path         = "/ready"
    port         = 443
    use_ssl      = true
    validate_ssl = true
  }

  monitored_resource {
    type = "uptime_url"
    labels = {
      project_id = var.project_id
      host       = each.value
    }
  }
}

# Fires when two or more checker regions fail at once, so one flaky region
# doesn't page.
resource "google_monitoring_alert_policy" "ready_down" {
  for_each = google_monitoring_uptime_check_config.ready

  display_name = "almanac ${each.key}: /ready failing"
  combiner     = "OR"

  conditions {
    display_name = "${local.monitored_hosts[each.key]}/ready failing from 2+ regions"
    condition_threshold {
      filter          = "metric.type=\"monitoring.googleapis.com/uptime_check/check_passed\" AND resource.type=\"uptime_url\" AND metric.label.check_id=\"${each.value.uptime_check_id}\""
      comparison      = "COMPARISON_GT"
      threshold_value = 1
      duration        = "120s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_NEXT_OLDER"
        cross_series_reducer = "REDUCE_COUNT_FALSE"
        group_by_fields      = ["resource.label.host"]
      }
    }
  }

  documentation {
    content   = "https://${local.monitored_hosts[each.key]}/ready is failing. A 503 body lists which dependency is down. ${local.runbook}"
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

# Every env, with or without a hostname. Where an uptime check exists, its
# steady /ready traffic keeps the ratio from swinging on one failed request.
resource "google_monitoring_alert_policy" "backend_5xx" {
  for_each = local.env_config

  display_name = "almanac ${each.key}: 5xx ratio above 5%"
  combiner     = "OR"

  conditions {
    display_name = "${each.value.service_name} 5xx / all requests > 5% for 5m"
    condition_threshold {
      filter             = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.label.service_name=\"${each.value.service_name}\" AND metric.label.response_code_class=\"5xx\""
      denominator_filter = "metric.type=\"run.googleapis.com/request_count\" AND resource.type=\"cloud_run_revision\" AND resource.label.service_name=\"${each.value.service_name}\""
      comparison         = "COMPARISON_GT"
      threshold_value    = 0.05
      duration           = "300s"
      aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
      denominator_aggregations {
        alignment_period     = "60s"
        per_series_aligner   = "ALIGN_RATE"
        cross_series_reducer = "REDUCE_SUM"
        group_by_fields      = ["resource.label.service_name"]
      }
    }
  }

  documentation {
    content   = "More than 5% of requests to ${each.value.service_name} returned 5xx for 5 minutes. If this started with a deploy, roll back with `scripts/rollback.sh ${each.key}`. ${local.runbook}"
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

resource "google_monitoring_alert_policy" "sql_down" {
  display_name = "almanac: Cloud SQL ${google_sql_database_instance.almanac.name} down"
  combiner     = "OR"

  conditions {
    display_name = "database/up < 1 for 5m"
    condition_threshold {
      filter          = "metric.type=\"cloudsql.googleapis.com/database/up\" AND resource.type=\"cloudsql_database\" AND resource.label.database_id=\"${var.project_id}:${google_sql_database_instance.almanac.name}\""
      comparison      = "COMPARISON_LT"
      threshold_value = 1
      duration        = "300s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MIN"
      }
    }
  }

  conditions {
    display_name = "database/up not reported for 10m"
    condition_absent {
      filter   = "metric.type=\"cloudsql.googleapis.com/database/up\" AND resource.type=\"cloudsql_database\" AND resource.label.database_id=\"${var.project_id}:${google_sql_database_instance.almanac.name}\""
      duration = "600s"
      aggregations {
        alignment_period   = "60s"
        per_series_aligner = "ALIGN_MIN"
      }
    }
  }

  documentation {
    content   = "Cloud SQL instance ${google_sql_database_instance.almanac.name} (shared by prod and staging) is down or not reporting. Check https://status.cloud.google.com and the instance's Operations log before restarting anything."
    mime_type = "text/markdown"
  }

  alert_strategy {
    auto_close = "1800s"
  }

  notification_channels = [google_monitoring_notification_channel.email.id]
}

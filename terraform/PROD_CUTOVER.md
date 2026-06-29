# Prod cutover: two-image + domain mappings → single image + shared LB

This branch moves prod onto the same architecture as staging:

- one Cloud Run service (`almanac-backend`) running the single `ai-almanac-web`
  image (FastAPI + bundled SPA) on port 8765, and
- the shared load balancer in `load_balancer.tf` instead of Cloud Run domain
  mappings (which strip `Authorization` and break Globus auth).

Order matters. Two things make a naive `tofu apply` unsafe:

1. The backend service has `ignore_changes = [image]`, so Terraform changes the
   **port** (8000→8765) but not the image. Applying that alone puts the old
   port-8000 image behind port 8765 → failed health checks.
2. `tofu apply` deletes the prod domain mappings. Doing that before DNS points
   at the LB takes prod down.

## Runbook

Set `custom_domain = "ai-almanac.org"` in `terraform.tfvars` first.

1. **Build + ship the single image to prod, atomically setting the port.** Run
   the `deploy-prod` workflow (or build manually), then one atomic update so the
   new image and its port land on the same revision:

   ```bash
   gcloud run services update almanac-backend \
     --region us-central1 \
     --image us-central1-docker.pkg.dev/ai-almanac/almanac/ai-almanac-web:<sha> \
     --port 8765
   ```

   Prod is still served via its domain mappings at this point; this only swaps
   the serving container.

2. **Stand up LB routing + cert for prod** without touching the domain mappings
   yet:

   ```bash
   tofu apply \
     -target=google_compute_region_network_endpoint_group.prod \
     -target=google_compute_backend_service.prod \
     -target=google_compute_managed_ssl_certificate.prod \
     -target=google_compute_url_map.shared \
     -target=google_compute_target_https_proxy.shared
   ```

3. **Point prod DNS** `ai-almanac.org` A record at the LB IP (`tofu output
   lb_ip`). The managed cert provisions once DNS resolves here (15–60 min).
   During provisioning the domain is unavailable, so do this in a maintenance
   window. (For zero-downtime, switch the prod cert to a Certificate Manager
   cert with DNS authorization so it can validate before the A record flips —
   not built here; ask if you want it.)

4. **Verify** `https://ai-almanac.org` serves the SPA and Globus login works
   (Authorization reaches the backend).

5. **Apply the rest** to delete the now-unused domain mappings and the retired
   frontend service/SA:

   ```bash
   tofu apply
   ```

Staging note: this apply also recreates the LB's url_map / proxies / forwarding
rules under shared names (the IP and certs are preserved), a sub-minute serving
blip for staging. The vestigial `almanac-frontend-staging` service and SA are
removed; nothing routed to them.

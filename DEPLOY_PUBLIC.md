# Public Deployment Guide

ai-almanac has no built-in authentication. To host an instance reachable from
the public internet (or even a corporate LAN), run it behind a reverse proxy
that handles auth and trust the proxy's `X-Forwarded-User` header for
attribution.

This is the same pattern used by Jupyter, Prometheus, Grafana, and most
internal-tool deployments — the app stays simple, the proxy handles all the
hard parts (OIDC, TLS, brute-force protection, MFA).

---

## Threat model

- **Trust boundary:** the reverse proxy. ai-almanac assumes any request that
  reaches it is from an authenticated user — so the app **must not** be
  reachable except through the proxy.
- **App posture:** binds to `127.0.0.1` by default. `ai-almanac serve` refuses
  to bind non-loopback addresses unless you pass `--allow-public`, which is
  exactly the moment you're putting it behind a proxy.
- **Attribution:** the app records the value of `X-Forwarded-User` (configurable
  via `SUBMITTED_BY_HEADER`) on every job and dataset as `submitted_by`. No
  enforcement happens in the app — the proxy is the trust boundary.

---

## Reference setup: Caddy + oauth2-proxy + Globus OIDC

```
Internet
   │
   ▼
Caddy (TLS termination, reverse proxy)
   │
   ├── /oauth2/* → oauth2-proxy (handles OIDC dance with Globus)
   └── /*        → ai-almanac (bound to 127.0.0.1:8765)
```

### 1. Run ai-almanac

On the host (or in a systemd unit):

```bash
sudo -u almanac AI_ALMANAC_DATA_DIR=/var/lib/ai-almanac \
  ai-almanac serve --bind 127.0.0.1 --port 8765 --no-open
```

### 2. Run oauth2-proxy against Globus

oauth2-proxy supports generic OIDC providers, which is what Globus exposes.

```yaml
# /etc/oauth2-proxy.cfg
provider = "oidc"
oidc_issuer_url = "https://auth.globus.org"
client_id = "<your-globus-confidential-app-client-id>"
client_secret = "<your-globus-confidential-app-secret>"
cookie_secret = "<32-byte-base64>"
redirect_url = "https://almanac.example.org/oauth2/callback"
upstreams = ["http://127.0.0.1:8765/"]
http_address = "127.0.0.1:4180"
email_domains = ["*"]
pass_user_headers = true        # sends X-Forwarded-User downstream
set_xauthrequest = true
```

### 3. Front it with Caddy

```caddyfile
almanac.example.org {
    encode gzip
    reverse_proxy /oauth2/* 127.0.0.1:4180
    forward_auth 127.0.0.1:4180 {
        uri /oauth2/auth
        copy_headers X-Auth-Request-User X-Auth-Request-Email
        header_up X-Forwarded-User {http.reverse_proxy.header.X-Auth-Request-Email}
    }
    reverse_proxy 127.0.0.1:8765
}
```

Caddy handles TLS automatically. Visitors hit `almanac.example.org`, get
bounced through Globus, then land in ai-almanac with their email attached as
`X-Forwarded-User`.

### 4. Tell ai-almanac which header to read

By default ai-almanac reads `X-Forwarded-User`. Override via env if your
proxy uses a different name:

```bash
SUBMITTED_BY_HEADER=X-Auth-Request-Email ai-almanac serve ...
```

---

## Alternatives

The same pattern works with any OIDC-capable proxy:

- **Cloudflare Access** in front of an HTTPS-exposed host — no oauth2-proxy
  needed. Forwards `Cf-Access-Authenticated-User-Email` (set
  `SUBMITTED_BY_HEADER=Cf-Access-Authenticated-User-Email`).
- **Tailscale Serve / Funnel** — limits access to your tailnet, no public
  authn required. Skip the `submitted_by` header entirely or set
  `SUBMITTED_BY_HEADER=X-Tailscale-User-LoginName`.
- **Nginx + oauth2-proxy / Authelia / Authentik** — same shape as Caddy.

---

## GPU host notes (NVIDIA DGX Spark / generic GPU VMs)

The benchmark environment needs an NVIDIA driver and a working CUDA install.
Pixi handles the rest:

```bash
# Verify the driver
nvidia-smi

# Install pixi (one-time)
curl -fsSL https://pixi.sh/install.sh | bash
export PATH="$HOME/.pixi/bin:$PATH"

# Pre-warm the benchmark env before users arrive
AI_ALMANAC_DATA_DIR=/var/lib/ai-almanac ai-almanac env prepare
```

The pixi env spec (`benchmark.pixi.toml`) pins `pytorch-cuda 12.4.*` on
linux-64 and uses conda-forge for the NetCDF/HDF5 stack. Edit the packaged
spec or drop a custom one at
`$AI_ALMANAC_DATA_DIR/benchmark-env/pixi.toml` if you need to change CUDA
versions or pin alternate ROMP / earth2studio refs.

---

## Backup

All state lives under `$AI_ALMANAC_DATA_DIR`. A snapshot of that directory
is a complete backup:

```bash
# Stop the service first to avoid copying a half-written SQLite file
systemctl stop ai-almanac
tar -czf almanac-$(date +%F).tar.gz -C /var/lib ai-almanac
systemctl start ai-almanac
```

The `benchmark-env/` and `cache/` subdirectories are regeneratable — exclude
them if you want a smaller backup.

---

## Updating

```bash
uv tool upgrade ai-almanac
systemctl restart ai-almanac
```

Migrations run automatically on startup. Roll back by downgrading the
package and re-running — the schema is forward-compatible across point
releases.

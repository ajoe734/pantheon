# Caddy provisioning for non-prod ingress

Each non-prod ingress VM runs **Caddy** as public HTTPS termination. Dev serves
both the operator-bff reverse proxy and the Pantheon-owned static frontend.
Caddy terminates TLS using Let's Encrypt certificates for the Pantheon-owned
dev DNS names `api.dev.mvl-cap.tw` and `app.dev.mvl-cap.tw`.

## Why this directory exists

The on-VM `/etc/caddy/Caddyfile` is root-owned and was historically set up by
hand, so it was **not** captured by any IaC. After the 2026-05-30 GCP cutover
(`pantheon-lupin-20260502` → `pantheon-benjamin-20260528`, new static IPs) the VM
Caddyfiles still pointed at the **old** sslip.io hostnames. Caddy then had no cert
for the new SNI and TLS died at the handshake with `tlsv1 alert internal error`
(alert 80): the BFF looked deployed (gh vars were updated) but was unreachable
over HTTPS. See the post-mortem in the 2026-05-30 migration notes.

These templates + `sync-caddy.sh` make the Caddyfile a **versioned, redeployable**
artifact so the breakage stops recurring on every rebuild/cutover.

## Files

| File | Purpose |
|---|---|
| `dev.Caddyfile.tmpl` | dev BFF upstream `127.0.0.1:18001` + static FE root |
| `staging.Caddyfile.tmpl` | staging-live BFF — upstream `127.0.0.1:38001` |
| `sync-caddy.sh` | render BFF/FE host placeholders → push to VM → validate → reload → verify |

## Usage

```bash
# dev (Pantheon-owned DNS)
deploy/caddy/sync-caddy.sh \
  chloe_ong_dev_cctech_support_com@34.81.52.222 \
  api.dev.mvl-cap.tw \
  deploy/caddy/dev.Caddyfile.tmpl \
  app.dev.mvl-cap.tw \
  /var/www/pantheon-dev-fe

# staging-live: no VM (docs/deployment/vm-dev-staging-prod-management-plan.md § 3.2)
```

The former cutover script was deleted. Run `sync-caddy.sh` explicitly after an
ingress host change.

> SSH note: these VMs reject the default agent key — `sync-caddy.sh` uses
> `~/.ssh/google_compute_engine` (override with `CADDY_SSH_KEY`).

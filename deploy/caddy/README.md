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

# staging-live
deploy/caddy/sync-caddy.sh \
  lupin@104.155.223.192 \
  pantheon-lupin-staging-bff.104.155.223.192.sslip.io \
  deploy/caddy/staging.Caddyfile.tmpl
```

`scripts/migrate_to_benjamin_cutover.sh` calls this automatically for both envs
after it updates the GitHub repo variables, so a future cutover re-points the
cert SNI without manual SSH.

> SSH note: these VMs reject the default agent key — `sync-caddy.sh` uses
> `~/.ssh/google_compute_engine` (override with `CADDY_SSH_KEY`).

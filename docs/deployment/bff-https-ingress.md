# BFF HTTPS Ingress

Status date: 2026-06-08

## Purpose

Pantheon-owned and external browser frontends run on HTTPS origins, so the
browser must call BFFs through HTTPS as well. Raw internal IPs and unsecured VM
ports are not valid for `VITE_BFF_BASE_URL`.

For current dev frontend hosting, read
`docs/frontend/execute-plans-dev-hosting.md` first. Lovable URLs in this file
are legacy/staging-live context, not the dev frontend acceptance source.

Current ingress uses:

- static GCP external IPs
- GCP firewall rules for `tcp:80,tcp:443`
- `sslip.io` DNS names that resolve to the VM IPs
- Caddy on each BFF VM as the HTTPS reverse proxy
- Let's Encrypt certificates managed automatically by Caddy

## Current Endpoints

| Environment | VM | Static IP | HTTPS BFF URL | Local upstream |
| --- | --- | --- | --- | --- |
| dev | `pantheon-dev-vm1` | `35.201.239.38` | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` | `127.0.0.1:18001` |
| staging-live | `pantheon-taiwan` | `34.81.225.122` | `https://pantheon-staging-bff.34.81.225.122.sslip.io` | `127.0.0.1:38001` |

Current dev FE URL:

```text
https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io
```

## GCP Resources

Static addresses:

```bash
gcloud compute addresses list \
  --project=pantheon-493602 \
  --filter='name=(pantheon-dev-vm1-ip pantheon-staging-vm1-ip)' \
  --format='table(name,region.basename(),address,status,users)'
```

Expected:

- `pantheon-dev-vm1-ip`: `35.201.239.38`
- `pantheon-staging-vm1-ip`: `34.81.225.122`

Firewall rules:

- `pantheon-dev-bff-https`, target tag `pantheon-dev`, allow `tcp:80,tcp:443`
- `pantheon-staging-bff-https`, target tag `pantheon-staging-control`, allow
  `tcp:80,tcp:443`

## Caddy Configuration

Dev Caddyfile on `pantheon-dev-vm1`:

```caddyfile
pantheon-lupin-dev-bff.35.201.239.38.sslip.io {
    reverse_proxy 127.0.0.1:18001
}

pantheon-lupin-dev-fe.35.201.239.38.sslip.io {
    root * /var/www/pantheon-dev-fe
    encode zstd gzip
    try_files {path} /index.html
    file_server
}
```

Staging VM1 Caddyfile on `pantheon-taiwan`:

```caddyfile
{
    auto_https disable_redirects
}

https://pantheon-staging-bff.34.81.225.122.sslip.io {
    encode zstd gzip
    reverse_proxy 127.0.0.1:38001
}
```

`auto_https disable_redirects` keeps Caddy from taking over port `80`. This is
important because staging VM1 already has nginx listening on `80`.

## Health Checks

Public HTTPS health:

```bash
curl -fsS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health
curl -fsS https://pantheon-staging-bff.34.81.225.122.sslip.io/health
```

CORS preflight:

```bash
curl -sS -D - -o /tmp/dev-cors-body \
  -X OPTIONS https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/health \
  -H 'Origin: https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io' \
  -H 'Access-Control-Request-Method: GET'

curl -sS -D - -o /tmp/staging-cors-body \
  -X OPTIONS https://pantheon-staging-bff.34.81.225.122.sslip.io/health \
  -H 'Origin: https://pantheon-ai-system-front-staging-live.lovable.app' \
  -H 'Access-Control-Request-Method: GET'
```

Expected `access-control-allow-origin` values:

- dev: `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io`
- staging-live: `https://pantheon-ai-system-front-staging-live.lovable.app`

## Dev Frontend Values

Current `execute-plans` dev build:

```env
VITE_PANTHEON_ENV=dev
VITE_BFF_MODE=live
VITE_BFF_BASE_URL=https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io
VITE_BFF_FALLBACK=strict
VITE_BFF_REAL_WRITES=false
VITE_PANTHEON_LIVE_BROKER_ENABLED=false
```

Legacy Lovable dev values are not the current Pantheon dev deployment target.

Staging-live Lovable project:

```env
VITE_PANTHEON_ENV=staging-live
VITE_BFF_BASE_URL=https://pantheon-staging-bff.34.81.225.122.sslip.io
VITE_PANTHEON_LIVE_BROKER_ENABLED=true
```

Changing staging-live Lovable `VITE_` values requires rebuilding and
republishing the Lovable project.

## Operational Notes

- These URLs depend on the static IPs staying assigned to the same VMs.
- `sslip.io` is a pragmatic non-prod DNS bridge. Move to owned domains before
  production.
- Do not expose broker or TWS ports publicly.
- Caddy should be the only public HTTPS listener for these BFFs.

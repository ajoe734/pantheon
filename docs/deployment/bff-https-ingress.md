# BFF HTTPS Ingress

Status date: 2026-04-27

## Purpose

Lovable-hosted frontends run on HTTPS origins, so the browser must call BFFs
through HTTPS as well. Raw internal IPs and unsecured VM ports are not valid for
`VITE_BFF_BASE_URL`.

Current ingress uses:

- static GCP external IPs
- GCP firewall rules for `tcp:80,tcp:443`
- `sslip.io` DNS names that resolve to the VM IPs
- Caddy on each BFF VM as the HTTPS reverse proxy
- Let's Encrypt certificates managed automatically by Caddy

## Current Endpoints

| Environment | VM | Static IP | HTTPS BFF URL | Local upstream |
| --- | --- | --- | --- | --- |
| dev | `pantheon-dev-vm1` | `35.236.178.81` | `https://pantheon-dev-bff.35.236.178.81.sslip.io` | `127.0.0.1:18001` |
| staging-live | `pantheon-taiwan` | `34.81.225.122` | `https://pantheon-staging-bff.34.81.225.122.sslip.io` | `127.0.0.1:38001` |

## GCP Resources

Static addresses:

```bash
gcloud compute addresses list \
  --project=pantheon-493602 \
  --filter='name=(pantheon-dev-vm1-ip pantheon-staging-vm1-ip)' \
  --format='table(name,region.basename(),address,status,users)'
```

Expected:

- `pantheon-dev-vm1-ip`: `35.236.178.81`
- `pantheon-staging-vm1-ip`: `34.81.225.122`

Firewall rules:

- `pantheon-dev-bff-https`, target tag `pantheon-dev`, allow `tcp:80,tcp:443`
- `pantheon-staging-bff-https`, target tag `pantheon-staging-control`, allow
  `tcp:80,tcp:443`

## Caddy Configuration

Dev Caddyfile on `pantheon-dev-vm1`:

```caddyfile
{
    auto_https disable_redirects
}

https://pantheon-dev-bff.35.236.178.81.sslip.io {
    encode zstd gzip
    reverse_proxy 127.0.0.1:18001
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
curl -fsS https://pantheon-dev-bff.35.236.178.81.sslip.io/health
curl -fsS https://pantheon-staging-bff.34.81.225.122.sslip.io/health
```

CORS preflight:

```bash
curl -sS -D - -o /tmp/dev-cors-body \
  -X OPTIONS https://pantheon-dev-bff.35.236.178.81.sslip.io/health \
  -H 'Origin: https://pantheon-ai-system-front-dev.lovable.app' \
  -H 'Access-Control-Request-Method: GET'

curl -sS -D - -o /tmp/staging-cors-body \
  -X OPTIONS https://pantheon-staging-bff.34.81.225.122.sslip.io/health \
  -H 'Origin: https://pantheon-ai-system-front-staging-live.lovable.app' \
  -H 'Access-Control-Request-Method: GET'
```

Expected `access-control-allow-origin` values:

- dev: `https://pantheon-ai-system-front-dev.lovable.app`
- staging-live: `https://pantheon-ai-system-front-staging-live.lovable.app`

## Lovable Values

Dev Lovable project:

```env
VITE_PANTHEON_ENV=dev
VITE_BFF_BASE_URL=https://pantheon-dev-bff.35.236.178.81.sslip.io
VITE_PANTHEON_LIVE_BROKER_ENABLED=false
```

Staging-live Lovable project:

```env
VITE_PANTHEON_ENV=staging-live
VITE_BFF_BASE_URL=https://pantheon-staging-bff.34.81.225.122.sslip.io
VITE_PANTHEON_LIVE_BROKER_ENABLED=true
```

Changing Lovable `VITE_` values requires rebuilding and republishing the
Lovable project.

## Operational Notes

- These URLs depend on the static IPs staying assigned to the same VMs.
- `sslip.io` is a pragmatic non-prod DNS bridge. Move to owned domains before
  production.
- Do not expose broker or TWS ports publicly.
- Caddy should be the only public HTTPS listener for these BFFs.

# Round 25 — Browser-facing edge security headers (FE + Caddy)

**Date:** 2026-06-15
**Depth/breadth step:** Round 24 added security headers at the BFF app. Round 25
covers the **browser-facing edge**: the FE (a static SPA served by Caddy) and
the Caddy reverse-proxy layer.

## Hypotheses

- H1: the FE serves `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, and HSTS.
- H2: the upstream `Server` banner is not leaked.

## Method

1. Probe live FE response headers.
2. Locate the edge config; the dev/staging Caddy templates are versioned in
   `deploy/caddy/`.
3. Add a `header` block to the FE and BFF sites; validate with the real `caddy`
   binary before committing (a malformed Caddyfile would break the live edge).

## Pass criteria

- H1/H2: templates declare the headers, drop the `Server` banner, and pass
  `caddy validate`. Guard test locks both.

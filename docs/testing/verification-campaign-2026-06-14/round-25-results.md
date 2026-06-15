# Round 25 — Results

**Executed:** 2026-06-15 (UTC).

## Finding

### F15 — FE/edge served no security headers (FIXED)

The live dev FE (`text/html` SPA) returned **no** `X-Content-Type-Options`,
`X-Frame-Options`, `Referrer-Policy`, CSP, or HSTS, and both the FE and BFF
leaked the upstream `Server` banner. The FE is browser-facing, so its missing
anti-clickjacking / anti-sniffing headers are the higher-value gap of this pair.

The edge is served by **versioned Caddy templates** (`deploy/caddy/
dev.Caddyfile.tmpl`, `staging.Caddyfile.tmpl`), so this is fixable in-repo.

## Fix

Added a `header` block:

- **FE site (dev):** `X-Content-Type-Options nosniff`, `X-Frame-Options DENY`,
  `Referrer-Policy no-referrer`, `Strict-Transport-Security max-age=31536000`,
  and `-Server` (drop the banner).
- **BFF site (dev + staging):** `Strict-Transport-Security` + `-Server` only —
  the BFF app already sets nosniff/frame/referrer (Round 24), so they are not
  duplicated.

CSP is intentionally **omitted** from the FE for now: a correct CSP needs the FE
build's script/style/connect origins (to the BFF) to avoid breaking the SPA;
tracked as a follow-up requiring the FE team's allowlist.

## Verification

Rendered both templates (placeholder substitution as `sync-caddy.sh` does) and
ran the **real `caddy validate`** → **"Valid configuration"** for both — so the
change cannot break the live edge on the next sync. Guard test
`deploy/caddy/test_caddy_security_headers.py` (5 passed): asserts the header
directives are present in both templates and that each template passes
`caddy validate`.

## Net

F15 fixed — browser-facing security headers added at the validated Caddy edge,
the `Server` banner dropped, locked by a config-regression + `caddy validate`
test. Takes live effect on the next `sync-caddy.sh` run (OPS). CSP for the FE
is a tracked follow-up.

# Round 23 — CORS configuration correctness

**Date:** 2026-06-15
**Depth/breadth step:** A security round on the cross-origin policy. Misconfigured
CORS (arbitrary-origin reflection while allowing credentials, or an unanchored
origin regex) is a credential-theft / CSRF vector.

## Hypotheses

- H1: only allowlisted origins receive `Access-Control-Allow-Origin`; arbitrary
  origins are rejected (no reflection).
- H2: no bypass via subdomain prefix, suffix, port, scheme downgrade, `null`, or
  an unanchored preview regex.
- H3: credentials are not usable from a non-allowlisted origin.

## Method

1. Live preflight (`OPTIONS`) with a battery of `Origin` values: arbitrary,
   exact-allowed, `localhost`, `null`, suffix/prefix/port/scheme variants of the
   allowed origin.
2. Audit the CORS middleware config and the preview-origin regex; confirm the
   match method (Starlette `match` vs `fullmatch`).

## Pass criteria

- H1–H3 hold with captured evidence; any reflection/bypass is a security defect
  fixed via the dev workflow.

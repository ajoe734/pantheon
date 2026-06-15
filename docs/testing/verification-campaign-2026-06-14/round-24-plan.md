# Round 24 — HTTP security response headers

**Date:** 2026-06-15
**Depth/breadth step:** A hardening round on the operator BFF's response
headers. An authenticated operator console should set baseline security headers
(anti-sniffing, anti-clickjacking, referrer privacy).

## Hypotheses

- H1: the BFF sets `X-Content-Type-Options`, `X-Frame-Options`, and
  `Referrer-Policy` on responses.

## Method

1. Inspect live BFF response headers; grep the code for any security-header
   middleware.
2. If missing, add a header layer that is safe for a cross-origin JSON API and
   for SSE streams (must not break CORS or buffer streaming responses).

## Pass criteria

- H1: the three baseline headers are present on all responses (including errors
  and preflights), CORS still works, and SSE streaming is unaffected.

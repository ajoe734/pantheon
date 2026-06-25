# Round 24 — Results

**Executed:** 2026-06-15 (UTC).

## Finding

### F14 — BFF emitted no security response headers (FIXED)

Live BFF responses carried **no** `X-Content-Type-Options`, `X-Frame-Options`,
`Referrer-Policy`, CSP, or HSTS — and leaked `server: uvicorn`. The codebase had
no security-header middleware. For an authenticated operator console this is a
(low-severity) hardening gap: MIME-sniffing, clickjacking of the API, and
referrer leakage of operator URLs were not mitigated at the app layer.

## Fix

Added `_SecurityHeadersMiddleware` — a **pure-ASGI** middleware (not
`BaseHTTPMiddleware`, which would buffer/break SSE) that appends three baseline
headers to every response:

- `X-Content-Type-Options: nosniff`
- `X-Frame-Options: DENY`
- `Referrer-Policy: no-referrer`

Registered after the CORS middleware (outermost) so it never disturbs the CORS
headers. Deliberately **omitted**: CSP (no HTML served by the API),
`Cross-Origin-Resource-Policy` (would block the legitimate cross-origin operator
FE), and HSTS (owned by the TLS edge / Caddy) — to avoid breaking the FE or
streaming.

## Verification

`test_security_headers.py` (4 passed):
- the three headers present on a 200 and on a 401 error;
- CORS `allow-origin` coexists with the security headers on a preflight;
- **streaming-safe** — a `StreamingResponse` through the middleware delivers its
  body intact (`chunk0;chunk1;chunk2;`) with the headers set, proving SSE is not
  buffered or broken.

In-process the BFF now returns `nosniff / DENY / no-referrer` on GET, OPTIONS,
and error responses; CORS unchanged.

## Net

F14 fixed — baseline security headers added via an SSE-safe layer, locked by a
streaming-safe regression test. (Takes live effect on the next BFF redeploy,
like the other code fixes.)

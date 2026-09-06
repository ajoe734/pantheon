# BFF-SSE-COOKIE-AUTH-001 — cookie-backed SSE auth so the live cockpit stream opens for logged-in operators

## Problem

The Management cockpit (execute-plans frontend, strict-live build) opens a
browser `EventSource` to the BFF SSE endpoint `/bff/events/stream` with
`withCredentials: true`. A browser `EventSource` **cannot attach an
`Authorization` header** — it can only send cookies. The BFF's strict-auth mode
wants a JWT bearer, so an authenticated operator's stream is rejected and the
shell shows the `STRICT TYPED ERROR sse_open_failed · seed fallback blocked`
banner (offline / FALLBACK DATA), with every page falling to `0 records`.

### Root cause (verified in code)

`services/control-plane/bff/main.py` — `stream_bff_events` (`@app.get("/bff/events/stream")`, ~L54977):

- L55012: `if authorization_value or pantheon_session_value:` — the presence of a
  `pantheon_session` cookie (which the browser EventSource **always** sends once
  the operator has logged in) forces the **authenticated branch**:
  `_extract_identity(...)` → `_require_read_role(identity)` (L55021-55026).
- If that cookie is stale/expired, or if `_extract_identity` does not accept a
  session cookie as a valid read identity, the handler raises `_bff_error(401)`.
  The EventSource then fires `error` **before** `open` → the frontend calls
  `reportFallback("sse_open_failed")` → strict `real-error` banner.
- The fall-through liveness path (L55035-55048, HTTP 200) is reached **only when
  no cookie and no bearer are present**. So a logged-in operator with a
  `pantheon_session` cookie can NEVER land on the safe liveness path — they are
  always routed into the strict-auth branch that the cookie may not satisfy.
- The docstring at L54953-54955 explicitly records the gap:
  *"Browser EventSource cannot attach Authorization headers. Until the shell has
  a cookie-backed SSE auth path, this endpoint only emits non-sensitive BFF
  liveness events…"* — i.e. cookie-backed SSE auth was never finished.

Perverse consequence: a **stale cookie is worse than no cookie**. No cookie →
200 liveness stream (banner survives). Stale cookie → 401 → `sse_open_failed`.
This is why "log out fully + clear the `pantheon_session` cookie + log back in"
is the current manual workaround, and why plain "retry" does not help.

## Goal

A logged-in operator whose only credential the browser can transmit is the
`pantheon_session` cookie must be able to open `/bff/events/stream` and receive
the authenticated, replay-capable stream (not just liveness), in strict-auth
mode — without an `Authorization` bearer header.

## Acceptance

1. With a **valid** `pantheon_session` cookie and **no** `Authorization` header,
   `GET /bff/events/stream?channel=<allowed>` returns **200** and streams the
   authenticated channel (X-BFF-Session-Kind header set), i.e.
   `_extract_identity` accepts the session cookie as a first-class read identity
   and `_require_read_role` passes.
2. With a **stale/invalid** `pantheon_session` cookie and no bearer, the endpoint
   does **not** hard-fail the shell. Decide and implement one explicit behavior
   (document which): either (a) 401 with a typed body the frontend maps to
   "re-authenticate" (not the generic `sse_open_failed` seed-blocked banner), or
   (b) fall back to the 200 liveness stream and surface a "degraded/re-auth"
   hint. No silent strict `real-error` for an operator who simply needs a token
   refresh.
3. Anonymous (no cookie, no bearer) still returns the 200 liveness stream —
   unchanged.
4. Bearer path (Authorization header, for non-browser clients) unchanged.
5. REST reads that back the same pages (e.g. `/bff/management/persona-fleet`,
   `/bff/management/trade-journeys/*`) accept the same cookie identity so the
   cockpit is consistent — an operator who can open SSE can also read the pages.
6. Frontend: confirm `LiveStatusBanner` / `liveStatus.reportFallback` no longer
   trips to `real-error` for the valid-cookie case; a genuine re-auth need shows
   a distinct, actionable state, not "seed fallback blocked".

## Notes / pointers

- Frontend chain (execute-plans repo):
  `src/lib/bff-v1/sse/liveSse.ts` (EventSource open + `reportFallback("sse_open_failed")`),
  `src/lib/bff-v1/paths.ts` (`paths.sse()` = `/bff/events/stream`),
  `src/lib/bff-v1/liveStatus.ts`, `src/lib/bff/liveTransport.ts`
  (`configuredMode==="real"` + `effective==="mock"` → `real-error`),
  `src/components/layout/LiveStatusBanner.tsx`.
- Strict posture is enforced by the deploy pipeline (`VITE_BFF_MODE=live` +
  `VITE_BFF_FALLBACK=strict`), so this cannot be worked around by relaxing the
  build — it must be fixed server-side + banner-side.
- Security: cookie-authenticated SSE must keep the same read-role gate as the
  bearer path; do not widen access. Watch CSRF posture for cookie-auth GET
  streams (SameSite / origin allow-list already exist for CORS preflight).
- Out of scope: the datasource health chips (yahoo/sec_edgar/finra/fred
  `read_unavailable`/`credential_unavailable` in `read_store.py`) — those are
  missing ingestion creds/API keys, a separate expected degradation.

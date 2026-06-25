# Round 27 — Results

**Executed:** 2026-06-15 (UTC).

## Finding

### F16 — no request body-size limit (FIXED)

Neither the BFF code nor the Caddy edge configured a request body-size limit. A
**2 MB** body to `POST /bff/evolution-programs` returned **201 Created** — the
oversized payload was accepted into memory and even persisted. A client could
submit hundred-MB / GB bodies to exhaust memory.

Some endpoints **do** enforce content limits (evidence-pack file metadata,
Management AI attachments, NL question → 413 `REQUEST_TOO_LARGE`), but the
generic command/create routes had none, and there is **no global edge limit**.

Context: the BFF has **no multipart/`UploadFile` endpoints** — every request
body is JSON (commands/metadata, KB-scale). So a generous edge limit cannot
break any legitimate request.

## Fix

Added `request_body { max_size 10MB }` to the BFF site in both Caddy templates
(`dev` + `staging`). 10 MB is far above any legitimate JSON body but rejects
memory-exhausting oversized payloads at the edge before they reach the app.
Validated both rendered templates with the real `caddy validate` →
**Valid configuration**. Guard test
`deploy/caddy/test_caddy_security_headers.py` extended (6 passed) to assert the
`request_body`/`max_size` directive is present.

## Note

The 2 MB probe created one junk evolution-program record (2 MB name) in dev;
there is no delete API for that resource (see Round 3 findings), so it persists
harmlessly. A tighter per-route limit + rate limiting are recommended follow-ups
for the team.

## Net

F16 fixed — an edge body-size backstop added and validated; takes live effect on
the next `sync-caddy.sh` (OPS). DoS via oversized bodies is now bounded.

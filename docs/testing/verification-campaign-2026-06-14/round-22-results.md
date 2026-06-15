# Round 22 — Results

**Executed:** 2026-06-15 (UTC). **Method:** per-service `TestClient` fuzz,
isolated subprocess, real module name.

## H1 — 500-hunt: PASS

**21 importable services** fuzzed (baseline GET + bad query params + malformed
bodies). After excluding harness artifacts: **0 real 500s.**

| Service | paths | 500s |
|---|---|---|
| capital | 20 | 0 |
| promotion | 12 | 0 |
| policy-learning | 14 | 0 |
| broker | 10 | 0 |
| evaluation | 11 | 0 |
| optimizer-svc | 12 | 0 |
| governance | 18 | 0 |
| research | 20 | 0 |
| lineage-read | 16 | 0 |
| training-session | 23 | 0 |
| evolution | 24 | 0 |
| incidents | 14 | 0 |
| reconciliation-drift | 23 | 0 |
| postmortems | 15 | 0 |
| research-worker-gateway | 14 | 0 |
| control-plane/{router,persona,feedback} | 10/11/11 | 0 |
| deployment | 29 | 0 |
| feedback | 10 | 0 |
| channels/web | 11 | 0 |

## Methodology note (important)

An initial pass produced false 500s; **all** were harness artifacts, corrected:

1. **`/openapi.json` 500** — `app.openapi()` schema-gen fails when the app is
   imported under a synthetic module name. Fixed by importing under the real
   module name (`main`) in an isolated subprocess and skipping `/openapi.json`.
2. **`body=[]` 500 → Pydantic "class-not-fully-defined"** — same synthetic-name
   cause; forward-refs (`Model.model_rebuild`) only resolve under the real
   module name. Fixed by the real-name import; bad bodies then correctly 422.
3. **`>= 500` caught graceful 503s** — e.g. broker `/api/broker/paper/orders`
   returns **503** `PAPER_ADAPTER_DISABLED` (config-gated), not a crash. Fixed by
   flagging only `== 500`.

After these corrections every service is clean. The lesson: when auditing a
fleet in-process, import each app under its real name and distinguish 503
(graceful) from 500 (crash).

## Net

H1 **PASS** — the non-BFF fleet handles malformed input gracefully (FastAPI 422
validation + config-gated 503s); no input-driven 500 outside the BFF (where F12
was found and fixed).

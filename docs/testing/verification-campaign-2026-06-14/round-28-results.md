# Round 28 — Results

**Executed:** 2026-06-15 (UTC). **Method:** package import + route/input audit.

## H1/H2 — PASS (the 5 deferred services are clean)

| Service | routes | shadow | dup | input-500 |
|---|---|---|---|---|
| search | 19 | 0 | 0 | 0 |
| source_ingestion | 61 | 0 | 0 | 0 |
| registry | 20 | 0 | 0 | 0 |
| consultation | 31 | 0 | 0 | 0 |
| memory | 16 | 0 | 0 | 0 |

(`consultation` and `memory` import as Python-3 namespace packages despite
lacking `__init__.py`.) `source_ingestion` is the largest non-BFF service (61
routes) — clean.

## Whole-fleet coverage achieved

Combining Rounds 21, 22, and 28, **all 26 FastAPI services** are now audited for
route shadowing, duplicate registration, and input-driven 500s:

- **0 shadowed routes** fleet-wide
- **0 duplicate `(method,path)` registrations** fleet-wide
- **0 input-driven 500s** fleet-wide

The BFF remains the only service where defects were found (F3 shadowed SSE
route, F9 benign duplicate aliases, F12 audit-filter 500) — all fixed. The rest
of the fleet is clean.

## Net

H1/H2 **PASS** — the deferred services are clean and whole-fleet route/input
coverage is complete.

# Review: MGMT-OODA-004 — BFF read routes for OODA packets

Reviewer: Claude
Date: 2026-05-15
Outcome: **Approved**

## Scope Verified

Commit d565f58b adds:
- `GET /bff/ooda/packets` — list with status/stage/strategy/runtime/evo-program filters
- `GET /bff/ooda/packets/{packet_id}` — detail with 404 on missing, degraded on missing source
- `GET /bff/strategies/{id}/ooda` — strategy-scoped packet list
- `GET /bff/runtimes/{id}/ooda` — runtime/binding-scoped packet list
- `GET /bff/evolution-programs/{id}/ooda` — evolution-program-scoped packet list

## Checklist

- [x] Auth gate: all routes call `_require_read_role(identity)` — no unauthenticated read surface
- [x] Feature flag: `PANTHEON_OODA_PACKET_ENABLED` defaults to enabled; set to false → 503 DOWNSTREAM_UNAVAILABLE (fail-closed)
- [x] Degraded mode: missing source (no env or file) returns 200 with `status: unavailable` meta — no 500
- [x] JSONL replay: `_load_record_store_payload` handles .jsonl multi-line and .json payloads; `_project_ooda_packet_store_payload` handles both `packet_snapshot` and `stage_transition` envelopes correctly
- [x] Ref matching: `_ooda_packet_matches_ref` traverses nested fields with normalized aliases for strategy/runtime/evolution_program; test covers `binding-paper-2` resolved via `act.runtime_binding_id`
- [x] Sort: `updated_at` descending with fallbacks to `closed_at`/`created_at`/`started_at`
- [x] Route manifest: OODA routes registered in `scripts/bff_route_manifest_backend.py` with correct family tag
- [x] Feature flags endpoint: `oodaPackets` flag surfaced in `/bff/feature-flags`
- [x] Tests: 6 passed (list+detail, JSONL envelope replay, filters+related routes, 404, missing source, feature flag fail-closed)
- [x] Contract path registration tests: 2 passed (paths registered, OpenAPI discoverable)
- [x] py_compile: clean on main.py, read_store.py, bff_route_manifest_backend.py

## Notes

Snapshot drift in `bff_route_manifest_backend.py --check` is a pre-existing issue (includes /bff/research-analyses* and /bff/auth/dev-login from previous tasks). Correct to not stage a generated manifest here.

The `_collect_ooda_ref_values` recursive visitor is slightly broad but safe given it only traverses, never mutates, and the field aliases are tightly scoped per entity type.

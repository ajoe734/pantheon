# BFF-CONSOL-017 Review - Codex

Disposition: Changes requested

## Findings

1. The submitted smoke/evidence does not actually prove the required research detail linkage.

   The Playwright spec accepts 404 for the research ticket and analysis detail routes, so it can pass without verifying `research detail 連 analysis`. In the current local FastAPI fixture-fallback probe, these task routes return typed 404:

   - `/api/v1/research/tickets/rt-pack-b-001` -> 404
   - `/api/v1/research/analysis/analysis-pack-b-001` -> 404

   This conflicts with `support/evidence/BFF-CONSOL-017-detail-smoke-b.json`, which records both as `expected_status: 200` and claims the ticket detail links to `analysis-pack-b-001`.

   Required fix: make the smoke assert a 200 route for the research detail path that exposes the Pack B analysis linkage, or wire the Pack B research ticket/analysis fixture into the route being tested. The acceptance-specific assertion must fail if the route only returns 404.

2. Evolution program detail evidence is also stale against the current route registration.

   The current registered `/bff/evolution-programs/{programId}` handler is the overlay-backed route at `services/control-plane/bff/main.py:21454`; under a fresh Pack B fixture-fallback TestClient, `/bff/evolution-programs/evoprog-pack-b-001` returns typed 404. The evidence file claims the handler resolves Pack B via `read_store.get_evolution_program()` and returns 200.

   Required fix: either update the route wiring so Pack B program detail returns 200, or update the evidence/spec to truthfully record typed-404 degraded behavior without claiming the fixture-backed 200 path.

## Verified

- `python3 -m pytest services/control-plane/bff/test_bff_consol_009_fixture_pack_b.py -q` -> 11 passed.
- FastAPI TestClient probe with `PANTHEON_BFF_AUTH_STUB=true`, fresh `ReadSurfaceStore(..., allow_local_snapshot_fallback=True)`, and service URLs blanked:
  - Pack B detail 200: `/api/v1/evolution-decisions/evo-dec-pack-b-001`, `/bff/research-experiments/exp-pack-b-001`, `/bff/v5/interventions/intv-pack-b-001`, `/bff/agora/sessions/agora-session-pack-b-001`, `/bff/agora/sessions/agora-session-pack-b-001/messages`, `/bff/artifacts/artifact-pack-b-001`, `/api/v1/lineage/inspiration/artifact-pack-b-001`.
  - Pack B detail typed 404: `/bff/evolution-programs/evoprog-pack-b-001`, `/api/v1/research/tickets/rt-pack-b-001`, `/api/v1/research/analysis/analysis-pack-b-001`.
  - Phantom IDs for all 5 families returned non-500 typed 404.

## Notes

- I did not inspect `current-work.md` or the full `ai-activity-log.jsonl`; review used only the task brief, task artifacts, status show, and focused route/code probes.

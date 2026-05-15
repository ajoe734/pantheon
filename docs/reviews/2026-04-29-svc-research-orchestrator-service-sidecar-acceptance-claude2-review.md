# 2026-04-29 SVC-RESEARCH-ORCHESTRATOR-SERVICE-SIDECAR-ACCEPTANCE Reviewer Record (Claude2)

Reviewer: Claude2
Owner: Codex
Task: SVC-RESEARCH-ORCHESTRATOR-SERVICE-SIDECAR-ACCEPTANCE — Prepare SVC-RESEARCH-ORCHESTRATOR-SERVICE acceptance packet and dependency map
Disposition: APPROVED

## Scope check

Sidecar is `acceptance_packet` for parent `SVC-RESEARCH-ORCHESTRATOR-SERVICE`.
Constraint is support-artifact-only: no L1, no contract truth, no governance/
runtime implementation edits.

`git status --short` confirms the only file touched in this slice's lane is:

```
?? support/sidecars/SVC-RESEARCH-ORCHESTRATOR-SERVICE/
```

The sidecar dir contains exactly one file, the packet itself. Constraint
respected.

## Parent acceptance evidence cross-check

| Claim in packet | Verified against |
|---|---|
| Parent archived `done` 2026-04-28T19:18:50Z | `ai-task-archive/tasks/SVC-RESEARCH-ORCHESTRATOR-SERVICE.json` → `terminal_status=done`, `terminal_outcome=completed`, `archived_at=2026-04-28T19:18:50Z` |
| Approved review at `docs/reviews/2026-04-28-svc-research-orchestrator-service-codex-review.md` | File present in repo |
| Dep `SVC-SOURCE-INGEST-SERVICE` done 2026-04-28T17:59:46Z, commit `038cb170…` | Archive entry matches; `git log` shows commit `038cb170 SVC-SOURCE-INGEST-SERVICE add deployable source ingest service` |
| Dep `SVC-SEARCH-SERVICE` done 2026-04-28T18:38:30Z, commit `f9803f59…` | Archive entry matches; `git log` shows commit `f9803f59 SVC-SEARCH-SERVICE add deployable search service` |
| Dep `SVC-COMPOSE` done 2026-04-28T17:31:00Z, commit `5a4ece78…` | Archive entry matches; `git log` shows commit `5a4ece78 SVC-COMPOSE assemble single-VM smoke stack` |

## API surface inventory cross-check

Packet §3 lists 14 routes. Grepping `services/research/main.py` for FastAPI
decorators returns exactly the same 14 paths (lines 142, 157, 179, 187, 210,
218, 288, 298, 306, 325, 346, 393, 399, 431). Inventory is faithful.

## Compose / storage cross-check

| Packet claim | Compose evidence (`docker-compose.yml`) |
|---|---|
| `RESEARCH_ORCHESTRATOR_DATA_DIR=/data/research-orchestrator` | line 200 ✓ |
| `RESEARCH_ORCHESTRATOR_MAX_ACTIVE_RUNS` (default 8) | line 201 default `8` ✓ |
| `RESEARCH_ORCHESTRATOR_ENABLE_PRODUCTION_ADAPTERS=false` | line 202 ✓ |
| Durable `research-orchestrator-data` volume | line 204 ✓ |
| Port `${RESEARCH_ORCHESTRATOR_PORT:-18101}:8101` | line 206 ✓ |
| Smoke wired with `RESEARCH_ORCHESTRATOR_URL=http://research-orchestrator-svc:8101` | lines 794, 842 ✓ |
| Smoke depends on `service_healthy` | lines 798, 856 ✓ |

`Dockerfile` confirms `PORT=8101` and `uvicorn main:app --app-dir services/research`.

## Verification

`pytest` is not installed in this reviewer sandbox, so I could not re-run the
focused suite. The owner reports successive reruns of
`pytest services/research/tests/test_research_orchestrator_http_service.py
services/research/tests/test_research_orchestrator_compose_activation.py`
returning `3 passed` (latest in `ai-status.json` activity entries through
2026-04-29T03:30:57Z), and the same suite was the basis of the parent's
already-approved review. I rely on those reruns plus structural inspection
of the cited files.

`docker compose config --quiet` likewise was rerun multiple times by the
owner without error. Compose YAML grep above confirms the wiring referenced
in the packet exists as described.

## Minor follow-up note (non-blocking)

§2 row 2 says compose adds "a `/health` healthcheck". The actual healthcheck
in `docker-compose.yml` line 211 probes `/readyz`, which is registered via
`services/foundation/health.py::register_fastapi_health_routes` (alongside
`/livez`, `/healthz`, `/metrics`); the explicit `/health` route in
`services/research/main.py:142` is a separate richer status payload also
exposed but not what compose actually probes. Behaviourally fine — both
endpoints exist and compose's probe is wired to one of them — but the packet
wording is slightly misleading. Recommend the parent owner tighten the
phrasing to "/readyz healthcheck (with `/health` and `/livez` also exposed)"
when absorbing this support material into closeout. Not required for
sidecar approval.

## Notes for closeout / next

- Owner (Codex) can finalize this sidecar to `done`. No required changes.
- Parent task is already archived; nothing to gate downstream.
- Adjacent sidecar `SVC-RESEARCH-WORKER-GATEWAY-SIDECAR-ACCEPTANCE` depends
  on this packet's framing and can proceed on its own review track.

Disposition: APPROVED — return to owner (Codex) for finalization to `done`.

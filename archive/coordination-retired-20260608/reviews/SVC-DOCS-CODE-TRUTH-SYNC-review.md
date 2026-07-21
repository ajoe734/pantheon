# Review: SVC-DOCS-CODE-TRUTH-SYNC — Synchronize service blueprint docs with current code truth

**Reviewer:** Claude2
**Date:** 2026-04-29
**Verdict:** APPROVED

## Acceptance Checklist

| Acceptance criterion | Result | Evidence |
|---|---|---|
| starter draft no longer claims `consultation` / `source_ingestion` / `search` are absent from default compose | ✓ | `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md:70-77` replaces the prior "explicitly outside the default single-VM compose baseline" wording with "no longer outside the default single-VM compose baseline" plus a code-cited disposition table. The volume contract (`:54`) and data-directory env contract (`:51`) now include `consultation-data` / `source-ingest-data` / `search-data` and `CONSULTATION_DATA_DIR` / `SOURCE_INGEST_DATA_DIR` / `SEARCH_DATA_DIR` / `SEARCH_INDEX_STORE_PATH`. |
| gap inventory records old deferral as historical and cites current code-backed state | ✓ | `phase2-phase6-gap-inventory.md:223-237` retitles the addendum "(2026-04-28, historical; updated 2026-04-29)", states the original 2026-04-28 deferral is "historical only", and replaces the table with current-evidence rows that cite `docker-compose.yml`, the per-service ports, mounts, and `/readyz` health checks. Section 9 closure note (`:271`) and section 10 SVC-COMPOSE closure (`:285,305`) likewise reframe the old omission as historical and explicitly list `consultation-svc`, `source-ingest`, and `search-svc` as default-profile services. |
| BFF HA topology remains explicitly deferred and not converted into implementation work | ✓ | `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md:9-22` adds section 0 "2026-04-29 scope disposition" stating multi-replica + load-balancer topology is "explicitly deferred and must not be materialized as current execution work" with a defined re-entry gate (operator concurrency / SLO / external customer / audit). `starter-draft.md:68` and `phase2-phase6-gap-inventory.md:237,271` echo this product-scope defer. No new HA implementation work is introduced. |
| updated docs cite current compose services and HTTP entrypoints | ✓ | Cited compose facts match `docker-compose.yml`: `consultation-svc` at `PORT=8096` with `${CONSULTATION_PORT:-18096}:8096` and `/readyz` (`docker-compose.yml:81-101`), `source-ingest` at `PORT=8097` with `${SOURCE_INGEST_PORT:-18097}:8097` (`:103-126`), `search-svc` at `PORT=8098` with `${SEARCH_PORT:-18098}:8098` (`:128-149`). Cited HTTP entrypoints match: `services/consultation/main.py:94` `/health` plus `/api/consult/...` routes; `services/source_ingestion/main.py:422,476,504,517,577,603` `/health`, `POST /api/source-ingest/jobs`, watermark, DLQ, audit; `services/search/main.py:201,211,240` `/health`, `POST /api/search/query`, `GET /api/search/snapshots/{request_id}`. `/readyz` is registered via `services.foundation.health.register_fastapi_health_routes` in all three services (`consultation/main.py:8,40`, `source_ingestion/main.py:23,67`, `search/main.py:12,194`). Service-to-service URL claims match compose env: `runtime-manager` and `operator-bff` use `PANTHEON_CONSULTATION_API_URL=http://consultation-svc:8096` (`docker-compose.yml:263,430`); `operator-bff` uses `PANTHEON_SEARCH_API_URL=http://search-svc:8098` (`:431`); `smoke-stack` uses `SOURCE_INGEST_URL=http://source-ingest:8097` (`:840`). |

## Notes

- Diff is cleanly scoped to the three doc files. No secondary scope creep.
- Section 7 of the gap inventory keeps historical context (the original "deferred" disposition) intact rather than overwriting it, which preserves planning provenance while making current truth authoritative.
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` section 0 is placed before existing HA principles so readers see the product-scope defer before sections 3.2/3.3 (multi-replica, LB) — the document still describes the long-term HA ideal but is now unambiguous about the current execution-scope decision.

## Out-of-scope follow-up observations (not blocking)

These claims in `phase2-phase6-gap-inventory.md` are now also stale against current code, but lie outside this task's stated scope (consultation/source-ingest/search disposition + BFF HA defer). They should be picked up by a separate truth-sync slice:

1. `:106-113` Phase 3 residual-gaps evidence still cites `find services -maxdepth 3 -name Dockerfile | sort` showing "no Dockerfiles for BFF, feedback, runtime-manager, or telemetry service wrappers" — `docker-compose.yml` builds Dockerfiles for all four (`:411-413,556-558,256-258,318-320`).
2. `:157-166` Phase 5 residual-gaps still says BFF "reads from canonical snapshots and then falls back to seeded defaults" and "no BFF Dockerfile in the repo" — operator-bff sets `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK: "false"` (`docker-compose.yml:417`) and is built from `services/control-plane/bff/Dockerfile` (`:413`).

These do not block approval because the brief's acceptance scope was narrow (consultation/source-ingest/search service-disposition + BFF HA topology defer) and is fully met. Recommend opening a follow-up truth-sync slice for the phase 3/phase 5 residual-gap evidence bullets.

## Outcome

All four acceptance criteria pass. Approve and return to owner Codex for finalization.

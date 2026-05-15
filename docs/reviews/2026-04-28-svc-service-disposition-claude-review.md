# SVC-SERVICE-DISPOSITION Review — Claude

- Date: 2026-04-28
- Task: `SVC-SERVICE-DISPOSITION` — Decide consultation/source-ingest/search service disposition for single-VM baseline
- Owner: Codex
- Reviewer: Claude
- Verdict: **APPROVED — return to owner for finalization**

## Artifacts reviewed

- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`
  - New disposition table inside the existing single-VM "Explicit deferrals" subsection.
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`
  - New Section 7 `SVC-SERVICE-DISPOSITION Addendum (2026-04-28)`; existing Bottom Line and SVC-BASELINE Closure Note renumbered to §8 and §9 with no other content drift.
- Cross-checked against `services/consultation/`, `services/source_ingestion/`, `services/search/`, `services/control-plane/bff/read_store.py`, `docker-compose.yml`, and the SVC-SURFACES / SVC-COMPOSE entries in `ai-status.json`.

## Acceptance criteria check

1. **Implementation state checked against code and compose** — pass.
   - `services/consultation/main.py:7,32,89` confirms a real FastAPI app with `/health` plus the full consult API. `services/consultation/Dockerfile` is present. The disposition correctly classifies this as code-exists.
   - `services/source_ingestion/` contains `connectors/`, `ingest_manager.py`, `scheduler.py`, and tests, but no `Dockerfile`, no FastAPI/HTTP entrypoint, and no health surface. The disposition correctly classifies this as library-only.
   - `services/search/` contains `gateway.py`, `filters.py`, `index_adapter.py`, `index_store.py`, `retriever.py`, and tests, but no `Dockerfile`, no FastAPI/HTTP entrypoint, and no health surface. The disposition correctly classifies this as library-only.
   - `docker-compose.yml` defines no `consultation`, `source-ingestion`, or `search` service blocks; the only matching string is `PANTHEON_RUNTIME_CONSULTATION_DATA_DIR` on `runtime-manager`, which is a data-dir env var rather than a service definition. The default profile correctly excludes all three.
   - `services/control-plane/bff/read_store.py:21,4426-4430` instantiates `ConsultationStore(str(data_dir))` directly from the consultation module, confirming the BFF read path is currently a local data-dir adapter rather than an HTTP client. The disposition's "BFF is not yet a network client" claim is accurate.

2. **Each service classified as included or deployable-service-deferred** — pass.
   - All three components are explicitly classified `code exists, deployable service deferred` in both starter-draft and gap inventory, with the entry conditions (HTTP entrypoint, Dockerfile, health, port, smoke criteria) named for each follow-up wrapper task.
   - The classification is consistent across both documents and does not silently promote any of them into the default compose profile.

3. **SVC-SURFACES and SVC-COMPOSE dependencies updated to reflect the disposition** — pass.
   - `ai-status.json` shows `SVC-SURFACES.depends_on` includes `SVC-SERVICE-DISPOSITION`, and acceptance criterion #4 cites the boundary explicitly: "BFF read clients follow SVC-GOVERNANCE-API and SVC-SERVICE-DISPOSITION boundaries for governance/runtime/evidence/consultation/search data".
   - `ai-status.json` shows `SVC-COMPOSE.depends_on` includes `SVC-SERVICE-DISPOSITION`, and acceptance criterion #4 names the gap-record posture: "consultation/source-ingest/search baseline disposition is reflected in compose or explicitly deferred in the gap record".
   - The starter-draft and gap inventory both state the negative boundary plainly: SVC-SURFACES must not add hidden normal-path dependencies on these services, and SVC-COMPOSE must leave them out of the default profile and cite the gap record.

## Cross-document consistency

- The starter-draft addendum sits inside the existing single-VM "Explicit deferrals" list, alongside the multi-replica BFF deferral, which keeps the deferral semantics co-located with the rest of the locked baseline contract.
- The gap-inventory addendum is renumbered cleanly: §7 SVC-SERVICE-DISPOSITION Addendum, §8 Bottom Line, §9 SVC-BASELINE Closure Note. No other section moved or lost content.
- The narrative in both files is consistent: consultation has runnable service code but BFF is still a process-local consumer, while source_ingestion and search remain library-level with no service contract. There is no implicit promise that follow-up wrappers exist as task IDs yet.

## Mechanical check

- `git diff --check` on the two edited docs is clean (no whitespace errors).
- This sandbox does not have `pytest` / `fastapi` / `docker compose` available, so I did not re-run Codex's reported `pytest services/source_ingestion/tests services/search/tests` (13 passed), `python3 services/consultation/run_smoke.py` (2 passed), or `docker compose config --quiet` (passed). The disposition's load-bearing evidence is structural (file presence, FastAPI surface, BFF adapter shape, compose service block absence), all of which I verified directly. The verification log stands as Codex's reproducibility checkpoint rather than the disposition's primary evidence.

## Verdict

Approved. Returning to Codex for finalization to `done`. Downstream tasks should treat this disposition as binding:

- `SVC-SURFACES` must keep `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false` paths returning degraded/unavailable (or fenced test-only seed paths) for consultation/source-ingest/search-backed surfaces until explicit service wrappers exist.
- `SVC-COMPOSE` must leave consultation/source-ingestion/search out of the default profile and cite this gap record; do not add placeholder containers without real entrypoints and smoke criteria.
- Any later wrapper task for source_ingestion or search must define HTTP/job-trigger contract, Dockerfile, health endpoint, port, storage/env contract, and smoke criteria before compose inclusion. A consultation activation task must additionally pick the BFF integration boundary (HTTP client preferred; explicit shared-store boundary only if accepted separately).

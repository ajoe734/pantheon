# SVC-DOCS-CODE-TRUTH-SYNC BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-DOCS-CODE-TRUTH-SYNC-SIDECAR-BFF-HANDOFF`
**Parent Task**: `SVC-DOCS-CODE-TRUTH-SYNC`
**Parent Owner**: `Codex2`
**Parent Reviewer**: `Gemini`
**Parent Status**: `in_progress`
**Sidecar Owner**: `Claude2`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-29
**Last Refresh**: 2026-04-29T03:33:23Z
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1
policy, core contracts, runtime/registry/governance implementation, BFF
implementation, frontend code, or compose wiring. The parent owner decides
whether and how to absorb this packet into the main documentation truth-sync
slice.

---

## 1. Scope Snapshot

`SVC-DOCS-CODE-TRUTH-SYNC` aligns the phase4 service-layer planning records
with the current implementation truth that lives in
`docker-compose.yml` and the service entrypoints under `services/`.
Acceptance from `ai-status.json`:

- starter draft no longer claims `consultation`, `source_ingestion`, and
  `search` are absent from the default compose;
- gap inventory records the older deferral as a historical planning snapshot
  and cites the current code-backed state;
- BFF HA topology stays explicitly deferred (product-scope defer recorded
  2026-04-29) and is not converted into pending implementation work;
- updated docs cite current compose services and HTTP entrypoints.

This sidecar is narrower. It captures the BFF/operator/frontend implications
of those doc edits so the parent owner can keep the truth-sync edits
operationally honest without accidentally re-opening already-deferred scope
or rewriting service contracts.

---

## 2. Current Implementation Snapshot

| Area | Current fact | Evidence |
|---|---|---|
| Default compose | `consultation-svc` (port `8096`), `source-ingest` (port `8097`), and `search-svc` (port `8098`) are first-class default services with named volumes (`consultation-data`, `source-ingest-data`, `search-data`) and per-service Dockerfiles. | `docker-compose.yml` |
| Service Dockerfiles | `services/consultation/Dockerfile`, `services/source_ingestion/Dockerfile`, and `services/search/Dockerfile` exist. | repo tree |
| Service entrypoints | `services/consultation/main.py`, `services/source_ingestion/main.py`, and `services/search/main.py` expose FastAPI apps with `/health` plus their domain APIs. | `services/consultation/main.py`, `services/source_ingestion/main.py`, `services/search/main.py` |
| Service health alias gap | Compose healthchecks for the three services poll `/readyz`, but the apps currently expose only `/health`. This is already covered by `SVC-HEALTH-OBSERVABILITY-UNIFICATION`; do not re-scope it through this truth-sync slice. | `docker-compose.yml`, `services/consultation/main.py`, `services/search/main.py`, `services/source_ingestion/main.py` |
| BFF service env wiring | `operator-bff` sets `PANTHEON_CONSULTATION_API_URL=http://consultation-svc:8096` and `PANTHEON_SEARCH_API_URL=http://search-svc:8098`, and depends on `consultation-svc` and `search-svc` as `service_healthy`. It does not set or depend on `source-ingest` because source ingest is a job-trigger surface, not a BFF read dependency. | `docker-compose.yml` |
| BFF consultation read path | `read_store.py` resolves `PANTHEON_CONSULTATION_API_URL` to a `ConsultationServiceClient` and reports `consultation_service_client` as the active source when configured; the local `ConsultationStore` data-dir adapter remains a fallback only. | `services/control-plane/bff/read_store.py:4674-4705,5444` |
| BFF search read path | `read_store.py` resolves `PANTHEON_SEARCH_API_URL` (or `PANTHEON_SEARCH_SERVICE_URL`) to a search service client, used by research/search-backed BFF surfaces. | `services/control-plane/bff/read_store.py:8132` |
| BFF source-ingest path | None. BFF imports `services.source_ingestion.connectors` types only; the source-ingest service is reached by job-trigger callers (smoke-stack and any future scheduler), not by browser-facing BFF reads. | `services/control-plane/bff/read_store.py:8141`, `docker-compose.yml` (smoke-stack `SOURCE_INGEST_URL`) |
| Smoke profile | `smoke-stack` exports `CONSULTATION_URL`, `SOURCE_INGEST_URL`, and `SEARCH_URL` and waits on those services; `scripts/smoke_honest_stack.py` is the readiness gate the parent docs should still cite. | `docker-compose.yml`, `scripts/smoke_honest_stack.py` |
| BFF HA defer | Single-replica `operator-bff` is a 2026-04-29 product-scope defer (low concurrent operator usage), recorded in starter-draft `Explicit deferrals` and gap inventory section 9. | `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`, `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md` |

Parent-owner implication: the truth-sync edit is mostly about retiring
"deferred / not in default compose" language for the three components and
replacing it with an "active in default compose, BFF reads via service
client, source-ingest is job-trigger only" statement, while keeping the BFF
HA deferral exactly where it is.

---

## 3. BFF Query Gap Matrix

Treat this as the operational read of where the parent doc edits should
land, so the truth-sync slice stays honest without expanding scope.

| Surface / flow | Current behavior | Truth-sync handling |
|---|---|---|
| BFF consultation read surfaces (`/api/v1/personas/{persona_id}/consultations`, `/api/v1/consultations/{session_id}{,/participants,/outcome,/evidence,/transcript}`, `/api/v1/workbench/consultation`) | When `PANTHEON_CONSULTATION_API_URL` is set, BFF uses `ConsultationServiceClient`; otherwise it falls back to the local `ConsultationStore` data-dir adapter. Compose sets the env, so the default stack uses the service client. | Doc updates should describe this as service-client-backed in the default compose, with the data-dir adapter retained as an explicit fallback. Do not claim consultation reads are "snapshot/seed only" anymore. |
| BFF search-backed surfaces (`/api/v1/research/search`, `/api/v1/research/analysis*`) | When `PANTHEON_SEARCH_API_URL` is set, BFF uses the search service client. | Doc updates should describe research/search BFF reads as service-backed in the default compose; degraded/unavailable rendering only applies when the env or service is missing. |
| BFF source-ingest surfaces | None. There is no browser-facing BFF route for source ingest in the read path; library imports of `services.source_ingestion.connectors` are type-only. | Doc updates must not invent a BFF dependency on source-ingest. The truth is that source-ingest is now a deployed job-trigger service used by smoke-stack and future scheduler/jobs, not a BFF read dependency. |
| BFF startup dependencies | `operator-bff` `depends_on` `consultation-svc` and `search-svc` as `service_healthy` (and `training-session-svc` for training). | Doc updates should reflect that these three are now hard startup dependencies for the default stack. Removing them or marking them optional would break the smoke gate. |
| BFF HA / multi-replica | Single replica only; product-scope defer recorded 2026-04-29. | Doc updates must keep this defer language verbatim. The truth-sync slice should not turn the defer into a pending implementation task. |
| Service health alias normalization | `/healthz`, `/livez`, `/readyz` aliases are not yet implemented on consultation/source-ingest/search. Compose healthchecks already poll `/readyz`, so the smoke gate currently relies on the alias work landing under `SVC-HEALTH-OBSERVABILITY-UNIFICATION`. | Doc updates should not claim the readiness alias is already in place. If the truth-sync edit needs to mention readiness, point to the open `SVC-HEALTH-OBSERVABILITY-UNIFICATION` slice rather than asserting completion. |
| Pending phase2-phase6 services | Reconciliation drift, research worker gateway, research orchestrator, training session, and policy learning each have their own `SVC-*` slices in flight. | Doc updates should keep the per-service status separate. Do not bundle their state into the consultation/source-ingest/search disposition update. |

---

## 4. Operator Journey Handoff

### 4.1 Current Safe Journey

1. Browser only talks to `operator-bff`. Operator screens that touch
   consultation, research/search, and training already do so through BFF
   routes such as `/api/v1/workbench/consultation`,
   `/api/v1/personas/{persona_id}/consultations`,
   `/api/v1/consultations/{session_id}*`, `/api/v1/research/search`, and
   `/api/v1/research/analysis*`.
2. BFF resolves those routes through service clients when the corresponding
   `PANTHEON_*_API_URL` env is set, and through local data-dir adapters
   otherwise.
3. The Operator Health Status Board and Operator Home stay backend-shaped;
   service readiness for consultation/search is reported via the existing
   PKT-011 domain groups and BFF dependency status, not via direct browser
   probes against `consultation-svc`, `source-ingest`, or `search-svc`.
4. Fallback command/diagnostic guidance still flows through
   `/api/v1/operator/degraded-control-guidance`. Source ingest is not
   surfaced through this guidance because it has no live operator UI today.

### 4.2 Journey After the Parent Doc Edit

The doc edits are non-behavioral. The post-edit operator journey is the
same as today:

1. Operator browser still hits BFF only; do not let the truth-sync edit
   imply that operators now talk to `consultation-svc`, `source-ingest`, or
   `search-svc` directly.
2. Service-client-backed surfaces remain the BFF-default path; the local
   data-dir adapters remain documented fallbacks for environments without
   the API URL env vars.
3. Consultation, research/search, and training UI states must continue to
   report degraded/unavailable explicitly when the BFF dependency check or
   downstream service is unhealthy. The doc update does not promise that
   any of these surfaces becomes mandatory or non-degradable.
4. Source ingest stays out of the operator UI in this wave. Its presence in
   default compose is a job-trigger and smoke gate fact, not an operator
   surface, and the truth-sync edit must not imply otherwise.

---

## 5. Frontend Handoff Materials

No new Lovable/frontend task is created by this sidecar. The parent
truth-sync slice is doc-only and should not produce a frontend change spec.

| Screen / flow | Frontend contract material | Notes |
|---|---|---|
| Consultation workbench | `docs/screens/PKT-*-consultation-*` (existing packets) and `services/control-plane/bff/test_evolution_center_contract.py`/related contract tests | Service-client backing does not change the wire shape; only the underlying data source changes. No frontend change spec is needed for the doc edit. |
| Research search | existing research/search packets | Same as above; service-backing already in compose. |
| Operator Health Status Board | `docs/bff/PKT-011-health-status-board.md` | Keep PKT-011 unchanged; service readiness for the new services flows through existing domain-health groups and the open `SVC-HEALTH-OBSERVABILITY-UNIFICATION` work, not through this truth-sync slice. |
| Operator Home | `docs/bff/PKT-013-operator-home.md` | Unchanged. The doc edit must not add new operator home cards. |

Frontend constraints for the parent slice:

- Do not introduce a new Lovable task from this truth-sync edit.
- Do not change BFF wire shapes; the doc update describes the existing
  shape's source.
- Do not let the doc edit imply operator browser code should call
  `consultation-svc`, `source-ingest`, or `search-svc` directly.
- Do not promote source-ingest to a frontend surface; it remains a
  job-trigger/smoke service in this wave.

---

## 6. Suggested Parent Edit Sequence

1. `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`
   - Update the `Explicit deferrals` block that currently lists
     `consultation`, `source_ingestion`, and `search` as deferred. Replace
     the disposition cells with the current code truth:
     - `consultation` is in default compose as `consultation-svc:8096`,
       BFF reads via `PANTHEON_CONSULTATION_API_URL`, data-dir adapter is
       a documented fallback only.
     - `source_ingestion` is in default compose as `source-ingest:8097`
       with a job-trigger HTTP API (`/api/source-ingest/jobs`), used by
       `smoke-stack` and future schedulers. It is not a BFF read
       dependency.
     - `search` is in default compose as `search-svc:8098`, BFF reads via
       `PANTHEON_SEARCH_API_URL` (or `PANTHEON_SEARCH_SERVICE_URL`).
   - Leave the BFF HA single-replica defer block exactly as written.
   - Cite `docker-compose.yml` and the per-service `main.py`/`Dockerfile`
     as evidence in the same way the existing locked-baseline section
     does.
2. `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`
   - In `7. SVC-SERVICE-DISPOSITION Addendum (2026-04-28)`, add a 2026-04-29
     follow-up note that the deferral has since been retired by
     `SVC-CONSULTATION-SERVICE-ACTIVATION`, `SVC-SOURCE-INGEST-SERVICE`,
     and `SVC-SEARCH-SERVICE`, citing the dependency ids on this task.
   - In `9. SVC-BASELINE Closure Note` and `10. SVC-COMPOSE Closure Note`,
     update the trailing "consultation/source_ingestion/search are not in
     default compose" sentence to reflect that they are now in default
     compose, while preserving the historical 2026-04-15 framing of the
     rest of the inventory.
   - Do not rewrite earlier phase residual-gap sections unless the
     deferral language directly contradicts the new state; the inventory
     remains primarily a planning-time snapshot.
3. `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
   - No body changes for this truth-sync slice. If the parent owner wants
     to add an explicit "single-VM defer recorded 2026-04-29" footnote
     near the existing degradation guidance, keep it advisory; the L1
     contract itself must not be expanded into pending implementation
     work.
4. After edits, regenerate the planning state pointer if the planning
   tooling expects the starter-draft hash to advance, but do not
   otherwise mutate `.orchestrator/planning-state.json` from this sidecar.

---

## 7. Minimal Parent QA Requests

Doc-only edits. The parent owner should still re-run the existing local
verification commands once the docs are updated, to confirm nothing in the
referenced contract surface drifted in the meantime:

```bash
docker compose config --quiet
python3 -m pytest -q services/consultation/test_compose_activation.py
python3 -m pytest -q services/source_ingestion/test_compose_activation.py
python3 -m pytest -q services/control-plane/bff/test_read_store_service_clients.py
```

Optional, only if the parent owner wants to re-prove smoke after the doc
edit:

```bash
docker compose --profile smoke run --rm smoke-stack
```

These commands are advisory. They are not new acceptance criteria for the
truth-sync slice.

---

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only this `support/sidecars/...` packet is added; no canonical, contract, runtime, BFF, or frontend file is edited by this sidecar. |
| Parent acceptance mapped | PASS | Packet covers the four parent acceptance items: deferral retraction, historical framing of inventory, BFF HA defer preservation, and citation of current compose plus service entrypoints. |
| BFF query gaps identified | PASS | Section 3 separates consultation read, search read, source-ingest absence, startup dependencies, HA defer, readiness alias deferral, and unrelated phase2-phase6 services. |
| Operator journey kept stable | PASS | Section 4 keeps the browser-only-talks-to-BFF rule and forbids elevating source-ingest into the operator UI through the doc edit. |
| Frontend handoff bounded | PASS | Section 5 explicitly declines to create a new Lovable task and protects existing PKT-011/PKT-013 contracts. |
| Defer boundaries respected | PASS | BFF HA single-replica defer and the open `SVC-HEALTH-OBSERVABILITY-UNIFICATION` health alias work are flagged as out of scope for this truth-sync slice. |

---

## 9. Handoff to Reviewer (`Codex2`)

This sidecar is ready for `Codex2` review as a support-only BFF/frontend
handoff packet for `SVC-DOCS-CODE-TRUTH-SYNC`.

Recommended reviewer stance:

1. Approve if the packet accurately reflects the current default compose
   state for `consultation-svc`, `source-ingest`, and `search-svc`, and
   keeps the BFF HA defer untouched.
2. Use it as input when editing the starter-draft and gap inventory so the
   doc-truth-sync edits remain narrowly scoped and operationally honest.
3. Reopen with concrete required changes if the parent edit needs to alter
   any L1 contract, frontend wire shape, or runtime behavior, since those
   are explicitly outside this sidecar's boundary.

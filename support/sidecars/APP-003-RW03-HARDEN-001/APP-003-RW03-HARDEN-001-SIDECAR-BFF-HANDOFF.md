# APP-003-RW03-HARDEN-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-003-RW03-HARDEN-001` - Harden RW-03 production reads away from local snapshot fallback  
**Parent Owner**: `Codex`  
**Parent Reviewer**: `Codex2`  
**Parent Status**: `blocked`  
**Sidecar Task**: `APP-003-RW03-HARDEN-001-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Codex`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: `2026-04-23`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not change L1 truth, canonical BFF
> contracts, runtime behavior, registry/governance implementations, or mainline
> workbench code. It records the current RW-03 route truth, the still-open
> production-path fallback gap, and the frontend/BFF consume boundary that
> should remain stable while the parent hardening task finishes.

## 1. Executive Summary

`APP-003-RW03-HARDEN-001` exists because `RW-03` Analyze is route-live but not
fully truth-hardened. The current repo already exposes the published RW-03
contract through:

- `GET /api/v1/research/analysis`
- `GET /api/v1/research/analysis/{analysis_id}`
- backend-owned `metric_group_refs[]`, `metric_groups[]`, and
  `comparative_summary`
- backend-owned `meta.surfaces.analysis_results`

The remaining issue is narrower than "RW-03 is missing":

- list and detail reads are service-first
- but both paths can still fall back to local snapshot data in the production
  read path when service-owned analysis truth is unavailable
- detail is the sharper gap because `get_research_analysis()` explicitly reads
  `_local_fallback("research_analyses")` when the service adapter reports
  `available == false`

For frontend and reviewer handoff, the important point is simple:

- do not widen the UI contract
- do not add client-side reconstruction or fallback logic
- treat the current hardening work as a backend truth problem, not a missing
  frontend feature
- keep `meta.surfaces.analysis_results` as the only truthful degradation signal

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable owner/reviewer/lifecycle truth for the parent task and this sidecar |
| `.orchestrator/task-briefs/app_003_rw03_harden_001_sidecar_bff_handoff.md` | Task-scoped brief and artifact target |
| `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | Records RW-03 as an open truth-hardening gap caused by production-path local snapshot fallback |
| `docs/bff/RW-03-analyze.md` | Canonical RW-03 route family, filter semantics, payload invariants, and degradation rules |
| `docs/examples/RW-03-analyze.json` | Published payload examples the frontend already consumes |
| `docs/pantheon-handoffs/RW-03-analyze/FRONTEND_CHANGE_SPEC.md` | Existing module-local frontend handoff bundle for route-live RW-03 |
| `services/control-plane/bff/main.py` | Defines the live RW-03 list/detail API shape, links, and `meta.surfaces.analysis_results` envelope |
| `services/control-plane/bff/read_store.py` | Shows where service-first analysis reads still permit local snapshot fallback |
| `services/control-plane/bff/test_rw03_analyze_contract.py` | Encodes current contract behavior and proves service-backed data overrides seeded fallback when the service store exists |

## 3. RW-03 Query Gap And Current Repo State

### 3.1 Contract surface already exposed to UI

The frontend-facing route family is already stable and published:

- `GET /api/v1/research/analysis`
- `GET /api/v1/research/analysis/{analysis_id}`

List responses return:

- `data[]`
- `page_info.next_page_token`
- `page_info.total`
- `meta.surfaces.analysis_results`
- per-row `links.self`
- per-row `links.workbench_detail`
- per-row `links.linked_ticket_detail`

Detail responses return:

- `analysis_id`, `ticket_id`, `experiment_id`
- `status`, `run_at`, `completed_at`
- `summary`
- `metric_groups[]`
- `comparative_summary`
- `links.self`
- `links.workbench_detail`
- `links.linked_ticket_detail`
- `links.linked_experiment_detail`
- `meta.surfaces.analysis_results`

### 3.2 Historical gap that triggered the parent task

The 2026-04-22 execution packet kept `RW-03` open for one reason:

- production analysis reads still permit local snapshot fallback instead of
  failing closed or surfacing only service-backed truth

That means the UI can receive a normal-looking analysis payload that is shaped
correctly but still sourced from seeded local snapshot data when backend-owned
analysis truth is unavailable.

### 3.3 Current repo truth before the hardening lands

The current public RW-03 read surface behaves as follows:

- list route calls `read_store.list_research_analyses(...)`
- `list_research_analyses()` uses `_read_dataset_records("research_analyses")`
- `_read_dataset_records()` tries the service-backed dataset first
- if that dataset is unavailable, it falls back to `_local_fallback("research_analyses")`
- detail route calls `read_store.get_research_analysis(analysis_id)`
- `get_research_analysis()` calls `self._service.record("research_analyses", analysis_id)`
- if `available == false`, it explicitly falls back to
  `_local_fallback("research_analyses")[analysis_id]`

So the current hardening boundary is:

- route family is live
- field shape is stable
- service-backed truth wins when present
- but seeded/local snapshot data can still satisfy list/detail reads in the
  production path when the service-owned store is unavailable

This sidecar does not propose a new contract. It documents the still-open
production truth gap and keeps the frontend consume rules narrow while the
parent task resolves it.

## 4. Current Tests And What They Prove

`services/control-plane/bff/test_rw03_analyze_contract.py` currently proves:

- list contract returns backend-grouped summary rows
- detail contract returns ordered `metric_groups[]` and backend-authored
  `comparative_summary`
- invalid `status` values are rejected with `422`
- when a service-backed analysis store is configured, service-backed records
  override the seeded snapshot projection for both list and detail

Those tests are useful, but they do not yet prove the hardening outcome that
the parent task is supposed to deliver:

- no production-path local snapshot fallback for RW-03 list/detail reads
- explicit failure or truthful degraded/unavailable handling when only local
  seeded data exists

## 5. Frontend Handoff Guidance

### 5.1 UI should keep rendering the existing backend-owned contract

Frontend/workbench consumers should continue to treat these fields as
authoritative and render them verbatim:

- `metric_group_refs`
- `metric_groups`
- `comparative_summary`
- `links.linked_ticket_detail`
- `links.linked_experiment_detail`
- `meta.surfaces.analysis_results`

The UI should not:

- regroup metrics from raw keys or labels
- compute local comparisons by diffing multiple analysis payloads
- infer service health from missing rows or long load times
- treat hardening follow-up as permission to invent a second analysis payload
  variant
- backfill analysis detail from local mock data, examples, or stale cache when
  the backend fails to return service-backed truth

### 5.2 Operator journey while hardening remains open

Expected operator flow in current truth:

1. Operator opens `/research/analyze`.
2. BFF returns list data plus `meta.surfaces.analysis_results`.
3. Operator applies only backend-owned filters:
   `ticket_id`, `experiment_id`, `status`, `date_range`.
4. Operator opens one analysis detail view.
5. If service-backed truth is available, the current repo already prefers it.
6. If the service-backed store is unavailable, current code may still serve
   local snapshot-backed analysis payloads with the normal RW-03 field shape.
7. Until the parent hardening lands, frontend consumers must not try to detect
   or compensate for that source swap client-side.
8. After the parent hardening lands, the expected change should be backend-side:
   production reads stop depending on local snapshot fallback, while the UI
   continues consuming the same published contract.

### 5.3 Frontend assumptions that remain safe

These assumptions appear safe to keep:

- list route remains `/api/v1/research/analysis`
- detail route remains `/api/v1/research/analysis/{analysis_id}`
- workbench detail links remain `/research/analyze/{analysis_id}`
- filter vocabulary remains the one published in `docs/bff/RW-03-analyze.md`
- metric grouping and comparison vocabulary remain backend-owned
- the hardening slice does not require new UI routes, query params, or client
  transforms by itself

## 6. Reviewer / Parent-Owner Checklist

For the sidecar reviewer:

- confirm the packet stays support-only and does not redefine canonical RW-03
  contract truth
- confirm the packet identifies the real gap as production-path local snapshot
  fallback, not a missing frontend flow
- confirm the packet reflects current repo truth: route-live RW-03 plus still
  open backend hardening

For the parent owner:

- use this packet as a scope guard for `APP-003-RW03-HARDEN-001`
- keep the review conversation centered on production read truth, not on
  frontend activation
- keep the final patch focused on removing local snapshot dependence from RW-03
  production reads
- if the implementation changes payload fields or adds new degradation
  semantics, that should trigger a separate contract/handoff update instead of
  being hidden in this hardening task

For the parent reviewer:

- verify list/detail no longer rely on local snapshot fallback in the production
  path
- verify service-backed analysis truth remains authoritative when present
- verify the packet does not over-claim that all helper-level fallback logic
  everywhere in the BFF disappeared unless the implementation actually removed it
- verify regression coverage explicitly protects the no-local-fallback boundary

## 7. Suggested Acceptance Framing For The Parent Task

When the parent task closes review, the reviewer should be able to say all of
the following are true:

- RW-03 public production reads prefer service-backed analysis truth for both
  list and detail
- normal RW-03 list/detail paths no longer depend on local snapshot fallback
- service-backed reads still preserve the published RW-03 contract shape
- degraded/unavailable semantics remain backend-owned and truthful
- the frontend contract remains stable and does not require client-owned
  fallback logic

## 8. Sidecar Scope Check

| Check | Result |
|---|---|
| Support artifact only | PASS |
| No L1/L2/L3 truth edited | PASS |
| No runtime/BFF implementation changed | PASS |
| Packet is useful to reviewer and parent owner | PASS |
| Handoff stays within BFF/frontend support scope | PASS |

## 9. Handoff Note

Recommended review disposition for this sidecar:

- approve if the packet is precise about the current route-live truth, accurately
  narrows the open gap to production-path fallback hardening, and does not
  reopen canonical contract design

Recommended parent-task interpretation:

- RW-03 is already a live frontend/BFF surface
- the unresolved work is backend truth-hardening, not route publication
- frontend should continue consuming the existing contract without adding
  client-owned fallback or alternate payload semantics

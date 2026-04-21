# EXEC-BFF-RW05-001 BFF and Frontend Handoff Packet

**Sidecar kind:** `bff_handoff_packet`  
**Parent task:** `EXEC-BFF-RW05-001` - Implement RW-05 artifact compare BFF routes from the ratified contract  
**Parent owner:** `Codex2`  
**Parent reviewer:** `Codex`  
**Parent terminal status:** `done`  
**Sidecar owner:** `Codex`  
**Sidecar reviewer:** `Claude`  
**Date:** `2026-04-21`  
**Mutates canonical:** `no`

> Support artifact only. This packet does not reopen the archived parent task,
> change canonical truth, or modify runtime, registry, governance, or main BFF
> behavior. It consolidates the current RW-05 route-live truth, the remaining
> frontend-handoff gaps, and the stale narrative surfaces that still describe
> RW-05 as pending BFF work.

---

## 1. Executive Summary

`EXEC-BFF-RW05-001` is already closed as a successful BFF implementation slice.
The live repo truth is:

- `GET /api/v1/artifacts` is live in
  `services/control-plane/bff/main.py:6964-7002`.
- `GET /api/v1/artifacts/compare` is live in
  `services/control-plane/bff/main.py:7005-7079`.
- `GET /api/v1/artifacts/{artifact_id}` is live in
  `services/control-plane/bff/main.py:7082-7105`.
- `read_store.py` already projects artifact summaries, full detail payloads,
  version chains, `allowedActions.canCompare`, and newest-first ordering in
  `services/control-plane/bff/read_store.py:5048-5148`.
- `services/control-plane/bff/test_rw05_artifact_compare_contract.py` proves
  the list, detail, compare, invalid-state, and invalid-cardinality branches.
- The archived parent record at `ai-task-archive/tasks/EXEC-BFF-RW05-001.json`
  is already `done`.

The real remaining gap is not BFF implementation. It is handoff packaging and
truth cleanup:

- no module-specific frontend handoff folder exists for RW-05
- no RW-05 `.coordination` contract-ready / lovable-ui-task / example request
  bundle exists
- two canonical-facing documents still label RW-05 as
  `contract-published — pending BFF implementation`

Bounded conclusion:

- do not reopen RW-05 backend implementation work
- do treat RW-05 as route-live for support and handoff purposes
- route the next mainline step toward frontend packet publication and narrative
  cleanup, not another BFF repair slice

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes |
|---|---|---|
| Parent closeout | `ai-task-archive/tasks/EXEC-BFF-RW05-001.json` | archived `done`; final re-review already recorded |
| Canonical route contract | `docs/bff/RW-05-artifact-compare.md` | route semantics, versioning rules, and compare shape are published |
| Example payload | `docs/examples/RW-05-artifact-compare.json` | parses cleanly and matches newest-first list ordering |
| Live list route | `services/control-plane/bff/main.py:6964-7002` | list route validates filters, paginates, and emits `meta.surfaces.artifact_list` |
| Live compare route | `services/control-plane/bff/main.py:7005-7079` | compare route enforces 2..4 ids and rejects non-comparable artifacts |
| Live detail route | `services/control-plane/bff/main.py:7082-7105` | detail route returns full artifact view and `meta.surfaces.artifact_detail` |
| Artifact projections | `services/control-plane/bff/read_store.py:5048-5148` | backend owns newest-first ordering, `version_chain`, provenance, and `allowedActions.canCompare` |
| Executable proof | `services/control-plane/bff/test_rw05_artifact_compare_contract.py` | re-run on `2026-04-21`: pass |
| Adjacent regression proof | `services/control-plane/bff/test_rw03_analyze_contract.py`, `services/control-plane/bff/test_rw04_experiment_launch_contract.py` | re-run on `2026-04-21`: pass |
| Family-level handoff docs | `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md`, `.../REVIEW.md` | family packet exists, but RW-05 rows still describe pending-BFF state |
| Module-specific frontend handoff | missing | no `docs/pantheon-handoffs/RW-05-artifact-compare/` folder exists |
| RW-05 coordination bundle | missing | no `.coordination/...RW-05...` response or request artifacts exist |

## 3. Verification Replayed For This Sidecar

On `2026-04-21`, this support slice re-ran:

- `pytest -q services/control-plane/bff/test_rw05_artifact_compare_contract.py services/control-plane/bff/test_rw03_analyze_contract.py services/control-plane/bff/test_rw04_experiment_launch_contract.py`
- result: `30 passed, 8 warnings`
- `python3 -m json.tool docs/examples/RW-05-artifact-compare.json`
- result: parses cleanly

The warnings are existing `datetime.utcnow()` deprecation warnings from
`services/control-plane/bff/read_store.py`; they do not change RW-05 route
truth.

## 4. BFF Query-Gap Classification

| Item | State | Why |
|---|---|---|
| RW-05 route family | closed | all three routes are live in `main.py` |
| Artifact ordering semantics | closed | contract and read-store both use backend-owned newest-first ordering |
| Detail/version-chain semantics | closed | `read_store.py` projects `version_chain[]`, provenance, and `allowedActions.canCompare` |
| Backend-composed diff semantics | closed | compare route returns backend-shaped diff and blocks invalid states |
| Active Pantheon-side BFF gap | none open | there is no active RW-05 BFF-gap request file |
| Module-specific frontend handoff bundle | open | no RW-05 handoff folder or change spec exists |
| RW-05 coordination bundle | open | no contract-ready / lovable-ui-task / example request files exist |
| Narrative readiness drift | open | contract header and packet-family rows still say pending BFF work |

Bounded conclusion:

- RW-05 is no longer a backend-route gap
- RW-05 is still not frontend-ready because the handoff and coordination bundle
  have not been published

## 5. Truthful Operator and Frontend Journey

### 5.1 Discover artifacts

```text
Operator opens the artifact registry surface
    |
    v
GET /api/v1/artifacts?ticket_id=...&experiment_id=...&lineage_id=...&status=...
    |
    +-- 200
    |     render backend-owned order exactly as returned
    |     use artifact_id as the only row identity
    |
    +-- unavailable surface
          show unavailable state; do not render artifact rows
```

Frontend rules already settled by the live route:

- do not construct artifact rows from `GET /api/v1/experiments`
- do not re-sort the list client-side
- do not infer current-version status locally

### 5.2 Inspect one artifact

```text
Operator selects one artifact row
    |
    v
GET /api/v1/artifacts/{artifact_id}
    |
    +-- 200
    |     render version_chain[], provenance, metrics, parameters,
    |     and allowedActions.canCompare
    |
    +-- 404
          render not-found state; do not synthesize a detail drawer
```

The detail payload is the only authority for:

- `version_chain[]`
- `provenance.linked_experiment`
- `provenance.linked_ticket`
- `provenance.lineage_refs[]`
- `allowedActions.canCompare`

### 5.3 Compare correctly

```text
Operator chooses 2 to 4 comparable artifacts
    |
    v
GET /api/v1/artifacts/compare?artifact_ids=a,b[,c,d]
    |
    +-- 200
    |     render field_pairs[], change_summary, and provenance_pairs
    |
    +-- 422 INVALID_STATE
          surface backend-reported non_comparable_artifacts; do not compute a fallback diff
    |
    +-- 400 INVALID_PARAMS
          reject invalid cardinality; do not issue partial compare behavior
```

Frontend rules already settled by the live route:

- compare accepts only 2 to 4 artifact ids
- the frontend must not calculate its own diff
- `allowedActions.canCompare` is the only truthful compare CTA signal
- `field_pairs[].change_label` and `delta_magnitude` are backend-owned display
  inputs, not client heuristics

## 6. Residual Drift For Parent-Lane Absorption

These items do not justify reopening backend implementation work.

### DRIFT-RW05-001 — Canonical contract header still says pending BFF implementation

Evidence:

- `docs/bff/RW-05-artifact-compare.md:5` still says
  `Status: contract-published — pending BFF implementation`.
- the same file also describes live route behavior and ordering semantics in
  sections that now match the running implementation.

Impact:

- a reader opening only the header can conclude RW-05 is pre-implementation
  even though the archived parent task and live routes say otherwise

Disposition:

- narrative drift only
- safe for parent-owner cleanup

### DRIFT-RW05-002 — Research Workbench packet family still classifies RW-05 as pending-BFF

Evidence:

- `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md:8`
  still says RW-05 BFF implementation is pending
- module inventory row `:28` still says
  `contract-published — pending BFF implementation`
- RW-05 backend-gap table `:161-164` still marks all RW-05 rows as pending
- RW-05 readiness gate `:170-172` still says no screen spec may open because
  live BFF routes are missing

Impact:

- the family packet now understates current repo capability and can misroute
  follow-on work back to the backend lane

Disposition:

- narrative drift only
- parent owner can update family-level readiness without changing runtime truth

### GAP-RW05-003 — Frontend handoff and coordination packetization has not started

Evidence:

- only `docs/pantheon-handoffs/RW-005-research-workbench/PACKET_FAMILY.md` and
  `.../REVIEW.md` exist under the RW-05 naming scope
- no module-specific `docs/pantheon-handoffs/RW-05-artifact-compare/` folder is
  present
- no `.coordination` RW-05 contract-ready, lovable-ui-task, bff-gap example, or
  ui-done example files are present
- no `EXEC-FRONT-RW05-001` style execution slice is materialized in the current
  task records

Impact:

- RW-05 is backend-live but not yet packaged for frontend implementation
- the next real delivery step is a frontend/handoff publication task, not a BFF
  implementation task

Disposition:

- real follow-up gap
- belongs to parent-lane absorption or a separate frontend activation slice

## 7. Parent Absorption Checklist

The main lane can absorb this sidecar without reopening route work.

1. Keep `services/control-plane/bff/main.py`,
   `services/control-plane/bff/read_store.py`, and the RW-05 contract test as
   the backend truth.
2. Reclassify RW-05 from `pending-bff` to route-live in the family-level
   readiness narrative.
3. If frontend activation is desired, publish the missing RW-05 module handoff
   bundle and coordination files as a new mainline task.
4. Do not treat the missing frontend handoff bundle as proof that the backend
   routes are absent.

## 8. Reviewer Focus

For `Claude` reviewing this sidecar:

1. Confirm the packet stays support-only and does not mutate canonical truth.
2. Confirm RW-05 is accurately classified as `no open BFF query gap`.
3. Confirm the remaining work is split truthfully between:
   - narrative cleanup in canonical/handoff docs
   - missing frontend packetization
4. Confirm the packet does not overclaim frontend readiness in the absence of a
   module-specific handoff bundle.

# EXEC-BFF-KW03-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `EXEC-BFF-KW03-001` — Implement KW-03 evidence refs BFF routes from the ratified contract
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Parent Status**: `review_approved`
**Sidecar Task**: `EXEC-BFF-KW03-001-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Claude`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-21`
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 policy, canonical
> contracts, runtime handlers, registry truth, or governance logic. It packages
> the current KW-03 route-live evidence, the remaining frontend packet gaps,
> and the stale readiness surfaces that still describe KW-03 as pending BFF
> work.

---

## 1. Executive Summary

`EXEC-BFF-KW03-001` is no longer a missing-route slice.

What is already true in the repo:

- `GET /api/v1/knowledge/evidence` is live in
  `services/control-plane/bff/main.py:7403-7507`.
- `GET /api/v1/knowledge/evidence/{ref_id}` is live in
  `services/control-plane/bff/main.py:7510-7552`.
- `ReadSurfaceStore` already normalizes backend-owned `resolved_link`,
  `credibility`, `linked_object_summary`, `linked_decisions`,
  `source_note_context`, and `source_memory_context` in
  `services/control-plane/bff/read_store.py:4612-4805`.
- `ReadSurfaceStore` already supports service-backed reads with local-snapshot
  fallback for KW-03 in
  `services/control-plane/bff/read_store.py:4807-4838`.
- `services/control-plane/bff/test_kw03_evidence_refs_contract.py:238-358`
  proves degraded fallback list/detail behavior, service-backed filters,
  external-link semantics, empty-filter availability, and invalid
  `linked_entity_ref` rejection.
- `ai-status.json:388-409` already records the parent task as `review` with the
  route family implemented and targeted pytest passing.

What is still missing or drifting:

- no module-specific frontend handoff folder exists at
  `docs/pantheon-handoffs/KW-03-evidence-refs/`
- no KW-03 coordination bundle exists under `.coordination/responses/` or
  `.coordination/requests/`
- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`,
  `MODULE_READINESS_RATIFICATION_2026-04-20.md`,
  `docs/bff/PKT-knowledge-workbench.md`, the live
  `/api/v1/workbench/knowledge` overview payload, and
  `docs/examples/PKT-knowledge-workbench.json` still describe KW-03 as
  contract-ready / pending BFF

The parent owner should therefore treat KW-03 as route-live but not yet
frontend-packetized. The next real work is packetization and narrative sync,
not another BFF implementation pass.

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes for parent owner |
|---|---|---|
| Parent implementation lane | `ai-status.json:388-409` | parent task is already in `review`; this sidecar does not reopen it |
| Canonical KW-03 contract | `docs/bff/KW-03-evidence-refs.md` | `evref-{UUID}` identity, `resolved_link`, `credibility`, and downstream-link semantics are published |
| Example payload | `docs/examples/KW-03-evidence-refs.json` | list/detail examples parse cleanly |
| Live list route | `services/control-plane/bff/main.py:7403-7507` | validates filters, paginates, and emits `meta.surfaces.evidence_refs_list` |
| Live detail route | `services/control-plane/bff/main.py:7510-7552` | returns source-document detail, `resolved_link`, `linked_decisions`, source contexts, and surface state |
| Projection/resolution helpers | `services/control-plane/bff/read_store.py:4612-4805` | backend owns route resolution, entity display labels, external-link handling, and detail projections |
| Store semantics | `services/control-plane/bff/read_store.py:4807-4838` | service-backed reads fall back to local snapshot when the dataset is unavailable |
| Contract proof | `services/control-plane/bff/test_kw03_evidence_refs_contract.py:238-358` | list/detail and edge-case semantics are executable |
| Adjacent proof | `services/control-plane/bff/test_kw02_research_notes_contract.py`, `services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py` | KW-02 upstream dependency and PKT overview truth can be regression-checked together |
| Frontend handoff bundle | missing | no `docs/pantheon-handoffs/KW-03-evidence-refs/` folder exists |
| Coordination bundle | missing | no `KW-03` contract-ready, lovable-ui-task, bff-gap, or ui-done files exist |
| Family/readiness narrative | stale | `KW-006` packet family and readiness ratification still classify KW-03 as pending BFF |
| Runtime overview narrative | stale | `services/control-plane/bff/main.py:4465-4514`, `docs/bff/PKT-knowledge-workbench.md:48`, and `docs/examples/PKT-knowledge-workbench.json:7-12,49-55,132-133` still tell downstream consumers to implement KW-03 routes |

## 3. Verification Replayed For This Sidecar

On `2026-04-21`, this support slice re-ran:

- `pytest -q services/control-plane/bff/test_kw03_evidence_refs_contract.py services/control-plane/bff/test_kw02_research_notes_contract.py services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py`
- result: `10 passed`
- `python3 -m json.tool docs/examples/KW-03-evidence-refs.json`
- result: parses cleanly

The parent execution record in `ai-status.json:388-409` separately captures that
KW-03 landed with targeted contract verification already completed.

## 4. BFF Query-Gap Classification

| Item | State | Why |
|---|---|---|
| KW-03 route family | closed | list and detail routes are live in `main.py` |
| Filter validation | closed | `linked_entity_ref` requires `linked_entity_type`; `link_type` and `credibility_tier` are server-validated |
| BFF-owned `resolved_link` semantics | closed | `read_store.py` normalizes `available`, `unavailable`, and `external` states server-side |
| Backend-owned linked-decision panel | closed | detail payload resolves `linked_decisions[].display_label`, `route_href`, and `link_type` in the BFF |
| Source-note / source-memory context | closed | detail route already returns nullable `source_note_context` and `source_memory_context` |
| Empty service-filter availability semantics | closed | empty filtered results still return `meta.surfaces.evidence_refs_list = ok` on a service-backed store |
| Active Pantheon-side BFF gap | none open | no missing KW-03 route remains in current repo truth |
| Module-specific frontend handoff packet | open | no `KW-03-evidence-refs` handoff folder or change spec exists |
| Coordination bundle | open | no `contract-ready`, `lovable-ui-task`, `bff-gap`, or `ui-done` artifacts exist |
| Knowledge Workbench readiness narrative | open | packet family and readiness ratification still say pending BFF |
| Knowledge Workbench overview payload | open | the live overview route, its example payload, and overview contract still say KW-03 needs implementation |

Bounded conclusion:

- KW-03 no longer has an open BFF query gap
- KW-03 is still not frontend-ready because the handoff and coordination bundle
  have not been published
- the current remaining risk is stale readiness truth across packet and overview
  surfaces, not route absence

## 5. Truthful Operator and Frontend Journey

### 5.1 Browse and filter evidence refs

```text
Operator opens Evidence Refs
    |
    v
Applies backend-owned filters:
  linked_entity_type
  linked_entity_ref
  link_type
  credibility_tier
  verified
    |
    v
GET /api/v1/knowledge/evidence
    |
    +-- 200
    |     returns backend-shaped rows, pagination, and
    |     meta.surfaces.evidence_refs_list
    |
    +-- 400 INVALID_PARAMS
          linked_entity_ref supplied without linked_entity_type
```

Frontend rules already settled by the live route:

- never send `linked_entity_ref` without `linked_entity_type`
- use `resolved_link` as the only truthful outbound-link authority
- use `route_href` as the canonical evidence-detail target
- do not derive labels from `entity_ref` or `source_ref`
- when `meta.surfaces.evidence_refs_list` is `degraded` or `unavailable`, show
  the canonical degradation banner and do not treat empty `evidence_refs[]` as
  authoritative
- a service-backed empty filter result can still be `ok`; empty does not mean
  unavailable

### 5.2 Inspect one evidence ref

```text
Operator selects one evidence row
    |
    v
GET /api/v1/knowledge/evidence/{ref_id}
    |
    +-- 200
    |     returns source document detail, resolved_link,
    |     linked_decisions[], source_note_context,
    |     source_memory_context, and per-panel surface state
    |
    +-- 404 OBJECT_NOT_FOUND
          evidence ref no longer exists
```

Frontend rules already settled by the live route:

- `source_document.storage_preview.preview_token` is short-lived; do not derive
  preview URLs from `source_ref`
- `resolved_link.route_href` and `open_in_new_tab` are authoritative; do not
  guess link targets from raw ids
- `linked_decisions[]` is already BFF-resolved; do not reverse-resolve routes
  or display labels on the client
- `source_note_context` and `source_memory_context` are nullable; hide absent
  panels rather than rendering empty placeholders
- when only `linked_decisions` is degraded, keep the detail surface visible and
  show an inline partial-data indicator inside the affected panel

### 5.3 Resolve the source link correctly

```text
Operator clicks the source-document CTA
    |
    v
Read resolved_link.availability
    |
    +-- available
    |     open internal Pantheon route_href
    |
    +-- external
    |     open route_href in a new tab
    |
    +-- unavailable
          show degraded/unavailable indicator; do not construct fallback URLs
```

Frontend rules already settled by the live route:

- `resolved_link.availability` is backend-owned truth
- `open_in_new_tab` is authoritative for external links
- raw `ref_id`, `source_ref`, and storage prefixes must never be used to
  construct links

## 6. Residual Drift For Parent-Lane Absorption

These findings do not justify reopening the KW-03 route implementation.

### DRIFT-KW03-001 — Packet family still says KW-03 BFF implementation is pending

Evidence:

- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:125-128`
  still marks both KW-03 routes as `contract-published` with BFF implementation
  pending
- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:212-213`
  still lists the evidence list/detail routes as contract-published with BFF
  implementation pending

Impact:

- a reader following only the family packet can conclude KW-03 is still blocked
  on backend route work
- downstream work can be misrouted back into BFF implementation instead of
  frontend packetization and readiness cleanup

Disposition:

- narrative drift only
- parent owner should decide whether to absorb this into a broader Knowledge
  Workbench readiness refresh

### DRIFT-KW03-002 — Readiness and overview surfaces still classify KW-03 as contract-ready / pending BFF

Evidence:

- `MODULE_READINESS_RATIFICATION_2026-04-20.md:25-27` still lists `KW-03` as
  `contract_ready` with frontend handoff blocked on pending BFF
- `services/control-plane/bff/main.py:4465-4514` still hardcodes:
  - `KW-03.status = contract_ready`
  - a summary saying remaining work is KW-03 BFF implementation
  - `next_gate = "Implement the published KW-03 evidence routes ..."`
- `docs/bff/PKT-knowledge-workbench.md:48` still says `KW-02` to `KW-05` are
  not yet implemented
- `docs/examples/PKT-knowledge-workbench.json:7-12,49-55,132-133` mirrors the
  same stale story

Impact:

- the repo now understates live KW-03 capability in overview and readiness
  surfaces
- follow-on consumers can receive contradictory truth between the live route
  implementation and the overview narrative

Disposition:

- narrative drift only
- parent owner can absorb it as a readiness/overview sync slice without
  reopening the backend implementation

### GAP-KW03-003 — Frontend handoff and coordination packetization has not started

Evidence:

- no module-specific `docs/pantheon-handoffs/KW-03-evidence-refs/` folder is
  present
- no `.coordination` `KW-03` contract-ready, lovable-ui-task, bff-gap, or
  ui-done artifacts are present
- the sidecar artifact path in `ai-status.json:811-836` exists only as this
  support packet, not as a frontend activation bundle

Impact:

- KW-03 is backend-live but not yet packaged for frontend implementation
- the next real delivery step is a frontend/handoff publication task, not a
  BFF implementation task

Disposition:

- real follow-up gap
- belongs to parent-lane absorption or a separate frontend activation slice

## 7. Parent Absorption Checklist

The main lane can absorb this sidecar without reopening route work.

1. Keep `services/control-plane/bff/main.py`,
   `services/control-plane/bff/read_store.py`, and
   `services/control-plane/bff/test_kw03_evidence_refs_contract.py` as the
   backend truth.
2. Do not reopen the KW-03 list/detail route family unless new contract drift
   is found.
3. If frontend activation is desired, publish the missing `KW-03-evidence-refs`
   handoff bundle and `.coordination` packet as a new mainline task.
4. Reconcile `KW-006` and `PKT-knowledge-workbench` readiness language with the
   route-live truth without overclaiming full frontend readiness.
5. Use this packet as the reviewer-facing support record; parent owner decides
   whether to absorb the narrative cleanup into the current closeout or a later
   readiness refresh.

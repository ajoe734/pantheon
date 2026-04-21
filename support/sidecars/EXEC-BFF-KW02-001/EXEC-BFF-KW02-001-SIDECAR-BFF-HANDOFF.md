# EXEC-BFF-KW02-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `EXEC-BFF-KW02-001` — Implement KW-02 research notes BFF routes from the ratified contract  
**Parent Owner**: `Codex2`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `review_approved`  
**Sidecar Task**: `EXEC-BFF-KW02-001-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: `Codex2`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: `2026-04-21`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify L1 policy, canonical
> contracts, primary runtime behavior, registry truth, or governance logic. It
> packages the current KW-02 route truth, the remaining frontend handoff gaps,
> and the readiness surfaces that still understate KW-02 as pending BFF work.

---

## 1. Executive Summary

`EXEC-BFF-KW02-001` is no longer a missing-route slice.

What is already true in the repo:

- `POST /api/v1/knowledge/notes` is live in
  `services/control-plane/bff/main.py:7108-7184`.
- `GET /api/v1/knowledge/notes` is live in
  `services/control-plane/bff/main.py:7188-7268`.
- `GET /api/v1/knowledge/notes/{note_id}` is live in
  `services/control-plane/bff/main.py:7273-7319`.
- KW-02 helper logic already enforces server-assigned `owner_ref`, attachment
  taxonomy validation, referential integrity, evidence-link resolution, memory
  anchor validation, markdown-stripped excerpts, and dataset-aware degradation
  semantics in `services/control-plane/bff/main.py:3750-4018`.
- `ReadSurfaceStore` already supports note list/detail/create behavior and
  distinguishes `service_store`, `local_snapshot`, and `missing` dataset sources
  in `services/control-plane/bff/read_store.py:3744-3754` and `:4427-4484`.
- `services/control-plane/bff/test_kw02_research_notes_contract.py` proves
  degraded fallback reads, service-backed create/list/detail round-trip,
  empty-service-store availability, empty-filter availability, and create-path
  validation.
- The parent task brief at `.orchestrator/task-briefs/exec_bff_kw02_001.md`
  already records KW-02 as `review_approved`, awaiting owner finalization to
  `done`.

What is still missing or drifting:

- no module-specific frontend handoff folder exists at
  `docs/pantheon-handoffs/KW-02-research-notes/`
- no KW-02 coordination bundle exists under `.coordination/responses/` or
  `.coordination/requests/`
- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md`,
  `MODULE_READINESS_RATIFICATION_2026-04-20.md`, and the live
  `/api/v1/workbench/knowledge` overview payload still describe KW-02 as
  contract-ready / pending BFF

The parent owner should therefore treat KW-02 as route-live but not yet
frontend-packetized. The next real work is packetization and narrative sync,
not another BFF implementation pass.

## 2. Current Repo Truth Snapshot

| Area | Current truth | Notes for parent owner |
|---|---|---|
| Parent implementation lane | `.orchestrator/task-briefs/exec_bff_kw02_001.md` | parent task is `review_approved`; this sidecar does not reopen it |
| Canonical KW-02 contract | `docs/bff/KW-02-research-notes.md` | attachment taxonomy, `owner_ref`, referential integrity, and degradation rules are published |
| Example payload | `docs/examples/KW-02-research-notes.json` | list/detail/create examples parse cleanly |
| Live create route | `services/control-plane/bff/main.py:7108-7184` | rejects client-supplied `owner_ref`, validates attachment target, and writes note records |
| Live list route | `services/control-plane/bff/main.py:7188-7268` | supports `owner_ref`, `attachment_type`, `attachment_ref`, `tags`, and keyset pagination |
| Live detail route | `services/control-plane/bff/main.py:7273-7319` | returns attachment route, evidence link state, and linked memory anchors |
| Surface/degradation helpers | `services/control-plane/bff/main.py:3750-4018` | `local_snapshot` reads degrade, `missing` becomes unavailable, and empty service-backed datasets stay available |
| Store semantics | `services/control-plane/bff/read_store.py:3744-3754`, `:4427-4484` | dataset source detection plus service/local create/list/detail behavior are already wired |
| Contract proof | `services/control-plane/bff/test_kw02_research_notes_contract.py` | route family and edge cases are executable |
| Adjacent proof | `services/control-plane/bff/test_kw01_institutional_memory_contract.py`, `services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py` | KW-01 anchor behavior passes; PKT overview still asserts stale KW-02 readiness |
| Frontend handoff bundle | missing | no `docs/pantheon-handoffs/KW-02-research-notes/` folder exists |
| Coordination bundle | missing | no `KW-02-research-notes-contract-ready`, `lovable-ui-task`, `bff-gap`, or `ui-done` files exist |
| Family/readiness narrative | stale | `KW-006` packet family and readiness ratification still classify KW-02 as pending BFF |
| Runtime overview narrative | stale | `/api/v1/workbench/knowledge` still emits `KW-02.status = contract_ready` and “implement KW-02 routes” guidance in `services/control-plane/bff/main.py:4378-4477` |

## 3. Verification Replayed For This Sidecar

On `2026-04-21`, this support slice re-ran:

- `pytest -q services/control-plane/bff/test_kw02_research_notes_contract.py services/control-plane/bff/test_kw01_institutional_memory_contract.py services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py`
- result: `10 passed`
- `python3 -m json.tool docs/examples/KW-02-research-notes.json`
- result: parses cleanly

The parent approval record at
`docs/reviews/2026-04-21-exec-bff-kw02-001-codex-approval.md` separately
records the targeted empty-service-store and empty-filter regression checks.

## 4. BFF Query-Gap Classification

| Item | State | Why |
|---|---|---|
| KW-02 route family | closed | create, list, and detail routes are live in `main.py` |
| Server-assigned `owner_ref` | closed | create route rejects client-supplied `owner_ref` and resolves owner from auth context |
| Attachment taxonomy and identity validation | closed | attachment types, `attachment_ref` format, and `free_standing` null semantics are enforced by helper validators |
| Referential integrity | closed | missing attachment target returns `422`; malformed or unknown memory anchors return `400` |
| Evidence and memory resolution | closed | detail route resolves evidence states and memory anchor lifecycle/route links server-side |
| Empty service-store availability semantics | closed | list route now derives availability from `dataset_source(\"research_notes\")` instead of filtered row count |
| Active Pantheon-side BFF gap | none open | no missing KW-02 route remains in the current repo truth |
| Module-specific frontend handoff packet | open | no `KW-02-research-notes` handoff folder or change spec exists |
| Coordination bundle | open | no `contract-ready`, `lovable-ui-task`, `bff-gap`, or `ui-done` artifacts exist |
| Knowledge Workbench readiness narrative | open | packet family and readiness ratification still say pending BFF |
| Knowledge Workbench overview payload | open | the live overview route, its example payload, and its contract test still tell downstream consumers to implement KW-02 routes |

Bounded conclusion:

- KW-02 no longer has an open BFF query gap
- KW-02 is still not frontend-ready because the handoff and coordination bundle
  have not been published
- the current remaining risk is stale readiness truth across packet and overview
  surfaces, not route absence

## 5. Truthful Operator and Frontend Journey

### 5.1 Create a research note

```text
Operator opens the note composer
    |
    v
Selects one backend-owned attachment type:
  research_ticket
  persona
  strategy_spec
  free_standing
    |
    v
POST /api/v1/knowledge/notes
    |
    +-- 201
    |     returns note_id, created_at, and route_href
    |
    +-- 400 INVALID_PARAMS
    |     body missing, attachment mismatch, or bad memory anchor
    |
    +-- 422 PRECONDITION_NOT_MET
          attachment target does not resolve
```

Frontend rules already settled by the live route:

- never send `owner_ref`; the server assigns it
- `free_standing` requires `attachment_ref: null`
- unknown evidence refs may be accepted on write, but later render as
  `resolution_state: unresolved`
- `linked_memory_anchors` must already be real `mem-{UUID}` entries

### 5.2 Browse and filter notes

```text
Operator opens Research Notes
    |
    v
GET /api/v1/knowledge/notes?owner_ref=...&attachment_type=...&attachment_ref=...&tags=...
    |
    +-- 200
    |     returns backend-shaped note rows, pagination, and
    |     meta.surfaces.research_note_list
    |
    +-- 400 INVALID_PARAMS
          attachment_ref supplied without attachment_type, or attachment filter invalid
```

Frontend rules already settled by the live route:

- `owner_ref` filters by backend-owned `owner_id`
- `attachment_ref` must not be sent without `attachment_type`
- `excerpt` is already plain text; do not render it as markdown
- use `route_href` as the canonical note-detail target
- when `meta.surfaces.research_note_list` is `degraded` or `unavailable`, show
  the canonical degradation banner and do not treat empty `notes[]` as
  authoritative
- a service-backed empty store or an empty post-filter result can still be
  `ok`; empty does not mean unavailable

### 5.3 Inspect one note

```text
Operator selects a note row
    |
    v
GET /api/v1/knowledge/notes/{note_id}
    |
    +-- 200
    |     returns markdown body, owner_ref, attachment route,
    |     linked_evidence_refs[], linked_memory_anchors[],
    |     and per-panel surface state
    |
    +-- 404 OBJECT_NOT_FOUND
          note no longer exists
```

Frontend rules already settled by the live route:

- render `attachment.route_href` exactly as provided; do not construct it from
  raw ids
- `linked_evidence_refs[].resolution_state` is authoritative:
  `resolved`, `unresolved`, or `unavailable`
- `linked_memory_anchors[].lifecycle_status` is authoritative; do not infer it
  from other fields
- when only `evidence_links` or `memory_anchors` are degraded, keep the note
  detail visible and show a partial-data indicator inside the affected panel

## 6. Residual Drift For Parent-Lane Absorption

These findings do not justify reopening the KW-02 route implementation.

### DRIFT-KW02-001 — Packet family and readiness docs still say pending BFF

Evidence:

- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:8`
  still says `KW-02`, `KW-03`, and `KW-04` are contract-ready with pending BFF
- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:45`
  still marks `KW-02` as `contract-ready; pending BFF`
- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:96-107`
  still describes all three KW-02 routes as contract-published with
  implementation pending
- `docs/pantheon-handoffs/KW-006-knowledge-workbench/PACKET_FAMILY.md:208-210`
  still lists the routes as contract published / BFF implementation pending in
  the backend gap matrix
- `MODULE_READINESS_RATIFICATION_2026-04-20.md:25` still says
  `Frontend production handoff may proceed? not yet; pending BFF`

Impact:

- a reader following only packet-family or readiness docs can conclude KW-02 is
  still blocked on route implementation
- downstream work may be misrouted back into backend execution instead of
  packetization and narrative sync

Disposition:

- narrative drift only
- parent owner should decide whether to absorb this into the KW-02 closeout or
  a broader Knowledge Workbench readiness refresh

### DRIFT-KW02-002 — The live Knowledge Workbench overview surface still tells consumers to implement KW-02 routes

Evidence:

- `docs/bff/PKT-knowledge-workbench.md:36-40` still says `KW-02` to `KW-05`
  are not yet implemented
- `services/control-plane/bff/main.py:4378-4477` still hardcodes:
  - `KW-02.status = contract_ready`
  - a summary saying remaining work is BFF implementation
  - `next_gate = "Implement the published KW-02 routes ..."`
  - top-level summary/note strings saying `KW-02` to `KW-04` are pending BFF
- `docs/examples/PKT-knowledge-workbench.json` mirrors the same stale story
- `services/control-plane/bff/test_pkt016_knowledge_workbench_contract.py`
  still asserts `payload["modules"][1]["status"] == "contract_ready"`

Impact:

- the runtime overview surface itself understates current repo truth
- frontend or packet-family consumers can keep seeing a “routes still missing”
  message after the KW-02 implementation is already review-approved

Disposition:

- real follow-up gap
- do not reopen KW-02 note-route work; treat this as a separate overview-truth
  refresh owned by the relevant mainline lane

### GAP-KW02-003 — Frontend handoff and coordination packetization has not started

Evidence:

- no `docs/pantheon-handoffs/KW-02-research-notes/` directory exists
- no `.coordination/responses/KW-02-research-notes-contract-ready.yaml` exists
- no `.coordination/responses/KW-02-research-notes-lovable-ui-task.yaml` exists
- no `.coordination/requests/KW-02-research-notes-bff-gap.example.yaml` exists
- no `.coordination/requests/KW-02-research-notes-ui-done.example.yaml` exists

Impact:

- the backend route family is live, but there is still no truthful production
  handoff packet for frontend implementation
- parent-closeout readers do not yet have a canonical module-specific place to
  anchor UX, CTA, degradation, and consume rules

Disposition:

- real follow-up gap
- belongs to parent-lane absorption or a separate frontend activation slice

## 7. Parent Absorption Checklist

The main lane can absorb this sidecar without reopening backend route work.

1. Keep `services/control-plane/bff/main.py`,
   `services/control-plane/bff/read_store.py`, and
   `services/control-plane/bff/test_kw02_research_notes_contract.py` as the
   route-truth anchor.
2. Let the parent owner finalize `EXEC-BFF-KW02-001` from `review_approved` to
   `done` once this support packet is recorded as sufficient context.
3. Reclassify KW-02 from `pending-bff` to route-live / implementation-complete
   in downstream readiness narratives without overclaiming frontend readiness.
4. Decide whether the stale `/api/v1/workbench/knowledge` overview payload,
   example, and contract test should be refreshed in the parent closeout or in a
   separate Knowledge Workbench truth-sync slice.
5. If frontend activation is desired, publish the missing KW-02 module handoff
   bundle and coordination artifacts as the next mainline step.
6. Do not treat the missing frontend handoff bundle as proof that KW-02 routes
   are absent.

## 8. Reviewer Focus

For `Claude` reviewing this sidecar:

1. Confirm the packet stays support-only and does not mutate canonical truth or
   the primary runtime.
2. Confirm KW-02 is accurately classified as `no open BFF query gap`.
3. Confirm the stale Knowledge Workbench overview surface is documented as
   readiness drift, not as evidence that the route family is still missing.
4. Confirm the remaining work is split truthfully between:
   - missing frontend / coordination packetization
   - stale readiness and overview surfaces
5. Confirm the packet does not overclaim frontend readiness in the absence of a
   module-specific handoff bundle.

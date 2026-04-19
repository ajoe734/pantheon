# CW-01-FOUNDATION-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `CW-01-FOUNDATION-001` - Publish Consult Request identity and request-to-session contract  
**Parent Owner**: `Codex2`  
**Parent Reviewer**: `Codex`  
**Parent Status**: `review_approved`  
**Sidecar Task**: `CW-01-FOUNDATION-001-SIDECAR-BFF-HANDOFF`  
**Sidecar Owner**: `Codex`  
**Sidecar Reviewer**: `Claude`  
**Helper Kind**: `bff_handoff_packet`  
**Generated**: `2026-04-19`  
**Mutates canonical**: `no`

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime, registry, governance, or control-plane
> implementations. It packages the current CW-01 consult-request handoff truth
> into one bounded reviewer packet.

---

## 1. Scope

`CW-01-FOUNDATION-001` already completed the contract-publication slice for the
first Consultation Workbench module:

- consult-request create/list/detail/cancel contract
- request lifecycle semantics
- request-to-session handoff semantics
- frontend handoff bundle and coordination artifacts

This sidecar does not reopen that parent result. It records the remaining
BFF/frontend truth after the contract bundle landed:

1. the docs and coordination artifacts are now `contract-published`
2. the actual BFF request routes are still not implemented
3. frontend work for `/consultation/requests` must remain `pending-bff placeholder only`
4. existing consultation session surfaces are reusable support inputs, not a substitute for `CW-01`

---

## 2. Source References

| Document or file | Why it matters |
|---|---|
| `ai-status.json` | live source for the sidecar assignment and parent task state |
| `.orchestrator/task-briefs/cw_01_foundation_001.md` | parent task brief and `review_approved` closeout posture |
| `.orchestrator/task-briefs/cw_01_foundation_001_sidecar_bff_handoff.md` | sidecar scope and artifact path |
| `docs/bff/CW-01-consult-request.md` | published BFF contract for create/list/detail/cancel plus request-to-session semantics |
| `docs/screens/CW-01-consult-request.md` | frontend screen rules and placeholder gate |
| `docs/examples/CW-01-consult-request.json` | published field-shape target |
| `docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md` | frontend integration and handoff bundle details |
| `docs/lovable/PANTHEON_FRONTEND_SA.md` | front-lane readiness table that marks `CW-01` as `contract-published` and `pending-bff placeholder only` |
| `.coordination/responses/CW-01-consult-request-contract-ready.yaml` | durable handoff record that explicitly sets `bff_route_live: false` |
| `.coordination/responses/CW-01-consult-request-lovable-ui-task.yaml` | front-lane task packet and BFF-gap escalation path |
| `services/control-plane/bff/main.py` | proof of live consultation overview and consultation-session routes; also proof that `consult/requests` routes are absent |
| `services/control-plane/bff/read_store.py` | proof that consultation read models are session-backed and do not yet expose a `ConsultRequest` projection |
| `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` | canonical boundary for existing consultation session reads that `CW-01` must not replace |

Planning note:

- `docs/02-architecture/consensus/sessions/phase7-2026-04-18-ep4-ep5-execution-proof/planning-session.json`
  is listed by the task brief as a relevant canonical source, but no explicit
  `CW-01` task object was discoverable via targeted `jq` lookup. This packet
  therefore uses it as planning-origin background only and relies on the parent
  brief plus published CW-01 artifacts for task-specific truth.

---

## 3. Current Truth Snapshot

### 3.1 Published contract and handoff bundle exist

The parent task already published the full contract-ready bundle:

| Artifact | Current state |
|---|---|
| `docs/bff/CW-01-consult-request.md` | published |
| `docs/screens/CW-01-consult-request.md` | published |
| `docs/examples/CW-01-consult-request.json` | published |
| `docs/pantheon-handoffs/CW-01-consult-request/FRONTEND_CHANGE_SPEC.md` | published |
| `.coordination/responses/CW-01-consult-request-contract-ready.yaml` | published |
| `.coordination/responses/CW-01-consult-request-lovable-ui-task.yaml` | published |

Coordination truth is explicit:

- `contract-ready.yaml` says `bff_route_live: false`
- `lovable-ui-task.yaml` says `do not start production UI until Pantheon confirms the CW-01 request routes are live`
- `PANTHEON_FRONTEND_SA.md` marks both `/consultation/requests` and
  `/consultation/requests/:request_id` as `contract-published` and
  `pending-bff placeholder only`

### 3.2 Live BFF surfaces available today

The current runtime does expose Consultation Workbench support surfaces:

| Surface | Route | Current role |
|---|---|---|
| Consultation overview | `GET /api/v1/workbench/consultation` | truthful workbench landing page only |
| Persona consultation list | `GET /api/v1/personas/{persona_id}/consultations` | existing persona-scoped session list |
| Consultation detail | `GET /api/v1/consultations/{session_id}` | existing session detail |
| Consultation participants | `GET /api/v1/consultations/{session_id}/participants` | existing participant roster |
| Consultation outcome | `GET /api/v1/consultations/{session_id}/outcome` | existing session outcome |
| Consultation evidence | `GET /api/v1/consultations/{session_id}/evidence` | existing evidence refs |

These are useful upstream inputs, but they are not the `CW-01` request identity
surface.

### 3.3 Missing BFF implementation is still real

`services/control-plane/bff/main.py` currently contains no live route decorator
for any of the published `CW-01` request endpoints:

- `POST /api/v1/consult/requests`
- `GET /api/v1/consult/requests`
- `GET /api/v1/consult/requests/{request_id}`
- `POST /api/v1/consult/requests/{request_id}/cancel`

The only occurrence of those route strings in `main.py` is inside
`_build_consultation_workbench_overview()`, where they are listed as missing
contracts for module `CW-01`.

`read_store.py`, `models.py`, and the targeted BFF tests likewise show no
repo-visible `ConsultRequest` projection or `request_to_session_status`
implementation. The targeted code search only surfaced one unrelated
`linked_session_id` in the committee projection, not a request-level read model.

### 3.4 Important nuance: docs moved forward, overview payload wording did not

There is one bounded discrepancy worth preserving for reviewer awareness:

- the newly published CW-01 docs and coordination artifacts say
  `contract-published / pending-bff`
- the live `GET /api/v1/workbench/consultation` builder still describes `CW-01`
  as `not_ready` with `next_gate: "Publish request identity, lifecycle, and request-to-session handoff truth."`

This sidecar does not resolve that discrepancy. It only records it so the
parent owner can decide whether the overview payload needs a follow-up sync
after the contract-publication slice.

---

## 4. BFF Gap Matrix

| Surface | Published contract state | Runtime state now | Honest frontend classification |
|---|---|---|---|
| Create consult request | contract published | no live POST route | pending-bff placeholder only |
| Request list | contract published | no live GET route | pending-bff placeholder only |
| Request detail | contract published | no live GET route | pending-bff placeholder only |
| Cancel request | contract published | no live POST route | pending-bff placeholder only |
| Request-to-session rail | field shape published | no request read model yet | blocked behind request detail route |

### 4.1 What `CW-01` still needs from Pantheon

For the frontend to leave placeholder mode, Pantheon still needs all of the
following to be true at runtime:

1. `POST /api/v1/consult/requests` accepts the published create shape
2. `GET /api/v1/consult/requests` returns the published list fields, filters,
   pagination, and `meta.surfaces.consult_request_list`
3. `GET /api/v1/consult/requests/{request_id}` returns the published detail
   shape including `linked_session_id`, `request_to_session_status`, and
   `session_handoff`
4. `POST /api/v1/consult/requests/{request_id}/cancel` returns the published
   cancel response and authority flip
5. the backing read model exists for `ConsultRequest` identity and
   request-to-session lifecycle truth

### 4.2 What existing consultation surfaces can and cannot do

| Existing surface | Safe reuse | Not safe to infer |
|---|---|---|
| Persona consultation list | session evidence after a request has linked to a real session | request creation state or request identity |
| Consultation detail/outcome/evidence | session-backed drilldown after `linked_session_id` exists | request lifecycle or cancel authority |
| Consultation overview | module order and current support refs | request list, request detail, or composer data |

The published BFF contract is explicit on this point: the frontend must not
reuse persona-scoped consultation endpoints as a substitute for the new
request-level identity surface.

---

## 5. Operator Journey and Frontend Handoff

### 5.1 What the frontend can safely do now

| Route | Current frontend posture | Why |
|---|---|---|
| `/consultation` | safe to implement as overview-only | backed by `GET /api/v1/workbench/consultation` |
| `/consultation/requests` | blocked placeholder only | request list/composer routes are not live |
| `/consultation/requests/:request_id` | blocked placeholder only | request detail route is not live |

### 5.2 Current operator journey implied by repo truth

```text
1. Operator enters Consultation Workbench overview:
   GET /api/v1/workbench/consultation
2. Operator may inspect existing persona-scoped consultation sessions through
   the already-live consultation session surfaces.
3. Operator cannot yet create, browse, inspect, or cancel a canonical
   ConsultRequest object because the CW-01 request routes are not live.
4. Frontend must therefore keep request routes in placeholder mode and escalate
   via the published BFF-gap path if implementation is attempted early.
```

### 5.3 Future journey once CW-01 routes land

```text
1. Open /consultation/requests
2. Submit POST /api/v1/consult/requests from the request composer
3. Read GET /api/v1/consult/requests for the request queue
4. Open /consultation/requests/{request_id}
5. Render request_to_session_status + session_handoff exactly as returned
6. Navigate to /api/v1/consultations/{session_id} only when linked_session_id
   and session_route_href are present
7. Show cancel CTA only when allowedActions.canCancel is true
```

### 5.4 Frontend rules that are safe right now

1. Do not start production UI for `/consultation/requests*` until Pantheon
   confirms the routes are live.
2. Do not invent request rows, request lifecycle, or request-to-session
   progression from timers or polling heuristics.
3. Do not infer cancel authority from `status`; use
   `allowedActions.canCancel` only when the BFF provides it.
4. Do not use persona consultation session endpoints as a surrogate request
   store.
5. If any front lane tries to activate early, emit
   `.coordination/requests/CW-01-consult-request-bff-gap.yaml` instead of
   mocking.

---

## 6. Handoff Materials Inventory

| Material type | Path | Current use |
|---|---|---|
| Contract-ready coordination record | `.coordination/responses/CW-01-consult-request-contract-ready.yaml` | source of `contract-published / pending-bff` truth |
| Front-lane task packet | `.coordination/responses/CW-01-consult-request-lovable-ui-task.yaml` | implementation handoff for the front repo once routes are live |
| BFF gap handoff path | `.coordination/requests/CW-01-consult-request-bff-gap.yaml` | required escalation path if frontend hits missing fields or missing routes |
| BFF gap template | `.coordination/requests/CW-01-consult-request-bff-gap.example.yaml` | front-lane template |
| UI done handoff path | `.coordination/requests/CW-01-consult-request-ui-done.yaml` | completion handoff after a future real UI implementation |
| UI done template | `.coordination/requests/CW-01-consult-request-ui-done.example.yaml` | front-lane template |

---

## 7. Reviewer Checklist

| Check | Expected result |
|---|---|
| Support artifact only | PASS if only this sidecar file was added |
| Canonical truth untouched | PASS if no L1 docs, runtime files, or mainline contracts changed |
| Parent result preserved | PASS if packet treats `CW-01-FOUNDATION-001` as contract-publication work already approved |
| Missing BFF routes are code-backed | PASS if packet correctly states that `consult/requests` routes are absent from `main.py` |
| Frontend guidance stays bounded | PASS if `/consultation/requests*` remains placeholder-only until routes are live |
| Existing consultation surfaces are not overstated | PASS if packet distinguishes session surfaces from request identity |
| Overview-payload discrepancy is recorded, not “fixed” here | PASS if packet notes the mismatch without mutating runtime |

---

## 8. Reviewer Handoff Notes

**Reviewer**: `Claude`

Primary reviewer question:

- Is this packet accurate as a support-only map of the current
  `contract-published / pending-bff` state for `CW-01`?

If yes, the parent owner can use it as a bounded handoff summary without
reopening the already-approved parent task.

If approved, use:

```bash
AI_NAME=Claude python3 scripts/ai_status.py approve CW-01-FOUNDATION-001-SIDECAR-BFF-HANDOFF "Sidecar handoff packet accurately captures the CW-01 contract-published / pending-bff state, distinguishes existing consultation session surfaces from the missing consult-request routes, and stays within support-only scope."
```

If changes are required, use:

```bash
AI_NAME=Claude python3 scripts/ai_status.py reopen CW-01-FOUNDATION-001-SIDECAR-BFF-HANDOFF "Describe the specific handoff-packet corrections needed."
```

# PKT-003 Post-Incident and Evolution Packet Family

## Overview

PKT-003 packetizes the Post-Incident Review Console (Operator Console) and the Evolution Workbench screen family from the APP-002-W3-POSTINCIDENT-EVOLUTION sidecar. This document is the canonical packet requirements record for these screens.

Three screens are packet-ready today: **Post-Incident Review Console**, **Evolution Center**, and **Lineage View**. Two screens — **Inspiration Graph** and **Mutation Review** — are blocked on BFF surface work and are recorded here with explicit gap requirements.

---

## Screen Inventory

### Operator Console — Post-Incident Review Console

| Attribute | Value |
|---|---|
| Workbench | Operator Console |
| Screen | Post-Incident Review Console |
| Screen ID | `screen-operator-post-incident-review` |
| Feature ID | `PKT-003-post-incident-review` |
| Packet status | **ready** |
| BFF backing | `GET /api/v1/incidents?status=resolved` (list), `GET /api/v1/operator/post-incident-review/{incident_id}` (composed view), `GET /api/v1/postmortems` (postmortem index) |
| Lovable readiness | Ready — APP-002 W3 sidecar defines the composed view and degraded-panel gating rules |
| Screen spec | `docs/screens/PKT-003-post-incident-review-console.md` |
| BFF contract | `docs/bff/PKT-003-post-incident-review-console.md` |
| Example payload | `docs/examples/PKT-003-post-incident-review-console.json` |
| Contract-ready | `.coordination/responses/PKT-003-post-incident-review-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-003-post-incident-review-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-003-post-incident-review-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-003-post-incident-review-ui-done.example.yaml` |

### Evolution Workbench — Evolution Center

| Attribute | Value |
|---|---|
| Workbench | Evolution Workbench |
| Screen | Evolution Center |
| Screen ID | `screen-evolution-center` |
| Feature ID | `PKT-003-evolution-center` |
| Packet status | **ready** |
| BFF backing | `GET /api/v1/evolution-decisions` (EV-01), `GET /api/v1/evolution-decisions/{decision_id}` (EV-02), `GET /api/v1/freeze-orders` (EV-03), `GET /api/v1/rollbacks` (EV-04) |
| Lovable readiness | Ready — all four EV read surfaces are implemented; execution boundary gap is non-blocking for read-only views |
| Screen spec | `docs/screens/PKT-003-evolution-center.md` |
| BFF contract | `docs/bff/PKT-003-evolution-center.md` |
| Example payload | `docs/examples/PKT-003-evolution-center.json` |
| Contract-ready | `.coordination/responses/PKT-003-evolution-center-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-003-evolution-center-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-003-evolution-center-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-003-evolution-center-ui-done.example.yaml` |

### Evolution Workbench — Lineage View

| Attribute | Value |
|---|---|
| Workbench | Evolution Workbench |
| Screen | Lineage View |
| Screen ID | `screen-evolution-lineage` |
| Feature ID | `PKT-003-lineage-view` |
| Packet status | **ready** |
| BFF backing | `GET /api/v1/lineage` (LN-01), `GET /api/v1/lineage/edges/{edge_id}` (LN-02), `GET /api/v1/lineage/graph` (LN-03) |
| Lovable readiness | Ready with caveats — `LN-03 root_type` is a no-op in v1; graph renders by `root_id` only |
| Screen spec | `docs/screens/PKT-003-lineage-view.md` |
| BFF contract | `docs/bff/PKT-003-lineage-view.md` |
| Example payload | `docs/examples/PKT-003-lineage-view.json` |
| Contract-ready | `.coordination/responses/PKT-003-lineage-view-contract-ready.yaml` |
| Lovable UI task | `.coordination/responses/PKT-003-lineage-view-lovable-ui-task.yaml` |
| BFF-gap template | `.coordination/requests/PKT-003-lineage-view-bff-gap.example.yaml` |
| UI-done template | `.coordination/requests/PKT-003-lineage-view-ui-done.example.yaml` |

### Evolution Workbench — Inspiration Graph

| Attribute | Value |
|---|---|
| Workbench | Evolution Workbench |
| Screen | Inspiration Graph |
| Screen ID | `screen-evolution-inspiration-graph` |
| Feature ID | `PKT-003-inspiration-graph` |
| Packet status | **blocked** |
| BFF backing | Missing — no inspiration surface exists in current BFF |
| Lovable readiness | Not ready |
| Screen spec | Not yet created |
| Example payload | Not yet created |

**BFF gap:** No inspiration graph surface exists. Required before this screen can be packetized:
- `GET /api/v1/lineage/inspiration/{artifact_id}` returning related artifacts, creative lineage edges, and strategy tags that shaped the current artifact version.
- The response must be BFF-composed; the UI must not construct an inspiration graph from raw lineage edges client-side.
- Field shape: `artifact_id`, `inspiration_edges[]` with `source_artifact_id`, `relationship_type`, and `influence_weight`; `meta.snapshot_at`; `meta.surfaces.inspiration`.

### Evolution Workbench — Mutation Review

| Attribute | Value |
|---|---|
| Workbench | Evolution Workbench |
| Screen | Mutation Review |
| Screen ID | `screen-evolution-mutation-review` |
| Feature ID | `PKT-003-mutation-review` |
| Packet status | **blocked** |
| BFF backing | Missing — evolution execution boundary (`EVO-004`) is not yet settled |
| Lovable readiness | Not ready |
| Screen spec | Not yet created |
| Example payload | Not yet created |

**BFF gap:** No mutation review surface or execute boundary exists. Required before this screen can be packetized:
- `GET /api/v1/operator/mutation-review/{decision_id}` returning a composed mutation review with `evolution_decision`, `proposed_changes`, `risk_assessment`, `required_approvals`, `allowedActions.canApproveMutation`, `allowedActions.canRejectMutation`, and `meta.surfaces`.
- `POST /api/v1/operator/commands` extended to accept `ApproveMutation` and `RejectMutation` commands with `target.type: EvolutionDecision`.
- The execute boundary decision (`EVO-004`) must be recorded as an L1 policy update before this packet is opened. EVO-004 has four unresolved action paths that must each be explicitly settled before any mutation surface is packetized: **freeze** (who may issue a freeze order and under what conditions), **rollback** (what constitutes a safe rollback target and who can authorize it), **retrain** (whether retrain is an operator-triggered action or an autonomous evolution step), and **redeploy** (how redeployment after a retrain or rollback is gated and which approval chain applies).

---

## Example Payload Gap Summary

| Screen | Example payload status | Gap |
|---|---|---|
| Post-Incident Review Console | Done | None — `docs/examples/PKT-003-post-incident-review-console.json` |
| Evolution Center | Done | None — `docs/examples/PKT-003-evolution-center.json` |
| Lineage View | Done | None — `docs/examples/PKT-003-lineage-view.json` |
| Inspiration Graph | Missing | Needs inspiration graph BFF route first |
| Mutation Review | Missing | Needs mutation execute boundary and BFF route first |

---

## Screen-Spec Gap Summary

| Screen | Screen spec status | Gap |
|---|---|---|
| Post-Incident Review Console | Done | None — `docs/screens/PKT-003-post-incident-review-console.md` |
| Evolution Center | Done | None — `docs/screens/PKT-003-evolution-center.md` |
| Lineage View | Done | None — `docs/screens/PKT-003-lineage-view.md` |
| Inspiration Graph | Missing | Blocked on BFF inspiration surface |
| Mutation Review | Missing | Blocked on EVO-004 execution boundary settlement |

---

## Lovable Readiness Matrix

| Screen | Lovable readiness | Blocker |
|---|---|---|
| Post-Incident Review Console | Ready | None |
| Evolution Center | Ready | None |
| Lineage View | Ready (with root_type caveat) | `LN-03 root_type` is a no-op in v1; document as known limitation |
| Inspiration Graph | Not ready | Missing inspiration graph BFF route |
| Mutation Review | Not ready | EVO-004 execute boundary not settled; missing mutation review BFF route |

---

## Known Gaps Inherited From APP-002 W3

The following read-surface limitations are inherited from the W3 sidecar. They are non-blocking for the ready screens but must be documented in handoff notes:

| Gap | Surface | Impact | Status |
|---|---|---|---|
| `time_range` filter ignored | TL-01, TL-02, TL-03, EV-04 | Time-scoped telemetry and rollback views use full store | Non-blocking in v1 |
| `aggregate_by` ignored | TL-02 | Summary aggregation is fixed in v1 store | Non-blocking in v1 |
| `LN-03 root_type` is a no-op | LN-03 | Graph filtered by `root_id` only; type-based filtering requires registry metadata | Non-blocking; document in Lineage View spec |
| `viewer` role rejected | All W3 surfaces | Only `operator`, `approver`, `admin`, `reviewer` tokens are accepted | Must be noted in permission requirements |

---

## Acceptance Verification

| Acceptance criterion | Status |
|---|---|
| Post-incident review, evolution decisions, and lineage/telemetry surfaces are promoted into canonical screen packet families | Done — Post-Incident Review Console, Evolution Center, and Lineage View are packet-ready |
| Blocked screens have explicit BFF gap requirements so WB-008 can reference them | Done — Inspiration Graph and Mutation Review gaps are explicit above |
| Existing W3 sidecar limitations are carried forward instead of silently dropped | Done — see Known Gaps table |

---

## Wave Assignment

| Screen | Recommended wave |
|---|---|
| Post-Incident Review Console | Wave 1 |
| Evolution Center | Wave 2 |
| Lineage View | Wave 2 |
| Inspiration Graph | Wave 2 (after BFF route is ready) |
| Mutation Review | Wave 3 (after EVO-004 boundary is settled and BFF route is ready) |

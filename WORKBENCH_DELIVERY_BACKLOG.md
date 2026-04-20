# Workbench Delivery Backlog

Last updated: 2026-04-20
Status: canonical module-level backlog for remaining product and workbench delivery
Tier: L2 Planning & Execution
Scope: remaining workbench modules, truthful delivery gates, and the order required to close productization gaps
Conflict rule: this file is the canonical remaining workbench backlog, but it does not override L1 architecture or packet-level contracts

## 1. Working Rule

This file tracks the remaining productization backlog after the Wave 1 operator and persona baselines.

A module stays on this backlog until one of these is true:

- its BFF route and packet contract are live and the current frontend loop is `loop-complete`
- it is explicitly deferred by a newer canonical planning decision

Closure-record sync by itself does not keep a module on the remaining backlog.
If a surface is already contract-live, frontend-reviewed, and only needs canonical
closeout bookkeeping, track that work in `ai-status.json` closeout tasks instead
of keeping the module listed here as unfinished product backlog.

Use `DELIVERY_CLOSURE_AND_LOOP_STATES.md` for status semantics.

## 2. Already Landed Baselines

These surfaces are not part of the remaining backlog:

- `F-042` Promotion Review
- `PKT-001` Governance Review Queue
- `PKT-002` Incident Home, Incident Detail, Incident Action Drawer
- `PKT-003` Post-Incident Review, Evolution Center, Lineage View baseline
- `PKT-004` Persona drilldowns and management Wave 1
- `PKT-005` Degradation Banner and SSE substrate
- `PKT-006` Approval Queue
- `PKT-007` Deployment Diff
- `PKT-008` Rollback Review
- `PKT-009` Governance Audit Rail
- `PKT-010` Runtime State Board
- `PKT-011` Health Status Board
- `PKT-012` Alerts Rail
- `PKT-013` Operator Home Dashboard
- `PKT-014` Paper / Live Drift

## 3. Remaining Backlog

The reviewed Operator Console Wave 2 and governance follow-on packet loops are no
longer treated as remaining module backlog in this file. Their residual work is
canonical closeout bookkeeping only and is tracked separately in
`ai-status.json` via `APP-003-CLOSEOUT-001`.

### Evolution Workbench

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `EW-04 Inspiration Graph` | contract published — BFF route live | route spec, composed object, `meta.surfaces.inspiration`, and handoff bundle published via `EW-04-OPEN-001`; `GET /api/v1/lineage/inspiration/{artifact_id}` is now implemented with contract coverage, so UI work can consume the live BFF response instead of the placeholder gate | Activate Lovable UI task against the live EW-04 route |
| `EW-05 Mutation Review` | contract-ready — BFF route and command vocabulary live | route spec, composed `MutationReviewProjection` object, `ApproveMutation` / `RejectMutation` command vocabulary, `allowedActions` authority signals (`canApproveMutation` / `canRejectMutation`), `meta.surfaces.mutation_review` staleness signal, and frontend handoff bundle are now aligned with the live BFF implementation | Lovable implements the production screen against the live handoff bundle |

### Research Workbench

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `RW-01 Research Ticket` | contract-live — create/list/detail/patch routes implemented | the route family is live, but the module still carries truth-hardening work because reads can still fall back to local snapshot data instead of fully service-owned truth | close `AUTO-HARDEN-RW01-001` and activate the Lovable UI task against the live routes |
| `RW-02 Search` | contract-live — search route and index-adapter payload implemented | BFF route, pagination, and adapter metadata are live; remaining work is UI activation and keeping adapter truth/degradation semantics aligned with the published contract | activate the Lovable UI task against the live RW-02 route |
| `RW-03 Analyze` | contract-live — analysis list/detail routes implemented | the route family is live, but the module remains backlog-open until analysis reads move off local fallback and onto service-owned truth | close `AUTO-HARDEN-RW03-001` and then activate the Lovable UI task against the live routes |
| `RW-04 Experiment Launch` | contract published — BFF implementation pending | launch, run status, history, cancel, and experiment state machine published via `RW-04-EXPERIMENT-001`; live BFF routes still pending | implement the published launch/detail/history/cancel routes against the canonical state machine |
| `RW-05 Artifact Compare` | not ready | missing artifact registry/detail/compare routes and versioning semantics | publish backend-owned diff and artifact identity rules |

### Knowledge Workbench

Overview packet note: `PKT-knowledge-workbench` now publishes a truthful overview route and handoff bundle. The module backlog below remains open until the actual browse and lifecycle contracts land.

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `KW-01 Institutional Memory` | contract-live — list/detail routes implemented | identity and browse projection are live, but the module remains backlog-open until KW-01 finishes truth-hardening from example/local snapshot behavior to service-owned truth | close `AUTO-HARDEN-KW01-001` and activate the Lovable UI task against the live routes |
| `KW-02 Research Notes` | overview packet live; module not ready | missing create/list/detail routes and attachment taxonomy | publish note ownership and attachment contract |
| `KW-03 Evidence Refs` | overview packet live; module not ready | missing evidence browse/detail routes and linked-entity projection | publish typed evidence-link contract |
| `KW-04 Insight Cards` | overview packet live; module not ready | missing insight-card list/detail projection and aggregation contract | publish card identity, filters, and linked-source drilldown |
| `KW-05 Strategy Spec` | overview packet live; module not ready | missing spec list/detail/version compare projection | lock versioned strategy-spec browse contract |

### Consultation Workbench

Overview packet note: `PKT-consultation-workbench` now publishes a truthful overview route and handoff bundle. The module backlog below remains open until the actual request, transcript, committee, and memo contracts land.

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `CW-01 Consult Request` | contract published; BFF implementation pending | bring create/list/detail/cancel routes live against the published request lifecycle and request-to-session contract | confirm the CW-01 routes return the published field shape |
| `CW-02 Debate Transcript` | overview packet live; module not ready | missing transcript route, append-only event schema, and actor-label contract | lock `TranscriptEvent` ordering and evidence-link behavior |
| `CW-03 Committee Board` | partial-live — committee list/detail routes and sponsor-decision authority are implemented | committee projections are live, but the module remains packet-gated until `CW-01` request identity and `CW-02` transcript ordering become live upstream dependencies rather than contract-only predecessors | keep CW-03 module-gated until CW-01 and CW-02 are live, then publish the full handoff bundle |
| `CW-04 Red-team Memo` | overview packet live; module not ready | missing memo list/detail/publish flow and downstream handoff contract | publish memo lifecycle and evidence rail contract |

### Trainer Workbench

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `TW-01 Teaching Dialog` | contract-live — create/list/detail/message routes implemented | the TW-01 route family is live and aligns with the published contract; remaining work is frontend activation rather than route implementation | activate the Lovable UI task against the live TW-01 routes |
| `TW-02 Parameter Controls` | not ready | missing controls read route, patch route, validation contract, and diff response shape | publish `ControlParameter` contract and patch semantics |
| `TW-03 Before/After Compare` | contract published — BFF implementation pending | preview read/refresh routes, warning hierarchy, `preview_unavailable` degraded branch, and polling semantics published via `TW-03-COMPARE-001`; BFF must implement the route family before UI work starts | BFF implements preview routes → activate Lovable UI task |
| `TW-04 Teaching Replay` | contract published — BFF implementation pending | replay list/detail routes, replay-grade `TeachingEvent` schema, BFF-resolved evidence links, commit/discard authority, and before/candidate/after artifact refs published via `TW-04-REPLAY-001`; live BFF routes still pending | BFF implements replay routes and commit/discard paths → activate Lovable UI task |

## 4. Cross-Family Order

Use this order unless a narrower canonical doc says otherwise:

1. finish canonical closeout bookkeeping tasks in `ai-status.json` when a reviewed loop still needs a record-layer closure sync
2. finish `EW-04` before `EW-05`
3. for Research, Knowledge, Consultation, and Trainer, land identity and lifecycle modules first, then read aggregates, then mutation or replay surfaces

## 5. Relationship To Other Canonical Files

- `ROADMAP.md` sequences the phases.
- `DEVELOPMENT_WORKBREAKDOWN.md` tracks canonical task IDs.
- `DELIVERY_CLOSURE_AND_LOOP_STATES.md` defines when a module is actually closed.
- packet-family files under `docs/pantheon-handoffs/*/PACKET_FAMILY.md` hold the per-surface field-level truth once a module is opened.

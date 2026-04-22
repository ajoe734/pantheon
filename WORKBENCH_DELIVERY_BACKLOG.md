# Workbench Delivery Backlog

Last updated: 2026-04-22
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
| `EW-04 Inspiration Graph` | route-live — frontend handoff active | route spec, composed object, `meta.surfaces.inspiration`, and handoff bundle published via `EW-04-OPEN-001`; `GET /api/v1/lineage/inspiration/{artifact_id}` is now implemented with contract coverage, so UI work can consume the live BFF response instead of the placeholder gate | Activate Lovable UI task against the live EW-04 route |
| `EW-05 Mutation Review` | contract-ready — BFF route and command vocabulary live | route spec, composed `MutationReviewProjection` object, `ApproveMutation` / `RejectMutation` command vocabulary, `allowedActions` authority signals (`canApproveMutation` / `canRejectMutation`), `meta.surfaces.mutation_review` staleness signal, and frontend handoff bundle are now aligned with the live BFF implementation | Lovable implements the production screen against the live handoff bundle |

### Research Workbench

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `RW-01 Research Ticket` | contract-live — create/list/detail/patch routes implemented | the route family is live, but the module still carries truth-hardening work because reads can still fall back to local snapshot data instead of fully service-owned truth | close `AUTO-HARDEN-RW01-001` and activate the Lovable UI task against the live routes |
| `RW-02 Search` | contract-live — search route and index-adapter payload implemented | BFF route, pagination, and adapter metadata are live; remaining work is UI activation and keeping adapter truth/degradation semantics aligned with the published contract | activate the Lovable UI task against the live RW-02 route |
| `RW-03 Analyze` | contract-live — analysis list/detail routes implemented | the route family is live, but the module remains backlog-open until analysis reads move off local fallback and onto service-owned truth | close `AUTO-HARDEN-RW03-001` and then activate the Lovable UI task against the live routes |
| `RW-04 Experiment Launch` | contract-live — launch/history/detail/cancel routes implemented | all four routes live and returning published field shape; frontend handoff bundle published at `docs/pantheon-handoffs/RW-04-experiment-launch/`; `allowedActions.canCancel` and async state machine live | activate the Lovable UI task against the live RW-04 route family |
| `RW-05 Artifact Compare` | contract-ready — pending BFF implementation | route and versioning semantics are ratified in `docs/bff/RW-05-artifact-compare.md`; the remaining gap is implementing artifact registry/detail/compare routes against the published contract | start BFF implementation of the ratified artifact list/detail/compare route family |

### Knowledge Workbench

Overview packet note: `PKT-knowledge-workbench` now publishes a truthful overview route and handoff bundle. The module backlog below remains open until the remaining BFF implementations and frontend activation loops close.

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `KW-01 Institutional Memory` | contract-live — list/detail routes implemented | identity and browse projection are live, but the module remains backlog-open until KW-01 finishes truth-hardening from example/local snapshot behavior to service-owned truth | close `AUTO-HARDEN-KW01-001` and activate the Lovable UI task against the live routes |
| `KW-02 Research Notes` | contract-ready — pending BFF implementation | ownership, attachment taxonomy, referential integrity, and degradation semantics are ratified; remaining gap is implementing note create/list/detail routes | start BFF implementation of the published KW-02 route family |
| `KW-03 Evidence Refs` | contract-ready — pending BFF implementation | link taxonomy, credibility metadata, resolved-link semantics, and detail projection are ratified; remaining gap is implementing evidence browse/detail routes | start BFF implementation of the published KW-03 route family |
| `KW-04 Insight Cards` | contract-ready — pending BFF implementation | aggregation/detail contract and backend-owned filter taxonomy are ratified; remaining gap is implementing insight list/detail routes | start BFF implementation of the published KW-04 route family |
| `KW-05 Strategy Spec` | contract-ready — versioning and compare semantics ratified; pending BFF implementation | version identity, ancestry, lifecycle, immutability, and compare semantics are now ratified in the canonical contract; remaining gap is implementing the browse/detail/history/compare route family | start BFF implementation of the published KW-05 route family against the ratified version model |

### Consultation Workbench

Overview packet note: `PKT-consultation-workbench` now publishes a truthful overview route and handoff bundle. The module backlog below remains open until the remaining transcript and memo route implementations, plus the current frontend follow-up loops, close.

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `CW-01 Consult Request` | contract-ready — BFF routes live; current UI return needs follow-up | Pantheon's create/list/detail/cancel route family is live and contract-verified; remaining work is front-owned publication replay plus the current CW-01 review fixes | republish the CW-01 ui-done + frontend-feedback bundle from one truthful front commit and resolve the current review findings |
| `CW-02 Debate Transcript` | contract-ready — append-only transcript contract ratified; pending BFF implementation | transcript event schema, actor identity rule, and `partial` enrichment semantics are ratified; remaining gap is implementing the ordered transcript route family | start BFF implementation of the published CW-02 route family |
| `CW-03 Committee Board` | partial-live — committee list/detail routes and sponsor-decision authority are implemented | committee projections are live and partial activation is ratified now; the remaining gap is transcript-linked production handoff, which still depends on CW-02 route-live transcript truth | open the partial committee board UI against the live routes now, and reserve transcript-dependent surfaces for the point when CW-02 is live |
| `CW-04 Red-team Memo` | contract-ready — memo and governance-handoff contract ratified; pending BFF implementation | memo lifecycle, mapping object, and governance-review initiation gate are ratified; remaining gap is implementing list/detail surfaces and review handoff behavior | start BFF implementation of the published CW-04 route family |

### Trainer Workbench

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `TW-01 Teaching Dialog` | contract-live — create/list/detail/message routes implemented | the TW-01 route family is live and aligns with the published contract; remaining work is frontend activation rather than route implementation | activate the Lovable UI task against the live TW-01 routes |
| `TW-02 Parameter Controls` | contract-ready — partial patch and diff semantics ratified; pending BFF implementation | control-state read route, patch route, rejected response shape, and canonical `diff.updated_controls[]` semantics are now ratified; remaining gap is implementing the route family | start BFF implementation of the published TW-02 route family |
| `TW-03 Before/After Compare` | route-live — frontend handoff active | preview read/refresh routes, warning hierarchy, `preview_unavailable` degraded branch, and polling semantics are already live via `TW-03-COMPARE-001`; frontend handoff bundle published at `docs/pantheon-handoffs/TW-03-before-after-compare/`; `allowedActions.canRefreshPreview` and polling semantics are backend-owned | activate Lovable UI task against the live preview routes |
| `TW-04 Teaching Replay` | route-live — frontend handoff ready | replay list/detail routes, replay-grade `TeachingEvent` schema, BFF-resolved evidence links, commit/discard authority, and before/candidate/after artifact refs live via `TW-04-REPLAY-001`; frontend handoff bundle published at `docs/pantheon-handoffs/TW-04-teaching-replay/` | activate Lovable UI task against the live TW-04 route family |

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

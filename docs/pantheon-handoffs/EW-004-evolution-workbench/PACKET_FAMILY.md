# EW-004 Evolution Workbench — Canonical Packet Family

## Header

- Packet family ID: `EW-004`
- Workbench: Evolution Workbench
- Phase origin: `BP5-WB-004`
- Lovable readiness: **all modules ready** — `EW-01`, `EW-02`, and `EW-03` are handoff-ready via `PKT-003`; `EW-04` BFF route `GET /api/v1/lineage/inspiration/{artifact_id}` is now **live** (confirmed via `EW-04-OPEN-001` rebaseline) and the Lovable UI task is active; `EW-05` contract and handoff bundle are published via `EW-05-OPEN-001`, and the live Pantheon BFF exposes the mutation-review read route plus the `ApproveMutation` / `RejectMutation` operator command extension
- Recommended wave: Wave 2 read-only baseline, then Wave 3 mutation authority
- Owner: Codex2
- Reviewer: Claude

---

## Objective

Consolidate the Evolution Workbench into one truthful family that reuses the ready `PKT-003` evidence surfaces, preserves the blocked state of the Inspiration Graph draft, and defines Mutation Review against the canonical `EvolutionDecision`, `IncidentCase`, `Postmortem`, `ApprovalDecision`, and rollback semantics already landed in policy and service-contract work.

No Evolution Workbench screen may invent graph structure, mutation authority, or rollback behavior client-side. Read surfaces stay BFF-composed, and all mutation authority remains bounded by the canonical write-owner split between governance, deployment, runtime, and research planes.

---

## Existing Pantheon Support (pre-conditions)

| Artifact | Location | What it defines |
|---|---|---|
| `PKT-003` packet family | `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/PKT-003-post-incident-evolution-packet-family.md` | Existing ready baseline for `EW-01 Post-Incident Review`, `EW-02 Evolution Center`, and `EW-03 Lineage View`; also records blocked draft expectations for `EW-04 Inspiration Graph` and `EW-05 Mutation Review` |
| `PKT-003` ready handoffs | `docs/pantheon-handoffs/PKT-003-post-incident-review/`, `docs/pantheon-handoffs/PKT-003-evolution-center/`, `docs/pantheon-handoffs/PKT-003-lineage-view/` | Contract-ready frontend handoff bundles for the three read-only baseline surfaces |
| `EW-04` contract artifacts | `docs/screens/PKT-003-inspiration-graph.md`, `docs/bff/PKT-003-inspiration-graph.md`, `docs/examples/PKT-003-inspiration-graph.json`, `docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md` | Contract published via `EW-04-OPEN-001`: route spec, composed object, example payload, and frontend handoff bundle now exist; BFF route implementation is pending |
| Evolution policy and object contract | `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `services/control-plane/governance/evolution_decision.contract.md`, `services/control-plane/governance/evolution_decision.schema.json` | Canonical `EvolutionDecision` lifecycle, normalized action families, review and approval chain, execution-result semantics, cooldown rules, and object field names |
| Approval authority contract | `services/control-plane/governance/contract.md` | Canonical `ApprovalDecision` lifecycle and the rule that evolution approvals must flow through `ApprovalDecision`, not shadow approval semantics |
| Incident backbone | `services/incident/contract.md`, `services/incident/incident_case.schema.json`, `services/incident/postmortem.schema.json` | Canonical `IncidentCase` and `Postmortem` objects, lineage links into evolution, and the incident/postmortem evidence chain used by post-incident and mutation review surfaces |
| Rollback semantics | `ROLLBACK_AND_POSITION_SEMANTICS.md` | Canonical rollback action types (`replace`, `pause_then_replace`, `liquidate_then_replace`), rollback ownership, and the rule that rollback remains an operational mitigation path owned by rollback/runtime planes |
| Evolution service realization | `BP5-SVC-012`; `services/evolution/`, `services/governance/contract.md` | The formerly draft `EvolutionDecision` read path is now materially realized as a service-backed contract anchor instead of phase-3 design intent only |
| Operational orchestration settlement | `BP5-SVC-013`; `services/runtime-manager/`, `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | Freeze, rollback follow-through, and kill-switch fast-path ownership are now explicitly settled in the runtime-manager path, so `EW-05` no longer depends on reopening the old execution-boundary debate |
| Shared degraded-state substrate | `docs/pantheon-handoffs/PKT-005-degradation-banner/`, `docs/pantheon-handoffs/PKT-005-sse-substrate/` | Canonical degradation banner inheritance and optional SSE guidance for read-only Evolution surfaces |

Historical note: older phase-3 packet text describes `EW-05 Mutation Review` as blocked on "EVO-004 execution boundary settlement." In current repo reality, that boundary is materially anchored by `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`, `services/control-plane/governance/evolution_decision.contract.md`, and the service realization work in `BP5-SVC-012` / `BP5-SVC-013`. The operator-facing mutation-review read model, explicit approve/reject command vocabulary, and backend-shaped `allowedActions` gating are now implemented in the local BFF workspace and no longer represent the remaining blocker for `EW-05`.

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Existing packet state | Lovable readiness | Wave order |
|---|---|---|---|---|---|
| `EW-01` | Post-Incident Review | resolved incident index, composed post-incident review, postmortem refs, linked evolution evidence | ready via `PKT-003` plus frontend handoff bundle | ready | Wave 1 baseline |
| `EW-02` | Evolution Center | evolution decision list/detail, freeze-order index, rollback history, read-only review context | ready via `PKT-003` plus frontend handoff bundle | ready | Wave 2 - 1st |
| `EW-03` | Lineage View | lineage list, edge detail, lineage graph, incident/evolution trace context | ready via `PKT-003` plus frontend handoff bundle; `LN-03 root_type` caveat documented | ready with caveat | Wave 2 - 2nd |
| `EW-04` | Inspiration Graph | artifact-centered creative lineage graph, strategy tags, influence-weight display | route live via `EW-04-OPEN-001` rebaseline: route spec, composed object, example payload, and frontend handoff bundle confirmed; BFF route `GET /api/v1/lineage/inspiration/{artifact_id}` is live | ready | Wave 2 - 3rd |
| `EW-05` | Mutation Review | composed mutation review with evolution decision context, incident/postmortem/rollback evidence, and approve/reject CTA | contract published via `EW-05-OPEN-001`: route spec, composed `MutationReviewProjection`, `ApproveMutation`/`RejectMutation` command vocabulary, `allowedActions` authority signals, `meta.surfaces.mutation_review`, and frontend handoff bundle now exist; the local Pantheon BFF now exposes the live route and operator command extension | ready | Wave 3 - 1st |

---

## EW-01 Post-Incident Review

### Surface scope

- Read-only resolved-incident index and review detail backed by `GET /api/v1/incidents?status=resolved`, `GET /api/v1/operator/post-incident-review/{incident_id}`, and `GET /api/v1/postmortems`
- Canonical object anchors: `IncidentCase`, `Postmortem`, and linked `EvolutionDecision` evidence; rollback evidence remains read-only context rather than a write path
- Cross-listed with `OC-08` in the Operator Console

### Existing packet state

Complete via `PKT-003`. The screen spec, BFF contract, example payload, and frontend handoff already exist. This family does not reopen the packet; it treats `EW-01` as the read-only evidence baseline that every later Evolution module builds on.

### Live-state gate

`meta.surfaces.*` degraded-panel gating and the shared degradation banner inherited from `PKT-005` are mandatory. No client-side incident or postmortem health inference is allowed.

---

## EW-02 Evolution Center

### Surface scope

- Read-only evolution decision list/detail, freeze-order index, and rollback history backed by `GET /api/v1/evolution-decisions`, `GET /api/v1/evolution-decisions/{decision_id}`, `GET /api/v1/freeze-orders`, and `GET /api/v1/rollbacks`
- Canonical object anchors: `EvolutionDecision`, `ApprovalDecision` linkage, and rollback-history evidence; runtime rollback execution remains downstream and is not initiated from this screen
- Provides the stable `decision_id` and action context that `EW-03` and `EW-05` consume

### Existing packet state

Complete via `PKT-003`. The screen is already handoff-ready as a read-only evidence surface.

### Live-state gate

The inherited EV-04 caveat still applies: `time_range` on the rollback list is a non-blocking partial filter in v1 and must stay documented in downstream handoff copy.

---

## EW-03 Lineage View

### Surface scope

- Read-only lineage list, edge detail, and graph view backed by `GET /api/v1/lineage`, `GET /api/v1/lineage/edges/{edge_id}`, and `GET /api/v1/lineage/graph`
- Canonical object anchors: lineage edges that connect artifacts, incidents, postmortems, and evolution decisions; this screen is the bridge from incident evidence into graph-based traceability
- Supplies the raw lineage primitives that Inspiration Graph extends, but may not be used by the frontend to synthesize `EW-04`

### Existing packet state

Complete via `PKT-003`. The `LN-03 root_type` no-op caveat is already documented and remains the only known readiness caveat.

### Live-state gate

Do not expose `root_type` as a working filter until the registry metadata prerequisite exists. The graph remains keyed by `root_id` in v1.

---

## EW-04 Inspiration Graph

### Surface scope

- **Artifact selector**: enter or route by `artifact_id`; this is the primary identity for the graph request
- **Inspiration Graph panel**: BFF-composed directed graph centered on the selected artifact, with `inspiration_edges[]` showing `source_artifact_id`, `relationship_type`, and `influence_weight`
- **Strategy Tags rail**: display-only tags returned by the BFF as part of the artifact's inspiration context
- **Inspiration Edge Detail drawer**: edge-level detail for a selected inspiration edge
- **Degradation handling**: the graph and drawer inherit the shared degradation banner through `meta.surfaces.inspiration`

### Canonical object anchors

- Primary object: the inspiration-graph composed view rooted at `artifact_id`
- Upstream graph dependency: lineage primitives from `LN-01` through `LN-03`
- Incident and evolution objects may appear only as BFF-composed context if the inspiration route chooses to expose them; the frontend may not traverse incident, postmortem, or evolution reads on its own to assemble this graph

### Existing packet state

`docs/screens/PKT-003-inspiration-graph.md`, `docs/bff/PKT-003-inspiration-graph.md`, and `docs/examples/PKT-003-inspiration-graph.json` provide the route contract and required field shape, confirmed live via `EW-04-OPEN-001` rebaseline. The frontend handoff bundle exists at `docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md`. The BFF route `GET /api/v1/lineage/inspiration/{artifact_id}` is confirmed live. Frontend production lane is unblocked.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/lineage/inspiration/{artifact_id}` | **contract published — pending BFF implementation** | route spec published via `EW-04-OPEN-001` in `docs/bff/PKT-003-inspiration-graph.md`; must own graph assembly and return `artifact_id`, `inspiration_edges[]`, `strategy_tags[]`, `meta.snapshot_at`, and `meta.surfaces.inspiration`; live implementation not yet confirmed |
| Inspiration graph composed object | **contract published — pending BFF implementation** | field shape published via `EW-04-OPEN-001` in `docs/bff/PKT-003-inspiration-graph.md`; no live implementation backs it yet |
| Lineage root-type registry prerequisite | **partial** | `LN-03` exists, but the creative-edge addressability prerequisite for typed inspiration traversal is not yet locked; do not treat raw lineage primitives as a substitute for the dedicated route |
| Frontend handoff bundle | **published** via `EW-04-OPEN-001` | `docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md` now exists; Lovable UI task activates once BFF confirms the live route |

### Packetization prerequisite

The `PKT-003` inspiration graph handoff is now active. The BFF inspiration route is live, its response shape is stable, and `meta.surfaces.inspiration` is wired through to `PKT-005`. The UI must never synthesize this graph from `LN-01`/`LN-03` results.

### Lovable readiness gate

`ready` — the route spec, field shape, staleness signal, and frontend handoff bundle are confirmed via `EW-04-OPEN-001` rebaseline. The BFF route `GET /api/v1/lineage/inspiration/{artifact_id}` is live. Lovable may implement the production page now; any runtime divergence should be emitted as a fresh `bff-gap`.

---

## EW-05 Mutation Review

### Surface scope

- **Decision context header**: `decision_id`, target object identity, normalized `action_type`, `risk_level`, decision state, review chain summary, and linked `approval_decision_id`
- **Proposed changes panel**: operator-readable explanation of what the approved or reviewable mutation would change, including target stage or downstream follow-through plane when applicable
- **Incident and postmortem evidence rail**: linked `IncidentCase` and `Postmortem` context for incident-driven mutations
- **Rollback follow-through panel**: read-only display of rollback semantics or refs when the mutation implies runtime mitigation; this panel cites rollback policy objects and rollback request refs, but does not mutate them
- **Required approvals and risk assessment**: explicit list of who must review or approve, plus the risk or threshold signals that justify the mutation
- **Approval and rejection CTA**: backend-shaped `allowedActions.canApproveMutation` and `allowedActions.canRejectMutation` gate the only two interactive CTAs on this screen
- **Degradation handling**: when mutation evidence is degraded or unavailable, the CTA must disappear entirely

### Canonical object anchors

| Object | Canonical source | Why it matters to `EW-05` |
|---|---|---|
| `EvolutionDecision` | `services/control-plane/governance/evolution_decision.contract.md`, `services/control-plane/governance/evolution_decision.schema.json` | primary review object; supplies `decision_id`, `action_type`, `risk_level`, `approval_decision_id`, `linked_incident_id`, `linked_postmortem_id`, `review_chain`, and `execution_result` |
| `ApprovalDecision` | `services/control-plane/governance/contract.md` | mutation review may approve or reject review authority, but may not invent a parallel approval chain |
| `IncidentCase` | `services/incident/contract.md`, `services/incident/incident_case.schema.json` | incident-backed evidence and runtime/deployment context |
| `Postmortem` | `services/incident/contract.md`, `services/incident/postmortem.schema.json` | root-cause and follow-up evidence linked into evolution |
| Rollback semantics | `ROLLBACK_AND_POSITION_SEMANTICS.md` | defines the meaning of rollback follow-through, rollback action types, and the rule that runtime mitigation is still owned by rollback/runtime planes |

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/mutation-review/{decision_id}` | **live** | primary BFF-composed mutation-review route; field shape published in `docs/bff/EW-05-mutation-review.md`; implementation verified in `services/control-plane/bff/main.py` and `services/control-plane/bff/test_ew05_mutation_review_contract.py` |
| `POST /api/v1/operator/commands` mutation-review vocabulary | **live** | `ApproveMutation` and `RejectMutation` command vocabulary published in `docs/bff/EW-05-mutation-review.md`; accepted by the live operator command path and verified in `services/control-plane/bff/test_governance_command_submission.py` |
| Mutation-review composed object | **live** | full field shape for `proposed_changes`, `risk_assessment`, `required_approvals`, incident/postmortem evidence refs, rollback-followthrough refs, and `allowedActions` is implemented and contract-tested |
| `allowedActions.canApproveMutation` and `allowedActions.canRejectMutation` | **live** | authority signal rules and evaluation preconditions are now returned by the live read route |
| `meta.surfaces.mutation_review` | **live** | staleness signal semantics and UI behavior table are now returned by the live read route, including the `unavailable` degradation branch |
| Frontend handoff bundle | **published and active** via `EW-05-OPEN-001` | `docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md`; Lovable may implement against the live route and command vocabulary |

### Packetization prerequisite

The execution-boundary semantics themselves are no longer the main blocker. They are already anchored in canonical policy and contract work:

- `freeze` remains a governance quarantine action
- `rollback` remains operational mitigation owned by rollback/runtime planes
- `retrain` follows research-plane work-item semantics
- `redeploy` is deployment follow-through and is not a new `EvolutionDecision.action_type`

The operator mutation-review projection is now materially realized as a live BFF surface. Remaining work for `EW-05` is in the frontend implementation loop, not in Pantheon's mutation-review route or operator command vocabulary.

### Lovable readiness gate

`ready` — the route spec, field shape, command vocabulary, authority signals, staleness signal, and frontend handoff bundle are aligned with the live BFF implementation. Lovable may proceed, and any later runtime divergence should be emitted as a fresh `bff-gap` instead of reopening this historical pre-live blocker.

---

## Backend Gap Matrix

Each row is scoped to one or more blocked modules. A blocked module advances to Lovable-ready only when every row attached to that module is resolved and its upstream prerequisite modules are already ready.

| Route or contract | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `GET /api/v1/lineage/inspiration/{artifact_id}` | `EW-04` | **live** | entire Inspiration Graph surface; route spec in `docs/bff/PKT-003-inspiration-graph.md`; confirmed live via `EW-04-OPEN-001` rebaseline |
| Inspiration graph composed object | `EW-04` | **live** | field shape in `docs/bff/PKT-003-inspiration-graph.md`; example in `docs/examples/PKT-003-inspiration-graph.json` |
| `meta.surfaces.inspiration` | `EW-04` | **live** | degradation banner and graph suppression rules; signal confirmed live in route contract |
| Frontend handoff bundle for Inspiration Graph | `EW-04` | **published and active** via `EW-04-OPEN-001` | `docs/pantheon-handoffs/PKT-003-inspiration-graph/FRONTEND_CHANGE_SPEC.md`; Lovable may implement against the live route |
| `GET /api/v1/operator/mutation-review/{decision_id}` | `EW-05` | live | entire Mutation Review surface; route spec in `docs/bff/EW-05-mutation-review.md` |
| Mutation-review composed object | `EW-05` | live | field shape in `docs/bff/EW-05-mutation-review.md`; example in `docs/examples/EW-05-mutation-review.json` |
| `ApproveMutation` / `RejectMutation` command vocabulary | `EW-05` | live | command vocabulary in `docs/bff/EW-05-mutation-review.md`; approve/reject CTA semantics |
| `allowedActions.canApproveMutation` / `canRejectMutation` | `EW-05` | live | authority signal rules defined in `docs/bff/EW-05-mutation-review.md` |
| `meta.surfaces.mutation_review` | `EW-05` | live | staleness signal semantics defined in `docs/bff/EW-05-mutation-review.md` |
| Frontend handoff bundle for Mutation Review | `EW-05` | **published and active** via `EW-05-OPEN-001` | `docs/pantheon-handoffs/EW-05-mutation-review/FRONTEND_CHANGE_SPEC.md` |

---

## Internal Ordering and Dependency Chain

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| Wave 1 baseline | `EW-01 Post-Incident Review` | establishes the incident and postmortem evidence baseline for the workbench; already packetized | none |
| Wave 2 - 1st | `EW-02 Evolution Center` | provides stable `decision_id`, read-only mutation context, and rollback-history evidence | inherits `EW-01` evidence inputs |
| Wave 2 - 2nd | `EW-03 Lineage View` | makes the graph-based lineage context explicit before creative inspiration or mutation review extends it | depends on `EW-02` decision identity and shared `PKT-003` evidence vocabulary |
| Wave 2 - 3rd | `EW-04 Inspiration Graph` | extends lineage into a dedicated inspiration graph; cannot begin until raw lineage is already stable and the dedicated BFF route exists | depends on `EW-03` lineage primitives and the new inspiration route |
| Wave 3 - 1st | `EW-05 Mutation Review` | must come after the full read-only review context is stable so operators are reviewing grounded evidence, not acting from a partial view | depends on `EW-02` decision identity plus the read-only context established by `EW-03` and `EW-04`; also requires the operator mutation-review route and authority signals |

---

## Promotion Criteria

A blocked Evolution Workbench module moves from **not ready** to **ready** only when all of the following are true:

1. All routes and contracts listed in that module's Backend Gaps table are implemented and have stable field shapes.
2. The module's `meta.surfaces.*` staleness signal is defined and wired through to the canonical degradation banner (`PKT-005`).
3. Any CTA authority on that module is fully backend-shaped through `allowedActions`; no client-side inference is used as the source of truth.
4. An example payload exists for the module's primary read surface and is no longer marked as blocked or draft-only.
5. A frontend handoff bundle exists for the module's screen and all upstream prerequisite modules are already handoff-ready.

No blocked Evolution Workbench module should be handed to Lovable before its own criteria and all upstream criteria are met.

---

## Cross-Cutting Rules

### No client-side graph synthesis

`EW-04 Inspiration Graph` must render only from the dedicated BFF inspiration route. The frontend must never traverse `GET /api/v1/lineage` or `GET /api/v1/lineage/graph` to reconstruct inspiration edges, influence weights, or strategy tags.

### No shadow mutation authority

`EW-05 Mutation Review` may only review or reject mutation authority in the governance sense. It must not:

- create a parallel approval object outside `ApprovalDecision`
- submit runtime rollback commands directly
- deploy or redeploy artifacts directly
- mutate `RuntimeBinding`, `DeploymentPlan`, or incident objects in place

The screen reviews mutation authority; downstream execution still belongs to the canonical owning plane.

### Incident and rollback evidence are references, not write ownership

Mutation Review may show incident refs, postmortem refs, rollback refs, and downstream execution refs, but those remain evidence or follow-through references. The screen does not become the write owner for incident, rollback, deployment, or runtime state.

### Degradation banner inheritance

Every Evolution Workbench module inherits `PKT-005` degraded-state rules. When the relevant `meta.surfaces.*` signal is degraded or unavailable, the banner is mandatory and any unsafe CTA must be hidden.

---

## Separation Rules

When authoring follow-on packet language for this family:

- Put graph layout copy, edge-detail wording, incident/postmortem evidence presentation, and approve/reject CTA copy in `Missing screen-spec work`.
- Put absent BFF routes, missing `allowedActions`, missing composed read models, or missing staleness signals in `Backend or contract dependencies`.
- Do not treat older `PKT-003` "blocked on EVO-004" wording as permission to reopen policy semantics that are already defined in current canonical docs. The remaining work is the operator-facing projection and handoff packaging.

---

## Canonical References

- Backlog source: `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`
- Existing packet baseline: `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/PKT-003-post-incident-evolution-packet-family.md`
- Existing ready handoffs: `docs/pantheon-handoffs/PKT-003-post-incident-review/`, `docs/pantheon-handoffs/PKT-003-evolution-center/`, `docs/pantheon-handoffs/PKT-003-lineage-view/`
- Inspiration graph route-live artifacts: `docs/screens/PKT-003-inspiration-graph.md`, `docs/bff/PKT-003-inspiration-graph.md`, `docs/examples/PKT-003-inspiration-graph.json`
- Evolution object anchors: `EVOLUTION_REVIEW_AND_THRESHOLDS.md`, `services/control-plane/governance/evolution_decision.contract.md`, `services/control-plane/governance/evolution_decision.schema.json`
- Incident object anchors: `services/incident/contract.md`, `services/incident/incident_case.schema.json`, `services/incident/postmortem.schema.json`
- Approval and rollback anchors: `services/control-plane/governance/contract.md`, `ROLLBACK_AND_POSITION_SEMANTICS.md`
- Dependent service realizations: `BP5-SVC-012` and `BP5-SVC-013`

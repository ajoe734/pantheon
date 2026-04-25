# OC-002 Operator Console Wave 2 — Canonical Packet Family

## Header

- Packet family ID: `OC-002`
- Workbench: Operator Console
- Phase origin: `BP5-WB-002`
- Lovable readiness: **ready** — `OC-01` through `OC-05` now have published operator-shell packets and handoff bundles; the remaining work for this family is frontend implementation and loop closure
- Recommended wave: Wave 2 after the existing Operator Console baseline (`PKT-001`, `PKT-002`, `PKT-003`, `PKT-005`)
- Owner: Codex2
- Reviewer: Claude

---

## Objective

Turn the loose Operator Console shell into a truthful Wave 2 packet family for:

- `OC-01` Operator Home dashboard
- `OC-02` Alerts rail
- `OC-03` Health status board
- `OC-04` Runtime state board
- `OC-05` Paper / Live Drift view

This family does not authorize the UI to join runtime, incident, governance, telemetry, or kill-switch state in the browser without an explicit backend-owned aggregation contract. Existing Wave 1 screens remain the authority for deployment review, incident response, post-incident review, degradation banners, and SSE behavior.

---

## Existing Pantheon Support (pre-conditions)

Before packetizing any Wave 2 module, treat the following artifacts as canonical:

| Artifact | Location | What it defines |
|---|---|---|
| Operator Console backlog | `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` | Wave 1 vs Wave 2 scope split, `OC-01` to `OC-10` inventory, and internal Wave 2 ordering |
| BFF HA and control-plane resilience | `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | BFF remains the only aggregation point, but degraded-state truth must be backend-shaped and the admin CLI / internal API remain the secondary control path |
| Degraded operator path | `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md` | per-surface degradation tiers, total-BFF-outage fallback behavior, and operator-facing secondary-path guidance |
| Paper / canary / live policy | `PAPER_CANARY_LIVE_POLICY.md` | canonical stage semantics, promotion thresholds, and the rule that `paper`, `canary`, `live`, and `frozen` are real deployment-stage states rather than UI labels |
| Existing Wave 1 operator packets | `docs/bff/PKT-001-deployment-review-console.md`, `docs/bff/PKT-001-governance-review-queue.md`, `docs/bff/PKT-002-incident-home.md`, `docs/bff/PKT-002-incident-detail.md`, `docs/bff/PKT-002-incident-action-drawer.md`, `docs/bff/PKT-003-post-incident-review-console.md`, `docs/bff/PKT-005-degradation-banner.md`, `docs/bff/PKT-005-sse-substrate.md` | existing operator command authority, incident and post-incident surfaces, degradation banner semantics, and SSE reconciliation rules |
| Governed BFF API contract | `services/control-plane/bff/BFF_API_CONTRACT.md` | canonical list/detail route inventory for runtime, telemetry, incident, governance, and kill-switch reads |

The critical boundary is unchanged: Wave 2 may summarize or cross-link existing operator screens, but it must not fork action authority, invent client-side health logic, or reintroduce snapshot/default fallbacks after `BP5-SVC-015`.

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Lovable readiness | Wave order |
|---|---|---|---|---|
| `OC-04` | Runtime state board | live runtime roster, current stage, runtime status, telemetry summary, rollback-history entry points, and last-updated timestamps | ready | Wave 2 — 1st |
| `OC-03` | Health status board | control-plane and data-surface health overview, degraded-surface summary, safe-mode state, and secondary control path guidance | ready | Wave 2 — 2nd |
| `OC-02` | Alerts rail | chronological operator alerts for incidents, governance risk, kill-switch changes, and runtime anomalies | ready | Wave 2 — 3rd |
| `OC-01` | Operator Home dashboard | top-level operator landing screen summarizing incidents, governance queue, runtime health, safe-mode state, and escalation shortcuts | ready | Wave 2 — 4th |
| `OC-05` | Paper / Live Drift view | paper-vs-live comparison with baseline snapshot, observed drift, evidence refs, and required follow-up path | ready | Wave 2 — 5th |

---

## OC-04 Runtime State Board

### Surface scope

- **Runtime roster**: multi-runtime list keyed by stable `runtime_id`, showing `deployment_stage`, runtime status, bound artifact or plan refs, and `last_updated_at`
- **Telemetry summary rail**: per-runtime TL-02 summary values displayed as backend-shaped summary cards, not as client-computed aggregations
- **Rollback entry points**: links into existing rollback history or review surfaces; this board does not become the rollback write owner
- **Live reconciliation**: subscribes to `PKT-005` SSE runtime events once the initial read model is fetched

### Canonical anchors already available

- `GET /api/v1/operator/runtime-state` (`PKT-010`)
- `GET /api/v1/runtimes/{runtime_id}/status` (`RT-03`)
- `GET /api/v1/runtimes/{runtime_id}/rollbacks` (`RT-04`)
- `GET /api/v1/telemetry/{runtime_id}/summary` (`TL-02`)
- `GET /api/v1/runtime/{runtime_id}/events/stream` (`PKT-005` substrate)
- `docs/bff/PKT-010-runtime-state-board.md`
- `docs/screens/PKT-010-runtime-state-board.md`
- `docs/pantheon-handoffs/PKT-010-runtime-state-board/FRONTEND_CHANGE_SPEC.md`

Pantheon now publishes the multi-runtime operator roster route and packet bundle. The lower-level runtime and telemetry primitives remain the underlying anchors, but the frontend no longer needs to stitch them together row-by-row.

### Contract status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/runtime-state` | **live** | primary operator-owned roster route now returns `runtimes[]`, `page_info`, `meta.total`, `meta.sort`, and `meta.surfaces.runtime_state` |
| Runtime-board aggregation contract | **published** | `docs/bff/PKT-010-runtime-state-board.md` locks row ownership for stage, status, telemetry summary, rollback summary, and refs |
| Multi-runtime pagination and sort contract | **published** | `sort_by`, `sort_order`, `page_token`, and `page_size` are now backend-owned route semantics |
| Runtime mismatch wording | **published** | stale vs unavailable wording is tied to `meta.surfaces.runtime_state`, `runtime_roster`, `telemetry_summary`, and `rollback_history` |

### Packetization prerequisite

`OC-04` now satisfies its packetization prerequisite in the current workspace. The route, BFF contract, example payload, screen spec, and frontend change spec all exist. The next step is frontend implementation, not more Pantheon-owned aggregation work for this packet.

---

## OC-03 Health Status Board

### Surface scope

- **Health summary header**: overall control-plane status, safe-mode state, and explicit degradation banner inheritance
- **Surface-group health cards**: grouped health for runtime, telemetry, incident, governance, and kill-switch dependencies
- **Secondary control path guidance**: operator-visible fallback instructions rooted in the degraded operator path, shown only when backend-supplied degradation or outage conditions warrant it
- **Escalation ordering**: links to existing incident-response or deployment-review screens rather than inventing new actions

### Canonical anchors already available

- `GET /api/v1/operator/health-status` (`PKT-011`)
- `GET /api/v1/kill-switch/status` (`IN-05`)
- `docs/bff/PKT-005-degradation-banner.md`
- `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md`
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
- `docs/bff/PKT-011-health-status-board.md`
- `docs/screens/PKT-011-health-status-board.md`
- `docs/pantheon-handoffs/PKT-011-health-status-board/FRONTEND_CHANGE_SPEC.md`

Pantheon now publishes the operator-owned health board route and packet bundle. The degradation and kill-switch documents remain the policy anchors, but the frontend no longer needs to invent a page-shaped health merge from those sources.

### Contract status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/health-status` | **live** | primary operator-owned health route now returns grouped health sections, safe-mode state, `secondary_control_path`, and `meta.surfaces.health_status` |
| Health merge contract over `meta.surfaces` | **published** | `docs/bff/PKT-011-health-status-board.md` locks group ownership and top-level health semantics |
| Surface-group taxonomy | **published** | the `runtime`, `telemetry`, `incident`, `governance`, and `kill_switch` taxonomy is now canonical in `PKT-011` |
| Secondary control path display contract | **published** | `secondary_control_path.mode`, `reason`, and `targets[]` are now backend-owned |

### Packetization prerequisite

`OC-03` now satisfies its packetization prerequisite in the current workspace. The route, BFF contract, example payload, screen spec, and frontend change spec all exist. The next step is frontend implementation, not more Pantheon-owned aggregation work for this packet.

---

## OC-02 Alerts Rail

### Surface scope

- **Chronological alert feed**: active incidents, pending governance risk, kill-switch changes, and runtime anomalies in one ordered rail or drawer
- **Severity and category labels**: backend-owned alert taxonomy, not a badge scheme assembled per source screen
- **Linked targets**: each alert carries a stable target ref into `PKT-001`, `PKT-002`, `OC-03`, `OC-04`, or `OC-05`
- **Acknowledgement affordance**: optional, but if present it must be backed by canonical authority and stable `alert_id`

### Canonical anchors already available

- `GET /api/v1/operator/alerts` (`PKT-012`)
- `GET /api/v1/incidents`
- `GET /api/v1/operator/governance/review-queue`
- `GET /api/v1/operator/governance/approval-queue`
- `GET /api/v1/kill-switch/status`
- `GET /api/v1/operator/runtime-state`
- `docs/bff/PKT-012-alerts-rail.md`
- `docs/screens/PKT-012-alerts-rail.md`
- `docs/pantheon-handoffs/PKT-012-alerts-rail/FRONTEND_CHANGE_SPEC.md`

Pantheon now publishes the operator-owned alert feed route and packet bundle. The lower-level incident, governance, kill-switch, and runtime primitives remain the underlying anchors, but the frontend no longer needs to decide what becomes an operator alert.

### Contract status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/alerts` | **live** | primary operator alert-feed route now returns `alerts[]`, `summary`, and `meta.surfaces.alerts` |
| Alert severity taxonomy | **published** | `critical`, `high`, `medium`, and `low` are now canonical operator alert severities |
| Stable `alert_id` contract | **published** | incident, governance, kill-switch, and runtime alerts now carry stable backend-owned IDs |
| Alert acknowledgement contract | **omitted by design** | `meta.acknowledgement_supported = false` keeps this packet explicitly read-only |
| Runtime anomaly projection | **published** | runtime anomalies are now backend-projected from runtime roster and telemetry summaries |

### Packetization prerequisite

`OC-02` now satisfies its packetization prerequisite in the current workspace. The route, BFF contract, example payload, screen spec, and frontend change spec all exist. The next step is frontend implementation, not more Pantheon-owned aggregation work for this packet.

---

## OC-01 Operator Home Dashboard

### Surface scope

- **Summary cards**: active incidents, pending governance items, runtime health overview, safe-mode state, and escalation shortcuts
- **Cross-panel freshness rules**: one snapshot boundary or explicit aggregation contract governing how card timestamps and degradation states are presented together
- **Escalation shortcuts**: links into existing screen owners (`PKT-001`, `PKT-002`, `OC-03`, `OC-04`) rather than duplicating their actions
- **Empty vs degraded distinction**: operator home must never flatten degraded or unavailable upstream state into a calm-looking empty dashboard

### Canonical anchors already available

- `GET /api/v1/operator/home` (`PKT-013`)
- `GET /api/v1/operator/alerts` (`PKT-012`)
- `GET /api/v1/operator/health-status` (`PKT-011`)
- `GET /api/v1/operator/runtime-state` (`PKT-010`)
- `docs/bff/PKT-013-operator-home.md`
- `docs/screens/PKT-013-operator-home.md`
- `docs/pantheon-handoffs/PKT-013-operator-home/FRONTEND_CHANGE_SPEC.md`

Pantheon now publishes the operator-home aggregation route and packet bundle. The dashboard summarizes already-published operator-shell truth instead of forcing the UI to invent card hierarchy or escalation ordering.

### Contract status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/home` | **live** | primary operator-home route now returns `overall_status`, `cards[]`, `escalation_shortcuts[]`, `safe_mode_state`, and `meta.surfaces.operator_home` |
| Operator-home aggregation contract | **published** | `docs/bff/PKT-013-operator-home.md` locks card ownership, card order, and shared snapshot semantics |
| Summary-card hierarchy | **published** | the `alerts`, `incidents`, `governance`, `runtime`, and `health` cards are now backend-owned |
| Escalation shortcut contract | **published** | shortcut ordering and priority are now backend-owned rather than browser-inferred |
| Cross-link contract | **published** | each card now ships stable target refs to existing owner screens |

### Packetization prerequisite

`OC-01` now satisfies its packetization prerequisite in the current workspace. The route, BFF contract, example payload, screen spec, and frontend change spec all exist. The next step is frontend implementation, not more Pantheon-owned aggregation work for this packet.

---

## OC-05 Paper / Live Drift View

### Surface scope

- **Comparison header**: target artifact or runtime identity, current observed stage (`canary` or `live`), paper baseline timestamp, and latest observation timestamp
- **Metric groups**: backend-shaped drift metrics grouped by execution, exposure, slippage, and risk signals
- **Threshold evaluation**: comparison result against `PAPER_CANARY_LIVE_POLICY.md` semantics and any strategy-specific overrides already resolved by the backend
- **Evidence drawer**: links to `ApprovalDecision`, `DeploymentPlan`, `EvolutionDecision`, incident, postmortem, or drift report refs
- **Follow-up path**: backend-shaped typed actions or links for the next review step; this screen does not become a new deployment or evolution write owner

### Canonical anchors already available

- `GET /api/v1/operator/paper-live-drift/{runtime_id}` (`PKT-014`)
- `PAPER_CANARY_LIVE_POLICY.md`
- existing promotion-review semantics in `F-042`
- `services/control-plane/governance/approval_decision.py` evidence type `drift_report`
- `docs/bff/PKT-014-paper-live-drift.md`
- `docs/screens/PKT-014-paper-live-drift.md`
- `docs/pantheon-handoffs/PKT-014-paper-live-drift/FRONTEND_CHANGE_SPEC.md`

Pantheon now publishes the paper/live drift route and packet bundle. The policy and evidence documents remain the canonical anchors, but the frontend no longer needs to invent a comparison object from them.

### Contract status

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/paper-live-drift/{runtime_id}` | **live** | primary drift route now returns `paper_baseline`, `observed_state`, `drift_groups[]`, `threshold_evaluation`, `evidence_refs[]`, and `recommended_actions[]` |
| Drift comparison object | **published** | `docs/bff/PKT-014-paper-live-drift.md` locks group ownership, threshold evaluation, and evidence linkage |
| Backend-shaped follow-up actions | **published** | recommended actions now point to existing owner screens rather than browser-inferred decisions |
| Drift threshold narrative | **published** | breach vs watch copy is now backend-owned in the threshold evaluation object |

### Packetization prerequisite

`OC-05` now satisfies its packetization prerequisite in the current workspace. The route, BFF contract, example payload, screen spec, and frontend change spec all exist. The next step is frontend implementation, not more Pantheon-owned aggregation work for this packet.

---

## Backend Gap Matrix

No Pantheon-owned backend gaps remain for the current Wave 2 packet scope. The remaining work is frontend implementation and loop closure across `OC-01` to `OC-05`.

---

## Internal Ordering and Dependency Chain

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| Wave 1 baseline | `OC-06` to `OC-10` | existing operator screens and substrates already settle deployment review, incident control, post-incident review, degradation, and SSE behavior | none |
| Wave 2 — 1st | `OC-04 Runtime state board` | all later Wave 2 modules need stable runtime identity and last-known runtime state before they can summarize or alert on it | inherits RT/TL reads and `PKT-005` SSE substrate |
| Wave 2 — 2nd | `OC-03 Health status board` | health grouping is now published and can anchor alert taxonomy and home-screen health cards | depends on `OC-04` roster identity plus `PKT-005` banner semantics |
| Wave 2 — 3rd | `OC-02 Alerts rail` | severity and routing rules become stable only after runtime-state and health-group vocabulary are defined | depends on `OC-03`, `OC-04`, incident feed, governance queue identity, and kill-switch state |
| Wave 2 — 4th | `OC-01 Operator Home dashboard` | the home screen should summarize already-defined runtime, health, and alert modules instead of inventing their contracts internally | depends on `OC-02`, `OC-03`, and `OC-04` summary contracts |
| Wave 2 — 5th | `OC-05 Paper / Live Drift view` | drift review is the farthest-from-ready module because it needs a net-new comparison object and follow-up action contract | depends on paper/live policy plus stable runtime and alert context from earlier Wave 2 modules |

---

## Promotion Criteria

A Wave 2 Operator Console module may move from **not ready** to **ready** only when all of the following are true:

1. Every route or explicit aggregation contract listed in that module's Backend Gaps table exists and has stable field ownership.
2. The module's degraded-state behavior is wired through canonical `meta.surfaces.*` fields and inherits `PKT-005` instead of inventing local health logic.
3. Any CTA or follow-up action is backend-shaped through an explicit command or target-ref contract; the UI does not infer authority from raw data.
4. The module clearly links to existing Wave 1 owner screens instead of duplicating deployment, incident, rollback, or kill-switch action semantics.
5. A screen spec, BFF contract, example payload, and frontend handoff bundle exist for the module.

All Wave 2 Operator Console modules now satisfy the aggregation-contract gate for Lovable handoff. The remaining gate is frontend implementation and review closure.

---

## Cross-Cutting Rules

### No client-side operator-shell synthesis

The frontend must not:

- merge `RT-03`, `TL-02`, `IN-01`, governance queue, and kill-switch data into a new operator surface without a published BFF contract
- compute health categories, alert severity, or drift follow-up from raw fields alone
- invent snapshot alignment or freshness guarantees that the backend did not provide
- reintroduce local snapshot or seed fallback as a substitute for missing service-backed reads

### Existing Wave 1 screens stay authoritative

Wave 2 surfaces may summarize or link into:

- `PKT-001` Deployment Review Console
- `PKT-001` Governance Review Queue
- `PKT-002` Incident Response surfaces
- `PKT-003` Post-Incident Review
- `PKT-005` degradation banner and SSE substrate

They must not duplicate the write authority, degraded-state wording, or command semantics already owned by those packets.

### Degradation and fallback truth remain backend-owned

If a Wave 2 module shows stale, degraded, or unavailable state:

- banner state comes from `PKT-005`
- fallback guidance comes from the degraded operator path and BFF HA policy
- safety-critical secondary control paths remain admin CLI / internal API concerns
- the UI must never translate unavailable data into a calm empty state

### Drift view is a review surface, not a write owner

`OC-05` may show evidence refs and backend-shaped follow-up targets, but it must not:

- promote or roll back deployments directly
- create `EvolutionDecision` records client-side
- infer incident severity from metric deltas
- bypass the existing governance, runtime, or incident owners

---

## Canonical References

- Backlog source: `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md`
- Existing operator packet families: `PKT-001`, `PKT-002`, `PKT-003`, `PKT-005`
- BFF route inventory: `services/control-plane/bff/BFF_API_CONTRACT.md`
- Degradation and fallback: `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`, `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md`
- Stage semantics: `PAPER_CANARY_LIVE_POLICY.md`
- Handoff directory: `docs/pantheon-handoffs/OC-002-operator-console-wave2/`

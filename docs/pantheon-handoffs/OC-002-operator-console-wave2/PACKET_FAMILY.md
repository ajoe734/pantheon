# OC-002 Operator Console Wave 2 — Canonical Packet Family

## Header

- Packet family ID: `OC-002`
- Workbench: Operator Console
- Phase origin: `BP5-WB-002`
- Lovable readiness: **not ready** — none of the five Wave 2 modules has a published operator-shell packet or handoff bundle yet; `OC-03` and `OC-04` can reuse live read primitives and degraded-path policy, but `OC-01`, `OC-02`, and `OC-05` still require net-new aggregation contracts before frontend handoff is honest
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
| `OC-04` | Runtime state board | live runtime roster, current stage, runtime status, telemetry summary, rollback-history entry points, and last-updated timestamps | not ready | Wave 2 — 1st |
| `OC-03` | Health status board | control-plane and data-surface health overview, degraded-surface summary, safe-mode state, and secondary control path guidance | not ready | Wave 2 — 2nd |
| `OC-02` | Alerts rail | chronological operator alerts for incidents, governance risk, kill-switch changes, and runtime anomalies | not ready | Wave 2 — 3rd |
| `OC-01` | Operator Home dashboard | top-level operator landing screen summarizing incidents, governance queue, runtime health, safe-mode state, and escalation shortcuts | not ready | Wave 2 — 4th |
| `OC-05` | Paper / Live Drift view | paper-vs-live comparison with baseline snapshot, observed drift, evidence refs, and required follow-up path | not ready | Wave 2 — 5th |

---

## OC-04 Runtime State Board

### Surface scope

- **Runtime roster**: multi-runtime list keyed by stable `runtime_id`, showing `deployment_stage`, runtime status, bound artifact or plan refs, and `last_updated_at`
- **Telemetry summary rail**: per-runtime TL-02 summary values displayed as backend-shaped summary cards, not as client-computed aggregations
- **Rollback entry points**: links into existing rollback history or review surfaces; this board does not become the rollback write owner
- **Live reconciliation**: subscribes to `PKT-005` SSE runtime events once the initial read model is fetched

### Canonical anchors already available

- `GET /api/v1/runtimes/{runtime_id}/status` (`RT-03`)
- `GET /api/v1/runtimes/{runtime_id}/rollbacks` (`RT-04`)
- `GET /api/v1/telemetry/{runtime_id}/summary` (`TL-02`)
- `GET /api/v1/runtime/{runtime_id}/events/stream` (`PKT-005` substrate)

These primitives are real, but they are single-runtime reads. They do not by themselves define a canonical multi-runtime operator board.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/runtime-state` | **missing** | primary operator-owned roster route; must return `runtimes[]` with stable `runtime_id`, `deployment_stage`, `status`, summary telemetry block, rollback link refs, and `meta.surfaces.runtime_state` |
| Runtime-board aggregation contract | **missing** | defines how `RT-03`, `RT-04`, and `TL-02` are merged into one roster row without client-side joins |
| Multi-runtime pagination and sort contract | **missing** | runtime board cannot rely on hidden browser ordering; roster sort and filter semantics must be backend-owned |
| Runtime mismatch wording | **missing** | stale vs unavailable runtime-state copy must be tied to `meta.surfaces.runtime_state`, not inferred from absent telemetry fields |

### Packetization prerequisite

`OC-04` may become Lovable-ready only when the operator-owned roster route exists or an equivalent explicit BFF aggregation contract is published with the same field ownership. The UI must not fan out per-row requests to stitch the roster together.

---

## OC-03 Health Status Board

### Surface scope

- **Health summary header**: overall control-plane status, safe-mode state, and explicit degradation banner inheritance
- **Surface-group health cards**: grouped health for runtime, telemetry, incident, governance, and kill-switch dependencies
- **Secondary control path guidance**: operator-visible fallback instructions rooted in the degraded operator path, shown only when backend-supplied degradation or outage conditions warrant it
- **Escalation ordering**: links to existing incident-response or deployment-review screens rather than inventing new actions

### Canonical anchors already available

- `GET /api/v1/kill-switch/status` (`IN-05`)
- `docs/bff/PKT-005-degradation-banner.md`
- `services/control-plane/bff/DEGRADED_OPERATOR_PATH.md`
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`

These documents define how degradation behaves. They do not yet define a page-shaped operator health board.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/health-status` | **missing** | preferred composed health route; must return grouped health sections, safe-mode state, `secondary_control_path`, and `meta.surfaces.health_status` |
| Health merge contract over `meta.surfaces` | **missing** | acceptable alternative to a dedicated route only if the merge rules, surface groups, and copy ownership are explicitly published; the browser must not invent them ad hoc |
| Surface-group taxonomy | **missing** | defines stable groups such as `runtime`, `telemetry`, `incident`, `governance`, and `kill_switch` plus their operator-facing labels |
| Secondary control path display contract | **missing** | determines when admin CLI / internal API guidance appears and which exact fallback targets are shown for degraded vs total outage states |

### Packetization prerequisite

`OC-03` is blocked until either a dedicated health route exists or the health-board merge contract is published as canonical BFF-facing truth. Merely having `PKT-005` and `IN-05` does not authorize the frontend to compute overall health from arbitrary existing responses.

---

## OC-02 Alerts Rail

### Surface scope

- **Chronological alert feed**: active incidents, pending governance risk, kill-switch changes, and runtime anomalies in one ordered rail or drawer
- **Severity and category labels**: backend-owned alert taxonomy, not a badge scheme assembled per source screen
- **Linked targets**: each alert carries a stable target ref into `PKT-001`, `PKT-002`, `OC-03`, `OC-04`, or `OC-05`
- **Acknowledgement affordance**: optional, but if present it must be backed by canonical authority and stable `alert_id`

### Canonical anchors already available

- `GET /api/v1/incidents`
- `GET /api/v1/operator/governance/review-queue`
- `GET /api/v1/kill-switch/status`
- `GET /api/v1/incidents/stream`, `GET /api/v1/kill-switch/updates`

These routes expose raw ingredients. They do not define one canonical operator alert feed.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/alerts` | **missing** | primary operator alert-feed route; must return `alerts[]` with stable `alert_id`, `severity`, `category`, `raised_at`, `summary`, `target_ref`, and `meta.surfaces.alerts` |
| Alert severity taxonomy | **missing** | canonical enum and grouping rules are required before any alert chip, banner, or sorting behavior is packetized |
| Alert acknowledgement contract | **missing** | if acknowledgement is part of the screen, the command path must be explicit, e.g. `POST /api/v1/operator/commands` with `AcknowledgeOperatorAlert`; otherwise the packet must remain read-only and omit the CTA |
| Runtime anomaly projection | **missing** | defines how runtime or telemetry anomalies become alert items without requiring the client to inspect raw telemetry deltas |

### Packetization prerequisite

`OC-02` cannot be handed to Lovable until the alert feed exists with stable `alert_id` values and a published severity taxonomy. No client-side merge of incident rows, governance queue items, SSE events, and kill-switch badges is permitted as a substitute.

---

## OC-01 Operator Home Dashboard

### Surface scope

- **Summary cards**: active incidents, pending governance items, runtime health overview, safe-mode state, and escalation shortcuts
- **Cross-panel freshness rules**: one snapshot boundary or explicit aggregation contract governing how card timestamps and degradation states are presented together
- **Escalation shortcuts**: links into existing screen owners (`PKT-001`, `PKT-002`, `OC-03`, `OC-04`) rather than duplicating their actions
- **Empty vs degraded distinction**: operator home must never flatten degraded or unavailable upstream state into a calm-looking empty dashboard

### Canonical anchors already available

- `GET /api/v1/operator/governance/review-queue`
- `GET /api/v1/incidents`
- `GET /api/v1/kill-switch/status`
- `OC-03` and `OC-04` once their contracts exist

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/home` | **missing** | preferred composed home route; must return all dashboard cards plus `meta.snapshot_at` and `meta.surfaces.operator_home` |
| Operator-home aggregation contract | **missing** | if the home screen is built from existing routes, the cross-card merge rules, card ownership, and freshness semantics must be published explicitly |
| Summary-card hierarchy | **missing** | card order, escalation priority, and empty/degraded copy must be backend-owned or packet-defined, not invented in the UI |
| Cross-link contract | **missing** | each summary card needs stable target refs into the authoritative downstream screen instead of synthetic browser routes |

### Packetization prerequisite

`OC-01` depends on the stable summary contracts established by `OC-02`, `OC-03`, and `OC-04`. It is explicitly blocked from Lovable until the operator-home aggregation contract is real. The dashboard must summarize existing operator truth, not become a mega-packet that redefines it.

---

## OC-05 Paper / Live Drift View

### Surface scope

- **Comparison header**: target artifact or runtime identity, current observed stage (`canary` or `live`), paper baseline timestamp, and latest observation timestamp
- **Metric groups**: backend-shaped drift metrics grouped by execution, exposure, slippage, and risk signals
- **Threshold evaluation**: comparison result against `PAPER_CANARY_LIVE_POLICY.md` semantics and any strategy-specific overrides already resolved by the backend
- **Evidence drawer**: links to `ApprovalDecision`, `DeploymentPlan`, `EvolutionDecision`, incident, postmortem, or drift report refs
- **Follow-up path**: backend-shaped typed actions or links for the next review step; this screen does not become a new deployment or evolution write owner

### Canonical anchors already available

- `PAPER_CANARY_LIVE_POLICY.md`
- existing promotion-review semantics in `F-042`
- `services/control-plane/governance/approval_decision.py` evidence type `drift_report`
- evolution and incident evidence objects already used in `PKT-003`

These are policy and evidence anchors only. They are not a front-end comparison surface.

### Backend gaps

| Route or contract | Status | Notes |
|---|---|---|
| `GET /api/v1/operator/paper-live-drift/{runtime_id}` | **missing** | primary composed drift route; must return `paper_baseline`, `observed_state`, grouped drift metrics, threshold evaluation, evidence refs, `recommended_actions[]`, and `meta.surfaces.paper_live_drift` |
| Drift comparison object | **missing** | canonical field shape for grouped metrics, threshold outcomes, and evidence linkage does not yet exist as BFF truth |
| Backend-shaped follow-up actions | **missing** | the route must return typed follow-up actions or target refs; the UI must not infer whether to open promotion review, incident response, or evolution review from raw metric values |
| Drift threshold narrative | **missing** | policy thresholds and override context need operator-facing copy and labels resolved in the backend or packet, not improvised in the browser |

### Packetization prerequisite

`OC-05` stays blocked until the dedicated drift route exists with a stable comparison object. Policy text plus existing approval or evolution objects are not enough for a truthful screen packet.

---

## Backend Gap Matrix

| Route or contract | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `GET /api/v1/operator/runtime-state` | `OC-04`, `OC-01` | missing read route | runtime roster, telemetry summary rail, home-screen runtime cards |
| Runtime-board aggregation contract | `OC-04`, `OC-01`, `OC-03` | missing BFF contract | roster rows, runtime health grouping, home-card rollups |
| `GET /api/v1/operator/health-status` or equivalent explicit merge contract | `OC-03`, `OC-01` | missing read route or merge contract | health board, safe-mode summary, operator-home health cards |
| Surface-group health taxonomy | `OC-03`, `OC-02`, `OC-01` | missing BFF contract | health sections, alert category mapping, home summary copy |
| `GET /api/v1/operator/alerts` | `OC-02`, `OC-01` | missing read route | alerts rail, home alert summary |
| Alert severity taxonomy and stable `alert_id` contract | `OC-02`, `OC-01` | missing BFF contract | alert chips, ordering, acknowledgement, home incident-risk rollup |
| `GET /api/v1/operator/home` or equivalent explicit aggregation contract | `OC-01` | missing read route or merge contract | entire home dashboard |
| `GET /api/v1/operator/paper-live-drift/{runtime_id}` | `OC-05` | missing read route | entire paper/live drift view |
| Drift comparison object and `recommended_actions[]` | `OC-05` | missing BFF contract | grouped metrics, threshold callouts, evidence drawer, follow-up CTAs |

---

## Internal Ordering and Dependency Chain

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| Wave 1 baseline | `OC-06` to `OC-10` | existing operator screens and substrates already settle deployment review, incident control, post-incident review, degradation, and SSE behavior | none |
| Wave 2 — 1st | `OC-04 Runtime state board` | all later Wave 2 modules need stable runtime identity and last-known runtime state before they can summarize or alert on it | inherits RT/TL reads and `PKT-005` SSE substrate |
| Wave 2 — 2nd | `OC-03 Health status board` | health grouping should be built on top of the runtime roster and the existing degradation substrate, not the other way around | depends on `OC-04` roster identity plus `PKT-005` banner semantics |
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

No `OC-01` or `OC-05` surface may be handed to Lovable before its aggregation contract is explicit. That is a hard gate for this family.

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


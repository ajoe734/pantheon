# Pantheon Console Workbench Backlog

## Summary

This backlog expands `front-ai-trading-system` from a loose page set into the full `Pantheon Console` defined in the blueprint. Each workbench records:

- objective
- screen or module inventory
- existing Pantheon contract or BFF support
- missing canonical screen spec
- Lovable readiness
- backend dependency
- recommended execution wave

## Workbench Summary Table

| Workbench | Objective | Existing Pantheon support | Missing canonical spec | Lovable-ready | Backend dependency | Recommended wave |
|---|---|---|---|---|---|---|
| Operator Console | operator command center for deployment, incident, health, runtime, and drift | strong APP-002 support for deployment, incident, post-incident, degradation, SSE | Operator Home and paper-live drift screens | partial | medium | Wave 1 |
| Persona Workbench | persona lifecycle, bindings, profiles, and consult controls | persona-management composed view plus remaining catalog drilldown surfaces | standalone persona list/detail IA, tool profile/consult policy packet language, and Wave 2 shared drilldown packets | partial | medium | Wave 1 |
| Research Workbench | research search, analysis, tickets, launches, compares | blueprint-level direction only | full canonical packet family | no | high | Wave 3 |
| Knowledge Workbench | institutional memory, evidence navigation, insight cards, and strategy specs | blueprint-level direction only; no BFF route backs any Knowledge screen | full canonical packet family (5 modules); Wave 3 internal order: Institutional Memory → Research Notes → Evidence Refs → Insight Cards → Strategy Spec | no | high — all 5 modules need net-new BFF routes before packetization can begin | Wave 3 |
| Trainer Workbench | teaching sessions and before-after review | demo-grade trainer shell plus read-only teaching-history surfaces inside Persona Management and `PS-05`; no dedicated Trainer BFF flow exists | full canonical packet family | no | high | Wave 3 |
| Consultation Workbench | consult requests, committee review, debate, and red-team outputs | consultation read-surface contract, consult-policy and session semantics, and L3 request or memo design; no standalone workbench packet family | full canonical packet family | no | high | Wave 4 |
| Governance Workbench | review queue, approval queue, promotion, rollback, and diff control | `PKT-001` Governance Review Queue, `F-042` Promotion Review, shared deployment or approval reads, and operator governance semantics | approval queue, deployment diff, rollback review, and governance audit rail | partial | medium | Wave 2 |
| Evolution Workbench | post-incident, evolution review, lineage, inspiration, and mutation review | post-incident/evolution sidecars and lineage surfaces | inspiration and mutation review packet family | partial | medium | Wave 2 |

## Operator Console

Objective: give operators one coherent control surface for runtime state, deployment, incidents, system health, and degraded operation.

Surface inventory and wave ownership:

| Surface or IA scope | Module owner | Existing Pantheon support | Primary wave | Notes |
|---|---|---|---|---|
| `Operator Home` dashboard shell | `OC-01 Operator Home dashboard` | no dedicated composed view yet; can only assemble from existing operator primitives such as governance queue, incident list, runtime status, telemetry summary, and kill-switch status | Wave 2 | keep this as backlog definition only; do not pretend the current loose pages equal a canonical home screen |
| `Alerts rail` | `OC-02 Alerts rail` | incident list, governance review queue, kill-switch status, and SSE events provide alert-like ingredients, but there is no canonical operator alert feed or severity schema | Wave 2 | depends on a dedicated alert taxonomy instead of ad hoc badges across screens |
| `Health status` + `Runtime state` | `OC-03 Health status board`; `OC-04 Runtime state board` | `GET /api/v1/runtimes/{runtime_id}/status`; `GET /api/v1/telemetry/{runtime_id}/summary`; `GET /api/v1/kill-switch/status`; degraded-path runbook and operator acceptance semantics | Wave 2 | live read primitives exist, but there is no composed operator-health packet or shell copy yet |
| `Paper/live drift view` | `OC-05 Paper / Live Drift view` | paper-live boundary policy exists; promotion review already uses paper-live stage semantics; evolution decisions and drift evidence types exist in governance contracts | Wave 2 | still missing a dedicated drift read model and operator-facing review surface |
| `Deployment Review Console` | `OC-06 Deployment Review Console` | `PKT-001`; `GET /api/v1/operator/deployment-review/{plan_id}`; operator command route; published screen spec, contract, example payload, and Lovable packet | Wave 1 baseline | ready packet should be reused, not redefined |
| `Incident Response Console` | `OC-07 Incident Response Console` | `PKT-002`; incident home, detail, and action drawer packet family; kill-switch status and command receipts already modeled | Wave 1 baseline | ready packet family already defines degraded-state and fallback rules |
| `Post-Incident Review Console` | `OC-08 Post-Incident Review Console` | `PKT-003`; `GET /api/v1/operator/post-incident-review/{incident_id}` plus postmortem and evidence reads | Wave 1 baseline | ready packet family already carries W3 inherited caveats |
| `Global degradation banner` | `OC-09 Global degradation banner` | `PKT-005`; `meta.staleness` + `meta.surfaces.*` inheritance rules are already packetized | Wave 1 baseline | cross-cutting substrate; not a standalone page |
| `SSE and reconciliation substrate` | `OC-10 SSE reconciliation substrate` | `PKT-005`; three SSE endpoints plus replay, heartbeat, reconnect, and reconciler rules | Wave 1 baseline | cross-cutting substrate shared by runtime, incident, and kill-switch surfaces |

Separation rule for this backlog:

- put dashboard shell copy, alerts vocabulary, health/runtime panel wording, and drift review screen language in `Missing screen-spec work`
- put absent composed views, alert or drift read models, and cross-surface aggregation contracts in `Backend or contract dependencies`

Canonical module inventory:

| Module | Screen or surface scope | Existing Pantheon support | Missing screen-spec work | Lovable readiness | Backend or contract dependencies | Recommended wave |
|---|---|---|---|---|---|---|
| `OC-01 Operator Home dashboard` | top-level operator landing screen with summary cards for incidents, governance queue, runtime health, safe-mode state, and escalation shortcuts | no canonical Operator Home packet today; only underlying inputs exist via `GET /api/v1/operator/governance/review-queue`, `GET /api/v1/incidents`, `GET /api/v1/runtimes/{runtime_id}/status`, `GET /api/v1/telemetry/{runtime_id}/summary`, and `GET /api/v1/kill-switch/status` | full home-screen packet: dashboard layout, summary-card hierarchy, escalation CTA wording, empty vs degraded states, and cross-panel refresh rules | no | needs a dedicated composed operator-home read model or an explicit aggregation contract over the existing routes; must preserve backend-owned degradation semantics instead of deriving health locally | Wave 2 — fourth |
| `OC-02 Alerts rail` | chronological alert strip or drawer for active incidents, pending governance risk, kill-switch changes, and runtime anomalies | incident list, governance review queue, kill-switch status, and SSE event streams expose raw alert ingredients; no canonical operator alert feed exists | full alerts packet: alert severity labels, grouping rules, acknowledgement affordance, and stale-data copy | no | needs a canonical alert feed or aggregation contract with stable alert ids, severity taxonomy, linked target refs, and acknowledgement semantics | Wave 2 — third |
| `OC-03 Health status board` | control-plane and data-surface health overview, degraded-surface summary, safe-mode state, and secondary control path guidance | `GET /api/v1/kill-switch/status`; degraded operator path guide; `PKT-005` degradation banner; operator acceptance matrix degraded-behavior rules | full health-board packet: health sections, surface grouping, operator guidance copy, and escalation ordering | partial | needs either a dedicated health composed view or a canonical merge contract over `meta.surfaces`, kill-switch status, and degraded-path guidance so the UI does not invent health logic | Wave 2 — second |
| `OC-04 Runtime state board` | live runtime roster, current stage, runtime status, telemetry summary, rollback-history entry points, and last-updated timestamps | `GET /api/v1/runtimes/{runtime_id}/status`; `GET /api/v1/telemetry/{runtime_id}/summary`; runtime-related SSE stream; RT and TL read surfaces from the remaining catalog sidecar | runtime roster shell, status badge language, stale/runtime mismatch copy, and drilldown entry rules | partial | lacks a canonical multi-runtime list or composed board route; single-runtime primitives exist, but operator-home runtime inventory still needs an aggregation contract | Wave 2 — first |
| `OC-05 Paper / Live Drift view` | paper-vs-live comparison screen showing promotion baseline, observed drift, evidence refs, and required follow-up decision path | paper-live boundary and comparison semantics exist in `PAPER_CANARY_LIVE_POLICY.md`; promotion review already carries stage boundary logic; evolution decisions and `drift_report` evidence types exist in governance contracts | full drift packet: comparison layout, metric grouping, evidence drawer, drift-threshold copy, and escalation or review CTA wording | no | requires a dedicated paper-live drift BFF surface with baseline snapshot, live observations, threshold evaluation, evidence refs, and backend-shaped follow-up actions; current policy and decision schemas are not a front-end packet by themselves | Wave 2 — fifth |
| `OC-06 Deployment Review Console` | deployment plan review, binding and pool context, runtime binding summary, rollback history, and operator command CTA | `PKT-001` ready packet; `GET /api/v1/operator/deployment-review/{plan_id}`; `POST /api/v1/operator/commands`; published screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task | none for the current packet; preserve the deployment-review framing and backend-shaped `allowedActions` semantics when embedding it inside the larger Operator Console IA | ready | none for the current packet contract; downstream Operator Home summaries should link to this screen instead of duplicating its approval logic | Wave 1 baseline — already packetized |
| `OC-07 Incident Response Console` | incident home, incident detail, and incident action drawer for emergency response | `PKT-002` ready packet family; `GET /api/v1/incidents`; `GET /api/v1/operator/incident-response/{incident_id}`; `GET /api/v1/kill-switch/status`; `POST /api/v1/operator/commands` | none for the current packet family; preserve degraded-state, secondary control path, and command-receipt wording | ready | none for the current packet family; Operator Console shell should inherit, not fork, the existing incident control semantics | Wave 1 baseline — already packetized |
| `OC-08 Post-Incident Review Console` | resolved-incident index, composed post-incident review, postmortem references, and evolution evidence summary | `PKT-003` ready packet; `GET /api/v1/incidents?status=resolved`; `GET /api/v1/operator/post-incident-review/{incident_id}`; `GET /api/v1/postmortems` | none for the current packet; carry forward TL/LN caveat wording and reviewer-role expectations from the W3 packet family | ready | none for the current packet beyond the inherited W3 caveats (`time_range` filters partial, `root_type` no-op) already documented in `PKT-003` | Wave 1 baseline — already packetized |
| `OC-09 Global degradation banner` | shared non-dismissable banner for degraded or unavailable surfaces across all operator screens | `PKT-005` ready substrate; derived from `meta.staleness` and `meta.surfaces.*`; screen spec, BFF contract, example payload, and Lovable packet are already published | none for the current substrate; future screens must inherit the existing banner states and copy rather than invent new degradation variants | ready | no new backend route; every downstream screen must pass through backend-owned `meta` fields exactly as defined in `PKT-005` | Wave 1 baseline — already packetized |
| `OC-10 SSE reconciliation substrate` | shared live-update layer for runtime, incident, and kill-switch state across operator screens | `PKT-005` ready substrate; `GET /api/v1/runtime/{runtime_id}/events/stream`; `GET /api/v1/incidents/stream`; `GET /api/v1/kill-switch/updates`; reconnect and replay semantics are already defined | none for the current substrate; future screens only need subscription-scope and reconciliation-hook wiring in their own packet notes | ready | no new backend route for the current substrate; downstream screens must reuse the canonical replay, heartbeat, and reconnect contract instead of custom polling loops | Wave 1 baseline — already packetized |

Wave framing:

- Wave 1 establishes the ready Operator Console baseline through `OC-06` to `OC-10`: deployment review, incident response, post-incident review, degradation banner, and SSE substrate.
- Wave 2 turns the loose operator shell into a real workbench by defining `OC-01` to `OC-05`: runtime board, health board, alerts rail, operator home dashboard, and paper-live drift.

Wave 2 internal ordering and dependency chain:

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| baseline | `OC-06` to `OC-10` | these ready packets and substrates already define the canonical operator patterns for degraded-state handling, live updates, and action authority | none — all are already packetized via `PKT-001`, `PKT-002`, `PKT-003`, and `PKT-005` |
| 1 | `OC-04 Runtime state board` | runtime-state inventory is the minimum missing operator shell because active incident, health, and drift views all need stable runtime identity and last-known state | inherits SSE substrate and RT/TL primitives from the Wave 1 baseline |
| 2 | `OC-03 Health status board` | health view can reuse runtime inventory plus degradation banner semantics to present control-plane health without inventing new action rules | depends on stable runtime board aggregation and `PKT-005` degradation semantics |
| 3 | `OC-02 Alerts rail` | alerts should be layered after runtime and health taxonomy are defined so severity and routing rules are not invented screen-by-screen | depends on runtime-state vocabulary, health grouping, incident feeds, and governance queue identity |
| 4 | `OC-01 Operator Home dashboard` | home screen should summarize already-defined runtime, health, and alert modules instead of forcing each of those contracts to be invented inside one mega packet | depends on `OC-02`, `OC-03`, and `OC-04` summary contracts |
| 5 | `OC-05 Paper / Live Drift view` | drift review is the farthest-from-ready operator module because it depends on policy thresholds, evidence identity, and a net-new comparison read model, not just shell IA | depends on paper-live boundary semantics plus stable runtime and alert context from the earlier Wave 2 modules |

Recommended wave:

- Operator Console starts in Wave 1 because the core command surfaces and cross-cutting substrates are already packetized.
- Operator Home, alerts, health, runtime board, and paper-live drift stay in Wave 2 because they still need net-new operator-shell contracts or composed views before Lovable handoff is honest.
- no Wave 2 Operator Console module should be handed to Lovable before the corresponding aggregation contract or dedicated BFF surface exists alongside the canonical packet family

## Persona Workbench

Objective: manage personas, their bindings, profiles, consult policy, and operator-facing lifecycle detail.

Primary packetization scope for `PKT-004`:

- `Persona management composed screen` is promoted from the approved APP-002 Wave 4 sidecar into a canonical screen packet.
- `Catalog drilldowns` are grouped into explicit modules instead of a vague catch-all so downstream packet work can inherit named surfaces.
- `Tool profile` and `Consult policy` remain visible as Wave 2 Persona Workbench backlog because they still lack canonical packet language and BFF routes.

Surface inventory and wave ownership:

| Surface or IA scope | Module owner | Existing Pantheon support | Primary wave | Notes |
|---|---|---|---|---|
| `PM-01 Persona Management` composed screen | `Persona management composed screen` | `GET /api/v1/operator/persona-management/{persona_id}`; APP-002 Wave 4 persona-management sidecar | Wave 1 | source-ready packet input; preserve `meta.surfaces.*`, `snapshot`, and backend-shaped `allowedActions` |
| `PS-01 Persona Catalog` + `PS-02 Persona Detail` | `Module A — Persona Drilldowns` | APP-002 remaining catalog sidecar; `/api/v1/personas`; `/api/v1/personas/{persona_id}` | Wave 1 grouping, Wave 2 IA shell | live read routes exist, but the standalone Persona Workbench list/detail shell copy is still missing |
| `PS-03` to `PS-06` sessions, teaching, capability drilldowns | `Module A — Persona Drilldowns` | APP-002 remaining catalog sidecar; `/api/v1/personas/{persona_id}/sessions`; `/api/v1/sessions/{session_id}`; `/api/v1/personas/{persona_id}/teaching`; `/api/v1/personas/{persona_id}/capabilities` | Wave 1 | packet family can inherit these as explicit drilldowns without inventing new BFF routes |
| `CP-01` to `CP-04` capital pool and binding drilldowns | `Module B — Capital / Binding Drilldowns` | APP-002 remaining catalog sidecar; `/api/v1/capital-pools`; `/api/v1/capital-pools/{pool_id}`; `/api/v1/bindings`; `/api/v1/bindings/{binding_id}` | Wave 1 | live read routes exist; selector, drawer, and shell wording are still packet work |
| `DP-01` to `DP-04` deployment / approval drilldowns | `Module C — Deployment / Approval Drilldowns` | APP-002 remaining catalog sidecar; `/api/v1/deployment-plans`; `/api/v1/deployment-plans/{plan_id}`; `/api/v1/approval-decisions`; `/api/v1/approval-decisions/{decision_id}` | Wave 1 shared | shared with Governance packetization; do not fork a Persona-specific approval contract |
| `RT-01` to `RT-04`, `TL-01` to `TL-03`, `LN-01` to `LN-03`, `IN-01` to `IN-05`, `EV-01` to `EV-04` | `Module D`, `Module E`, `Module F` | APP-002 remaining catalog sidecar and current BFF read routes | Wave 2 | read surfaces exist, but Persona Workbench still lacks standalone packet language and shared drilldown shells for these families |
| `Tool profile` | standalone Persona Workbench module | persona registry fields exist, but no canonical BFF route or packet handoff yet | Wave 2 | blocked on a dedicated BFF read surface plus panel contract |
| `Consult policy` | standalone Persona Workbench module | consult-policy model exists in policy docs, but no canonical BFF route or packet handoff yet | Wave 2 | blocked on a dedicated BFF read surface plus panel contract |

Separation rule for this backlog:

- put missing list/detail shell copy, drilldown wording, and panel/drawer packet language in `Missing screen-spec work`
- put absent routes, shared cross-workbench contracts, or missing persona-specific composed views in `Backend or contract dependencies`

Canonical packet and module inventory:

| Module | Screen or surface scope | Existing Pantheon support | Missing screen-spec work | Lovable readiness | Backend or contract dependencies | Recommended wave |
|---|---|---|---|---|---|---|
| `Persona management composed screen` | persona lifecycle page with persona summary, bindings, capital pool metadata, sessions, teaching history, and backend-shaped `allowedActions` | `GET /api/v1/operator/persona-management/{persona_id}`; APP-002 Wave 4 persona-management sidecar | none for the composed packet itself; preserve `snapshot` caveat and role-gating notes in handoff copy | ready | must carry forward `meta.surfaces.*` degraded-panel semantics and backend-shaped `allowedActions` | Wave 1 |
| `Module A — Persona Drilldowns` | `PS-01` to `PS-06`: persona catalog, persona detail, session list/detail, teaching history, capability snapshot | APP-002 remaining catalog sidecar; `/api/v1/personas`; `/api/v1/personas/{persona_id}`; `/api/v1/personas/{persona_id}/sessions`; `/api/v1/sessions/{session_id}`; `/api/v1/personas/{persona_id}/teaching`; `/api/v1/personas/{persona_id}/capabilities` | persona list shell; persona detail shell packet language; session and capability drilldown copy | partial | standalone Persona Workbench IA still needs explicit list/detail shell contracts even though the underlying read routes are live | Wave 1 for catalog packetization; Wave 2 for full Persona Workbench IA |
| `Module B — Capital / Binding Drilldowns` | `CP-01` to `CP-04`: capital-pool list/detail and binding list/detail | APP-002 remaining catalog sidecar; `/api/v1/capital-pools`; `/api/v1/capital-pools/{pool_id}`; `/api/v1/bindings`; `/api/v1/bindings/{binding_id}` | capital pool binding panel or drawer packet spec | partial | selector and binding-detail contract language still needs canonical packet wording | Wave 1 |
| `Module C — Deployment / Approval Drilldowns` | `DP-01` to `DP-04`: deployment plan and approval decision drilldowns linked from persona or binding journeys | APP-002 remaining catalog sidecar; `/api/v1/deployment-plans`; `/api/v1/deployment-plans/{plan_id}`; `/api/v1/approval-decisions`; `/api/v1/approval-decisions/{decision_id}` | standalone approval-decision drilldown spec | partial, shared | shared with `PKT-001`; do not duplicate the governance packet contract | Wave 1 shared |
| `Module D — Runtime Drilldowns` | `RT-01` to `RT-04`: runtime binding detail, runtime status, rollback history | APP-002 remaining catalog sidecar; `/api/v1/runtime-bindings`; `/api/v1/runtime-bindings/{binding_id}`; `/api/v1/runtimes/{runtime_id}/status`; `/api/v1/runtimes/{runtime_id}/rollbacks` | runtime status or rollback drilldown packet | partial, shared | no Persona-specific composed view yet; remains a drilldown dependency only | Wave 2 |
| `Module E — Telemetry / Lineage Drilldowns` | `TL-01` to `TL-03`, `LN-01` to `LN-03`: telemetry list/summary/performance and lineage edge/graph drilldowns | APP-002 remaining catalog sidecar; `/api/v1/telemetry`; `/api/v1/telemetry/{runtime_id}/summary`; `/api/v1/telemetry/{artifact_id}/performance`; `/api/v1/lineage`; `/api/v1/lineage/edges/{edge_id}`; `/api/v1/lineage/graph` | telemetry timeline drilldown spec; lineage graph drilldown spec | partial, shared | telemetry `time_range` filters remain deferred; lineage `root_type` is still a no-op | Wave 2 |
| `Module F — Incident / Evolution Drilldowns` | `IN-01` to `IN-05`, `EV-01` to `EV-04`: incident, postmortem, kill-switch, evolution decision, freeze-order, and rollback drilldowns | APP-002 remaining catalog sidecar; `/api/v1/incidents`; `/api/v1/incidents/{incident_id}`; `/api/v1/postmortems`; `/api/v1/postmortems/{report_id}`; `/api/v1/kill-switch/status`; `/api/v1/evolution-decisions`; `/api/v1/evolution-decisions/{decision_id}`; `/api/v1/freeze-orders`; `/api/v1/rollbacks` | kill-switch status drilldown spec; evolution decision drilldown spec | partial, shared | shared with `PKT-002` and `PKT-003`; mutation actions remain outside Persona scope | Wave 2 |
| `Tool profile` | standalone tool-profile panel for a persona | persona registry fields exist, but no canonical BFF read route or packet handoff yet | full screen packet language | no | requires a canonical BFF route before Lovable handoff | Wave 2 |
| `Consult policy` | standalone consult-policy panel for a persona | consult-policy model exists in policy docs and contracts, but no Persona Workbench packet or BFF route yet | full screen packet language | no | requires a canonical BFF route and panel contract | Wave 2 |

Wave framing:

- Wave 1 delivery for `PKT-004`: persona management composed screen, Persona Drilldowns, Capital / Binding Drilldowns, and the shared Deployment / Approval drilldown references.
- Wave 2 follow-up for `WB-002`: standalone persona list and detail IA, Tool profile, Consult policy, plus the shared Runtime, Telemetry / Lineage, and Incident / Evolution drilldowns.

Wave boundary detail:

| Wave | Included Persona Workbench scope | Why it stays in that wave | Open dependency type |
|---|---|---|---|
| Wave 1 | `PM-01`; `PS-01` to `PS-06`; `CP-01` to `CP-04`; shared `DP-01` to `DP-04` references | all required read routes already exist, or the composed packet input is approved through APP-002 Wave 4 evidence | mostly screen-spec and packet-language work, not net-new BFF routes |
| Wave 2 | standalone persona list/detail IA, `Tool profile`, `Consult policy`, shared `RT-*`, `TL-*`, `LN-*`, `IN-*`, `EV-*` drilldowns | these surfaces need either Persona-specific shells, new packet families, or fresh dedicated routes before Lovable handoff is honest | mixed: new BFF routes for `Tool profile` and `Consult policy`; shared contract and IA work for the rest |

## Research Workbench

Objective: provide research search, experiment launch, analysis, and artifact comparison workflows in the console.

Screens and modules:

- `Search`
- `Analyze`
- `Research Ticket`
- `Experiment Launch`
- `Artifact Compare`

Existing Pantheon support:

- blueprint-level workbench definition only
- no Pantheon-side canonical packet family in current planning artifacts
- front-end direction exists in `front-ai-trading-system` (research-related pages may be present), but none of this work is packet-ready; no canonical BFF contract backs any research screen today

Missing canonical screen specs:

- all Research Workbench screens

Lovable readiness:

- not ready — all five modules lack a canonical screen spec and a backed BFF route; the front repo may have directional scaffolding but it cannot be treated as authoritative until canonical packet families exist

Backend dependencies and packetization prerequisites per module:

### Research Ticket

- backend gap: no `POST /api/v1/research/tickets` create route or `GET /api/v1/research/tickets/{id}` read route in current BFF
- packetization prerequisite: ticket lifecycle states (open, in-progress, closed, archived) must be defined before the detail and list screens can be spec'd; this is the foundation that Search and Analyze build on

### Search

- backend gap: no `GET /api/v1/research/search` route or search index adapter in current BFF
- packetization prerequisite: define query parameters, result shape, and filter semantics before Lovable can render a real search surface; depends on Research Ticket read model for corpus identity

### Analyze

- backend gap: no analysis result or metric aggregation endpoint in current BFF
- packetization prerequisite: research-result payload contract must be agreed before an analysis view can be packet-defined; depends on the research ticket read model

### Experiment Launch

- backend gap: no launch-request route, no experiment state machine, and no run status read model in current BFF
- packetization prerequisite: launch request contract must define input parameters, validation rules, and async status polling; depends on Research Ticket for lineage tracking

### Artifact Compare

- backend gap: no artifact registry read model and no diff or comparison surface in current BFF
- packetization prerequisite: artifact identity and versioning semantics must be locked before a compare view can be packet-defined; depends on Experiment Launch producing versioned artifact references

Canonical module inventory:

| Module | Screen or surface scope | Existing Pantheon support | Missing screen-spec work | Lovable readiness | Backend or contract dependencies | Recommended wave |
|---|---|---|---|---|---|---|
| `Research Ticket` | ticket list, ticket detail, lifecycle state machine (open → in-progress → closed → archived) | blueprint-level only; no canonical BFF route | full screen packet family: list shell, detail shell, lifecycle action spec | no | `POST /api/v1/research/tickets`; `GET /api/v1/research/tickets/{id}`; lifecycle state transitions | Wave 3 — first |
| `Search` | query input, result list, filter rail, result drilldown | blueprint-level only; no BFF search route | full screen packet: query parameters, result shape, filter semantics | no | `GET /api/v1/research/search`; search index adapter; depends on Research Ticket read model for corpus identity | Wave 3 — second |
| `Analyze` | analysis result view, metric aggregation panel, comparative summary | blueprint-level only; no analysis endpoint | full screen packet: analysis payload contract, metric display spec | no | metric aggregation and analysis result endpoint; depends on Research Ticket read model | Wave 3 — third |
| `Experiment Launch` | launch request form, parameter inputs, async run status, run history | blueprint-level only; no experiment state machine or launch route | full screen packet: launch request contract, async polling contract, run status display | no | `POST /api/v1/experiments/launch`; run status read model; experiment state machine; depends on Research Ticket for lineage tracking | Wave 3 — fourth |
| `Artifact Compare` | artifact selector, version diff view, side-by-side comparison surface | blueprint-level only; no artifact registry or diff surface | full screen packet: artifact identity and versioning semantics, diff display contract | no | artifact registry read model; diff or comparison surface; depends on Experiment Launch producing versioned artifact refs | Wave 3 — fifth |

Wave 3 internal ordering and dependency chain:

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| 1 | Research Ticket | foundational entity that all other modules reference for lineage, corpus, and history | none — can start as soon as Wave 3 opens |
| 2 | Search | queries the research ticket corpus and surfaces ticket-linked results | Research Ticket read model and `GET /api/v1/research/tickets/{id}` |
| 3 | Analyze | aggregates ticket and experiment result data; needs ticket identity for scoping | Research Ticket read model |
| 4 | Experiment Launch | tracks lineage back to the originating ticket; run status polling is a blocking UX gate | Research Ticket lineage; Analyze result contract as input context |
| 5 | Artifact Compare | compares versioned artifacts produced by experiments; requires stable artifact identity from Launch | Experiment Launch versioned artifact refs |

Separation rule for this backlog:

- put missing list and detail shell copy, packet language, and form or display spec work in `Missing screen-spec work`
- put absent BFF routes, missing state machines, or unresolved read models in `Backend or contract dependencies`

Recommended wave:

- Wave 3 after closed-loop infra and operator/persona packetization are settled
- internal module order within Wave 3: Research Ticket → Search → Analyze → Experiment Launch → Artifact Compare
- no Research Workbench module should be handed to Lovable before the corresponding BFF route and canonical packet family exist

## Knowledge Workbench

Objective: expose institutional memory, evidence, strategy specs, and durable research notes as navigable, queryable product surfaces backed by canonical BFF routes.

Screens and modules:

- `Institutional memory`
- `Research notes`
- `Evidence refs`
- `Insight cards`
- `Strategy spec`

Existing Pantheon support:

- blueprint-level workbench definition only
- no Pantheon-side canonical packet family in current planning artifacts
- no BFF route backs any Knowledge Workbench screen today; the knowledge domain is entirely pre-route

Missing canonical screen specs:

- all Knowledge Workbench screens

Lovable readiness:

- not ready — all five modules lack a canonical screen spec and a backed BFF route; the front repo may have directional scaffolding but it cannot be treated as authoritative until canonical packet families exist

Backend dependencies and packetization prerequisites per module:

### Institutional Memory

- backend gap: no `GET /api/v1/knowledge/memory` list route or `GET /api/v1/knowledge/memory/{entry_id}` detail route in current BFF; no durable knowledge-store read model
- packetization prerequisite: the memory entry lifecycle (draft, active, archived) and the identity schema (entry type, tags, linked artifacts) must be defined before any other Knowledge Workbench module can reference institutional knowledge; this is the foundation of the entire workbench

### Research Notes

- backend gap: no `POST /api/v1/knowledge/notes` create route or `GET /api/v1/knowledge/notes/{note_id}` read route in current BFF
- packetization prerequisite: note ownership and attachment semantics must be locked (which entity a note is attached to — a research ticket, a persona, a strategy spec, or a free-standing record) before the list and detail shells can be spec'd; depends on the Institutional Memory identity schema for linked-entry references

### Evidence Refs

- backend gap: no `GET /api/v1/knowledge/evidence` list route or `GET /api/v1/knowledge/evidence/{ref_id}` detail route in current BFF; no evidence-reference read model linking decisions or artifacts to source documents
- packetization prerequisite: the evidence reference shape (source document identity, link type, linked decision or artifact, credibility metadata) must be agreed before a browse or detail view can be packet-defined; depends on Institutional Memory for the anchor entity and on Research Notes for source-document context

### Insight Cards

- backend gap: no insight aggregation endpoint and no card-surface read model in current BFF; insight cards require a synthesis layer that pulls from memory, notes, and evidence
- packetization prerequisite: insight card identity, display contract (card header, body, linked sources), and filter semantics (by tag, linked entity, recency) must be locked before Lovable can render a real card surface; depends on Institutional Memory and Evidence Refs as source inputs to the aggregation

### Strategy Spec

- backend gap: no strategy-spec list route or versioned spec detail route in current BFF; no versioning or diff semantics defined for strategy documents
- packetization prerequisite: spec lifecycle states (draft, approved, deprecated) and versioning semantics must be established before a viewer or comparison surface can be packet-defined; depends on Institutional Memory for lineage and Evidence Refs for backing citations

Canonical module inventory:

| Module | Screen or surface scope | Existing Pantheon support | Missing screen-spec work | Lovable readiness | Backend or contract dependencies | Recommended wave |
|---|---|---|---|---|---|---|
| `Institutional Memory` | memory entry list, entry detail, lifecycle state (draft, active, archived), tag and type filters | blueprint-level only; no canonical BFF route | full screen packet family: list shell, detail shell, lifecycle state machine, filter spec | no | `GET /api/v1/knowledge/memory`; `GET /api/v1/knowledge/memory/{entry_id}`; entry lifecycle transitions; identity schema (entry type, tags, linked artifacts) | Wave 3 — first |
| `Research Notes` | note list, note detail, attach-to-entity selector, ownership view | blueprint-level only; no BFF note route | full screen packet: note ownership and attachment semantics, list shell, detail shell | no | `POST /api/v1/knowledge/notes`; `GET /api/v1/knowledge/notes/{note_id}`; attachment semantics; depends on Institutional Memory identity schema | Wave 3 — second |
| `Evidence Refs` | evidence reference list, reference detail, linked-decision panel, source-document link | blueprint-level only; no BFF evidence route | full screen packet: evidence reference shape, link type taxonomy, credibility metadata display | no | `GET /api/v1/knowledge/evidence`; `GET /api/v1/knowledge/evidence/{ref_id}`; depends on Institutional Memory for anchor entity and Research Notes for source context | Wave 3 — third |
| `Insight Cards` | browsable card grid, card detail panel, filter rail (tag, entity, recency), linked-source drilldown | blueprint-level only; no insight aggregation endpoint | full screen packet: card identity, display contract (header, body, linked sources), filter semantics | no | insight aggregation endpoint; card-surface read model; depends on Institutional Memory and Evidence Refs as aggregation inputs | Wave 3 — fourth |
| `Strategy Spec` | spec list, versioned spec viewer, lifecycle state (draft, approved, deprecated), evidence citation panel | blueprint-level only; no strategy-spec route or versioning semantics | full screen packet: spec lifecycle, versioning semantics, citation display, diff or compare surface | no | strategy-spec list and versioned detail routes; depends on Institutional Memory for lineage and Evidence Refs for backing citations | Wave 3 — fifth |

Wave 3 internal ordering and dependency chain:

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| 1 | Institutional Memory | foundational identity store that all other Knowledge Workbench modules reference for anchoring, lineage, and linked-entity resolution | none — can start as soon as Wave 3 opens |
| 2 | Research Notes | needs the Institutional Memory identity schema to define what an attached-entity reference means | Institutional Memory identity schema and `GET /api/v1/knowledge/memory/{entry_id}` |
| 3 | Evidence Refs | references both memory entries and research notes as source context; link type taxonomy builds on both | Institutional Memory anchor entity; Research Notes source-document context |
| 4 | Insight Cards | synthesis layer that aggregates memory, notes, and evidence into browsable cards; cannot define filter or display semantics until the input read models are stable | Institutional Memory and Evidence Refs as aggregation inputs |
| 5 | Strategy Spec | formal specification viewer that cites evidence and traces lineage through memory entries; versioning and diff semantics build on the full knowledge graph | Institutional Memory for lineage; Evidence Refs for backing citations |

Separation rule for this backlog:

- put missing list and detail shell copy, packet language, filter spec, and card or diff display work in `Missing screen-spec work`
- put absent BFF routes, missing read models, unresolved lifecycle state machines, or unresolved versioning semantics in `Backend or contract dependencies`

Recommended wave:

- Wave 3 after closed-loop infra and operator/persona packetization are settled
- internal module order within Wave 3: Institutional Memory → Research Notes → Evidence Refs → Insight Cards → Strategy Spec
- no Knowledge Workbench module should be handed to Lovable before the corresponding BFF route and canonical packet family exist

## Trainer Workbench

Objective: turn the current demo trainer direction into a real BFF-backed teaching workflow.

Screens and modules:

- `Teaching dialog`
- `Parameter controls`
- `Before/after compare`
- `Teaching replay`

Existing Pantheon support:

- blueprint-level workbench definition plus a demo-grade Trainer shell with preview or backtest refresh direction
- read-only teaching-session history already exists inside Persona surfaces via `GET /api/v1/operator/persona-management/{persona_id}` and `GET /api/v1/personas/{persona_id}/teaching`
- no dedicated Trainer Workbench BFF route or mutation flow exists yet; current support is evidence or drilldown only, not a standalone Trainer packet family

Missing canonical screen specs:

- all four standalone Trainer Workbench modules still need canonical packet language
- existing teaching-history read surfaces are evidence inputs only; they do not replace a Trainer-specific screen family

Lovable readiness:

- not ready — the current shell is demo-grade and the only live support is read-only teaching history under Persona Management, not a Trainer-owned workflow

Backend dependencies and packetization prerequisites per module:

### Teaching dialog

- backend gap: no canonical BFF route today for `POST /api/v1/trainer/sessions`, `GET /api/v1/trainer/sessions/:id`, or `POST /api/v1/trainer/sessions/:id/message`; these only exist in the L3 service-contract design
- packetization prerequisite: lock the trainer-session lifecycle, transcript or event-log contract, and session header fields before a dialog shell can be packet-defined; depends on persona identity and `session_type=trainer` staying canonical

### Parameter controls

- backend gap: no canonical BFF route today for `POST /api/v1/trainer/sessions/:id/patch`, and no stable read model exposes the mutable control-state schema or allowed parameter ranges
- packetization prerequisite: define the control-patch payload, validation or warning contract, and control-state diff semantics before a parameter panel can be packet-defined; depends on Teaching dialog establishing the active session context

### Before/after compare

- backend gap: the current Trainer page only has a demo preview or backtest refresh skeleton; there is no canonical BFF compare route or preview result payload even though the design docs name preview, rapid-eval, and before/after compare responsibilities
- packetization prerequisite: lock the preview or rapid-eval response contract, metric taxonomy, warning shape, and degraded `preview_unavailable` semantics before a compare surface can be packet-defined; depends on Parameter controls producing a patchable candidate state

### Teaching replay

- backend gap: Persona surfaces expose only a teaching-session list; there is no standalone Trainer replay screen, no canonical event-stream read route, and no BFF-level commit, discard, or replay contract
- packetization prerequisite: lock the append-only `TeachingEvent` schema, event ordering and replay cursor semantics, and before/after artifact refs before a replay surface can be packet-defined; depends on Teaching dialog and Before/after compare producing stable session evidence

Canonical module inventory:

| Module | Screen or surface scope | Existing Pantheon support | Missing screen-spec work | Lovable readiness | Backend or contract dependencies | Recommended wave |
|---|---|---|---|---|---|---|
| `Teaching dialog` | start session, show transcript, send coaching messages, display session status and actor context | blueprint workflow plus demo-grade Trainer shell; read-only teaching-session lists exist in Persona Management and `PS-05`, but no Trainer-owned dialog route exists | full dialog packet: session shell, transcript layout, event composer, status copy | no | `POST /api/v1/trainer/sessions`; `GET /api/v1/trainer/sessions/:id`; `POST /api/v1/trainer/sessions/:id/message`; trainer-session lifecycle and transcript contract | Wave 3 — first |
| `Parameter controls` | inspect current control state, edit control patches, surface validation or warning feedback | demo-grade control editing direction only; no canonical BFF route or control-state contract | full control-panel packet: editable fields, patch confirmation, validation or warning states | no | `POST /api/v1/trainer/sessions/:id/patch`; control-state schema; allowed-range and validation contract | Wave 3 — second |
| `Before/after compare` | preview metrics, warnings, control-state diff, and rapid-eval result summary | demo preview or backtest refresh skeleton only; no canonical compare payload | full compare packet: metric panels, diff layout, warning hierarchy, degraded preview copy | no | preview or rapid-eval route; before/after compare payload; `preview_unavailable` degraded contract | Wave 3 — third |
| `Teaching replay` | teaching-session history, ordered event replay, commit or discard evidence, and replay entrypoint | read-only teaching-session list via Persona Management and `PS-05`; no standalone Trainer replay screen or event-detail route | full replay packet: history shell, event timeline, evidence drawer, replay action copy | no | canonical event-stream read route; commit/discard/replay contract; append-only `TeachingEvent` schema and before/after artifact refs | Wave 3 — fourth |

Wave 3 internal ordering and dependency chain:

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| 1 | Teaching dialog | establishes the trainer-session entity, transcript shell, and session identity that all later modules rely on | none — can start when Wave 3 opens |
| 2 | Parameter controls | patches only make sense after a concrete teaching session and current control state exist | Teaching dialog session context and lifecycle contract |
| 3 | Before/after compare | preview and rapid-eval outputs must compare a candidate patch against a known session state | Parameter controls patch payload and Teaching dialog session state |
| 4 | Teaching replay | replay is only honest once session events, compare artifacts, and commit or discard evidence are stable and addressable | Teaching dialog transcript events plus Before/after compare evidence refs |

Wave boundary detail:

| Wave | Included Trainer Workbench scope | Why it stays in that wave | Open dependency type |
|---|---|---|---|
| Pre-Wave 3 evidence only | read-only teaching-session history inside Persona Management and `PS-05` | these surfaces prove teaching data exists, but they are Persona drilldowns rather than a Trainer-owned workflow | read-only evidence only; no standalone Trainer IA or mutation routes |
| Wave 3 | `Teaching dialog`, `Parameter controls`, `Before/after compare`, `Teaching replay` | the full Trainer Workbench needs session mutation, preview, compare, and replay contracts to exist together before packetization or Lovable handoff is honest | mostly net-new BFF routes and payload contracts, not just screen copy |

Separation rule for this backlog:

- put dialog shell copy, control widgets, compare layouts, replay timeline copy, and degraded-state wording in `Missing screen-spec work`
- put absent trainer mutation routes, preview or rapid-eval contracts, replay ordering semantics, and before/after artifact refs in `Backend or contract dependencies`

Recommended wave:

- Wave 3 remains the conservative placement even though partial read-only teaching history already exists in Persona surfaces
- no Trainer Workbench module should be handed to Lovable before the session-mutation, compare, and replay contracts stop being L3 design intent and become canonical BFF-facing truth

## Consultation Workbench

Objective: support consult requests, committees, debate transcripts, and red-team review as first-class product surfaces.

Screens and modules:

- `Consult request`
- `Committee board`
- `Debate transcript`
- `Red-team memo`

Existing Pantheon support:

- `PERSONA_RUNTIME_MODEL.md` and `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md` already define consult policy, `consult`, `committee`, and `red_team` session types, committee escalation conditions, and committee outputs
- `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` defines six canonical read surfaces (`CS-01` to `CS-06`) for consultation list, detail, participants, outcome, evidence, and consult policy, including degraded behavior
- `Pantheon_總索引版系統分析文件.md`, `Pantheon_API_Service_Contract_設計版.md`, and `Pantheon_資料表_Schema_設計版.md` already name `ConsultRequest` or `ConsultMemo`, `POST /api/v1/consult/requests`, `GET /api/v1/consult/requests/:id`, and memo lifecycle semantics as design intent
- no standalone Consultation Workbench packet family, no transcript or event-stream read surface, and no implemented BFF handlers or composed workbench IA exist today

Missing canonical screen specs:

- all four Consultation Workbench modules still need canonical packet language
- the existing consultation surface contract is read-surface oriented and does not define a workbench IA, request composer, transcript layout, committee-board shell, or red-team memo presentation family

Lovable readiness:

- not ready — consultation object semantics and read contracts exist, but the workbench still lacks canonical screen packets and command-side or transcript truth for end-to-end flows

Backend dependencies and packetization prerequisites per module:

### Consult request

- backend gap: the current canonical consultation contract is GET-only and session-backed; it does not define the canonical write path that turns a request into a consultation session. `POST /api/v1/consult/requests` and `GET /api/v1/consult/requests/:id` only exist in L3 design docs today
- packetization prerequisite: lock the `ConsultRequest` lifecycle (`created` -> `running` -> `completed` or `canceled`), target taxonomy (`persona` or `committee` or `red_team`), and the request-to-session handoff contract before a request composer or request detail screen can be packet-defined; depends on `ConsultPolicy` and persona session creation remaining canonical

### Debate transcript

- backend gap: current consultation read surfaces expose participants, outcome, and evidence, but no canonical transcript or ordered event-stream route exists for consult or committee conversations
- packetization prerequisite: define the append-only transcript schema, actor labeling, event ordering, evidence attachment inline behavior, and degraded partial-transcript semantics before a transcript screen can be packet-defined; depends on stable consultation session identity from Consult request

### Committee board

- backend gap: committee escalation conditions and outputs are defined in policy, but no canonical board projection exists for committee membership, escalation reason, sponsor selection, synthesis rule, quorum or consensus state, or linked `committee_ref`
- packetization prerequisite: lock committee lifecycle states, participant or referral semantics, and how committee outputs reference conflict-resolution evidence before a board screen can be packet-defined; depends on Consult request identity and Debate transcript event ordering

### Red-team memo

- backend gap: `red_team` exists as a session type and `ConsultMemo` design supports `red_team_findings`, but there is no canonical red-team memo list/detail route or read model in current L1 or L2 truth
- packetization prerequisite: define how a red-team session produces a published memo, finding severity taxonomy, recommendation shape, and evidence-link contract before a memo screen can be packet-defined; depends on Consult request identity and Debate transcript or session evidence semantics

Canonical module inventory:

| Module | Screen or surface scope | Existing Pantheon support | Missing screen-spec work | Lovable readiness | Backend or contract dependencies | Recommended wave |
|---|---|---|---|---|---|---|
| `Consult request` | request composer, request detail, target selector, lifecycle state, and request-to-session status | `ConsultPolicy` is canonical in `PERSONA_RUNTIME_MODEL.md`; `ConsultRequest` object, status model, and `POST /api/v1/consult/requests` plus `GET /api/v1/consult/requests/:id` exist only in L3 design docs | full request packet family: composer shell, target-selection copy, request detail layout, lifecycle and escalation copy | no | canonical write route plus request-to-session handoff; `ConsultRequest` lifecycle and target taxonomy must be promoted beyond L3 design intent | Wave 4 — first |
| `Debate transcript` | ordered conversation timeline, actor badges, inline evidence links, transcript replay, and degraded partial-state handling | consultation detail, participants, outcome, and evidence are defined as read contracts in `CONSULTATION_SURFACE_CONTRACT.md`, but no transcript route or event schema exists | full transcript packet: timeline layout, event grouping, inline evidence affordances, partial or stale transcript copy | no | transcript or event-stream route; append-only event schema; actor labeling and ordering semantics for consult or committee sessions | Wave 4 — second |
| `Committee board` | committee queue or board view, participant roster, escalation reason, sponsor decision, synthesis summary, and linked evidence | `committee` session type and escalation outcomes are canonical in `PERSONA_RUNTIME_MODEL.md` and `MULTI_PERSONA_AGGREGATION_AND_CONFLICT_RESOLUTION.md`; consultation participants or outcome surfaces exist at contract level | full board packet: quorum or consensus state, escalation badges, sponsor or synthesis summary, linked evidence and referral copy | no | canonical board projection for committee membership, referral state, `committee_ref`, sponsor selection, and conflict-resolution evidence linkage | Wave 4 — third |
| `Red-team memo` | findings summary, recommendation list, publish state, evidence drawer, and downstream review handoff | `red_team` is a canonical session type; `ConsultMemo` L3 design includes `memo_type = red_team_findings`, but no BFF-facing red-team memo route exists | full memo packet: findings layout, severity taxonomy, recommendation copy, publish-state and evidence-drawer semantics | no | canonical red-team memo list or detail read model; published memo lifecycle and evidence-link contract; red-team session-to-memo mapping | Wave 4 — fourth |

Wave 4 internal ordering and dependency chain:

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| 1 | Consult request | establishes the foundational request object, target taxonomy, and request-to-session handoff that every later Consultation module references | none — can start when Wave 4 opens |
| 2 | Debate transcript | defines the ordered conversation evidence model that both committee and red-team views rely on | Consult request identity and stable session creation semantics |
| 3 | Committee board | adds policy-driven committee state and synthesis outputs on top of the request and transcript evidence chain | Consult request identity plus Debate transcript event ordering |
| 4 | Red-team memo | publishes finalized adversarial findings against the same request and evidence chain, after request and transcript semantics stop shifting | Consult request identity plus Debate transcript or session evidence semantics |

Separation rule for this backlog:

- put request-composer copy, transcript layouts, board widgets, memo presentation, and degraded-state wording in `Missing screen-spec work`
- put absent write routes, transcript or event-stream contracts, committee board projections, and red-team memo read models in `Backend or contract dependencies`

Recommended wave:

- Wave 4 remains the conservative placement even though consultation read contracts now exist, because the workbench still lacks canonical request-write truth, transcript ordering truth, committee board projection, and red-team memo read models
- internal module order within Wave 4: Consult request -> Debate transcript -> Committee board -> Red-team memo
- no Consultation Workbench module should be handed to Lovable before the corresponding command or transcript contract and canonical packet family exist together

## Governance Workbench

Objective: give governance operators queue-based review, approval, promotion, rollback, and deployment diff control across all persona deployment and policy decisions.

Screens and modules:

- `GV-01 Review queue`
- `GV-02 Approval queue`
- `GV-03 Promotion Review` (`F-042`)
- `GV-04 Deployment diff`
- `GV-05 Rollback review`
- `GV-06 Governance audit rail`

Existing Pantheon support:

- `PKT-001 Governance Review Queue` — ready governance packet with screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task already published
- `F-042 Promotion Review` — ready single-screen packet inside the Governance Workbench; screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task are already published
- `PKT-001` governance and deployment review packet family — formalizes `F-042` as one Governance Workbench screen, marks `GV-01 Review queue` as ready, and preserves blocked requirements for `GV-02`, `GV-04`, `GV-05`, and `GV-06`
- `PAPER_CANARY_LIVE_POLICY.md` — promotion stage thresholds and paper-live boundary conditions that back promotion review accept/reject logic
- `ROLLBACK_AND_POSITION_SEMANTICS.md` — rollback action semantics and position handling that define what a rollback review surface must expose before a governance operator can safely approve a rollback
- operator acceptance matrix — defines path semantics, CTA visibility conditions, and degraded behavior for governance and deployment surfaces; backs `allowedActions` shape for promotion and approval surfaces
- `/api/v1/deployment-plans`, `/api/v1/deployment-plans/{plan_id}`, `/api/v1/approval-decisions`, `/api/v1/approval-decisions/{decision_id}` — live read routes already referenced in the Persona Workbench module table; shared with Governance packetization

Missing canonical screen specs:

- `GV-02 Approval queue` — no canonical approval queue shell, decision CTA copy, or `allowedActions` extension beyond the `F-042` precedent
- `GV-04 Deployment diff` — no canonical diff view packet; layout schema, hunk presentation, and approval gating copy are entirely undefined
- `GV-05 Rollback review` — no rollback review screen packet; what the reviewer must confirm and what fields the BFF must supply before approval are not yet specified
- `GV-06 Governance audit rail` — no canonical audit trail packet, entry shape, actor labeling, or filtering affordance

Lovable readiness:

- partial — `GV-01 Review queue` and `GV-03 Promotion Review` already have contract-ready packets and Lovable UI tasks; every remaining module still needs a canonical packet plus BFF backing before handoff

Backend dependencies and packetization prerequisites per module:

### GV-01 Review queue

- backend support: `GET /api/v1/operator/governance/review-queue` already provides the canonical queue projection, filter semantics, pagination contract, backend-shaped `allowedActions`, and detail-drawer payload defined by `PKT-001 Governance Review Queue`
- packetization status: complete for the current queue packet; screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task are already published, so `GV-01` now serves as the queue baseline that later Governance modules inherit

### GV-02 Approval queue

- backend gap: `/api/v1/approval-decisions` provides decision-level read access but does not define a queue projection with `pending` filter, operator assignment, or `allowedActions` extension for bulk or staged approval workflows
- packetization prerequisite: extend the `approval-decisions` contract to expose a queue view with `pending` filtering, `allowedActions.canApprove` or `canReject` authority signals, and a decision-update write path before the approval queue shell can be packet-defined; the `F-042` precedent for `allowedActions.canPromoteToPaper` is the closest existing model

### GV-03 Promotion Review (`F-042`)

- backend support: `GET /api/v1/operator/deployment-review/{plan_id}` and `POST /api/v1/operator/commands` are already defined in the published `F-042` contract, and the shipped example payload includes `allowedActions.canPromoteToPaper`
- packetization status: complete for the current promotion screen; `PKT-001` formally reclassifies `F-042` inside Governance Workbench, and the ready packet now serves as the `allowedActions` and governance-outcome template for later Governance modules

### GV-04 Deployment diff

- backend gap: no canonical diff read model exists; the deployment plan detail route supplies the plan payload but not a structured diff against the previous deployment or a human-readable change summary
- packetization prerequisite: define the diff data shape (old/new field pairs, semantic change labels, risk tier annotation, and per-field change reason), degraded behavior when the previous plan is unavailable, and gating conditions for approval CTA display before a diff screen can be packet-defined; depends on stable deployment plan identity from `GV-01` or `GV-02` queue context

### GV-05 Rollback review

- backend gap: `ROLLBACK_AND_POSITION_SEMANTICS.md` defines the semantic model, but no BFF rollback review read surface exists; the operator needs the position impact summary, affected personas, trigger reason, and rollback approval authority before the review can be safely presented
- packetization prerequisite: lock the rollback review data shape (rollback scope, position impact summary, affected bindings, trigger reason, and `allowedActions.canApproveRollback`), the write path for rollback approval, and degraded behavior when position data is stale before a rollback review screen can be packet-defined; depends on `GV-01` queue context and stable deployment plan identity

### GV-06 Governance audit rail

- backend gap: no canonical governance audit trail route or read model exists; audit semantics are implied by operator acceptance matrix and deployment review policy but not surfaced as a filterable, paginated BFF endpoint
- packetization prerequisite: define the audit entry schema (actor, action type, target resource and ID, timestamp, outcome, and optional evidence ref), filter contract (by actor, action type, target, and date range), and degraded presentation when audit data is delayed before the audit rail can be packet-defined; can be developed in parallel with other Governance modules once the audit schema is locked

Canonical module inventory:

| Module | Screen or surface scope | Existing Pantheon support | Missing screen-spec work | Lovable readiness | Backend or contract dependencies | Recommended wave |
|---|---|---|---|---|---|---|
| `GV-01 Review queue` | unified pending-items queue across deployment plans, approval decisions, and rollback requests; filter bar and queue pagination | `PKT-001 Governance Review Queue` ready packet; `GET /api/v1/operator/governance/review-queue`; published screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task | none for the current queue packet; treat it as the baseline queue pattern for later Governance modules | ready | none for the current packet; downstream modules inherit the queue item model, pagination, and backend-shaped `allowedActions` pattern | Wave 2 baseline — already packetized by `PKT-001` |
| `GV-02 Approval queue` | pending approval decisions with `allowedActions` CTA extensions, decision confirmation drawer, and bulk approval affordance | `/api/v1/approval-decisions` and `/api/v1/approval-decisions/{decision_id}` live routes; `F-042` precedent for `allowedActions` shape | full approval queue packet: pending-filter query, `allowedActions.canApprove` and `canReject` shape, confirmation drawer copy, and decision write path | no | `allowedActions` extension for bulk or staged approval; decision-update write path via approval-decisions endpoint | Wave 2 — second |
| `GV-03 Promotion Review` | promotion stage display, paper-live boundary copy, accept/reject CTA, `allowedActions.canPromoteToPaper` gate | `F-042` ready packet; published screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task; `PKT-001` packet family formally places it inside Governance Workbench | none for the current promotion screen; preserve the Governance Workbench framing and backend-owned authority semantics when reusing it as the model for later modules | ready | none for the current packet contract; later queue and rollback screens should reuse its backend-shaped `allowedActions` and governance outcome semantics | Wave 1 baseline — already dispatched |
| `GV-04 Deployment diff` | side-by-side or sequential field diff, semantic change labels, risk tier annotation, per-field reason, and approval gating | deployment plan detail route exists; no structured diff model | full diff packet: diff data shape, hunk layout, semantic label copy, risk tier display, and degraded-state handling when previous plan is unavailable | no | canonical diff read model against previous deployment plan; risk tier annotation and gating semantics | Wave 2 — third |
| `GV-05 Rollback review` | rollback scope summary, position impact table, affected bindings, trigger reason, approval CTA with `allowedActions.canApproveRollback` | `ROLLBACK_AND_POSITION_SEMANTICS.md` defines semantic model; no BFF rollback review surface | full rollback review packet: rollback data shape, position impact layout, trigger reason copy, approval CTA, and degraded-state copy for stale position data | no | BFF rollback review read surface; position impact summary; `allowedActions.canApproveRollback` authority signal and rollback approval write path | Wave 2 — fourth |
| `GV-06 Governance audit rail` | chronological audit trail of governance actions; filter by actor, action type, target, and date range; evidence drawer | audit semantics implied by operator acceptance matrix and deployment policy; no canonical BFF audit endpoint | full audit rail packet: entry schema, filter contract, actor and action labeling, evidence drawer, and degraded-state copy when audit data is delayed | no | canonical governance audit trail BFF endpoint; audit entry schema with actor, action type, target, timestamp, and outcome | Wave 2 — parallel with GV-04 and GV-05 once audit schema is locked |

Wave 2 internal ordering and dependency chain:

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| baseline | GV-01 Review queue + GV-03 Promotion Review | both screens are already packetized and define the queue model, backend-shaped `allowedActions`, governance outcome semantics, and handoff pattern that the remaining Governance modules should inherit | none — both are ready baselines published through `PKT-001` and `F-042` |
| 1 | GV-02 Approval queue | extends the ready queue baseline with decision-specific CTA authority and write path; should reuse the queue vocabulary from GV-01 and the `allowedActions` style proven by GV-03 | GV-01 queue data shape and GV-03 `allowedActions` precedent |
| 2 | GV-04 Deployment diff | adds diff presentation on top of the deployment plan identity established by the ready queue and approval surfaces | GV-01 queue context and stable deployment plan identity from GV-02 |
| 3 | GV-05 Rollback review | adds rollback approval on top of the position and deployment plan identity established by the queue and diff surfaces; reuses the same backend-owned authority pattern | GV-01 queue context, stable deployment plan identity, and position impact contract |
| parallel | GV-06 Governance audit rail | audit trail is readable independently of the write-path modules; can be developed in parallel with GV-04 and GV-05 once the audit entry schema is locked | none beyond audit entry schema lock |

Separation rule for this backlog:

- put missing queue copy, diff layout, rollback review wording, audit rail presentation, and CTA label work in `Missing screen-spec work`
- put absent unified queue projections, diff read models, rollback BFF surfaces, `allowedActions` authority extensions, and audit trail endpoints in `Backend or contract dependencies`

Recommended wave:

- Governance Workbench stays in Wave 2 because the unfinished modules (`GV-02`, `GV-04`, `GV-05`, `GV-06`) still need net-new BFF surfaces and canonical packets, but it is materially ahead of Research, Knowledge, and Trainer because `GV-01` and `GV-03` are already packetized
- `GV-01 Review queue` and `GV-03 Promotion Review` define the ready baseline for the workbench and should not be regressed back into future missing-spec work
- no Governance Workbench module beyond the ready baseline (`GV-01`, `GV-03`) should be handed to Lovable before the corresponding BFF surface and canonical packet family exist together

## Evolution Workbench

Objective: expose post-incident review, evolution decision review, lineage tracing, inspiration graph, and mutation review as one coherent workbench backed by canonical BFF surfaces and explicit execution authority boundaries.

Screens and modules:

- `EW-01 Post-Incident Review` (cross-listed with `OC-08`; primary definition lives in Operator Console)
- `EW-02 Evolution Center`
- `EW-03 Lineage View`
- `EW-04 Inspiration Graph`
- `EW-05 Mutation Review`

Existing Pantheon support:

- `PKT-003 Post-Incident and Evolution packet family` — three screens are packet-ready: Post-Incident Review Console, Evolution Center, and Lineage View; two screens (Inspiration Graph and Mutation Review) are explicitly blocked with gap requirements
- `APP-002-W3-POSTINCIDENT-EVOLUTION sidecar` — the source sidecar that PKT-003 upgrades into canonical packet families; defines composed views, degraded-panel gating, and W3-inherited read surface caveats
- `GET /api/v1/operator/post-incident-review/{incident_id}` — composed post-incident view with postmortem refs and evolution evidence summary
- `GET /api/v1/evolution-decisions`, `GET /api/v1/evolution-decisions/{decision_id}`, `GET /api/v1/freeze-orders`, `GET /api/v1/rollbacks` — the four EV read surfaces that back Evolution Center
- `GET /api/v1/lineage`, `GET /api/v1/lineage/edges/{edge_id}`, `GET /api/v1/lineage/graph` — the three LN read surfaces that back Lineage View
- `PKT-005 Degradation Banner and SSE Substrate` — cross-cutting substrates inherited by all Evolution Workbench screens; SSE subscription is optional for read-only surfaces but banner inheritance is required

Missing canonical screen specs:

- `EW-04 Inspiration Graph` — blocked on a dedicated BFF inspiration surface (`GET /api/v1/lineage/inspiration/{artifact_id}`); screen spec, BFF contract, and example payload do not yet exist
- `EW-05 Mutation Review` — blocked on EVO-004 execution boundary settlement plus a dedicated BFF mutation review route; all four EVO-004 action paths (freeze, rollback, retrain, redeploy) must be explicitly settled before any mutation surface can be packetized
- final Evolution Workbench IA shell that composes EW-01 through EW-05 into a unified workbench navigation model

Lovable readiness:

- partial — `EW-01 Post-Incident Review Console`, `EW-02 Evolution Center`, and `EW-03 Lineage View` have contract-ready packets and Lovable UI tasks published through `PKT-003`; `EW-04` and `EW-05` are not ready pending BFF surface work and EVO-004 boundary settlement

Backend dependencies and packetization prerequisites per module:

### EW-01 Post-Incident Review

- backend support: `GET /api/v1/incidents?status=resolved`, `GET /api/v1/operator/post-incident-review/{incident_id}`, `GET /api/v1/postmortems` — all live; composed view includes postmortem refs and evolution evidence summary
- packetization status: complete for the current packet via `PKT-003`; screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task are already published; known W3 caveats (`time_range` filter partial, `root_type` no-op) are carried forward
- live-state dependency: `meta.surfaces.*` degraded-panel gating is required; Lovable must pass `meta.staleness` and `meta.surfaces` into the shared degradation banner rather than deriving panel health locally

### EW-02 Evolution Center

- backend support: `GET /api/v1/evolution-decisions` (EV-01), `GET /api/v1/evolution-decisions/{decision_id}` (EV-02), `GET /api/v1/freeze-orders` (EV-03), `GET /api/v1/rollbacks` (EV-04) — all four EV surfaces are implemented; read-only views are non-blocking despite the execution boundary gap
- packetization status: complete for the current packet via `PKT-003`; ready for Lovable; published screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task exist
- live-state dependency: `time_range` filter is ignored on EV-04 (`GET /api/v1/rollbacks`) in v1; document as W3 inherited caveat in handoff copy; SSE subscription is optional for Evolution Center read-only surfaces

### EW-03 Lineage View

- backend support: `GET /api/v1/lineage` (LN-01), `GET /api/v1/lineage/edges/{edge_id}` (LN-02), `GET /api/v1/lineage/graph` (LN-03) — all three LN surfaces are implemented
- packetization status: complete for the current packet via `PKT-003`; ready with the LN-03 caveat that `root_type` is a no-op in v1 and the graph renders by `root_id` only; published screen spec, BFF contract, example payload, contract-ready handoff, and Lovable UI task exist
- live-state dependency: LN-03 `root_type` filtering requires registry metadata not yet in v1; document this limitation in the Lineage View spec and Lovable handoff copy; do not expose `root_type` as a working filter until the registry metadata prerequisite lands

### EW-04 Inspiration Graph

- backend gap: no inspiration surface exists in current BFF; `GET /api/v1/lineage/inspiration/{artifact_id}` is required but not yet implemented; the response must be BFF-composed with `artifact_id`, `inspiration_edges[]` (each carrying `source_artifact_id`, `relationship_type`, `influence_weight`), `meta.snapshot_at`, and `meta.surfaces.inspiration`; the UI must not construct an inspiration graph from raw lineage edges client-side
- packetization prerequisite: the BFF inspiration surface must be implemented and its field shape locked before a screen spec or example payload can be created; depends on LN-03 lineage graph primitives being stable and the lineage `root_type` registry prerequisite being resolved so that creative lineage edges are addressable
- live-state dependency: `meta.surfaces.inspiration` staleness must be reflected in the degradation banner once the BFF route exists; the banner must not be sourced from client-side graph traversal

### EW-05 Mutation Review

- backend gap: no mutation review surface or execute boundary exists; `GET /api/v1/operator/mutation-review/{decision_id}` and `POST /api/v1/operator/commands` extended with `ApproveMutation` and `RejectMutation` commands are required but not yet implemented; the EVO-004 execution boundary decision (covering freeze, rollback, retrain, and redeploy action paths) must be recorded as an L1 policy update before this packet is opened
- packetization prerequisite: EVO-004 must explicitly settle all four action paths — **freeze** (who may issue a freeze order and under what conditions), **rollback** (what constitutes a safe rollback target and who can authorize it), **retrain** (whether retrain is an operator-triggered action or an autonomous evolution step), and **redeploy** (how redeployment after a retrain or rollback is gated and which approval chain applies); only after EVO-004 is settled can the composed mutation review shape (`evolution_decision`, `proposed_changes`, `risk_assessment`, `required_approvals`, `allowedActions.canApproveMutation`, `allowedActions.canRejectMutation`, `meta.surfaces`) be locked
- live-state dependency: `allowedActions.canApproveMutation` and `allowedActions.canRejectMutation` must be backend-shaped authority signals consistent with the EVO-004 policy; the mutation review CTA must never be visible unless both signals are present and truthy; `meta.surfaces` degraded state must suppress the approval CTA entirely if the mutation evidence surface is unavailable

Canonical module inventory:

| Module | Screen or surface scope | Existing Pantheon support | Missing screen-spec work | Lovable readiness | Backend or contract dependencies | Recommended wave |
|---|---|---|---|---|---|---|
| `EW-01 Post-Incident Review` | resolved-incident index, composed post-incident review, postmortem references, and evolution evidence summary; cross-listed with `OC-08` in Operator Console | `PKT-003` ready packet; `GET /api/v1/incidents?status=resolved`; `GET /api/v1/operator/post-incident-review/{incident_id}`; `GET /api/v1/postmortems`; W3 sidecar composed view and degraded-panel gating rules | none for the current packet; carry forward TL/LN caveat wording and reviewer-role expectations from the W3 packet family | ready | none for the current packet beyond inherited W3 caveats (`time_range` filters partial, `root_type` no-op) already documented in `PKT-003`; `meta.surfaces.*` banner inheritance required | Wave 1 baseline — already packetized by `PKT-003` |
| `EW-02 Evolution Center` | evolution decision list and detail, freeze order index, rollback history, and read-only operator review surface | `PKT-003` ready packet; `GET /api/v1/evolution-decisions`; `GET /api/v1/evolution-decisions/{decision_id}`; `GET /api/v1/freeze-orders`; `GET /api/v1/rollbacks`; all four EV read surfaces implemented | none for the current packet; execution boundary gap is non-blocking for the read-only view; carry forward EV-04 `time_range` caveat in handoff copy | ready | none for the current read-only packet; mutation and execution actions are explicitly deferred to `EW-05 Mutation Review` and EVO-004 settlement | Wave 2 — first |
| `EW-03 Lineage View` | lineage edge list, edge detail, and lineage graph for root artifact or decision tracing | `PKT-003` ready packet; `GET /api/v1/lineage` (LN-01); `GET /api/v1/lineage/edges/{edge_id}` (LN-02); `GET /api/v1/lineage/graph` (LN-03); W3 sidecar LN surface definitions | none for the current packet; document LN-03 `root_type` no-op caveat explicitly in screen spec and Lovable handoff copy | ready (with caveat) | none for the current packet; `LN-03 root_type` type-based filtering requires registry metadata not in v1; do not expose as a working filter | Wave 2 — second |
| `EW-04 Inspiration Graph` | creative lineage graph for a given artifact, showing related artifacts, creative inspiration edges, strategy tags, and influence-weight display | no inspiration surface in current BFF; lineage graph primitives exist via LN-01 to LN-03 but must not be used client-side to construct inspiration graphs | full screen packet: inspiration edge display, influence-weight visualization, strategy-tag rail, `meta.surfaces.inspiration` staleness handling | not ready | `GET /api/v1/lineage/inspiration/{artifact_id}`; BFF-composed inspiration graph with `inspiration_edges[]` shape; `meta.snapshot_at` and `meta.surfaces.inspiration` fields; depends on LN-03 lineage primitives being stable | Wave 2 — third (after BFF inspiration route is implemented) |
| `EW-05 Mutation Review` | composed mutation review screen with evolution decision context, proposed changes, risk assessment, required approvals, and backend-shaped approve/reject CTA | no mutation review surface or execute boundary exists; EV read routes and `OC-08` post-incident context provide evidence inputs but not a mutation authority surface | full screen packet: mutation review layout, risk assessment display, approval and rejection CTA copy, `allowedActions` authority check, degraded-state handling when mutation evidence surface is unavailable | not ready | EVO-004 execution boundary settlement (freeze, rollback, retrain, redeploy); `GET /api/v1/operator/mutation-review/{decision_id}`; `POST /api/v1/operator/commands` extended with `ApproveMutation` and `RejectMutation`; `allowedActions.canApproveMutation` and `allowedActions.canRejectMutation` authority signals | Wave 3 — first (after EVO-004 boundary settled and BFF route implemented) |

Wave framing:

- Wave 1 establishes the ready Evolution Workbench baseline through `EW-01 Post-Incident Review`: the composed post-incident review surface is already packetized via `PKT-003` and shared with the Operator Console as `OC-08`.
- Wave 2 delivers the read-only Evolution Workbench core: `EW-02 Evolution Center`, `EW-03 Lineage View`, and `EW-04 Inspiration Graph` (the last pending BFF inspiration route delivery). All three are evolution read surfaces that operators use before any mutation decision is made.
- Wave 3 adds mutation authority through `EW-05 Mutation Review` only after EVO-004 execution boundary policy is locked as an L1 canonical update and the BFF mutation review surface is implemented.

Wave 2 and Wave 3 internal ordering and dependency chain:

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| Wave 1 baseline | `EW-01 Post-Incident Review` | this ready packet and composed view defines the canonical evidence inputs that evolution decisions and lineage reviews build on; cross-shared with Operator Console as `OC-08` | none — already packetized via `PKT-003` |
| Wave 2 — 1 | `EW-02 Evolution Center` | evolution decision list and freeze/rollback history establish the central review context for the workbench; the lineage and inspiration views need a stable decision identity to anchor their graph queries | inherits `EW-01` evidence inputs and `PKT-005` degradation banner; EV read surfaces already live |
| Wave 2 — 2 | `EW-03 Lineage View` | lineage graph builds on the decision identity established by Evolution Center; LN-01 to LN-03 read surfaces are already live and the LN-03 caveat is documented | inherits stable evolution decision identity from `EW-02`; `LN-03 root_type` caveat must be documented before Lovable handoff |
| Wave 2 — 3 | `EW-04 Inspiration Graph` | inspiration graph is a BFF-composed creative lineage view that extends the raw lineage primitives; requires the BFF inspiration route to exist before packetization begins | depends on LN-03 lineage graph primitives being stable and the BFF `GET /api/v1/lineage/inspiration/{artifact_id}` route being implemented |
| Wave 3 — 1 | `EW-05 Mutation Review` | mutation authority surface must come after the full read-only review context (evolution decisions, lineage, inspiration) is stable; EVO-004 policy lock is a hard prerequisite that must precede both the BFF route and the screen packet | depends on EVO-004 L1 policy update, `EW-02` evolution decision identity, and the BFF mutation review route; do not open this packet before EVO-004 is recorded in canonical policy |

Live-state and evidence dependencies before Lovable packetization:

| Module | Live-state or evidence gate | Must be resolved before packetization? |
|---|---|---|
| `EW-01 Post-Incident Review` | `meta.surfaces.*` degraded-panel gating; banner inheritance from `PKT-005` | already resolved in `PKT-003`; carry forward in handoff copy |
| `EW-02 Evolution Center` | EV-04 `time_range` filter caveat; `meta.surfaces` banner inheritance | already documented as W3 inherited caveat; non-blocking |
| `EW-03 Lineage View` | LN-03 `root_type` no-op caveat; `meta.surfaces` banner inheritance | already documented; must be explicit in Lovable handoff copy |
| `EW-04 Inspiration Graph` | `meta.surfaces.inspiration` staleness signal; BFF-composed graph shape (no client-side construction from raw lineage edges) | BFF route must exist and field shape must be locked before screen spec can be opened |
| `EW-05 Mutation Review` | `allowedActions.canApproveMutation` and `canRejectMutation` backend-shaped signals; CTA must be hidden when mutation evidence surface is degraded or unavailable; EVO-004 boundary policy | EVO-004 must be recorded in L1 policy AND BFF route must be implemented before any mutation screen spec work can begin |

Separation rule for this backlog:

- put missing screen copy, approval/rejection CTA wording, graph display spec, lineage panel layout, and degraded-state copy in `Missing screen-spec work`
- put absent BFF routes (inspiration graph route, mutation review route), unresolved EVO-004 action boundary policy, and missing `allowedActions` authority signals in `Backend or contract dependencies`

Recommended wave:

- Evolution Workbench starts in Wave 1 because the post-incident read surface is already packetized.
- Wave 2 delivers the read-only evolution review core: Evolution Center, Lineage View, and Inspiration Graph (the last after BFF inspiration route lands).
- Wave 3 adds `EW-05 Mutation Review` only after EVO-004 execution boundary policy is formally locked as an L1 update and the BFF mutation review route is implemented.
- no Evolution Workbench module should be handed to Lovable before its live-state and evidence dependencies are resolved; in particular, `EW-05` must not be handed off until the `allowedActions` mutation authority signals are backend-shaped and the `meta.surfaces` degradation gate is wired through to the CTA

## Cross-Cutting Notes

- `F-042` is only one Governance Workbench screen, not a full admin or governance implementation.
- APP-002 sidecars make Operator and Persona packetization feasible now, but the rest of the workbenches still need canonical screen specs before Lovable should touch them.
- the front repo is currently missing locally, so no live mirror validation can happen until `LOOP-003` closes.

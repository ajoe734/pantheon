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
| Knowledge Workbench | institutional memory and evidence navigation | blueprint-level direction only | full canonical packet family | no | high | Wave 3 |
| Trainer Workbench | teaching sessions and before-after review | blueprint-level direction only; current implementation is demo-grade | full canonical packet family | no | high | Wave 3 |
| Consultation Workbench | consult requests, committee review, debate, and red-team outputs | blueprint-level direction only | full canonical packet family | no | high | Wave 4 |
| Governance Workbench | review queue, approval queue, promotion, rollback, and diff control | `F-042` plus operator governance semantics | every governance screen beyond Promotion Review | partial | medium | Wave 2 |
| Evolution Workbench | post-incident, evolution review, lineage, inspiration, and mutation review | post-incident/evolution sidecars and lineage surfaces | inspiration and mutation review packet family | partial | medium | Wave 2 |

## Operator Console

Objective: give operators one coherent control surface for runtime state, deployment, incidents, system health, and degraded operation.

Screens and modules:

- `Operator Home`
- `Deployment Review Console`
- `Incident Response Console`
- `Post-Incident Review Console`
- `Alerts rail`
- `Health and runtime status`
- `Paper/live drift view`
- `Global degradation banner`
- `SSE and reconciliation substrate`

Existing Pantheon support:

- `F-042 Promotion Review` packet and prompt already exist
- APP-002 sidecars define deployment review, incident response, post-incident review, degradation banner, and SSE semantics
- operator acceptance matrix defines path semantics, permissions, and degraded behavior

Missing canonical screen specs:

- Operator Home dashboard
- paper/live drift screen
- alerts and health as one operator-home packet family rather than loose pages

Lovable readiness:

- ready or near-ready: deployment review, incident response, post-incident review, degradation banner shell
- not yet ready: operator home and drift screens

Backend dependencies:

- live dashboard or drift read models
- explicit runtime and health composed views if current BFF surfaces are not enough

Recommended wave:

- Wave 1 for deployment, incident, post-incident, degradation, and SSE packetization
- Wave 2 for operator home, drift, and the remaining operator shell

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

### Search

- backend gap: no `GET /api/v1/research/search` route or search index adapter in current BFF
- packetization prerequisite: define query parameters, result shape, and filter semantics before Lovable can render a real search surface

### Analyze

- backend gap: no analysis result or metric aggregation endpoint in current BFF
- packetization prerequisite: research-result payload contract must be agreed before an analysis view can be packet-defined; depends on the research ticket read model

### Research Ticket

- backend gap: no `POST /api/v1/research/ticket` create route or `GET /api/v1/research/ticket/{id}` read route in current BFF
- packetization prerequisite: ticket lifecycle states (open, in-progress, closed, archived) must be defined before the detail and list screens can be spec'd; this is the foundation that Search and Analyze build on

### Experiment Launch

- backend gap: no launch-request route, no experiment state machine, and no run status read model in current BFF
- packetization prerequisite: launch request contract must define input parameters, validation rules, and async status polling; depends on Research Ticket for lineage tracking

### Artifact Compare

- backend gap: no artifact registry read model and no diff or comparison surface in current BFF
- packetization prerequisite: artifact identity and versioning semantics must be locked before a compare view can be packet-defined; depends on Experiment Launch producing versioned artifact references

Recommended wave:

- Wave 3 after closed-loop infra and operator/persona packetization are settled
- internal module order within Wave 3: Research Ticket → Search → Analyze → Experiment Launch → Artifact Compare

## Knowledge Workbench

Objective: expose institutional memory, evidence, strategy specs, and durable research notes.

Screens and modules:

- `Insight cards`
- `Strategy spec`
- `Evidence refs`
- `Research notes`
- `Institutional memory`

Existing Pantheon support:

- blueprint-level workbench definition only

Missing canonical screen specs:

- all Knowledge Workbench screens

Lovable readiness:

- not ready

Backend dependencies:

- evidence reference read model
- knowledge aggregation and note retrieval surfaces

Recommended wave:

- Wave 3

## Trainer Workbench

Objective: turn the current demo trainer direction into a real BFF-backed teaching workflow.

Screens and modules:

- `Teaching dialog`
- `Parameter controls`
- `Before/after compare`
- `Teaching replay`

Existing Pantheon support:

- blueprint-level workbench definition only
- front-end direction exists, but it is still demo-grade

Missing canonical screen specs:

- all Trainer Workbench screens

Lovable readiness:

- not ready

Backend dependencies:

- teaching-session contracts
- replay artifacts and comparison surfaces

Recommended wave:

- Wave 3

## Consultation Workbench

Objective: support consult requests, committees, debate transcripts, and red-team review as first-class product surfaces.

Screens and modules:

- `Consult request`
- `Committee board`
- `Debate transcript`
- `Red-team memo`

Existing Pantheon support:

- blueprint-level workbench definition only

Missing canonical screen specs:

- all Consultation Workbench screens

Lovable readiness:

- not ready

Backend dependencies:

- consultation orchestration and transcript surfaces
- committee and red-team domain truth

Recommended wave:

- Wave 4

## Governance Workbench

Objective: give governance operators queue-based review, approval, promotion, rollback, and deployment diff control.

Screens and modules:

- `Review queue`
- `Approval queue`
- `Promotion Review`
- `Deployment diff`
- `Rollback review`
- `Governance audit rail`

Existing Pantheon support:

- `F-042 Promotion Review`
- operator acceptance matrix for governance and deployment surfaces

Missing canonical screen specs:

- every governance screen beyond `F-042`

Lovable readiness:

- partial

Backend dependencies:

- approval queue read model
- rollback review and deployment diff composed views

Recommended wave:

- Wave 2

## Evolution Workbench

Objective: expose post-incident, evolution review, lineage, inspiration, and mutation review as one coherent workbench.

Screens and modules:

- `Post-Incident Review`
- `Evolution Center`
- `Lineage`
- `Inspiration Graph`
- `Mutation review`

Existing Pantheon support:

- post-incident and evolution sidecars
- lineage and telemetry read surfaces

Missing canonical screen specs:

- inspiration graph
- mutation review
- final Evolution Workbench IA

Lovable readiness:

- partial

Backend dependencies:

- final mutation and execute boundary
- lineage or inspiration graph shaping for front-end packetization

Recommended wave:

- Wave 2

## Cross-Cutting Notes

- `F-042` is only one Governance Workbench screen, not a full admin or governance implementation.
- APP-002 sidecars make Operator and Persona packetization feasible now, but the rest of the workbenches still need canonical screen specs before Lovable should touch them.
- the front repo is currently missing locally, so no live mirror validation can happen until `LOOP-003` closes.

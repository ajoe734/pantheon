# Pantheon Lovable Master Solution Architecture

Last updated: 2026-04-19
Status: master frontend IA and solution-architecture brief for Lovable
Audience: Lovable and any frontend implementation lane working from Pantheon handoff packets
Authority: implementation guide derived from canonical blueprint and packet-family documents; this file does not override L1 or L2 canonical policy

## 1. Purpose

This document exists to stop Pantheon frontend delivery from turning into isolated page work.

Lovable should read this file first to understand:

- what Pantheon is as a product
- how the whole application is organized
- which workbenches and pages belong together
- what each surface is responsible for
- which layouts and interaction patterns should repeat across the app
- which modules are ready to implement now and which must stay blocked until Pantheon publishes the missing BFF contracts

After reading this file, Lovable should then read:

1. the relevant packet-family file
2. the relevant screen spec in `docs/screens/`
3. the relevant BFF contract in `docs/bff/`
4. the module-local `FRONTEND_CHANGE_SPEC.md`

If this master brief conflicts with canonical policy, the following files win:

- [TARGET_ARCHITECTURE.md](../../TARGET_ARCHITECTURE.md)
- [ROADMAP.md](../../ROADMAP.md)
- [DEVELOPMENT_WORKBREAKDOWN.md](../../DEVELOPMENT_WORKBREAKDOWN.md)
- [WORKBENCH_DELIVERY_BACKLOG.md](../../WORKBENCH_DELIVERY_BACKLOG.md)

## 2. Product Frame

Pantheon is not a collection of unrelated dashboards.

It is one governed operating system for:

1. research and proposal formation
2. approval and deployment planning
3. runtime binding and capital allocation
4. telemetry, incident response, and rollback
5. evolution review and controlled follow-up
6. knowledge, consultation, and training workflows around those same canonical objects

The frontend must preserve that governed flow.

The UI must never imply:

- that browser state is a source of truth
- that a screen may invent its own lifecycle or approval semantics
- that read-model aggregation can be done client-side if the BFF does not already own it
- that a summary page has write authority just because it links to a write-owning surface

## 3. Frontend Rules Lovable Must Follow

### 3.1 BFF-first only

Every page must render backend-shaped read models.

Lovable must not:

- join multiple Pantheon routes in the browser unless a packet explicitly allows it
- compute health, governance state, drift, approval risk, or mutation authority client-side
- invent filters, statuses, or summary counts not present in the BFF contract

### 3.2 Authority is explicit

All write actions must be gated by backend-shaped `allowedActions` or by an explicit write contract.

If a CTA lacks an authority field, the CTA should not appear.

### 3.3 Degradation is first-class

Pantheon distinguishes:

- healthy
- degraded
- unavailable
- stale or delayed

The app must never flatten degraded state into an empty state.

The degradation banner and surface-state logic from `PKT-005` are global cross-cutting infrastructure, not page-specific decoration.

### 3.4 Live updates are an overlay, not the source of truth

Initial read comes from the composed BFF response.
SSE only updates the already-rendered surface.

Do not build pages that require SSE to make sense at all.

### 3.5 Repeated UX patterns should stay consistent

Pantheon should feel like one operating system with repeated patterns:

- list-detail for queues and registries
- board plus side panel for state overviews
- graph plus inspector for lineage-style exploration
- compare view for diff and before/after work
- evidence drawer for linked supporting context
- command footer or command rail only on pages that truly own actions

## 4. Recommended App Shell

Lovable should treat the application as one shared shell with section-level navigation rather than unrelated standalone pages.

### 4.1 Global shell structure

- Left navigation rail for major workbenches
- Top bar for environment and session context
- Global degradation banner area at top of content column
- Main content region for the active workbench page
- Optional right-side contextual drawer for evidence, detail, or inspector content

### 4.2 Top-bar content

The top bar should always reserve space for:

- environment or deployment context
- current operator persona or viewer context
- system freshness and degradation cues
- alerts or notifications entry
- quick navigation or command search entry

### 4.3 Recommended primary navigation

Use this section order:

| Section | Purpose | Notes |
|---|---|---|
| `Overview` | operator landing and cross-system awareness | starts at Operator Home |
| `Operator Console` | live operational monitoring and response | runtime, incidents, health, alerts, drift |
| `Governance` | review, approval, promotion, rollback, audit | queue and decision work |
| `Personas` | persona, binding, capital, deployment context | read-heavy Wave 1 workbench |
| `Evolution` | post-incident, evolution decisions, lineage, mutation | partly ready, partly blocked |
| `Research` | tickets, search, analysis, experiments, artifact compare | not ready for full implementation |
| `Knowledge` | memory, notes, evidence, insights, specs | overview exists; modules blocked |
| `Consultation` | consult request, transcript, committee, red-team memo | overview exists; modules blocked |
| `Trainer` | teaching dialog, controls, compare, replay | blocked until BFF contracts land |

### 4.4 Recommended shared page anatomy

Most workbench pages should follow this page shape:

| Layer | What it should contain |
|---|---|
| `Header` | title, short purpose copy, status chips, freshness timestamp |
| `Summary strip` | KPI cards or state chips for the current page scope |
| `Primary body` | list, board, graph, compare view, or detail content |
| `Secondary panel` | detail drawer, evidence drawer, inspector, or drilldown rail |
| `Footer state rail` | connection state, delayed-update note, or secondary-path guidance when relevant |

## 5. Cross-Cutting Presentation Guidance

### 5.1 Use summary cards sparingly

Summary cards belong on home, health, runtime, and dashboard-like pages.
They should summarize, not become shadow data owners.

### 5.2 Prefer split-pane layouts for operational work

These surfaces should generally use a stable list-detail or board-detail pattern:

- review queues
- incident home
- approval queue
- deployment diff
- rollback review
- persona management
- knowledge and research registries

### 5.3 Use drawers for evidence, not for primary page identity

Good drawer use:

- evidence refs
- audit entry detail
- rollback trigger detail
- mutation evidence
- lineage edge detail

Bad drawer use:

- making the only usable version of the page live inside a drawer

### 5.4 Graph pages need a real inspector

Any lineage or inspiration graph should be:

- graph canvas in the center
- selected node or edge inspector on the side
- filters and graph mode controls above or beside the canvas

### 5.5 Compare pages need structure, not generic diffs

Diff and compare pages should always group data by meaning, not by raw field order.

Use:

- change summary rail
- grouped diff table
- risk or threshold summary
- explicit recommended next action

## 6. Workbench-Level IA

## 6.1 Operator Console

Operator Console is the main live-operating surface for Pantheon.
It should feel like the control room of the system.

### Primary mission

- know what is happening now
- know what is unhealthy
- know what requires operator attention
- move quickly into the correct owner surface

### Recommended section structure

| Module | Purpose | Recommended presentation | Readiness |
|---|---|---|---|
| `PKT-001 Deployment Review Console` | review deployment plans and submit governed deployment decisions | list-detail review console | ready |
| `PKT-002 Incident Home` | browse active incidents | list with severity filters plus active detail preview | ready |
| `PKT-002 Incident Detail` | inspect one incident, bindings, kill switch, and action authority | detail page with two-column operational layout | ready |
| `PKT-002 Incident Action Drawer` | execute incident response actions | drawer or standalone emergency action host | ready |
| `PKT-003 Post-Incident Review` | inspect resolved incident evidence and postmortem context | split-pane history plus detail | ready |
| `PKT-003 Evolution Center` | read evolution decisions and follow-up context | board or list-detail | ready |
| `PKT-003 Lineage View` | inspect lineage edges and graph relations | graph plus inspector | ready |
| `PKT-005 Degradation Banner` | shared degraded-state chrome | global surface | ready |
| `PKT-005 SSE Substrate` | shared live-update behavior | infrastructure, not a standalone page | ready |
| `OC-01 Operator Home` | one landing page summarizing operator state | summary dashboard with strong shortcuts | contract-ready |
| `OC-02 Alerts Rail` | chronological operator alerts | rail or board with alert detail drawer | contract-ready |
| `OC-03 Health Status Board` | control-plane and dependency health | grouped health board | contract-ready |
| `OC-04 Runtime State Board` | runtime roster, status, telemetry, rollback summary | dense operational board | contract-ready |
| `OC-05 Paper / Live Drift` | paper-vs-live comparison | compare page with grouped metrics and evidence | contract-ready |

### Recommended Operator Console navigation order

1. `Operator Home`
2. `Alerts`
3. `Health`
4. `Runtime State`
5. `Incidents`
6. `Deployment Review`
7. `Paper / Live Drift`
8. `Post-Incident Review`
9. `Evolution Center`
10. `Lineage`

### Recommended page intent

| Surface | Must include | Must not do |
|---|---|---|
| `Operator Home` | cards for alerts, incidents, governance, runtime, health, safe mode, and escalation shortcuts | become a shadow command center with duplicated write actions |
| `Alerts Rail` | ordered alert feed with category, severity, and target links | infer alerts from multiple pages in the browser |
| `Health Status` | grouped dependency health and secondary control path guidance | calculate health locally from scattered calls |
| `Runtime State` | runtime roster, stage, status, telemetry summary, rollback hooks | let the UI invent runtime joins |
| `Paper / Live Drift` | grouped drift metrics, threshold evaluation, evidence refs, next-step links | compute drift semantics client-side |

## 6.2 Governance Workbench

Governance is the decision-and-audit section.
It should feel deliberate, evidence-backed, and less live-chaotic than Operator Console.

### Primary mission

- review pending items
- inspect diff and evidence
- approve or reject with explicit authority
- inspect historical governance actions

### Recommended section structure

| Module | Purpose | Recommended presentation | Readiness |
|---|---|---|---|
| `GV-01 Review Queue` | unified governance queue | list-detail | ready |
| `GV-03 Promotion Review` | promotion-stage review | focused detail review page | ready |
| `GV-02 Approval Queue` | approval decisions queue | queue with decision drawer | backend live / loop largely settled |
| `GV-04 Deployment Diff` | compare current plan to prior plan | structured diff page | backend live / frontend replay-sensitive |
| `GV-05 Rollback Review` | review rollback scope and risk | detail page with scope and impact tables | ready |
| `GV-06 Governance Audit Rail` | inspect governance history | chronological audit list with detail drawer | ready |

### Recommended layout language

- review queue and approval queue should look related
- diff and rollback review should feel like "high-focus review surfaces"
- audit should feel like history or ledger, not a dashboard

## 6.3 Persona Workbench

Persona Workbench is a read-heavy operational and governance context surface.

### Primary mission

- inspect personas
- inspect sessions and capabilities
- inspect bindings and capital relationships
- inspect deployment and approval context connected to personas

### Recommended section structure

| Module | Purpose | Recommended presentation | Readiness |
|---|---|---|---|
| `PM-01 Persona Management` | composed persona surface with bindings and teaching history | detail workspace | ready |
| `PS-01` to `PS-06` Persona Drilldowns | catalog, detail, sessions, teaching, capabilities | registry plus drilldown | ready |
| `CP-01` to `CP-04` Capital / Binding Drilldowns | capital pools and persona-capital bindings | list-detail and drilldown | ready |
| `DP-01` to `DP-04` Deployment / Approval Drilldowns | deployment and approval context | read-only drilldown | ready |

### Design intent

This workbench should feel analytical and structured, not like a command center.

Wave 1 is read-heavy.
Governance writes stay in Governance Workbench or Operator Console owner screens.

## 6.4 Evolution Workbench

Evolution Workbench bridges incident evidence, lineage, and governed follow-up.

### Primary mission

- understand what changed
- understand why
- understand what evolution action is under review
- eventually allow bounded mutation approval

### Recommended section structure

| Module | Purpose | Recommended presentation | Readiness |
|---|---|---|---|
| `EW-01 Post-Incident Review` | resolved incident review context | split-pane evidence review | ready via `PKT-003` |
| `EW-02 Evolution Center` | evolution decision browse and detail | board or list-detail | ready via `PKT-003` |
| `EW-03 Lineage View` | lineage graph and edge detail | graph plus inspector | ready via `PKT-003` |
| `EW-04 Inspiration Graph` | artifact-centered inspiration graph | graph plus strategy-tag rail | pending-bff — contract published via `EW-04-OPEN-001`; activates once BFF route is live |
| `EW-05 Mutation Review` | approve or reject mutation follow-up | high-focus evidence review page | ready — contract and live BFF route/command vocabulary are aligned; implement against the published handoff bundle |

### Design intent

The first three modules are the read-only evidence baseline.
`EW-04` still waits on a live BFF route; `EW-05` may now be implemented as a production screen against the live handoff bundle.

## 6.5 Research Workbench

Research Workbench is for creating and moving research work through a governed research flow.

### Primary mission

- create research tickets
- search the corpus
- inspect analysis results
- launch experiments
- compare produced artifacts

### Recommended module structure

| Module | Purpose | Recommended presentation | Readiness |
|---|---|---|---|
| `RW-01 Research Ticket` | ticket list, detail, lifecycle | list-detail | blocked |
| `RW-02 Search` | backend-owned search corpus | search page with filter rail | blocked |
| `RW-03 Analyze` | analysis results and grouped metrics | detail plus compare panels | blocked |
| `RW-04 Experiment Launch` | launch async runs and inspect status | wizard or form plus status board | **ready** — routes live; handoff bundle at `docs/pantheon-handoffs/RW-04-experiment-launch/` |
| `RW-05 Artifact Compare` | compare versioned artifacts | compare surface | blocked |

### Design intent

Do not let Lovable invent this IA from generic "research app" intuition.
This workbench only becomes buildable module by module after Pantheon lands the missing routes.

If exploratory design is needed before BFF readiness, it should remain shell or wireframe only.

## 6.6 Knowledge Workbench

Knowledge Workbench is the durable memory and evidence browsing surface.

### Primary mission

- browse institutional memory
- browse notes and evidence links
- browse insight cards
- inspect strategy specs and their citations

### Recommended module structure

| Module | Purpose | Recommended presentation | Readiness |
|---|---|---|---|
| `PKT-knowledge-workbench` | overview shell only | overview dashboard shell | overview ready |
| `KW-01 Institutional Memory` | memory browse and detail | list-detail | blocked |
| `KW-02 Research Notes` | notes with ownership and attachments | registry plus detail | blocked |
| `KW-03 Evidence Refs` | evidence reference browser | ledger plus detail drawer | blocked |
| `KW-04 Insight Cards` | synthesized insight cards | card grid plus detail inspector | blocked |
| `KW-05 Strategy Spec` | versioned strategy spec browser | list-detail plus compare | blocked |

### Design intent

This workbench should feel more library-like and evidence-oriented than operational.
Use calm information density and strong inspectability.

## 6.7 Consultation Workbench

Consultation Workbench is for structured consult flows, committee review, and red-team findings.

### Primary mission

- initiate a consultation
- inspect transcript and evidence
- inspect committee outcome
- inspect red-team memo output

### Recommended module structure

| Module | Purpose | Recommended presentation | Readiness |
|---|---|---|---|
| `PKT-consultation-workbench` | overview shell only | overview dashboard shell | overview ready |
| `CW-01 Consult Request` | request composer and request lifecycle | list-detail plus composer | blocked |
| `CW-02 Debate Transcript` | ordered consultation transcript | transcript timeline plus evidence inspector | blocked |
| `CW-03 Committee Board` | committee state and sponsor outcome | board plus detail | blocked |
| `CW-04 Red-team Memo` | published memo and recommendations | document-like detail surface | blocked |

### Design intent

This workbench should feel like a structured deliberation system, not chat UI.
Chronology, actor identity, and evidence traceability matter more than conversational flair.

## 6.8 Trainer Workbench

Trainer Workbench is for persona coaching and controlled parameter adjustment.

### Primary mission

- open training sessions
- coach through dialog
- patch controls safely
- inspect before/after compare
- replay and commit or discard

### Recommended module structure

| Module | Purpose | Recommended presentation | Readiness |
|---|---|---|---|
| `TW-01 Teaching Dialog` | training-session chat and status | transcript plus control sidebar | pending-bff — contract published; activates once the BFF routes are live |
| `TW-02 Parameter Controls` | inspect and patch controls | form plus diff rail | pending-bff — contract published; activates once the BFF routes are live |
| `TW-03 Before/After Compare` | preview deltas and warnings | compare page | pending-bff — contract published; activates once the BFF routes are live |
| `TW-04 Teaching Replay` | replay finished session and commit or discard | timeline plus evidence drawer | pending-bff — contract published; activates once the BFF routes are live |

### Design intent

This workbench must not feel like a generic chatbot.
It is a governed operator-training and control-adjustment workflow.

## 7. Recommended Delivery Order For Lovable

Lovable should work in this order:

1. shared shell, navigation, degradation, and surface-state language
2. already-ready baseline packets:
   `F-042`, `PKT-001`, `PKT-002`, `PKT-003`, `PKT-004`, `PKT-005`, `PKT-008`, `PKT-009`
3. ready or contract-ready Operator Console Wave 2:
   `PKT-010` to `PKT-014`
4. governance follow-on closeout where frontend loop is still active:
   `PKT-006`, `PKT-007`
5. overview shells:
   `PKT-consultation-workbench`, `PKT-knowledge-workbench`
6. blocked workbench modules only after Pantheon publishes their missing BFF routes:
   `RW-*`, `KW-*`, `CW-*`, `TW-*`, `EW-04`, `EW-05`

## 8. What Lovable Should Build As A System, Not As Isolated Pages

Lovable should think in terms of reusable Pantheon primitives:

| Primitive | Reused by |
|---|---|
| `Global degradation banner` | all workbenches |
| `Surface-state renderer` | all list/detail pages |
| `Queue shell` | review queue, approval queue, alerts, audit |
| `Board shell` | runtime state, health status, operator home, committee board |
| `Graph shell` | lineage view, inspiration graph |
| `Compare shell` | deployment diff, paper/live drift, artifact compare, before/after compare |
| `Evidence drawer` | governance, evolution, consultation, knowledge, trainer |
| `Command rail` | deployment review, incident detail, rollback review, mutation review, trainer controls |
| `Realtime footer rail` | live operator pages using PKT-005 SSE |

These primitives should be designed once and reused.
That is the key to avoiding "東一塊西一塊".

## 9. Hard Boundaries

Lovable must not do these things:

- build blocked modules as if they were production-ready
- invent data models for missing BFF routes
- treat overview packets as permission to invent module detail pages
- move governance writes into Persona, Operator Home, or any summary screen
- turn degraded state into a quiet empty state
- treat packet-local pages as separate products with unrelated interaction patterns

## 10. Practical Handoff Rule

When implementing any surface, use this order:

1. read this master SA
2. confirm the target surface is actually ready in `WORKBENCH_DELIVERY_BACKLOG.md`
3. read the relevant packet family
4. read the relevant screen spec
5. read the relevant BFF contract
6. read the relevant `FRONTEND_CHANGE_SPEC.md`
7. implement using Pantheon shared primitives rather than ad hoc page-local logic

If a module is not ready, Lovable may only do one of these:

- stop and wait for Pantheon BFF work
- produce IA or wireframe exploration explicitly marked as non-production shell work

It must not silently turn missing backend truth into frontend invention.

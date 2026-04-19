# RW-005 Research Workbench — Canonical Packet Family

## Header

- Packet family ID: `RW-005`
- Workbench: Research Workbench
- Phase origin: `BP5-WB-005`
- Lovable readiness: **partial** — RW-01 Research Ticket contract published via `RW-01-FOUNDATION-001`, RW-02 Search contract published via `RW-02-SEARCH-001`; remaining three modules still lack canonical BFF routes; Lovable handoff must not open for any module until its own BFF prerequisites are satisfied
- Recommended wave: Wave 3 — after Operator Console (Wave 1–2) and Persona Workbench (Wave 1–2) packetization are settled
- Reviewer: Codex

---

## Objective

Give researchers one coherent workbench for creating and tracking research tickets, running searches against the research corpus, composing analysis views, launching and monitoring experiments, and comparing versioned artifacts produced by those experiments. All data and CTA authority must come from the Pantheon BFF — no local derivation, no mock state, no client-side corpus assembly.

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Lovable readiness | Wave order |
|---|---|---|---|---|
| `RW-01` | Research Ticket | ticket list, ticket detail, lifecycle state machine | contract-published — pending BFF implementation | Wave 3 — 1st |
| `RW-02` | Search | query input, result list, filter rail, result drilldown | contract-published — pending BFF implementation | Wave 3 — 2nd |
| `RW-03` | Analyze | analysis result view, metric aggregation, comparative summary | contract-published — pending BFF implementation | Wave 3 — 3rd |
| `RW-04` | Experiment Launch | launch form, parameter inputs, async run status, run history | not ready | Wave 3 — 4th |
| `RW-05` | Artifact Compare | artifact selector, version diff view, side-by-side compare | not ready | Wave 3 — 5th |

---

## RW-01 Research Ticket

### Surface scope

- **Ticket list**: paginated list of all research tickets with `ticket_id`, `title`, `status`, `created_at`, `owner`, and severity or priority label. Filter by `status` and `owner`. Row click navigates to ticket detail.
- **Ticket detail**: full ticket view showing title, description, status, lifecycle history, linked experiments, linked artifacts, and `allowedActions` (edit, close, archive).
- **Lifecycle state machine**: `open → in-progress → closed → archived`. Each state transition is a backend-shaped `allowedAction` — do not derive transition availability client-side.
- **Create ticket**: form for title, description, priority, and owner assignment; submits to `POST /api/v1/research/tickets`.
- **Degradation**: when `meta.surfaces.ticket_list` or `meta.surfaces.ticket_detail` is `degraded` or `unavailable`, show the canonical non-dismissable degradation banner. Do not show stale ticket state as authoritative.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `POST /api/v1/research/tickets` | **contract published — pending BFF implementation** | create route spec published via `RW-01-FOUNDATION-001` in `docs/bff/RW-01-research-ticket.md`; BFF must implement the published title / description / priority / owner body schema |
| `GET /api/v1/research/tickets` | **contract published — pending BFF implementation** | list route field shape, pagination, and `meta.surfaces.ticket_list` published via `RW-01-FOUNDATION-001` |
| `GET /api/v1/research/tickets/{ticket_id}` | **contract published — pending BFF implementation** | detail route, lifecycle history, linked refs, and `allowedActions` published via `RW-01-FOUNDATION-001` |
| `PATCH /api/v1/research/tickets/{ticket_id}` | **contract published — pending BFF implementation** | lifecycle transition and editable field semantics published via `RW-01-FOUNDATION-001` |

### Packetization prerequisite

The ticket lifecycle states, `allowedActions` shape, example payload, and frontend handoff bundle are now published as canonical BFF truth via `RW-01-FOUNDATION-001`. This remains the foundational entity for Search, Analyze, and Experiment Launch, and the live BFF routes still need to honor that published field shape.

### Lovable readiness gate

`pending-bff` — the route specs, screen spec, example payload, and frontend handoff bundle already exist, but all four routes above still need live BFF implementation before UI work can begin.

---

## RW-02 Search

### Surface scope

- **Query input**: search bar with corpus selector (all tickets, open only, by experiment, by artifact). Query is submitted as a BFF request — no client-side corpus assembly.
- **Result list**: ranked result rows showing `result_id`, `match_type` (ticket, experiment, artifact), `title`, `excerpt`, `linked_ticket_id`, and `relevance_score`. Pagination via `page_token`.
- **Filter rail**: filter by `match_type`, `status` of the linked entity, `date_range`. All filters are query parameters sent to the BFF — do not filter client-side.
- **Result drilldown**: clicking a result navigates to the canonical entity detail (ticket detail for ticket matches, run detail for experiment matches, artifact detail for artifact matches).
- **Degradation**: when `meta.surfaces.search_results` is `degraded`, show available results plus a non-dismissable staleness banner. When `unavailable`, show unavailable banner with no result list.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/research/search` | **contract published — pending BFF implementation** | route spec published via `RW-02-SEARCH-001` in `docs/bff/RW-02-search.md`; must support `q`, `match_type`, `status`, `date_range`, `page_token`, `page_size`, return the published `SearchResult` row shape, and include `meta.surfaces.search_results` plus `meta.index_adapter.*` |
| Search index adapter | **contract published — pending BFF implementation** | adapter semantics, corpus coverage metadata, and source watermark expectations are published via `RW-02-SEARCH-001`; BFF must implement backend-owned corpus indexing without UI-side corpus construction |

### Packetization prerequisite

The query parameters, result shape, filter semantics, example payload, and frontend handoff bundle are now published as canonical BFF truth via `RW-02-SEARCH-001`. Search still depends on the Research Ticket read model (`RW-01`) for corpus identity so that `linked_ticket_id` is resolvable, and the live BFF route still needs to implement the published contract.

### Lovable readiness gate

`pending-bff` — the screen spec, example payload, and frontend handoff bundle already exist, but the search route and search index adapter still need live BFF implementation before UI work can begin.

---

## RW-03 Analyze

### Surface scope

- **Analysis result view**: shows the result of an analysis run bound to a research ticket or experiment. Displays `analysis_id`, `ticket_id` or `experiment_id`, `run_at`, `status`, `summary`, and a metric breakdown panel.
- **Metric aggregation panel**: renders backend-computed metric groups (e.g., performance, drawdown, signal quality). Metric keys and grouping are backend-owned — do not infer grouping logic client-side.
- **Comparative summary**: side-by-side summary comparing two or more analysis runs for the same ticket. Comparison data is backend-shaped; the UI renders the provided diff, not a local diff.
- **Filter**: filter by `ticket_id`, `experiment_id`, `status`, `date_range`. All filters are query parameters.
- **Degradation**: when `meta.surfaces.analysis_results` is `degraded` or `unavailable`, show the degradation banner; do not fall back to locally cached or mocked results.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/research/analysis` | **contract published — pending BFF implementation** | route spec published via `RW-03-ANALYZE-001` in `docs/bff/RW-03-analyze.md`; must support `ticket_id`, `experiment_id`, `status`, `date_range`, `page_token`, `page_size`, and return the published analysis summary projection |
| `GET /api/v1/research/analysis/{analysis_id}` | **contract published — pending BFF implementation** | detail route, grouped metric panels, comparative summary, and `meta.surfaces.analysis_results` published via `RW-03-ANALYZE-001` |
| Analysis result / metric aggregation endpoint | **contract published — pending BFF implementation** | backend owns metric grouping and comparison deltas; the analysis payload must stay pre-aggregated and must not regress into a raw metric dump |

### Packetization prerequisite

The research result payload contract (metric keys, aggregation shape, comparison payload, and `meta.surfaces` staleness fields) is now published as canonical BFF truth via `RW-03-ANALYZE-001`. Analyze still depends on the Research Ticket read model (`RW-01`) for ticket-scoped queries, and the live BFF routes still need to honor the published field shape.

### Lovable readiness gate

`pending-bff` — the route spec and example payload now exist, but the live BFF routes and aggregation layer still need implementation before UI work can begin.

---

## RW-04 Experiment Launch

### Surface scope

- **Launch form**: form for experiment name, linked ticket, input parameters, algorithm or strategy selector, and run configuration. Submits to `POST /api/v1/experiments/launch`. Validation rules and allowed parameter ranges are backend-shaped.
- **Async run status**: after launch, the screen enters a polling or SSE-driven status view. Displays `experiment_id`, `status` (`queued`, `running`, `completed`, `failed`, `canceled`), `progress` (if available), and `started_at` / `completed_at`. Status polling or subscription is the BFF's responsibility — do not infer run completion from elapsed time.
- **Run history**: paginated list of past experiment runs for a given ticket. Each row shows `experiment_id`, `status`, `started_at`, `completed_at`, `artifact_ids` produced, and `ticket_id`. Row click opens run detail drawer.
- **Run detail drawer**: `experiment_id`, `status`, input parameters, validation warnings, and `artifact_ids` produced.
- **Allowable actions**: `cancel` action for in-progress runs is gated by `allowedActions.canCancel` from the BFF — never derive cancellation authority client-side.
- **Degradation**: when `meta.surfaces.experiment_status` is `degraded`, show last-known run state with a staleness banner. When `unavailable`, show unavailable state — do not assume the run is still active.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `POST /api/v1/experiments/launch` | **missing** | launch route; must define input parameter schema, validation rules, and initial response with `experiment_id` and `status: queued` |
| `GET /api/v1/experiments/{experiment_id}` | **missing** | run detail and status route; must expose `status`, `progress`, `artifact_ids`, `allowedActions.canCancel`, and `meta.surfaces.experiment_status` |
| `GET /api/v1/experiments` | **missing** | run history list route; must support `ticket_id`, `status`, `page_token`, `page_size` |
| `POST /api/v1/experiments/{experiment_id}/cancel` | **missing** | cancel command route; must be backed by `allowedActions.canCancel` signal |
| Experiment state machine | **missing** | BFF-side experiment lifecycle (`queued → running → completed | failed | canceled`); must be explicit so the UI can map states to display copy |

### Packetization prerequisite

The launch request contract (input parameters, validation rules, async status polling, and `allowedActions.canCancel`) must be agreed as canonical BFF truth before this screen can be packet-defined. Depends on Research Ticket (`RW-01`) for lineage tracking and Analyze (`RW-03`) for feeding result context into launch configuration.

### Lovable readiness gate

`false` — the launch route, experiment state machine, run status route, and `allowedActions.canCancel` shape must all be implemented and field shapes locked before a screen spec can be opened.

---

## RW-05 Artifact Compare

### Surface scope

- **Artifact selector**: allows the researcher to choose two or more artifacts from the artifact registry. Artifact identity comes from `GET /api/v1/artifacts` — do not construct artifact lists from experiment run data client-side.
- **Version diff view**: backend-composed diff between selected artifact versions. The BFF returns the structured diff (field pairs, change labels, delta magnitudes) — the UI renders the provided diff shape without constructing its own comparison logic.
- **Side-by-side comparison surface**: two-panel display of key attributes and metrics for each selected artifact. Metric groupings are backend-shaped.
- **Evidence drawer**: per-artifact view of the originating experiment run, parent ticket, and lineage refs. Links navigate to experiment detail and ticket detail.
- **Degradation**: when `meta.surfaces.artifact_compare` is `degraded`, show last-known artifact data with a staleness banner. When `unavailable`, show unavailable state.

### Backend gaps

| Route | Status | Notes |
|---|---|---|
| `GET /api/v1/artifacts` | **missing** | artifact registry list route; must support `experiment_id`, `ticket_id`, `version`, `page_token`, `page_size` |
| `GET /api/v1/artifacts/{artifact_id}` | **missing** | artifact detail route; must expose version, provenance (linked experiment, ticket), metric summary, and lineage refs |
| `GET /api/v1/artifacts/compare` | **missing** | comparison diff route; must accept two or more `artifact_id` params and return a backend-composed structured diff (`field_pairs`, `change_labels`, `delta_magnitudes`); do not compute the diff client-side |
| Artifact versioning semantics | **missing** | artifact identity and versioning contract (version numbering, immutability rules, and version ancestry) must be agreed before artifact selectors can be packet-defined |

### Packetization prerequisite

Artifact identity and versioning semantics must be locked before a compare view can be packet-defined. Depends on Experiment Launch (`RW-04`) producing versioned artifact refs with stable `artifact_id` values that can be resolved through the registry.

### Lovable readiness gate

`false` — the artifact registry routes, comparison diff route, and versioning semantics must all be implemented and field shapes locked before a screen spec can be opened.

---

## Backend Gap Matrix

Each row is scoped to one or more modules. A module advances to Lovable-ready when all rows assigned to that module (and its upstream prerequisites) are resolved — not when every gap in the family is resolved. See the Promotion Criteria section for the per-module gate definition.

| Route | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `POST /api/v1/research/tickets` | RW-01 | contract published — BFF implementation pending | ticket creation form and lifecycle foundation |
| `GET /api/v1/research/tickets` | RW-01, RW-02, RW-03, RW-04 | contract published — BFF implementation pending | ticket list surface and cross-module corpus identity |
| `GET /api/v1/research/tickets/{ticket_id}` | RW-01 | contract published — BFF implementation pending | ticket detail and `allowedActions` gating |
| `PATCH /api/v1/research/tickets/{ticket_id}` | RW-01 | contract published — BFF implementation pending | lifecycle transitions |
| `GET /api/v1/research/search` | RW-02 | contract published — BFF implementation pending | entire Search module |
| Search index adapter | RW-02 | contract published — BFF implementation pending | BFF-side search corpus; blocks query execution |
| `GET /api/v1/research/analysis` | RW-03 | contract published — BFF implementation pending | analysis list and filter surface |
| `GET /api/v1/research/analysis/{analysis_id}` | RW-03 | contract published — BFF implementation pending | analysis detail and metric aggregation |
| Metric aggregation endpoint | RW-03 | contract published — BFF implementation pending | backend-owned metric grouping; blocks Analyze screen spec until live |
| `POST /api/v1/experiments/launch` | RW-04 | missing write route | experiment creation and async run entry |
| `GET /api/v1/experiments/{experiment_id}` | RW-04, RW-05 | missing read route | run status, `allowedActions.canCancel`, and artifact lineage |
| `GET /api/v1/experiments` | RW-04 | missing read route | run history list |
| `POST /api/v1/experiments/{experiment_id}/cancel` | RW-04 | missing write route | cancellation command |
| Experiment state machine | RW-04 | missing lifecycle contract | run status copy and state machine display |
| `GET /api/v1/artifacts` | RW-05 | missing read route | artifact selector and registry |
| `GET /api/v1/artifacts/{artifact_id}` | RW-05 | missing read route | artifact detail and evidence drawer |
| `GET /api/v1/artifacts/compare` | RW-05 | missing read route | diff view; backend must own the comparison computation |
| Artifact versioning semantics | RW-05 | missing contract | version identity and ancestry needed before selector can be spec'd |

---

## Internal Ordering and Dependency Chain

| Position | Module | Why this order | Upstream dependency within workbench |
|---|---|---|---|
| Wave 3 — 1st | `RW-01 Research Ticket` | foundational entity that all other modules reference for corpus identity, lineage, and history; must be implemented first so search, analysis, and experiment launch can cite stable ticket IDs | none — can start as soon as Wave 3 opens |
| Wave 3 — 2nd | `RW-02 Search` | queries the research ticket corpus and surfaces ticket-linked results; requires the ticket read model to resolve `linked_ticket_id` hits | Research Ticket: `GET /api/v1/research/tickets/{ticket_id}` must be live |
| Wave 3 — 3rd | `RW-03 Analyze` | aggregates ticket and experiment result data; scoped by `ticket_id`; metric aggregation builds on the same corpus identity as Search | Research Ticket read model; analysis payload contract must be agreed after ticket IDs are stable |
| Wave 3 — 4th | `RW-04 Experiment Launch` | tracks lineage back to the originating ticket; run status polling must be a stable contract before Artifact Compare can rely on the produced artifact refs | Research Ticket lineage; Analyze result contract as launch input context |
| Wave 3 — 5th | `RW-05 Artifact Compare` | compares versioned artifacts produced by experiments; requires stable artifact identity from Launch, versioned refs, and a backend-composed diff surface | Experiment Launch: versioned artifact refs must be live (the artifact registry route `GET /api/v1/artifacts` is RW-05's own backend gap, not an upstream Launch dependency) |

---

## Promotion Criteria

A Research Workbench module moves from **not ready** to **ready** (and may be handed to Lovable) when all of the following are true:

1. All BFF routes listed in that module's Backend Gaps table are implemented and have agreed field shapes.
2. The module's `meta.surfaces.*` staleness signals are defined and wired through to the canonical degradation banner.
3. All `allowedActions` authority signals for that module are backend-shaped and documented.
4. An example payload JSON exists for the module's primary read surface.
5. The upstream prerequisite modules are already Lovable-ready (per the dependency chain above).

No Research Workbench module should be handed to Lovable before its own criteria and all upstream criteria are met.

---

## Separation Rules

When authoring packet language for these modules:

- Put missing list/detail shell copy, packet language, form or display spec work, and state machine wording in `Missing screen-spec work`.
- Put absent BFF routes, missing state machines, unresolved read models, unresolved versioning semantics, or absent search index infrastructure in `Backend or contract dependencies`.

---

## Canonical References

- Backlog source: `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` (Research Workbench section)
- Dependent services: `BP5-SVC-005` (deployment orchestration saga), `BP5-SVC-014` (persona platform and consultation read surfaces)
- Cross-cutting substrates: `PKT-005` degradation banner and SSE substrate must be inherited by any Research Workbench module that adopts live-update behavior
- Handoff directory: `docs/pantheon-handoffs/RW-005-research-workbench/`

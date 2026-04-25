# RW-005 Research Workbench — Canonical Packet Family

## Header

- Packet family ID: `RW-005`
- Workbench: Research Workbench
- Phase origin: `BP5-WB-005`
- Lovable readiness: **partial** — RW-01 Research Ticket routes live; RW-02 Search routes live; RW-03 Analyze routes live; RW-04 Experiment Launch routes live (handoff bundle published 2026-04-20); RW-05 Artifact Compare routes live (handoff bundle published 2026-04-21) — all five modules are route-live; Lovable may now proceed with production UI for all five modules
- Recommended wave: Wave 3 — after Operator Console (Wave 1–2) and Persona Workbench (Wave 1–2) packetization are settled
- Reviewer: Codex

---

## Objective

Give researchers one coherent workbench for creating and tracking research tickets, running searches against the research corpus, composing analysis views, launching and monitoring experiments, and comparing versioned artifacts produced by those experiments. All data and CTA authority must come from the Pantheon BFF — no local derivation, no mock state, no client-side corpus assembly.

---

## Module Inventory

| Module ID | Module name | Screen / surface scope | Lovable readiness | Wave order |
|---|---|---|---|---|
| `RW-01` | Research Ticket | ticket list, ticket detail, lifecycle state machine | route-live — current frontend loop closed; no open Pantheon hardening blocker remains for the current wave | Wave 3 — 1st |
| `RW-02` | Search | query input, result list, filter rail, result drilldown | **route-live — ready for Lovable implementation** | Wave 3 — 2nd |
| `RW-03` | Analyze | analysis result view, metric aggregation, comparative summary | **route-live — ready for Lovable implementation** | Wave 3 — 3rd |
| `RW-04` | Experiment Launch | launch form, parameter inputs, async run status, run history | **route-live — ready for Lovable implementation** | Wave 3 — 4th |
| `RW-05` | Artifact Compare | artifact selector, version diff view, side-by-side compare | **route-live — ready for Lovable implementation** | Wave 3 — 5th |

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
| `POST /api/v1/research/tickets` | **live** | create route is mounted on the published title / description / priority / owner contract; no additional Pantheon route work is required for the current wave |
| `GET /api/v1/research/tickets` | **live** | list route field shape, pagination, and `meta.surfaces.ticket_list` are mounted on the published contract; the prior hardening follow-up is now closed |
| `GET /api/v1/research/tickets/{ticket_id}` | **live** | detail route, lifecycle history, linked refs, and `allowedActions` are mounted on the published contract; the prior hardening follow-up is now closed |
| `PATCH /api/v1/research/tickets/{ticket_id}` | **live** | lifecycle transition and editable field semantics are mounted on the live route family; no Pantheon hardening blocker remains for the current wave |

### Packetization prerequisite

The ticket lifecycle states, `allowedActions` shape, example payload, and frontend handoff bundle are now published as canonical BFF truth via `RW-01-FOUNDATION-001`. This remains the foundational entity for Search, Analyze, and Experiment Launch, and the live BFF routes now honor that published field shape for the current wave.

### Lovable readiness gate

`route-live` — the RW-01 route family is mounted, the current frontend packet is already closed, and no Pantheon hardening blocker remains for the current wave. Lovable and downstream packet-family docs must not treat RW-01 as blocked or pending-BFF; reopen only if a later regression creates a new UI-facing contract change.

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
| `GET /api/v1/research/search` | **live** | route live and returning the published `SearchResult` row shape, pagination contract, `meta.surfaces.search_results`, and `meta.index_adapter.*` as of `2026-04-20T13:40:00Z` |
| Search index adapter | **live** | backend-owned corpus indexing, adapter state, indexed match types, and source watermark metadata are confirmed live; UI must preserve backend ranking and coverage semantics |

### Packetization prerequisite

The query parameters, result shape, filter semantics, example payload, and frontend handoff bundle are now published as canonical BFF truth via `RW-02-SEARCH-001`, and the live BFF route is returning that field shape. Search still depends on the Research Ticket read model (`RW-01`) for corpus identity so that `linked_ticket_id` is resolvable, but no additional route implementation is required before frontend activation.

### Lovable readiness gate

`route-live` — the search route and search index adapter are live, the frontend handoff bundle is published, and Lovable UI implementation may begin.

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
| `GET /api/v1/research/analysis` | **live** | route live and returning the published ResearchAnalysisSummary projection, canonical filters, pagination contract, and `meta.surfaces.analysis_results` as of `2026-04-21T18:30:00Z` |
| `GET /api/v1/research/analysis/{analysis_id}` | **live** | detail route live and returning backend-grouped ordered `metric_groups[]`, backend-authored `comparative_summary`, and canonical `links.*` fields |
| Analysis result / metric aggregation payload | **live** | backend-owned metric grouping and comparison deltas are now shipped inside the RW-03 detail payload; the frontend must render the pre-aggregated payload as returned |

### Packetization prerequisite

The research result payload contract (metric keys, aggregation shape, comparison payload, and `meta.surfaces` staleness fields) is now published as canonical BFF truth via `RW-03-ANALYZE-001`, and the live BFF routes now honor that field shape. Analyze still depends on the Research Ticket read model (`RW-01`) for ticket-scoped queries, but no additional RW-03 BFF implementation is required before frontend activation.

### Lovable readiness gate

`route-live` — the RW-03 list/detail routes are live, the backend-owned metric grouping and comparative summary are published through those routes, and the frontend handoff bundle is published. Lovable UI implementation may begin.

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
| `POST /api/v1/experiments/launch` | **live** | route live and returning published field shape as of `2026-04-20T12:45:00Z` |
| `GET /api/v1/experiments/{experiment_id}` | **live** | detail/status route, durable progress payload, `artifact_ids`, `allowedActions.canCancel`, and `meta.surfaces.experiment_status` live |
| `GET /api/v1/experiments` | **live** | run history list route, pagination, and `meta.surfaces.experiment_history` live; persisted run records confirmed |
| `POST /api/v1/experiments/{experiment_id}/cancel` | **live** | cancel command route live; `allowedActions.canCancel` gating confirmed |
| Experiment state machine | **live** | BFF-owned lifecycle (`queued → running → completed | failed | canceled`) and cancel authority semantics confirmed live |

### Packetization prerequisite

All four RW-04 routes are live and returning the published field shape. The frontend handoff bundle is published at `docs/pantheon-handoffs/RW-04-experiment-launch/FRONTEND_CHANGE_SPEC.md`. The coordination bundle (`contract-ready` + `lovable-ui-task`) is ready.

### Lovable readiness gate

`route-live` — all four routes are live, `allowedActions.canCancel` is wired, and the frontend handoff bundle is published. Lovable UI implementation may begin.

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
| `GET /api/v1/artifacts` | **live** | route live and returning the published artifact list row shape, `experiment_id`/`ticket_id`/`lineage_id`/`status` filters, pagination, and `meta.surfaces.artifact_list` |
| `GET /api/v1/artifacts/{artifact_id}` | **live** | detail route live; returns version chain, provenance (linked experiment and ticket), full metrics, lineage refs, `allowedActions.canCompare`, and `meta.surfaces.artifact_detail` |
| `GET /api/v1/artifacts/compare` | **live** | comparison diff route live; accepts two to four `artifact_id` params; returns `field_pairs` with `change_label`, `delta_magnitude`, `delta_direction`, `delta_display`, `change_summary`, and `provenance_pairs`; BFF owns all comparison computation |
| Artifact versioning semantics | **live** | artifact identity, immutability rules, version ancestry (`lineage_id`, `parent_artifact_id`, `version_chain`), and lifecycle states are locked and implemented |

### Packetization prerequisite

All three artifact routes are live and returning the published field shape. The frontend handoff bundle is published at `docs/pantheon-handoffs/RW-05-artifact-compare/FRONTEND_CHANGE_SPEC.md`. The coordination bundle (`contract-ready` + `lovable-ui-task`) is ready.

### Lovable readiness gate

`route-live` — all three routes are live, `allowedActions.canCompare` is wired, artifact versioning and immutability semantics are confirmed, and the frontend handoff bundle is published. Lovable UI implementation may begin.

---

## Backend Gap Matrix

Each row is scoped to one or more modules. A module advances to Lovable-ready when all rows assigned to that module (and its upstream prerequisites) are resolved — not when every gap in the family is resolved. See the Promotion Criteria section for the per-module gate definition.

| Route | Module(s) | Gap type | Blocking what |
|---|---|---|---|
| `POST /api/v1/research/tickets` | RW-01 | contract published — BFF implementation pending | ticket creation form and lifecycle foundation |
| `GET /api/v1/research/tickets` | RW-01, RW-02, RW-03, RW-04 | contract published — BFF implementation pending | ticket list surface and cross-module corpus identity |
| `GET /api/v1/research/tickets/{ticket_id}` | RW-01 | contract published — BFF implementation pending | ticket detail and `allowedActions` gating |
| `PATCH /api/v1/research/tickets/{ticket_id}` | RW-01 | contract published — BFF implementation pending | lifecycle transitions |
| `GET /api/v1/research/search` | RW-02 | **live** | resolved — ranked result projection, pagination, `meta.surfaces.search_results`, and `meta.index_adapter.*` are live; handoff bundle at `docs/pantheon-handoffs/RW-02-search/` |
| Search index adapter | RW-02 | **live** | resolved — backend-owned search corpus, adapter state, indexed match types, and source watermark metadata are live |
| `GET /api/v1/research/analysis` | RW-03 | **live** | resolved — analysis list, canonical filters, pagination, and `meta.surfaces.analysis_results` are live; handoff bundle at `docs/pantheon-handoffs/RW-03-analyze/` |
| `GET /api/v1/research/analysis/{analysis_id}` | RW-03 | **live** | resolved — analysis detail, ordered `metric_groups[]`, backend-authored `comparative_summary`, and canonical `links.*` fields are live |
| Metric aggregation payload | RW-03 | **live** | resolved — backend-owned grouping is delivered through the RW-03 detail payload; no client-side grouping or diff computation is allowed |
| `POST /api/v1/experiments/launch` | RW-04 | **live** | resolved — `docs/bff/RW-04-experiment-launch.md`; handoff bundle at `docs/pantheon-handoffs/RW-04-experiment-launch/` |
| `GET /api/v1/experiments/{experiment_id}` | RW-04, RW-05 | **live** | resolved — run status, `allowedActions.canCancel`, and artifact lineage live |
| `GET /api/v1/experiments` | RW-04 | **live** | resolved — run history list with pagination and `meta.surfaces.experiment_history` live |
| `POST /api/v1/experiments/{experiment_id}/cancel` | RW-04 | **live** | resolved — cancellation command and cancel authority gating live |
| Experiment state machine | RW-04 | **live** | resolved — BFF-owned lifecycle and cancel authority semantics confirmed live |
| `GET /api/v1/artifacts` | RW-05 | **live** | resolved — `docs/bff/RW-05-artifact-compare.md`; handoff bundle at `docs/pantheon-handoffs/RW-05-artifact-compare/` |
| `GET /api/v1/artifacts/{artifact_id}` | RW-05 | **live** | resolved — `docs/bff/RW-05-artifact-compare.md`; version chain, provenance, metrics, and `allowedActions.canCompare` live |
| `GET /api/v1/artifacts/compare` | RW-05 | **live** | resolved — `docs/bff/RW-05-artifact-compare.md`; BFF owns all comparison computation including `field_pairs`, `change_summary`, and `provenance_pairs` |
| Artifact versioning semantics | RW-05 | **live** | resolved — `docs/bff/RW-05-artifact-compare.md`; version identity, immutability rules, and ancestry semantics are confirmed live |

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

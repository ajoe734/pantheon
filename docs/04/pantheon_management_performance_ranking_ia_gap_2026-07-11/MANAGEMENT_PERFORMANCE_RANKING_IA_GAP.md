# Management Performance And Ranking Information Architecture Gap - 2026-07-11

Status: archived gap analysis and implementation source of truth

Owner: Codex

Scope:

- management sidebar groups and route aliases;
- Performance Attribution, Portfolio Book, Persona League, Quarterly Ranking,
  Promotion Allocation, cockpit summaries, and Human Review entry points;
- Persona, Strategy, Capital Pool, Rebalance, and Ranking Policy details;
- Agora strategy performance boundary;
- BFF contracts required by these operator workflows.

## Executive Decision

The current console has useful data, but its navigation was assembled by
feature delivery order rather than by operator intent. Performance, exposure,
ranking, recommendation, allocation, and approval appear as separate pages or
are embedded again inside other pages. An operator cannot reliably tell which
page is authoritative, and a developer can add another route without noticing
that the same workflow already exists elsewhere.

The target console has three canonical centers:

1. **Performance Center** for results, attribution, exposure, holdings, source
   coverage, and risk diagnostics.
2. **Rankings Center** for short-cycle operational ranking and quarterly formal
   evaluation. Ranking is evidence, not an action.
3. **Governance Decisions** for recommendations, capital-allocation proposals,
   ranking policy, Human Review state, and apply receipts. Governance consumes
   ranking evidence but does not render another ranking table.

The operating loop becomes:

```text
Cockpit / Fleet
      |
      v
Performance Center -> Rankings Center -> Governance Decisions -> Human Review
      ^                     |                    |                     |
      |                     v                    v                     v
capital + risk       comparative evidence   proposed action      apply receipt
```

No existing URL is allowed to become a silent dead end. Old URLs redirect to a
canonical center while preserving persona, period, dimension, capital pool,
and review context.

## What Was Audited

Audit date: 2026-07-11 UTC.

The audit covered:

- all 50 visible management sidebar items in 7 current groups;
- 25 live management URLs relevant to monitoring, performance, risk, ranking,
  allocation, and governance;
- embedded ranking and allocation tabs inside Promotion Allocation;
- Persona and Strategy detail performance tabs;
- management cockpit links and secondary operations navigation;
- Agora Trading Room strategy-performance page;
- App route aliases, redirects, exported-but-unrouted page components, and the
  management acceptance route baseline;
- live BFF result counts for personas, strategies, exposure, holdings,
  attribution, ranking, recommendations, pools, rebalances, and formulas.

Detailed evidence is archived beside this document and must be used during
implementation rather than reconstructed from memory.

## Core Gaps

### 1. Duplicate Authority

Persona League exists as a standalone page and again as the `real-ranking` tab
inside Promotion Allocation. Quarterly Ranking exists as a standalone page and
again as `paper-candidates`. Cockpit links can enter either copy. This creates
multiple answers to the same question: who is ranked and why?

Decision:

- Rankings Center is the only full ranking authority.
- Governance Decisions may show a recommendation's rank snapshot and evidence
  link, but cannot host a second sortable ranking table.

### 2. Performance And Capital Are Split By Backend Shape

Portfolio Book and Performance Attribution are separate because they consume
different BFF payloads, not because operators perform unrelated jobs. The
result is a broken investigation path and ambiguous fallback summaries.

Decision:

- Performance Center combines Overview, Attribution, and Exposure & Holdings.
- Formal, partial, fallback, degraded, and unavailable source states remain
  explicit. Consolidation must not hide missing joins or turn fallback into
  formal attribution.

### 3. Ranking And Governance Are Conflated

Ranking tables currently sit beside promotion and allocation controls. This
suggests that rank can directly mutate access or capital, even though those
actions require governed review.

Decision:

- Rankings Center compares and explains.
- Governance Decisions recommends, submits, approves, rejects, expires, and
  records application receipts.
- All capital, access, freeze, promotion, demotion, and rebalance mutations are
  gated by Human Review and precondition evidence.

### 4. Menu Groups Reflect Implementation History

The current Pathreon Management group mixes cockpit, fleet, inbox, trading,
research, evolution, evidence, and intent. The performance group mixes
monitoring and governance. Operations contains many execution and governance
tools. Human decisions are split among Inbox, Interventions, Approvals, and
Governance.

Decision:

- Reclassify navigation by operator job, using the seven target groups in the
  archived target IA.
- Keep one sidebar and remove the duplicated `ManagementOperationsNav` from
  individual pages.
- Command palette labels and route inventory must derive from the same route
  manifest as the sidebar.

### 5. Entity Detail Pages Do Not Share A Performance Contract

Persona Detail's Performance tab currently shows win rate, routed strategies,
and recent activity. It looks like performance analysis but does not provide
the same period, attribution, source confidence, or risk model as the formal
performance pages. Strategy Detail has a legitimate strategy-context time
series. Capital Pool, Rebalance, and Ranking Formula detail components exist,
but canonical App routes redirect into Promotion Allocation instead.

Decision:

- Persona Detail shows a compact performance summary and deep-links into the
  Performance Center with persona context.
- Strategy Detail keeps contextual performance, with a link to formal
  attribution.
- Capital, rebalance, and policy each get one canonical detail route only when
  their live contracts can honestly support the page; otherwise show an
  explicit unavailable state, never fixture authority.

### 6. Trading Performance And Management Performance Lack A Boundary

Agora `/agora/strategy-performance` measures Trading Room execution behavior.
It is not a duplicate of management attribution, but the naming and navigation
do not explain the distinction.

Decision:

- Keep Agora Strategy Performance in Trading Room.
- Add context links between execution diagnostics and management performance.
- Do not merge Agora into the management sidebar or reuse management ranking
  actions there.

### 7. Route Debt Hides Dead Or Stale Surfaces

`RankingDashboardPage` is implemented without an App route. Capital Pool,
Rebalance, and Ranking Formula detail exports are not reached because routes
redirect elsewhere. Old aliases exist at both top level and under management.
The acceptance route baseline does not fully represent the current route tree.

Decision:

- Build a canonical route manifest and migration tests first.
- Preserve only documented compatibility redirects.
- Remove dead components and stale aliases after hosted migration evidence
  proves no required entry point is lost.

## Target Operating Model

### Daily Monitoring

1. Cockpit gives summaries and alerts, never a second detailed table.
2. Fleet identifies who is running, degraded, stale, or awaiting review.
3. Performance Center explains PnL, drawdown, risk, exposure, holdings, and
   source coverage using shared filters.
4. Rankings Center compares eligible personas over a declared period and shows
   missing evidence or exclusions.
5. Governance Decisions turns evidence into a proposal and Human Review packet.
6. The operator verifies the approval and apply receipt before treating the
   capital or access change as complete.

### Incident Triage

1. Enter from Cockpit, Fleet, Risk Center, or a performance anomaly.
2. Preserve persona, runtime, pool, strategy, asset, broker, period, and source
   context while navigating.
3. Separate investment-performance incidents from missing-telemetry incidents.
4. Create a data-quality incident when evidence is unavailable.
5. Emergency containment may reduce risk, but cannot promote or increase
   allocation without normal governance.

### Governance Cycle

1. Rankings Center produces a reproducible ranking snapshot.
2. Governance Decisions creates a recommendation referencing that snapshot.
3. Human Review verifies eligibility, evidence coverage, policy, and action
   preconditions.
4. Approved apply emits an auditable receipt and updates status.
5. Rejected, expired, superseded, and blocked decisions remain visible.

## Canonical Centers

### Performance Center

Canonical route:

```text
/management/performance?tab=overview|attribution|exposure
```

Shared filters:

- period and as-of time;
- persona, runtime, strategy, pool, asset class, broker;
- paper, canary, and live stage;
- source confidence and freshness.

Required page behavior:

- Overview summarizes NAV/PnL/drawdown/exposure and data coverage.
- Attribution explains contribution and identifies unmatched sources.
- Exposure & Holdings shows capital ownership, marks, staleness, and risk
  exceptions.
- Filters survive tab changes and deep links.
- `nan`, `NaN`, `undefined`, and fake zeroes are never operator-facing values.

### Rankings Center

Canonical route:

```text
/management/rankings?tab=rolling|quarterly
```

Required page behavior:

- Rolling is the current Persona League responsibility: daily or weekly
  operational comparison, watch state, and retraining/freeze candidates.
- Quarterly is formal evaluation: rank, score, criteria, eligibility,
  exclusions, evidence coverage, period, and governance snapshot id.
- Both tabs deep-link to Fleet and Performance Center.
- A row can create or inspect a recommendation, but cannot apply capital or
  access changes.

### Governance Decisions

Canonical route:

```text
/management/governance-decisions?tab=recommendations|capital|policy
```

Required page behavior:

- Recommendations Queue shows proposed actions and review/apply lifecycle.
- Capital Allocation shows proposals, pool impacts, rebalance state, limits,
  and receipts.
- Ranking Policy shows formula versions, effective periods, evidence
  requirements, and policy history.
- The center references immutable ranking snapshots instead of embedding a
  live ranking table.

## Shared Read Model

Every center and entity deep link must preserve a common identity envelope:

```text
persona_id
runtime_ids
capital_pool_ids
sleeve_ids
strategy_ids
artifact_ids
broker_ids
deployment_stage
period
as_of
source_refs
```

Every displayed metric set must include:

```text
source_confidence = formal | partial | fallback | degraded | unavailable
freshness_state   = current | stale | unknown
observed_at
coverage
missing_bindings
```

Every governed operation must include:

```text
recommendation_id
ranking_snapshot_id
review_id
requested_action
precondition_results
approval_state
apply_receipt_id
```

## Delivery Strategy

### Wave 0: Lock Foundations

- Create one canonical route/menu manifest and compatibility redirect map.
- Lock the cross-page BFF query envelope and source-confidence semantics.
- Add regression tests that enumerate canonical routes and redirects.

### Wave 1: Build The Three Centers

- Consolidate Portfolio Book and Performance Attribution into Performance
  Center without weakening diagnostics.
- Consolidate Persona League and Quarterly Ranking into Rankings Center.
- Refactor Promotion Allocation into Governance Decisions without embedded
  ranking copies.

### Wave 2: Integrate And Migrate

- Update cockpit, fleet, entity details, command palette, breadcrumbs, and
  Agora context links.
- Migrate aliases while preserving query context.
- Remove secondary page navigation and dead route components after migration
  tests pass.

### Wave 3: Hosted Acceptance

- Verify desktop and mobile navigation, deep links, filter persistence, empty
  and degraded states, and no overlapping controls.
- Prove the full loop from Fleet/Performance through Rankings, Governance,
  Human Review, and apply receipt.
- Archive PRs, merge SHAs, deployments, screenshots, API evidence, and residual
  risks.

## Global Acceptance

The initiative is complete only when all of the following are true:

1. One canonical page answers each operator question.
2. Sidebar, command palette, breadcrumbs, cockpit links, and route tests use the
   same information architecture.
3. Legacy URLs redirect and preserve operational context.
4. Performance fallback data is labeled and never counted as formal
   attribution.
5. Rankings are not duplicated inside Governance Decisions.
6. No ranking, recommendation, or button silently mutates live capital or
   access.
7. Persona and Strategy detail pages link to formal performance analysis.
8. Agora execution performance remains a separate, clearly linked domain.
9. Dead routes and components are removed only after hosted migration proof.
10. The closeout records merged PRs, deployed revisions, validation, and known
    residual data gaps.

## Non-Goals

- Rewriting investment formulas during information-architecture work.
- Inventing ranking or attribution values when live sources are empty.
- Combining Human Inbox, Approvals, and every governance tool into one backend
  mutation endpoint.
- Direct live allocation, promotion, or access mutation from a ranking row.
- Moving Agora Trading Room pages into Management.

## Execution Source

Implementation is dispatched from:

- `docs/bff/execution-tasks/2026-07-11-management-performance-ranking-ia/INDEX.md`

Task IDs are `MGMT-PERF-IA-001` through `MGMT-PERF-IA-008`. Older
`MGMT-OPS-*` work remains historical evidence and is not reopened by this
packet.

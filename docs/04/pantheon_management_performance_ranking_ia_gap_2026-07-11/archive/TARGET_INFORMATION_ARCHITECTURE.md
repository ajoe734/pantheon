# Target Management Information Architecture

Status: implementation contract

## Sidebar Groups

### Operational Overview

- Management Cockpit
- Persona Fleet
- Trading Pulse
- Human Inbox

Purpose: answer what is happening now and what needs attention.

### Performance And Risk

- Performance Center
- Risk Center

Purpose: answer where money and risk sit, what changed, and why.

### Ranking And Governance

- Rankings Center
- Governance Decisions

Purpose: compare evidence, create recommendations, and inspect governed action
state.

### Research And Evolution

- research portfolio and experiment surfaces;
- Evolution Journal and mutation investigation;
- other existing research/evolution entries after duplicate removal.

### Registry And Lineage

- personas, strategies, artifacts, evidence, and identity/lineage registries.

### Execution Operations

- runtime, broker, reconciliation, intervention, and execution health surfaces.

### Live Readiness And System

- canary/live readiness;
- capabilities and access;
- platform and system administration.

The implementation task must inventory every existing sidebar item and assign
it exactly once. Group labels can be localized, but responsibilities and order
must remain stable.

## Canonical Route Manifest

| Center | Canonical route | Tabs |
|---|---|---|
| Performance Center | `/management/performance` | `overview`, `attribution`, `exposure` |
| Rankings Center | `/management/rankings` | `rolling`, `quarterly` |
| Governance Decisions | `/management/governance-decisions` | `recommendations`, `capital`, `policy` |

The route manifest must drive:

- sidebar entries;
- command palette entries;
- breadcrumbs and page titles;
- cockpit destination links;
- route acceptance tests;
- compatibility redirect tests.

## Navigation Rules

1. The management sidebar is the only full-section navigation.
2. Center tabs switch responsibilities inside a center.
3. Entity detail pages use breadcrumbs and contextual actions, not a second
   management navigation bar.
4. Links preserve relevant query parameters and discard unrelated ones.
5. Back navigation returns to the originating center and filter state when
   practical.
6. Mobile uses the same hierarchy and labels as desktop.

## Shared Filters

Performance and ranking deep links share a normalized query vocabulary:

```text
persona
runtime
strategy
capital_pool
asset_class
broker
stage
period
as_of
source_confidence
```

Frontend adapters may translate backend field names but URLs and analytics
events use these canonical names.

## Data Confidence UI

Each center displays:

- source confidence: formal, partial, fallback, degraded, unavailable;
- last updated / observed at;
- coverage summary;
- missing binding count;
- explicit empty or unavailable states.

Fallback data can support triage but cannot support an unqualified performance,
rank, promotion, or allocation claim.

## Governed Action Rules

Allowed directly from analysis pages:

- inspect evidence;
- open related entity/runtime/pool;
- create or inspect a recommendation;
- create a Human Review request;
- flag data quality or request retraining.

Not allowed without governed apply:

- increase allocation;
- promote deployment stage;
- expand permissions;
- execute rebalance;
- resume a frozen live runtime.

Every applied action links to the recommendation, ranking snapshot, review,
precondition results, and apply receipt.

## Design Constraints

- No cards inside cards and no page sections styled as floating cards.
- Dense operational tables must have stable columns, responsive overflow, and
  readable empty states.
- Use tabs for center views, menus for option sets, and icons for familiar
  commands.
- Never show visible `nan`, `NaN`, `undefined`, or raw backend exceptions.
- Page headings match their operational scope; compact panels do not use hero
  typography.
- Desktop and mobile screenshots must prove that controls and text do not
  overlap.

# MGMT-PERF-IA-007 BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-007-SIDECAR-BFF-HANDOFF` |
| Parent task | `MGMT-PERF-IA-007` — Migration cleanup and regression |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Date | `2026-07-12` |
| Status | Review handoff prepared; parent absorption pending |
| Mutates canonical truth | `false` |

This is a support-only packet. It does not change L1 canonical truth, BFF
runtime or schemas, route registries, governance behavior, or `execute-plans`
source. It does not approve or finalize the parent task.

## 1. Current Dependency Truth

At packet time the parent is `todo` and depends on `MGMT-PERF-IA-003` through
`006`. Cleanup must not remove compatibility paths or page implementations
until those center and contextual-integration tasks provide merged migration
evidence.

`MGMT-PERF-IA-001` has already recorded `execute-plans` PR `#250`, merge commit
`7d1f011074a72e36e0da24e658e0b7b75d4317de`, as the canonical route/menu
manifest baseline. That baseline is the input to cleanup; this sidecar does not
redefine it.

The following claims remain dependency-gated:

| Dependency | Required before cleanup can claim completion |
|---|---|
| `MGMT-PERF-IA-002` | One identity/query vocabulary; explicit confidence, freshness, coverage, missing-binding, and observed-time semantics; compatibility disposition for existing consumers. |
| `MGMT-PERF-IA-003` | Performance Center is the proven canonical owner of overview, attribution, exposure, and holdings. |
| `MGMT-PERF-IA-004` | Rankings Center is the proven canonical owner of rolling and quarterly ranking. |
| `MGMT-PERF-IA-005` | Governance Decisions is the proven canonical owner of recommendations, capital proposals, policy, Human Review state, and apply receipts. |
| `MGMT-PERF-IA-006` | Cockpit, Fleet, entity detail, Inbox, and Agora links preserve context and no longer depend on duplicate surfaces. |

## 2. BFF Query And Migration Gap Ledger

The cleanup task should not invent a new BFF endpoint. Its BFF concern is
whether legacy navigation and canonical destinations preserve enough query
context to issue the same governed reads.

| Gap / check | Required behavior | Cleanup disposition |
|---|---|---|
| Common identity | Preserve `persona_id`, runtime, strategy, capital pool, sleeve, artifact, broker, and deployment stage identifiers supported by the merged manifest/read model. | Redirect mapping and crawl tests must compare input and final query context. Do not silently drop unknown-but-supported context. |
| Time context | Preserve period, as-of time, and ranking snapshot context where applicable. | Tab normalization may change destination tabs, but must not reset the investigation window. |
| Source truth | Canonical reads expose formal, partial, fallback, degraded, or unavailable confidence plus freshness, coverage, missing bindings, and observed time. | Removal of an old page must not introduce fixture fallback, fake zeroes, or client-derived confidence. |
| Ranking versus action | Ranking snapshots are evidence; recommendation, approval, and apply receipt are distinct records. | Redirects from ranking aliases must land in Rankings, not directly execute or imply a governance action. |
| Empty detail contracts | Capital Pool, Rebalance, or Ranking Policy detail may be genuinely empty/unavailable. | Preserve an explicit unavailable state or redirect decision; never retain a dead page by filling it with fixtures. |
| Compatibility consumers | Existing BFF consumers must remain compatible or have an explicit migration response. | Dead-code removal requires call-site/import/route evidence, not export-name inspection alone. |
| Error and degraded states | Empty, stale, fallback, degraded, unavailable, and transport-failure states remain distinguishable. | Crawl and hosted smoke must cover non-happy paths; an empty collection is not automatically a route failure. |

If a redirect cannot preserve a required identity or period field because the
merged manifest/read model does not define its mapping, the parent should log a
bounded BFF/frontend gap and retain the compatibility entry. Cleanup must not
guess a translation.

## 3. Operator Journeys To Preserve

### A. Legacy performance investigation

1. Operator follows a legacy Portfolio Book or Performance Attribution URL
   carrying persona, runtime, pool, strategy, period, and source filters.
2. Compatibility routing resolves once to
   `/management/performance?tab=overview|attribution|exposure`.
3. The final URL retains applicable investigation context.
4. Performance Center distinguishes formal evidence from fallback, degraded,
   unavailable, or missing joins and never renders `nan` or fabricated zeroes.
5. Back/forward navigation does not bounce through a redirect loop.

### B. Ranking evidence to governed decision

1. Operator follows a Persona League, Quarterly Ranking, or old embedded
   Promotion Allocation ranking entry.
2. The route lands in `/management/rankings?tab=rolling|quarterly`, preserving
   persona, period, dimension, and snapshot context.
3. Operator inspects comparative evidence and exclusions.
4. Creating or opening a recommendation navigates to Governance Decisions or
   Human Review with the immutable ranking snapshot reference.
5. No ranking row directly mutates capital, access, promotion, freeze, or
   rebalance state; completion requires an apply receipt.

### C. Entity and incident drill-down

1. Operator enters from Cockpit, Fleet, Persona/Strategy detail, or an alert.
2. Compact entity summaries deep-link to the canonical center rather than
   recreating a full analysis table.
3. Persona, runtime, strategy, pool, broker, stage, period, and source context
   survive the transition.
4. Missing or stale evidence leads to diagnostics/data-quality triage; risk
   containment remains a governed path.
5. Human Review return navigation restores the originating decision context.

### D. Agora boundary

1. Operator views Agora Strategy Performance as Trading Room execution
   diagnostics.
2. A context link may open Management Performance with strategy and period.
3. Labels make the domain boundary explicit; the management cleanup does not
   move Agora into the management sidebar or reuse management ranking actions.

## 4. Frontend Cleanup Handoff

The parent should perform cleanup in this order after dependencies merge:

1. Re-read the shared route manifest and compatibility redirect map from the
   merged `MGMT-PERF-IA-001` baseline.
2. Build an inventory of App routes, manifest entries, sidebar/command-palette
   items, breadcrumbs, contextual links, exported pages, and tests.
3. Assign each legacy URL one explicit outcome: canonical route,
   query-preserving compatibility redirect, explicit unavailable detail, or
   deliberate removal backed by zero call-site/route evidence.
4. Remove `ManagementOperationsNav` only after every affected page has the
   canonical sidebar, tabs, breadcrumbs, and contextual return path.
5. Resolve `RankingDashboardPage`, Capital Pool Detail, Rebalance Detail, and
   Ranking Formula Detail individually; do not treat “exported but unrouted” as
   sufficient proof that removal is safe.
6. Regenerate the route acceptance baseline from the manifest rather than
   maintaining a second handwritten route list.
7. Run desktop/mobile crawl, redirect-loop, broken-link, query-preservation,
   focus/accessibility, and no-overlap checks, followed by hosted smoke.
8. Record redirect telemetry/expiry ownership and retain compatibility routes
   until the agreed evidence window closes.

Frontend invariants:

- One sidebar hierarchy feeds desktop, mobile, command palette, and route
  acceptance inventory.
- Compatibility redirects resolve in one bounded chain and preserve applicable
  query parameters and fragments.
- Tab changes preserve shared filters.
- Redirect code never converts fallback data into formal attribution.
- Rankings compare and explain; Governance Decisions proposes and governs.
- No direct broker, runtime, capital, promotion, freeze, or access mutation is
  added by migration cleanup.
- Unknown routes and unavailable detail contracts render explicit states rather
  than fixtures or silent blank pages.

## 5. Regression Evidence Matrix

| Evidence | Minimum assertion |
|---|---|
| Manifest unit tests | Unique ids/labels/destinations; canonical centers appear once; expected legacy aliases are enumerated. |
| Redirect tests | No loops; bounded redirect depth; persona/runtime/strategy/pool/period/snapshot context survives. |
| Route crawl | Every sidebar, command-palette, breadcrumb, Cockpit, Fleet, entity, Inbox, and Agora management link resolves. |
| Dead-code audit | Removed exports have no App route, manifest entry, dynamic import, contextual link, test dependency, or compatibility obligation. |
| Data-state tests | Formal, partial, fallback, stale, empty, degraded, unavailable, and transport failure remain visually/semantically distinct. |
| Accessibility/layout | Keyboard/focus behavior works; desktop and mobile expose the same hierarchy; no secondary-nav overlap. |
| Hosted smoke | Canonical and legacy URLs use the deployed BFF in strict live mode and prove filter persistence and return navigation. |
| Delivery record | Frontend PR, merge SHA, deployed revision, screenshots/API evidence, redirect telemetry owner, and residual gaps are recorded. |

## 6. Parent Absorption Checklist

Parent owner `Claude` should absorb this packet only when:

- dependencies `MGMT-PERF-IA-003` through `006` have merged evidence and the
  cleanup is based on those delivered routes rather than planned shapes;
- the `MGMT-PERF-IA-002` query/confidence vocabulary used by the frontend is
  recorded and compatibility gaps are explicit;
- every removed alias/component has an evidence-backed disposition;
- automated and hosted checks cover canonical routes plus compatibility
  redirects on desktop and mobile;
- migration telemetry and redirect-expiry ownership are recorded;
- no canonical/BFF/runtime/governance change is attributed to this sidecar.

Residual gaps belong to the parent or a separately scoped follow-up. This
packet is advisory and must not be used as proof that the parent is implemented,
reviewed, deployed, or complete.

## 7. Reviewer Handoff

Reviewer `Claude` should verify:

- only this support artifact is intentionally changed;
- all current-state claims distinguish merged baseline from pending
  dependencies;
- the query ledger does not invent endpoints or promote support text into
  contract truth;
- operator journeys preserve source confidence and governed-action boundaries;
- the parent absorption checklist is sufficient to prevent premature dead-code
  or redirect removal.

Recommended approval command after review:

```bash
AI_NAME=Claude \
  REVIEW_FILE=support/sidecars/MGMT-PERF-IA-007/MGMT-PERF-IA-007-SIDECAR-BFF-HANDOFF.md \
  REVIEW_NOTES_ZH="Support-only BFF/frontend handoff approved: dependency-gated cleanup, query-context preservation, operator journeys, regression evidence, and no-direct-action boundary are explicit without changing canonical or runtime truth." \
  ./scripts/ai-status.sh approve MGMT-PERF-IA-007-SIDECAR-BFF-HANDOFF \
  "Support-only packet approved for parent owner absorption."
```

Recommended correction command:

```bash
AI_NAME=Claude ./scripts/ai-status.sh reopen \
  MGMT-PERF-IA-007-SIDECAR-BFF-HANDOFF \
  "Describe the factual correction or missing handoff evidence."
```

## 8. Preparation Evidence

- Started on `task/MGMT-PERF-IA-007-SIDECAR-BFF-HANDOFF` with `origin` pointing
  to `ajoe734/pantheon`.
- Read the task-scoped brief, collaboration guide, anchor/closeout rules, and
  task records using `AI_NAME=Codex2`.
- Read the initiative gap archive and task packets for `MGMT-PERF-IA-001`,
  `002`, `006`, and `007`.
- Did not scan `current-work.md` or the full `ai-activity-log.jsonl`.


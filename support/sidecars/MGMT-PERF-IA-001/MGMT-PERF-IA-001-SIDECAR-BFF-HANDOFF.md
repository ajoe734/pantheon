# MGMT-PERF-IA-001 BFF and Frontend Handoff Packet

Date: 2026-07-11
Sidecar owner: Codex2
Sidecar reviewer: Claude
Parent task: `MGMT-PERF-IA-001`
Helper kind: `bff_handoff_packet`
Scope: support-only material; this packet does not change canonical truth, the
route manifest, BFF contracts or runtime, frontend implementation, registry, or
governance behavior. The parent owner decides what to absorb.

## Purpose

The parent task owns the typed management route/menu manifest in
`execute-plans`. This packet identifies which existing Pantheon BFF reads can
support the proposed Performance, Rankings, and Governance centers, where the
query contract still needs the sibling `MGMT-PERF-IA-002` read-model task, and
how frontend navigation should preserve operator context without inventing a
second data model.

## Current BFF Surface Map

| Canonical center | Existing BFF reads | What the route manifest may safely assume |
|---|---|---|
| Performance / Overview | `GET /bff/management/portfolio-book`, `/portfolio-book/pools` | A composed portfolio summary and pool rows exist. The frontend must retain source/degraded metadata and must not present fallback summaries as formal attribution. |
| Performance / Attribution | `GET /bff/management/performance-attribution`; `/by-persona`; `/by-strategy`; `/by-pool` | Persona, strategy, and pool entry links can use specialized routes. Other supported dimensions remain on the generic route via its dimension query. |
| Performance / Exposure and Holdings | `GET /bff/management/portfolio-book/exposure`, `/holdings`, `/positions` | Exposure, holdings, and positions are separate reads today; the route manifest may group them under one tab but must not imply one atomic BFF response. |
| Rankings | `GET /bff/management/persona-league`, `GET /bff/management/quarterly-ranking` and quarterly drilldown/recommendation reads | Rolling operational league and quarterly governance evidence are distinct datasets. A common center may host both, but query/tab state must preserve that distinction. |
| Governance Decisions | Quarterly-ranking recommendation/review routes plus existing promotion/allocation surfaces | Governance consumes ranking evidence; it must not duplicate the ranking table as a new source of truth. Submission, Human Review, approval, and applied receipt are separate states. |

Evidence inspected: route registrations in
`services/control-plane/bff/main.py`, contract coverage in
`services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py`, and the
operations fallback rules in
`services/control-plane/bff/test_bff_mgmt_ops_001_operations_read_model_contract.py`.

## BFF Query-Gap Conclusions

No new BFF endpoint is required merely to create the typed route/menu manifest.
The parent can define canonical frontend destinations and compatibility
redirects using existing reads. However, the full three-center implementation
is blocked on `MGMT-PERF-IA-002` resolving these contract gaps:

| Gap | Required handoff to `MGMT-PERF-IA-002` / center owners |
|---|---|
| Shared identity | Lock common keys for persona, strategy, pool, asset, broker, runtime, and regime. Redirects must carry only typed, recognized keys; never pass an opaque legacy query string wholesale. |
| Shared time vocabulary | Lock `period`/quarter semantics and defaults. A quarterly snapshot must not silently become a rolling period after redirect. |
| Snapshot identity and freshness | Expose a stable snapshot/as-of identity and source confidence consistently enough that cross-center links refer to the same evidence cut. Do not compare independently refreshed rows as if atomic. |
| Filter capability discovery | The generic attribution route supports more dimensions than the specialized endpoints. The frontend needs an explicit supported-filter contract instead of guessing from URL names. |
| Partial/degraded/unavailable state | Preserve BFF `meta.surfaces` and distinguish missing formal attribution from fleet/performance-summary fallback. Empty rows are not proof of a fresh zero result. |
| Cross-center link contract | Rows need typed entity/context links, or the frontend manifest must construct them only from documented identity fields. Do not parse display labels into IDs. |
| Governance evidence lineage | Ranking snapshot, recommendation, Human Review decision, and apply receipt require separate identifiers and links. Navigation must not collapse them into one mutable “approved” row. |

The route manifest should therefore encode navigation ownership now, while
treating filter names, snapshot matching, source confidence, and governance
receipt fields as read-model inputs owned by `MGMT-PERF-IA-002`.

## Operator Journey

The intended journey is a linked evidence loop, not three disconnected pages:

1. The operator enters Persona Fleet or Cockpit with a known `persona_id` and
   follows a typed link to Performance Overview.
2. Performance opens with the preserved persona/pool and period context. The
   operator sees source status before interpreting totals.
3. The operator drills into Attribution or Exposure and Holdings without losing
   the same entity and time context. If formal attribution is unavailable, the
   UI labels fallback summary evidence and disables conclusions that require
   attribution.
4. The operator follows “View in Rankings” to the rolling or quarterly tab.
   The destination preserves identity plus period/quarter and displays the
   ranking snapshot/as-of value.
5. A governed recommendation links from that immutable ranking evidence to
   Governance Decisions. Governance shows recommendation, submitted review,
   decision, and applied receipt as separate lifecycle records.
6. Human Inbox deep-links to the same governance record and returns the
   operator to its originating ranking/performance context after review.
7. Legacy URLs redirect once to the canonical center/tab, retain only validated
   context keys, and never redirect back to a legacy alias.

For degraded reads, navigation remains available but the affected panel must
show explicit degraded/unavailable state. A missing result must not redirect the
operator to a different page that happens to contain fallback data.

## Frontend Route-Manifest Handoff

The parent-owned typed manifest should provide, for every sidebar item,
command-palette entry, breadcrumb, and legacy alias:

| Field | Requirement |
|---|---|
| Canonical route and tab | One destination owner for Performance, Rankings, and Governance Decisions. |
| Accepted context | Typed allowlist such as `persona_id`, `pool_id`, `strategy_id`, `period`, `quarter`, and governance record ID; final names must follow `MGMT-PERF-IA-002`. |
| BFF dependency | Name the read route(s) used by the destination, including whether the tab fans out to several reads. |
| Context mapping | Explicit legacy-key-to-canonical-key mapping, including incompatible or dropped keys. |
| Redirect behavior | Replace history, preserve allowed context, retain hash/tab intent where valid, and terminate after one canonical redirect. |
| Degraded behavior | Keep the canonical page shell visible, render BFF-owned source status, and disable dependent actions. |
| Cross-center targets | Construct links from stable IDs and snapshot context; never from labels or row position. |

Suggested canonical tab semantics for parent composition:

- Performance: `overview`, `attribution`, `exposure-holdings`.
- Rankings: `rolling`, `quarterly`.
- Governance Decisions: `recommendations`, `capital`, `policy`.

These names are frontend IA suggestions, not BFF contract changes. The parent
must reconcile them with the actual execute-plans manifest and the sibling read
model before implementation.

## Acceptance and Negative Boundaries

Parent/reviewer checks for absorbing this packet:

- Every existing management performance/ranking/governance entry is assigned
  once to a canonical center or an explicit compatibility redirect.
- Redirect tests cover persona/pool/period/quarter preservation, unknown-key
  removal, history replacement, refresh, and redirect-loop prevention.
- Frontend tests prove fallback performance is labeled and never promoted to
  formal attribution.
- Rolling ranking, quarterly ranking evidence, recommendation, review decision,
  and apply receipt remain visibly distinct.
- The browser calls Pantheon BFF routes only; it does not build a shadow ranking
  or aggregate holdings/attribution into a new client-owned truth.
- This sidecar creates no execute-plans files and changes no canonical or
  runtime surface.

## Parent Composition Checklist

1. `MGMT-PERF-IA-001` owner absorbs the navigation ownership, redirect, and
   typed-context guidance into the execute-plans route manifest.
2. `MGMT-PERF-IA-002` owner closes the shared identity/filter/snapshot/source
   contract gaps before Wave 1 centers depend on them.
3. Wave 1 center owners map each tab to the BFF routes above and add honest
   partial/degraded/unavailable tests.
4. Reviewer verifies this packet was used as support material only and that no
   proposed field was treated as canonical merely because it appears here.


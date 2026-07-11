# MGMT-PERF-IA-002 BFF And Frontend Handoff Follow-up 2

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-002` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar task | `MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical truth | `false` |

This packet supplements `MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF.md`. It is a
support-only delta for parent-owner absorption. It does not change BFF runtime,
frontend code, ranking formulas, governance semantics, or canonical truth.

## 1. Evidence Delta

The earlier packet described the intended cross-surface context and governed
journey. Since then, `origin/task/MGMT-PERF-IA-002` has advanced to candidate
commit `d0d4d0497` (`MGMT-PERF-IA-002: preserve scoped surface compatibility`).
That branch is not merged truth. Its contract test currently exercises the
common query keys on Performance Attribution, Persona League, Quarterly
Ranking, and Quarterly Recommendations, and checks recommendation evidence,
Human Review state, source metadata, and empty formula/rebalance collections.

The observed tests prove that requests are accepted and selected top-level
fields exist. They do not yet, by themselves, prove that every requested
filter is applied, echoed, or bound to one snapshot; that list and drilldown
share stable row identity; or that stale and unavailable requests fail
honestly. Parent review should distinguish those stronger claims from schema
presence.

## 2. Remaining BFF Query Gaps

| Gap | Required observable behavior | Suggested contract proof |
|---|---|---|
| Filter effectiveness | Every supplied identity filter narrows or explicitly rejects the result; unsupported combinations are not silently ignored. | Seed matching and non-matching rows, vary one filter at a time, and assert row identities plus an echoed effective-query object. |
| Snapshot fidelity | Requested `asOf` either selects that snapshot or returns explicit unavailable/unsupported semantics; it never relabels latest data. | Compare latest, known historical, unknown historical, and malformed timestamps. |
| Cross-surface continuity | Performance, ranking, recommendation, and drilldown preserve the same persona/runtime/strategy/pool/sleeve and snapshot boundary. | Follow backend-authored links and assert stable context and evidence references at every hop. |
| Official ranking | Rank, cohort, eligibility, exclusion reason, formula/version, and tie-breaking are backend-authored. | Supply ties, excluded rows, missing metrics, and non-finite metrics; assert deterministic ranks without client reconstruction. |
| Collection semantics | Ordering and pagination are deterministic and snapshot-bound; totals describe the filtered cohort. | Page through a tied cohort and prove no duplicates, omissions, or order drift. |
| Source degradation | `formal`, `partial`, `fallback`, `degraded`, and `unavailable` have explicit freshness, coverage, observed time, and missing-binding diagnostics. | Exercise every state and assert missing/non-finite values remain `null`, not zero or JSON-invalid numbers. |
| Recommendation boundary | Recommendation evidence is immutable and distinct from review approval and applied effect. | Assert submit receipts preserve recommendation, evidence, actor/idempotency, quarter, and snapshot identity while `live_capital_mutation` remains false. |
| Apply loopback | A governed command exposes one canonical operation/receipt link only after valid approval and current preconditions. | Reject stale snapshot, changed binding, missing approval, unauthorized actor, and replay mismatch; converge all read surfaces on the same receipt. |

The parent may intentionally defer the final two gaps to a governed-action
owner. If so, it should name the follow-up contract rather than exposing a
frontend-inferred action state.

## 3. Operator Journey Invariants

These invariants should survive route consolidation into Performance Center,
Rankings Center, and Governance Decisions:

1. Entering from Persona Fleet, Strategy, or Capital keeps the selected entity
   and snapshot visible as removable context, not a hidden one-time filter.
2. Moving from Performance to Rankings does not silently widen the cohort or
   replace `asOf` with latest.
3. Short-cycle Persona League rank and formal Quarterly Ranking are visibly
   different evidence types; neither is presented as approval.
4. A recommendation is traceable back to an immutable ranking snapshot and
   forward to Human Review. It cannot skip the review surface.
5. `approved` means a human decision exists. `applied` appears only when the
   canonical operation receipt confirms the effect.
6. Partial, fallback, degraded, unavailable, excluded, and stale states remain
   visible during drilldown. Navigation must not upgrade confidence.
7. Browser sorting may reorder presentation but must preserve the official
   backend rank label and must not manufacture ranks for excluded rows.
8. Missing links or unsupported history fail closed: the UI offers no direct
   service, registry, allocation, runtime, or broker mutation fallback.

## 4. Frontend Integration Delta

The `execute-plans` implementer should consume a backend response as three
separate concerns:

- `effectiveQuery`: the normalized filters and snapshot the BFF actually used;
- `items`: stable identities, backend-authored metrics/ranks, evidence, and
  diagnostics;
- `links` / action capability: backend-authored navigation and governed-action
  availability, absent when unsupported.

Names above are descriptive, not a canonical schema proposal. The frontend
adapter should map the parent-approved shape into one typed route context and
must not reconstruct missing query, snapshot, rank, confidence, or action
state from unrelated payload fields.

Minimum component and browser cases:

| Case | Expected UI behavior |
|---|---|
| Deep-link with persona + period + `asOf` | Context is visible and remains unchanged through Performance and Rankings drilldowns. |
| Unknown historical snapshot | Explicit unavailable state; no latest-data substitution. |
| Partial/fallback metrics | Values and diagnostics are labeled; recommendation/apply controls fail closed where policy requires formal evidence. |
| Excluded or null-metric row | Exclusion reason is shown; no zero coercion and no client-generated official rank. |
| Client table sort | Visual order changes, official rank labels do not. |
| Recommendation submitted | UI shows submitted/review-pending and links to Human Review; it does not show approved/applied. |
| Approval becomes stale before apply | Apply is unavailable or rejected with a recoverable precondition explanation. |
| Apply accepted asynchronously | Operation state is shown until the canonical receipt confirms applied or failed. |

Frontend delivery remains in `ajoe734/execute-plans`, built in strict live BFF
mode on Pantheon-owned dev hosting. This Pantheon sidecar must not materialize
frontend source inside this repository.

## 5. Parent Absorption Checklist

- Decide and document which response field carries the effective query and
  snapshot identity for all four read families.
- Upgrade request-acceptance tests into filter-effectiveness and historical
  snapshot tests before claiming the common query envelope is locked.
- Lock deterministic ordering, pagination, totals, stable row ids, and
  list-to-drilldown evidence continuity.
- Name the official-rank owner and prove null, non-finite, tied, excluded,
  partial, stale, empty, and unavailable cohorts.
- Return backend-authored cross-center links so the browser does not rebuild
  joins from ids.
- Either bind recommendation submission to Human Review and a later receipt
  loop, or explicitly assign that work to a governed-action follow-up.
- Give the frontend owner the approved response examples and the negative
  cases above; do not treat this interoperability packet as the schema source.

## 6. Handoff

Reviewer `Codex` should verify that this follow-up stays support-only and that
its stronger acceptance claims are framed as gaps, not as implemented truth.
After review, return it to parent owner `Antigravity` for selective absorption.
Approval of this packet does not approve the parent runtime branch or authorize
any live-capital action.

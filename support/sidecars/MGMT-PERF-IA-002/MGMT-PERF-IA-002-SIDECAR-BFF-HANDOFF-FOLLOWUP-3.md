# MGMT-PERF-IA-002 BFF And Frontend Handoff Follow-up 3

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-002` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar task | `MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical truth | `false` |

This packet is a support-only delta to the original handoff and follow-up 2.
It does not change BFF or frontend code, canonical contracts, ranking formulas,
governance, registry state, or runtime behavior. Parent owner `Antigravity`
decides whether to absorb any recommendation.

## 1. Candidate Evidence Checkpoint

At inspection time `origin/task/MGMT-PERF-IA-002` remains at candidate commit
`d0d4d0497` (`MGMT-PERF-IA-002: preserve scoped surface compatibility`). It is
not merged truth. No newer parent commit was available to close the gaps listed
in follow-up 2, so this packet narrows the handoff into an implementable query
envelope and acceptance sequence instead of claiming additional behavior.

The parent branch also contains unrelated merge-base churn when compared with
the current `dev`. Reviewers should assess the parent-owned performance/ranking
files and tests directly, not treat the branch-wide diff as evidence that every
listed deletion or configuration change belongs to this task.

## 2. Minimum Query Envelope For Parent Absorption

The four read families—Performance Attribution, Persona League, Quarterly
Ranking, and Quarterly Recommendations—need one observable request/response
boundary:

```text
requested context:
  personaId, runtimeId, strategyId, capitalPoolId, sleeveId,
  artifactId, brokerId, stage, period, quarter, asOf

response context:
  effective filters, resolved snapshot, filtered cohort identity,
  deterministic order/version, pagination cursor and total/coverage
```

Field names remain parent-owned. Whatever names are selected must let a client
distinguish these outcomes without inference:

| Outcome | Required observable result |
|---|---|
| Supported filter with matches | Only matching stable row identities are returned and the normalized effective filter is recoverable. |
| Supported filter with no matches | Empty filtered cohort; no fallback to the unfiltered dataset. |
| Unsupported filter or combination | Explicit validation error or documented unsupported result; never silently ignored. |
| Known historical `asOf` | Response identifies the exact resolved snapshot used. |
| Unknown historical `asOf` | Explicit unavailable result; latest data is not relabeled as historical. |
| Malformed `asOf` | Validation error distinct from historical unavailability. |
| Pagination | Cursor/order is bound to the same snapshot and cohort; no duplicates, omissions, or rank drift. |

## 3. Contract-Test Slice

Parent review can close the remaining query uncertainty with a small matrix,
using two matching rows, one non-matching row, tied metrics, one excluded row,
and one null/non-finite source metric:

1. Vary each supported identity filter independently and assert returned row
   ids plus the effective context.
2. Combine persona, pool/sleeve, stage, period/quarter, and `asOf`; assert that
   all four route families preserve the same cohort and snapshot.
3. Compare latest, known historical, unknown historical, and malformed `asOf`
   requests.
4. Page through tied rows and assert stable official ranks, tie-break order,
   totals, and evidence refs.
5. Assert excluded and missing-metric rows retain reasons and `null` values;
   they are not ranked by zero coercion.
6. Follow list-to-drilldown and recommendation links; assert stable row,
   ranking-evidence, quarter, and snapshot identities.

Schema-presence assertions alone do not satisfy these cases. The proof must
show that request values affect selection or produce an explicit rejection.

## 4. Frontend Consumption Boundary

The `execute-plans` adapter should receive three independently typed concerns:

- effective read context and resolved snapshot;
- stable backend-authored items, ranks, exclusions, evidence, and diagnostics;
- backend-authored links and governed-action capability.

The browser may alter visual ordering but must not renumber official ranks. It
must retain visible context while navigating Performance Center, Rankings
Center, drilldowns, recommendations, and Human Review. Missing effective
context, historical support, or governed links must produce a typed unavailable
state—not a client-side join, latest-data fallback, or direct service write.

Recommended frontend acceptance sequence:

1. Deep-link with persona, pool/sleeve, period or quarter, and `asOf`.
2. Verify visible context and stable snapshot through performance and ranking
   navigation.
3. Exercise empty, excluded, partial, degraded, unavailable, and stale states.
4. Sort the table and verify official rank labels remain unchanged.
5. Submit a recommendation and verify it remains distinct from approval and
   applied effect.
6. Verify that apply is absent or fails closed until a current Human Review
   decision and canonical operation/receipt link exist.

Frontend delivery belongs in the separate `ajoe734/execute-plans` repository
and must use strict live BFF mode on Pantheon-owned dev hosting. This sidecar
does not authorize or materialize frontend changes.

## 5. Explicit Deferral Boundary

If governed apply and receipt loopback are outside the parent scope, the parent
handoff should name the owning follow-up and return no inferred `apply`
capability. Recommendation submission may truthfully end at `review_pending`;
it must not be presented as approval, capital mutation, or applied effect.

Likewise, if historical snapshots are not implemented, the approved contract
should say so and reject historical `asOf`. Accepting the parameter while
serving latest is not compatible behavior.

## 6. Parent And Reviewer Handoff

Parent owner `Antigravity` should selectively absorb:

- one recoverable effective-query and resolved-snapshot boundary;
- filter-effectiveness, historical-snapshot, pagination, and continuity tests;
- backend-authored links across read families;
- an explicit owner for any deferred Human Review/apply/receipt work.

Reviewer `Antigravity` should verify that this packet remains support-only,
that candidate branch observations are not described as merged truth, and that
the negative cases are acceptance guidance rather than a new canonical schema.
After review, the parent owner decides composition into `MGMT-PERF-IA-002`.

## 7. Sidecar Verification

```bash
git diff --check -- \
  support/sidecars/MGMT-PERF-IA-002/MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
```

Approval of this packet does not approve the parent runtime implementation and
does not authorize live-capital operations.

## 8. Closeout Record

Reviewer `Antigravity` approved this support-only packet for closeout on
`2026-07-11`. Owner finalization rechecked the packet boundary and confirmed
that it changes no canonical truth or runtime implementation. The parent owner
retains sole discretion over whether and how to absorb these recommendations.

Final verification:

```bash
git diff --check -- \
  .orchestrator/task-briefs/mgmt_perf_ia_002_sidecar_bff_handoff_followup_3.md \
  support/sidecars/MGMT-PERF-IA-002/MGMT-PERF-IA-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-3.md
```

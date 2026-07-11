# MGMT-PERF-IA-005 BFF Handoff Follow-up 3

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-005` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar task | `MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-3` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical | `false` |

This support-only follow-up turns the earlier identity-chain guidance into a
capability-gated frontend handoff. It does not publish a schema, change a BFF
route, authorize capital mutation, edit `execute-plans`, or approve the parent.

## 1. Capability Matrix

| Governance Decisions capability | Verified BFF surface | Frontend disposition |
|---|---|---|
| Recommendation queue | `GET /bff/management/quarterly-ranking/recommendations` | Available for a read-only queue. Consume backend items, `meta.surfaces`, evidence references, governance destinations, and pagination. Preserve unknown lifecycle values rather than mapping them to success. |
| Promotion Human Review list/detail | `GET /bff/management/promotion-reviews` and `GET /bff/management/promotion-reviews/{review_id}` | Prefer these composed surfaces over browser joins to a generic queue. They expose recommendation/review identities, decision state, Human Inbox context, surface health, and an explicit no-live-mutation policy. |
| Submit for Human Review | `POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit` | Capability exists, but submission only means submitted to the Human Gate. The response explicitly says `requires_human_gate_decision: true` and `live_capital_mutation: false`; retain command/review identifiers and do not render approved or applied. |
| Record promotion decision | `POST /bff/management/promotion-reviews/{review_id}/decisions` | Treat as a separately role-gated Human Review step. A decision is not capital application. Use the returned backend state and receipt/command identity; never derive it from the clicked button. |
| Governance history | `GET /bff/management/governance-ledger` | Available as read-only history, not as a reliable recommendation join unless a stable source record reference is present. Do not correlate by actor, label, or timestamp. |
| Capital proposals | `GET /bff/rebalances` and `GET /bff/rebalances/{rebalance_id}` | Available as collection/detail. Empty collection is empty only when the surface metadata is healthy. A usable Governance Decisions row still requires an explicit recommendation/snapshot/review-to-rebalance link. |
| Rebalance application | `POST /bff/rebalances/{rebalance_id}/apply` | Governed command entry exists. It requires idempotency and checks Human approval for live increases, but acceptance remains a command state; it is not an apply receipt. Keep the control disabled when eligibility, preconditions, stable approval linkage, or receipt destination is absent. |
| Ranking policy collection | `GET /bff/management/quarterly-ranking/formula` | Compatible read surface exists for formula weights, version/history, policy, governance evidence references, and `meta.surfaces` health. Render backend-authored policy only when its surface metadata is healthy; degraded, stale, redacted, fallback, or unavailable metadata remains visible and fail-closed. |

The parent can therefore ship the shell, recommendation queue, and promotion
review journey without waiting for a speculative aggregate endpoint. The
capital tab remains read-only/capability-gated until the BFF supplies explicit
cross-record links and receipt truth. The policy tab may consume the compatible
formula surface, while remaining unavailable whenever its surface metadata is
absent or unhealthy.

## 2. Adapter Contract Boundary

The frontend adapter should expose presence and provenance, not fill gaps. A
safe support-level projection has these groups:

- `recommendation`: backend recommendation id, state, action id, immutable
  ranking snapshot/evidence references, and Rankings Center destination;
- `review`: review id, promotion review id, Human Inbox id, decision state,
  reviewer/timestamps, allowed decisions, and backend links;
- `source`: per-surface status, freshness/snapshot time, coverage or redaction
  diagnostics, and missing-source reasons;
- `proposal`: explicit rebalance id, current/proposed values, constraints,
  simulation, rollback target, approval reference, and precondition evidence;
- `application`: backend eligibility, command id/status, idempotency context,
  and durable apply receipt when one actually exists;
- `diagnostics`: missing identity links and unavailable capabilities.

This grouping is an adapter handoff, not a proposed wire schema. Fields are
populated only when present in compatible backend responses. Missing values
remain missing and disable downstream claims or actions.

## 3. Fail-Closed Capability Gates

| Gate | Required evidence | Failure presentation |
|---|---|---|
| Inspect recommendation | recommendation id plus backend-authored evidence destination/reference | Evidence unavailable; no guessed Rankings Center lookup. |
| Submit review | backend eligible action, authorized role response, recommendation id, and idempotency key | Disabled with backend/diagnostic reason. |
| Show approved | stable review/decision identity and backend decision state | Not approved or unavailable; recommendation wording is insufficient. |
| Show capital proposal | explicit rebalance/proposal identity and current-versus-proposed values | Informational/unavailable; missing numeric values are not zero. |
| Offer apply | explicit proposal-to-approval link, current eligibility and preconditions, authorized action, idempotency, and receipt destination | Disabled; role alone never creates eligibility. |
| Show applied | durable backend apply receipt linked to the proposal/command | Approved or applying, never applied. |
| Show policy | healthy `quarterly-ranking/formula` surface metadata plus backend-authored weights/version/policy and governance evidence references | Unavailable or degraded according to backend diagnostics; no synthesized policy. |

An unhealthy or absent `meta.surfaces` entry cannot be upgraded to healthy by
the adapter. `partial`, `fallback`, `degraded`, stale, redacted, and unavailable
evidence remains visible and prevents unsafe progression where required.

## 4. Operator Journey For Frontend Handoff

1. Enter `tab=recommendations`; render source status and recommendation state
   before any action.
2. Follow the immutable ranking evidence link to Rankings Center and preserve
   relevant canonical filter context on return.
3. Open the composed promotion-review detail by stable review identity. Show
   recommendation and Human Review as separate states.
4. Submit to Human Review only through the eligible backend action. Render the
   accepted command as submitted, with `live_capital_mutation: false`.
5. An approver records a decision through the review route. Keep approval
   separate from any rebalance proposal or application.
6. In `tab=capital`, show a rebalance only when an explicit backend link makes
   it part of this recommendation journey. Otherwise show it as unlinked or
   omit it with a diagnostic, never by heuristic correlation.
7. Offer governed apply only after all gates in §3 pass. Poll or follow the
   backend command/receipt destination; accepted/applying/failed/applied remain
   distinct.
8. In `tab=policy`, consume `GET /bff/management/quarterly-ranking/formula`
   only when `meta.surfaces` reports the compatible surface healthy. Preserve
   backend weights, version/history, policy, and governance evidence references;
   otherwise show the reported degraded/unavailable state without synthesizing
   a formula. Unknown tabs safely return to recommendations without discarding
   valid canonical filters.

## 5. Focused Tests Requested From Parent Owners

- recommendation submit response with `live_capital_mutation: false` never
  renders approval, application, or current allocation change;
- promotion review list/detail uses stable ids and does not join by display
  label, actor, or timestamp;
- absent or unhealthy surface metadata preserves degraded/unavailable UI and
  disables unsafe actions;
- a decision without a linked rebalance remains a review decision only;
- an apply command without a durable receipt renders submitted/applying, not
  applied;
- empty healthy rebalance data and unavailable rebalance data have distinct
  presentations;
- healthy formula metadata renders backend-authored weights/version/policy and
  governance evidence references, while missing or unhealthy metadata cannot
  produce a default formula;
- desktop/mobile order remains status/confidence, evidence/review, impact, then
  governed action;
- legacy Promotion Allocation redirects are loop-free and preserve only the
  canonical filters relevant to the selected tab.

## 6. Ownership And Compose Point

- Parent owner `Claude` decides whether to absorb this capability cut into
  `MGMT-PERF-IA-005` and coordinates any missing Pantheon BFF work.
- The `execute-plans` owner implements adapter/rendering/redirect/UI tests in
  the frontend repository; this sidecar does not edit that repository.
- A separately assigned Pantheon BFF owner must formalize and test any missing
  cross-record projection, policy collection, eligibility, or receipt surface.
- Parent reviewer `Antigravity` verifies that the implementation never promotes
  recommendation, submission, decision, or accepted command into applied truth.

## 7. Verification Notes

Source inspection only; no canonical, runtime, registry, governance, or
frontend implementation changed. Cross-checked the parent and dependency task
packets, both preceding sidecar packets, and current route implementations for:

- quarterly recommendation read and Human Review submission;
- composed promotion-review list/detail and decision handling;
- governance ledger read history;
- rebalance list/detail/create/apply boundaries and idempotency;
- source-surface metadata and explicit no-live-mutation response fields.
- quarterly ranking formula weights/version/policy, governance evidence
  references, and policy surface-health gating.

`current-work.md` and the complete `ai-activity-log.jsonl` were intentionally
not scanned. Formal review remains with `Claude`; absorption remains with the
parent owner.

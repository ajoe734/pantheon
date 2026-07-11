# MGMT-PERF-IA-005 BFF Handoff Follow-up 2

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-005` |
| Parent owner | `Claude` |
| Sidecar task | `MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical | `false` |

This follow-up narrows the original handoff into an implementation decision
packet. It is support material only: it neither publishes a BFF contract nor
authorizes frontend, governance, capital, registry, or runtime mutations.

## 1. Recommended Integration Cut

The frontend can build the Governance Decisions shell and recommendations tab
from the existing recommendation read surface, but it must treat the capital
and policy tabs as capability-dependent. Do not delay the honest workspace
shell on a speculative all-in-one endpoint, and do not hide missing joins with
client-side correlation.

| Workspace concern | Existing route | Safe use now | Remaining proof or gap |
|---|---|---|---|
| Recommendation queue | `GET /bff/management/quarterly-ranking/recommendations` | Render backend recommendation and Human Review state plus immutable ranking evidence. | Prove every parent-required lifecycle state is represented explicitly; unknown values render unavailable. |
| Review submission | `POST /bff/management/quarterly-ranking/recommendations/{recommendation_id}/submit` | Start the existing governed review flow only when the backend exposes an eligible action. | Do not interpret submission acceptance as approval or application. Preserve returned receipt/job identity. |
| Governance history | `GET /bff/management/governance-ledger` | Link or display backend-authored decision evidence when a stable identity is supplied. | No label, actor, or timestamp matching is an acceptable join to a recommendation. |
| Review queue | `GET /api/v1/operator/governance/review-queue` | Use as the Human Review destination when the recommendation exposes the corresponding stable review identity/link. | If no stable relation is present, show review context unavailable and request BFF composition. |
| Rebalance collection/detail | `GET /bff/rebalances`, `GET /bff/rebalances/{rebalance_id}` | Render proposals and empty collections honestly; detail may show backend-owned state. | Parent needs traceable recommendation/snapshot/review/precondition/apply-receipt linkage. Absence of any link must block the applied claim. |
| Governed rebalance apply | `POST /bff/rebalances/{rebalance_id}/apply` and catalogued rebalance action routes | Invoke only after backend eligibility, authorization, preconditions, Human Review approval reference, and idempotency setup are present. | A successful request is applying/accepted until a durable backend receipt proves application. |
| Ranking policy/formula | No compatible collection established by this inspection | Render an explicit unavailable state with retry/diagnostic affordance. | BFF owner must publish or identify a compatible read model before real policy rows are shown. |

## 2. Minimum Identity Chain

The parent implementation should accept a row as actionable only when the BFF
can preserve this chain without browser inference:

`recommendation_id -> ranking_snapshot_id/evidence_ref -> review_id/decision_id
-> proposal_or_rebalance_id -> precondition_result_refs -> apply_command_id
-> apply_receipt_id`

The chain can be delivered by one bounded BFF projection or by explicit links
between compatible responses. Route count is not the issue; backend-owned,
stable identity is. A missing link has these consequences:

- no ranking snapshot identity: evidence inspection may be unavailable and the
  recommendation cannot progress to governed apply;
- no review/decision identity: the UI must say not approved;
- no proposal or rebalance identity: impact is informational only;
- no precondition result: the mutation control is disabled;
- no apply receipt: state is approved or applying, never applied.

## 3. Frontend Adapter Boundary

Keep wire translation in one adapter and expose a discriminated workspace
model. The adapter should pass backend states through, normalize only names,
and reject incomplete actionability rather than fill fields with defaults.

Recommended view-model groups:

- `source`: confidence, freshness, coverage, missing bindings, observed time;
- `evidence`: recommendation identity, immutable snapshot/evidence references,
  Rankings Center link;
- `review`: review identity, decision identity, backend state, reviewer, times;
- `proposal`: proposal/rebalance identity, current-versus-proposed impact,
  limits and precondition results;
- `application`: eligible backend action, command identity, progress and receipt;
- `diagnostics`: explicit missing-link and unavailable reasons.

Do not synthesize an action from role alone, merge recommendation and review
states, turn missing numeric impact into zero, or promote an accepted command
to applied. Canonical URL filters remain `capital_pool` and `as_of`; wire-name
translation stays inside the adapter.

## 4. Operator Journey And Failure Branches

1. Open `/management/governance-decisions?tab=recommendations` with canonical
   filters and source confidence visible before actions.
2. Inspect immutable ranking evidence in Rankings Center and return with the
   relevant context preserved.
3. Open recommendation detail and compare current versus proposed impact.
4. If identity, confidence, freshness, review, or precondition evidence is
   incomplete, inspect diagnostics or request Human Review; do not expose apply.
5. Submit to Human Review and display the review lifecycle independently from
   the recommendation lifecycle.
6. After backend approval and renewed eligibility checks, start governed apply
   with idempotency protection.
7. Display accepted/applying/failed separately. Display applied only after the
   backend supplies a receipt connected to the identity chain.
8. Preserve rejected, expired, blocked, failed, and superseded records as
   inspectable history; never collapse them into empty state.

For `tab=capital`, an empty backend collection is a valid empty state, while a
missing/failed source is unavailable. For `tab=policy`, remain unavailable
until a compatible backend collection is identified. Unknown tabs may fall
back to recommendations, but must not discard valid canonical filters.

## 5. Parent Acceptance Tests To Request

- recommendation rows show an immutable snapshot/evidence link and cannot
  directly mutate capital or live runtime state;
- absent review identity renders not approved, even when recommendation text
  sounds affirmative;
- approved without an apply receipt never renders applied;
- accepted apply commands render applying until backend receipt evidence arrives;
- missing/non-finite impact is unavailable rather than zero;
- partial, fallback, degraded, stale, and unavailable source states remain
  visible and disable unsafe actions;
- zero rebalances render an honest empty collection, while source failure
  renders unavailable;
- zero policy/formula data never creates a default policy;
- recommendation, ledger, queue, and rebalance records are joined only through
  explicit stable identifiers or backend links;
- legacy Promotion Allocation redirects are loop-free, choose the intended tab,
  and preserve only relevant canonical filters;
- desktop and mobile keep confidence/status before evidence/impact and governed
  action last.

## 6. Ownership And Compose Handoff

- Parent owner `Claude`: decide which recommendations workspace slice to absorb
  and whether missing identity links require a separately scoped Pantheon BFF
  task.
- Pantheon BFF owner/reviewer for any follow-on: publish and test missing read
  compatibility or composed projection; do not treat this packet as its schema.
- `execute-plans` owner: implement the adapter, fail-closed rendering, redirects,
  responsive journey, and UI tests in the frontend repository.
- Parent reviewer `Antigravity`: verify the delivered frontend uses backend
  identities and receipts rather than recreating governance truth.

## 7. Verification Notes

Source inspection only; no runtime, canonical, or frontend code changed.
Reviewed the parent and dependency task packets, the original sidecar handoff,
and repository route/test references for quarterly recommendations, governance
ledger/review queue, rebalances, governed apply, source confidence, and live
wiring. `current-work.md` and the complete `ai-activity-log.jsonl` were not
scanned.

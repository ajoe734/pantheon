# MGMT-PERF-IA-005 BFF Handoff Follow-up 5

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-005` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar task | `MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical | `false` |

This support-only packet gives the parent a final absorption boundary. It does
not define a wire contract, change Pantheon or `execute-plans`, authorize a
capital mutation, or approve the parent task.

## 1. Absorbable Frontend Slice

The parent can implement these behaviors against existing BFF surfaces without
waiting for a new aggregate endpoint:

- render the Recommendations Queue from
  `GET /bff/management/quarterly-ranking/recommendations`;
- submit an eligible recommendation through
  `POST .../recommendations/{recommendation_id}/submit`, while keeping the
  accepted command distinct from approval and application;
- render promotion-review list/detail and record a Human Gate decision through
  `GET /bff/management/promotion-reviews`,
  `GET /bff/management/promotion-reviews/{review_id}`, and
  `POST .../{review_id}/decisions`;
- render Ranking Policy only from a healthy
  `GET /bff/management/quarterly-ranking/formula` response;
- render the rebalance collection/detail as independent, read-only capital
  context through `GET /bff/rebalances` and `GET /bff/rebalances/{rebalance_id}`.

The frontend adapter must preserve backend identifiers, lifecycle values, and
surface-health metadata. Unknown or missing values remain unavailable. Ranking
evidence is linked to Rankings Center; Governance Decisions must not recreate a
competing ranking table.

## 2. Non-absorbable Gap Requiring A Separate BFF Owner

The inspected responses still do not establish a durable backend-owned chain:

`recommendation -> ranking snapshot -> review decision -> rebalance/proposal ->
precondition results -> apply command -> completed apply receipt`

In particular, the recommendation and promotion-review projections do not
publish an explicit rebalance/proposal link or a durable application receipt.
Optional rebalance values such as `ranking_snapshot_id` and `approval_ref` do
not by themselves prove the complete relationship.

If the parent acceptance requires recommendation-specific capital application,
the parent should assign a separate Pantheon BFF task to publish and test the
missing links, current eligibility/preconditions, command progress, and durable
receipt truth. This sidecar does not choose route or field names.

Until that work lands, the frontend must not correlate records by persona,
quarter, label, actor, timestamp, or matching text. It must not enable a
recommendation-specific apply action. `POST /bff/rebalances/{rebalance_id}/apply`
returning HTTP 202 or a command id means accepted/applying, not applied.

## 3. Operator Journey Gate

1. Show source health and recommendation state before actions.
2. Open immutable ranking evidence in Rankings Center and preserve relevant
   canonical filter context on return.
3. Submit to Human Review only through the backend eligible action.
4. Display submission and Human Gate decision as separate states, both with
   `live_capital_mutation=false` where supplied by the BFF.
5. Display Capital Allocation as healthy-empty, unavailable, or unlinked based
   on backend evidence; never infer a recommendation join.
6. Display policy only from backend-authored formula/policy truth with healthy
   surface metadata; never synthesize defaults.
7. Offer governed apply only after explicit identity, authorization,
   eligibility, approval, preconditions, idempotency, and receipt destination
   are all present.
8. Display applied only after a durable linked completion receipt. Keep failed,
   expired, rejected, blocked, and superseded records inspectable.

## 4. Parent Acceptance Checklist

- Recommendation, submission, review decision, applying, and applied are
  visibly distinct states.
- No ranking or recommendation row directly mutates live state.
- No browser-side heuristic joins promotion reviews to rebalances.
- Missing or non-finite proposal impact is unavailable, never zero.
- Missing, stale, degraded, fallback, redacted, or unhealthy source metadata is
  visible and fails closed for unsafe actions.
- Healthy empty and unavailable collections have different presentations.
- An accepted apply command never renders as a completed application.
- Legacy Promotion Allocation redirects are loop-free and preserve only
  relevant canonical filters.
- Desktop and mobile preserve the order: health/status, evidence/review,
  impact, governed action.

## 5. Compose And Review Ownership

- Parent owner `Claude` decides whether to absorb the frontend-ready slice and
  whether to create the separate BFF contract task.
- The `execute-plans` owner implements adapters, rendering, redirects, and UI
  tests in the frontend repository.
- A separately assigned Pantheon BFF owner formalizes any missing identity or
  receipt contract and supplies focused contract tests.
- Sidecar reviewer `Claude` checks that this remains a support artifact;
  parent reviewer `Antigravity` checks the composed delivery against the parent
  acceptance criteria.

## 6. Verification Notes

Source inspection only. Re-read the parent task packet and all four preceding
sidecar packets, and confirmed the named recommendation, promotion-review,
formula, rebalance list/detail, and rebalance apply routes remain registered in
`services/control-plane/bff/main.py`. No canonical truth, runtime, registry,
governance implementation, BFF route, schema, or frontend file was changed.
`current-work.md` and the complete `ai-activity-log.jsonl` were not scanned.

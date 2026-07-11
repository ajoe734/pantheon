# MGMT-PERF-IA-005 BFF Handoff Follow-up 4

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-005` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar task | `MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Sidecar owner / reviewer | `Codex2` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical | `false` |

This support-only packet converts the preceding route inventory into an
absorption checklist for the parent. It does not define a wire schema, modify
Pantheon or `execute-plans`, authorize an apply, or approve the parent task.

## 1. What The Frontend Can Integrate Without A New BFF Contract

The parent may implement the Governance Decisions shell, recommendation queue,
promotion-review journey, and policy tab against these existing surfaces:

- `GET /bff/management/quarterly-ranking/recommendations` supplies stable
  `recommendation_id`, ranking evidence references, Human Review state,
  governance destinations, pagination, and per-surface health;
- `POST .../recommendations/{recommendation_id}/submit` creates the governed
  submission command; its command/receipt identity proves submission only;
- `GET /bff/management/promotion-reviews` and
  `GET /bff/management/promotion-reviews/{review_id}` expose the composed
  recommendation/review record, stable review and Human Inbox identities,
  allowed decisions, submission, and decision projections;
- `POST /bff/management/promotion-reviews/{review_id}/decisions` records the
  Human Gate decision while explicitly retaining `live_capital_mutation=false`;
- `GET /bff/management/quarterly-ranking/formula` supplies backend-authored
  formula and policy truth with surface-health metadata.

The adapter must keep recommendation, submission command, review decision, and
application as different states. The existing promotion-review `receipt_id` is
the corresponding command id; it is not a capital-application receipt.

## 2. Remaining Capital Join Gap

The inspected recommendation and promotion-review projections do not publish
an explicit `rebalance_id`, proposal link, precondition-result link, or durable
apply-receipt link. Rebalance create/detail/apply surfaces exist, and a proposal
may carry `ranking_snapshot_id` and `approval_ref`, but those optional values do
not establish a backend-owned recommendation-to-rebalance relationship.

Therefore the parent must not join recommendation/review and rebalance records
by persona, label, quarter, actor, timestamp, or matching text. Until a BFF
owner publishes explicit compatible links, the Capital Allocation tab may show
the independent rebalance collection as read-only context, but must label it
unlinked and keep recommendation-specific apply unavailable.

`POST /bff/rebalances/{rebalance_id}/apply` returns an accepted governed command
and requires Human approval for a live increase. Its implementation explicitly
states that execution remains separate. An HTTP 202 or command id is applying,
not applied; the parent acceptance requirement for an apply receipt remains
unmet until a durable completion/receipt surface is identified and linked.

## 3. Split Suggested To The Parent Owner

### Frontend-owned slice

- build the three-tab shell and shared canonical query adapter;
- consume recommendations, promotion reviews, decisions, formula, and surface
  health without synthesizing missing values;
- render ranking evidence as links rather than a competing ranking table;
- distinguish recommended, submitted, decided, applying, applied, failed,
  expired, blocked, rejected, and superseded, with unknown values preserved;
- keep capital actions disabled when an explicit proposal/review/precondition/
  receipt chain is incomplete;
- test healthy-empty versus unavailable, accepted versus applied, responsive
  information order, and loop-free legacy redirects.

### Separately assigned Pantheon BFF slice, if the parent needs actionable capital

- publish explicit recommendation/snapshot/review-to-proposal identifiers;
- expose backend eligibility and precondition-result references;
- expose command progress and durable application receipt identity;
- prove the receipt links back to the proposal and approval decision;
- add contract tests for missing links, stale approval, idempotent replay,
  failed execution, and supersession.

This list is a gap statement, not permission for this sidecar or the frontend
owner to choose route names or fields.

## 4. Operator Journey Acceptance Hand-off

1. Open Recommendations and inspect surface health before any action.
2. Follow immutable ranking evidence to Rankings Center, preserving relevant
   canonical filters on return.
3. Submit an eligible recommendation and show it as pending Human Review.
4. Record and display the Human Gate decision independently from application.
5. Show policy only from a healthy formula response; never create defaults.
6. In Capital Allocation, distinguish healthy empty, unavailable, and unlinked
   proposal data.
7. Offer apply only after explicit identity, eligibility, approval,
   precondition, idempotency, and receipt-destination evidence is present.
8. Show accepted/applying until a durable linked receipt proves applied; retain
   failures and superseded records as inspectable history.

## 5. Reviewer Checks

- No recommendation or ranking row directly mutates live state.
- Submission and decision command receipts are not labeled apply receipts.
- No browser heuristic joins promotion reviews to rebalances.
- Missing/non-finite impact remains unavailable rather than zero.
- Unhealthy or absent `meta.surfaces` remains visible and fail-closed.
- The policy tab consumes backend formula truth only.
- This packet stays support-only and parent absorption is explicit.

## 6. Verification Notes

Source inspection only. Cross-checked the current route implementations and
focused BFF tests for quarterly recommendations, promotion reviews and Human
Gate decisions, formula, and rebalance apply. Also re-read the parent packet and
the three earlier sidecar packets. No runtime, registry, governance, canonical,
or frontend file was changed. `current-work.md` and the complete
`ai-activity-log.jsonl` were not scanned.


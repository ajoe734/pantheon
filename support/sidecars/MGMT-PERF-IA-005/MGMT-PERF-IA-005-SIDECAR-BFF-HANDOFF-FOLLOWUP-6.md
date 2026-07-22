# MGMT-PERF-IA-005 BFF Handoff Follow-up 6

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-005` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar task | `MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-11` |
| Mutates canonical | `false` |

This support-only packet is a decision-ready BFF/frontend handoff for the
parent owner. It does not publish a wire contract, change Pantheon or
`execute-plans`, authorize a capital mutation, or approve the parent task.

## 1. Parent Decision

The parent can absorb the Governance Decisions shell, Recommendations and
Policy reads, and the Human Review journey now. Recommendation-specific capital
application is not absorbable yet because the inspected BFF projections do not
provide a complete backend-owned identity and receipt chain.

| Slice | Existing BFF surface | Parent disposition |
|---|---|---|
| Recommendations | `GET /bff/management/quarterly-ranking/recommendations` | Absorb. Preserve recommendation and ranking-evidence identities, lifecycle values, pagination, and surface health. |
| Submit for review | `POST .../recommendations/{recommendation_id}/submit` | Absorb. Treat the returned command/receipt as submission evidence only. |
| Human Review | `GET /bff/management/promotion-reviews`, `GET .../{review_id}`, `POST .../{review_id}/decisions` | Absorb. Keep recommendation, submission, and Human Gate decision distinct; a decision is not capital application. |
| Ranking Policy | `GET /bff/management/quarterly-ranking/formula` | Absorb only from a healthy backend response. Never synthesize policy defaults. |
| Capital context | `GET /bff/rebalances`, `GET /bff/rebalances/{rebalance_id}` | Read-only and explicitly unlinked unless the BFF supplies a compatible stable relationship. Healthy empty and unavailable are different states. |
| Governed apply | `POST /bff/rebalances/{rebalance_id}/apply` | Do not expose as recommendation-specific apply. HTTP 202 or a command id means accepted/applying, not applied. |

## 2. Query And Contract Gap

The missing proof is an explicit, backend-owned chain:

`recommendation_id -> ranking_snapshot_id/evidence_ref -> review_id/decision_id
-> proposal_or_rebalance_id -> precondition_result_refs -> apply_command_id
-> completed_apply_receipt_id`

The inspected recommendation and promotion-review projections do not publish a
durable proposal/rebalance or completed-application link. Optional rebalance
fields do not prove the whole relationship. The browser must not join records
by persona, quarter, label, actor, timestamp, or matching text.

If parent acceptance requires actionable recommendation-specific capital, the
parent should create a separately owned Pantheon BFF task to:

- publish explicit compatible identifiers across recommendation, evidence,
  review decision, proposal/rebalance, preconditions, command, and receipt;
- expose current backend eligibility and precondition results;
- distinguish command acceptance/progress/failure from durable completion;
- prove idempotent replay, stale approval, failed execution, and supersession
  with focused contract tests.

This packet identifies the gap but deliberately does not choose route or field
names.

## 3. Frontend Handoff

- Build one three-tab shell with canonical query parsing for
  `recommendations`, `capital`, and `policy`.
- Keep wire translation in one adapter and preserve unknown backend values as
  unavailable rather than inventing defaults.
- Link immutable ranking evidence to Rankings Center; do not recreate a full
  ranking table in Governance Decisions.
- Display source confidence, freshness, coverage, and missing bindings before
  actions. Missing or unhealthy health metadata fails closed.
- Model recommended, submitted, decided, accepted/applying, applied, failed,
  rejected, expired, blocked, and superseded as distinct backend-owned states.
- Render missing/non-finite impact as unavailable, never zero.
- Keep capital actions disabled until explicit identity, authorization,
  eligibility, approval, preconditions, idempotency, and a durable receipt
  destination are present.
- Preserve relevant canonical filters through Rankings Center links and
  loop-free legacy redirects; preserve the order health/status, evidence and
  impact, governed action on desktop and mobile.

## 4. Operator Journey

1. Open Recommendations and inspect source health and lifecycle state.
2. Follow immutable ranking evidence to Rankings Center and return with
   relevant filter context preserved.
3. Submit an eligible recommendation to Human Review; show it as submitted,
   not approved or applied.
4. Inspect and record the Human Gate decision independently from application.
5. Inspect Policy only when formula surface health is valid.
6. Inspect Capital as healthy-empty, unavailable, or unlinked read-only
   context while the identity-chain gap remains.
7. After a future BFF contract supplies every required link and renewed
   eligibility/preconditions, start governed apply with idempotency protection.
8. Show accepted/applying until a durable linked completion receipt proves
   applied; retain failed and superseded outcomes as inspectable history.

## 5. Parent Acceptance And Compose Boundary

- No recommendation or ranking row directly mutates live state.
- Submission and Human Gate receipts are never labeled apply receipts.
- No client-side heuristic joins promotion reviews to rebalances.
- Unhealthy, stale, fallback, redacted, missing, and unknown source truth stays
  visible and disables unsafe actions.
- Healthy empty, unavailable, and unlinked capital data render differently.
- Accepted apply never renders as completed application.
- Parent owner `Claude` decides which frontend-ready slice to absorb and
  whether to assign the separate BFF contract task.
- The `execute-plans` owner implements adapters, UI behavior, redirects, and
  responsive tests in that repository; this Pantheon sidecar changes none of
  those files.
- Parent reviewer `Antigravity` evaluates the composed parent delivery. Sidecar
  reviewer `Claude` only verifies this support packet and its boundary.

## 6. Verification Notes

Source inspection only. Re-read the task brief, original handoff, and follow-up
packets 2 through 5. Confirmed the named recommendation, submission,
promotion-review list/detail/decision, formula, rebalance list/detail, and
rebalance-apply routes remain registered in
`services/control-plane/bff/main.py`. No canonical truth, runtime, registry,
governance implementation, BFF route/schema, or frontend file was changed.
`current-work.md` and the complete `ai-activity-log.jsonl` were not scanned.

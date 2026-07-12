# MGMT-PERF-IA-005 BFF Handoff Follow-up 8

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-005` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar task | `MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-8` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Verified | `2026-07-12` |
| Mutates canonical | `false` |

This is a support-only absorption packet. It updates the parent owner on the
current frontend delivery gate and restates the safe BFF boundary. It does not
publish a contract, change canonical/runtime/registry/governance truth, edit
the frontend, authorize capital mutation, or approve the parent task.

## 1. Parent Absorption Snapshot

The parent implementation remains execute-plans PR #260 at
`2954481ba3540bc2627eb379b70b401d40b3ef39`. On this verification pass GitHub
reported it `OPEN`, `CLEAN`, and `MERGEABLE` against `dev`; integration-gate
run `29162742365` was successful. There was no review, auto-merge request, or
merge timestamp. The parent may absorb the reviewed implementation intent, but
it must not claim merged or hosted delivery yet.

The current frontend slice is deliberately read-only and fail-closed:

- one Governance Decisions workspace separates Recommendations, Capital, and
  Policy without adding another ranking table;
- queue items use backend-returned Human Inbox and governance state and send
  the operator to Human Gate detail instead of exposing inline mutation;
- signatures and decision history remain Human Review evidence, not proof of
  capital application;
- legacy Promotion & Allocation content directs operators toward the canonical
  centers rather than preserving a duplicate workflow.

## 2. BFF Query Gap And Safe Boundary

No new BFF query is needed for the read-only queue and Human Review journey in
PR #260. Preserve backend identities, lifecycle values, pagination, health,
and unavailable states when composing that slice.

Recommendation-specific capital application is not proven. The browser still
lacks an explicit backend-owned chain:

`recommendation/ranking evidence -> review decision -> proposal or rebalance
-> renewed eligibility and preconditions -> apply command -> durable completed
apply receipt`

Do not join records by persona, quarter, category, timestamp, actor, label, or
display text. A Human Review signature or decision-history entry is not an
apply receipt. An accepted command is applying, not applied.

If recommendation-specific apply becomes parent scope, create a separately
owned Pantheon BFF contract task. It must publish compatible stable links,
backend eligibility and precondition state, distinct accepted/running/failed/
superseded/completed outcomes, and idempotent replay evidence. This sidecar
does not choose route or field names.

## 3. Operator Journey Handoff

1. Open Governance Decisions using the canonical tab and query context.
2. Inspect source health and backend lifecycle before following an item to
   Human Gate detail.
3. Treat Human Gate signatures and history as review evidence only.
4. Follow immutable ranking evidence to Rankings Center; do not recreate its
   table inside Governance Decisions.
5. Render capital context as healthy-empty, unavailable, stale, or unlinked as
   appropriate; none of those states authorizes mutation.
6. Keep future apply controls disabled until stable identity, authorization,
   eligibility, renewed preconditions, idempotency, and a durable receipt
   destination are all backend-provided.
7. After merge and deployment, repeat the canonical, Human Gate, Rankings
   Center, and legacy-navigation journey at desktop and mobile widths using
   strict live BFF mode.

## 4. Parent Delivery Gate

Parent owner `Claude` may compose the read-only queue and Human Review journey.
Parent completion still requires:

- PR #260 merged into its declared target and the merge SHA recorded;
- Pantheon-owned dev hosting built from that merged frontend commit in strict
  live BFF mode;
- desktop/mobile proof of all three tabs, Rankings Center link-out, Human Gate
  detail, and loop-free legacy navigation;
- evidence that unavailable backend data cannot fall back to seed data or
  enable mutation;
- visibly distinct recommendation, review decision, command acceptance, and
  durable application states;
- recommendation-specific apply withheld until its separate identity/receipt
  contract is implemented and proven.

Parent reviewer `Antigravity` evaluates the composed parent delivery. Sidecar
reviewer `Codex` verifies only this packet's factual boundary and canonical
non-mutation. Frontend implementation and hosted proof remain with the
execute-plans owner.

## 5. Verification Record

- Re-read the task brief, task-scoped operating skills, parent state, and the
  preceding follow-up packet.
- Queried execute-plans PR #260 through GitHub and recorded its head, target,
  open/mergeable state, review/auto-merge absence, and successful check.
- Confirmed this task changes only its generated brief and this support packet.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.

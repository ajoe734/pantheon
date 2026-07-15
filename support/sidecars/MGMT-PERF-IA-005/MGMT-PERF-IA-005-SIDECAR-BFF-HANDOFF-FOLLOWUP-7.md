# MGMT-PERF-IA-005 BFF Handoff Follow-up 7

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-005` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar task | `MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` |
| Sidecar owner / reviewer | `Codex2` / `Codex` |
| Helper kind | `bff_handoff_packet` |
| Verified | `2026-07-12` |
| Mutates canonical | `false` |

This packet is support material for the parent owner. It records the current
frontend/BFF compose boundary and the remaining delivery gate. It does not
publish a BFF contract, change runtime or governance truth, authorize capital
mutation, or approve the parent task.

## 1. Current Delivery Snapshot

The parent implementation is execute-plans PR #260 at
`2954481ba3540bc2627eb379b70b401d40b3ef39`. At verification time the PR was
open against `dev`; its `integration-gate` check had succeeded. Merge and
Pantheon-hosted desktop/mobile evidence were not present, so the parent is not
delivery-complete.

The frontend change establishes a useful fail-closed slice:

- Governance Decisions exposes Recommendations, Capital, and Policy tabs
  without embedding another sortable ranking table.
- Recommendations and capital-context queues consume backend-owned Human Inbox
  categories and link operators to Human Gate detail rather than exposing
  inline approve, reject, or apply controls.
- Governance lifecycle labels are derived from returned BFF fields; missing
  values are not promoted into a successful state.
- Human Gate detail renders returned signature timestamps and decision history.
  Those are decision evidence, not proof that capital was applied.
- Legacy Promotion & Allocation content points operators toward the canonical
  centers instead of preserving duplicate ranking or allocation workspaces.

## 2. BFF Query And Identity Boundary

No additional BFF route is required to review the read-only queue and Human
Gate journey delivered in PR #260. The frontend may absorb that slice while it
preserves backend identities, lifecycle values, pagination, and surface health.

Recommendation-specific capital application remains outside this packet. No
inspected projection proves the complete backend-owned chain:

`recommendation/ranking evidence -> review decision -> proposal or rebalance
-> renewed eligibility and preconditions -> apply command -> durable completed
apply receipt`

Decision history must therefore remain labelled as review/decision evidence.
It must not be relabelled as an apply receipt. Human Inbox category, persona,
quarter, timestamps, display text, or similar values are not valid join keys
between recommendations, reviews, and rebalances.

If the parent later requires recommendation-specific apply, commission a
separately owned Pantheon BFF contract task to publish compatible stable links,
backend eligibility/precondition state, and distinct accepted, running,
failed, superseded, and durably completed outcomes with idempotent replay
tests. This sidecar intentionally does not invent route or field names.

## 3. Operator Journey Handoff

1. Open Governance Decisions and select Recommendations, Capital, or Policy
   through the canonical tab/query state.
2. Inspect the queue's source health and backend lifecycle before following an
   item to Human Gate detail.
3. Treat signatures and decision history as the Human Review record only.
4. Follow immutable ranking evidence to Rankings Center; do not recreate the
   ranking table inside Governance Decisions.
5. Treat capital lists as read-only context unless the BFF supplies an explicit
   compatible relationship to the selected recommendation.
6. Render healthy-empty, unavailable, stale, and unlinked as different states;
   none permits an unsafe action.
7. Keep any future apply control disabled until authorization, stable identity,
   current eligibility, preconditions, idempotency, and a durable receipt
   destination are all backend-provided.
8. After PR merge, repeat the journey on the Pantheon-owned dev host at desktop
   and mobile widths with strict live BFF mode and record the frontend SHA,
   BFF origin, required request results, console errors, redirects, and state
   labels.

## 4. Parent Absorption And Acceptance Gate

The parent owner may absorb the frontend-ready read-only queue and Human Review
journey now. Parent completion still requires all of the following:

- execute-plans PR #260 is merged into its declared target;
- the deployed Pantheon frontend is built from the merged commit with strict
  live BFF settings;
- desktop and mobile hosted evidence proves the canonical tabs, Rankings Center
  link-out, Human Gate detail, and loop-free legacy navigation;
- failed/unavailable data does not fall back to seed data or enable mutation;
- recommendation, review decision, command acceptance, and durable application
  remain visibly distinct;
- any recommendation-specific apply capability is withheld until the separate
  BFF identity/receipt contract is implemented and proven.

Parent reviewer `Antigravity` evaluates the composed delivery. Sidecar reviewer
`Codex` only verifies this packet's factual support boundary. The execute-plans
owner remains responsible for frontend implementation and hosted proof; this
Pantheon sidecar changes no frontend files.

## 5. Verification Record

- Re-read the task brief, parent execution packet, and prior follow-up 6.
- Queried execute-plans PR #260 with `gh pr view`; recorded its head, open
  state, changed-file summary, and successful integration gate.
- Compared the PR description and files with the support boundary above.
- Confirmed this change is limited to this task-scoped support artifact.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.

# MGMT-OPS-003-GAP-002 BFF Handoff Follow-up 6

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-002` |
| Sidecar task | `MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Date | `2026-07-11` |
| Scope | Support-only delta and dispatch stop condition |

This packet does not change canonical truth, contracts, BFF/runtime code,
reconciliation behavior, frontend code, registry/governance, deployment
configuration, or hosted data. The parent owner decides whether to absorb it.

## Delta Since Follow-up 5

No parent implementation or hosted-evidence commit newer than parent anchor
`6d83145c6` was found. Follow-up 5 is merged to `dev` in PR #3230 at merge
commit `0c71286bb`. Consequently, the BFF query gap, frontend compose boundary,
and operator journey documented by Follow-ups 3 through 5 are unchanged.

Creating more support packets is not evidence of parent progress. After this
handoff, another sidecar should be dispatched only when a concrete delta exists:

- a parent BFF/runtime commit changes the projection or reconciliation result;
- a deployed backend SHA and authenticated capture supersede the snapshot at
  `6d83145c6`;
- an `execute-plans` commit implements the fail-closed rendering/navigation
  seam; or
- reviewer feedback identifies a specific omission in the existing packet set.

Without one of those inputs, the correct action is to keep the parent task open,
not to infer closure or restate the same gap in another packet.

## BFF Input Required For The Next Compose Cycle

The next meaningful BFF handoff must provide a single authenticated observation
window across Portfolio Book summary, holdings, incidents, positions, and
Performance Attribution, together with:

1. deployed backend SHA ancestry and capture time;
2. a stable identity for every previously unresolved holding and runtime;
3. `repaired`, `quarantined`, or `unchanged` disposition, before/after issue
   codes, reason, evidence references, and reconciliation/idempotency identity;
4. explicit population definitions for summary-level and holding-level binding
   counters;
5. authoritative broker and paper-ledger/canary-sleeve/live-pool identity, or
   an explicit unknown/unbound state; and
6. non-formal attribution whenever an included holding has a required missing,
   quarantined, stale, or unavailable join.

The last authenticated capture remains contradictory: summary-level binding
health does not remove 10 holding-level missing bindings, and the `unassigned`
persona bucket containing those holdings must not be `formal`. The frontend
must not resolve that contradiction with client-side arithmetic.

If existing BFF responses cannot expose disposition or audit evidence, the
parent must record a bounded contract gap. This sidecar does not define a new
route, field, or mutation.

## Frontend Input Required For The Next Compose Cycle

The separate `ajoe734/execute-plans` lane should consume only a deployed BFF
contract and should:

- label each count by its actual population and observation window;
- preserve null/unknown identity, capital scope, and source values;
- keep unresolved and quarantined rows visible under filters and pagination;
- render confidence and source status exactly as delivered by the BFF, never
  upgrading `partial`, `degraded`, `unavailable`, or `unknown` to `formal`;
- preserve available holding, runtime, binding, pool/ledger/sleeve, incident,
  evidence, and period context across Portfolio Book, Persona Fleet,
  Performance Attribution, and Human Review; and
- in strict live mode, show missing required data as unavailable rather than
  filling it with mock or neighboring-row values.

No frontend source belongs in this Pantheon worktree.

## Operator Journey And Evidence Gate

The next hosted verification should use one repeatable journey:

1. record runtime and telemetry coverage before filtering;
2. filter to missing, stale, or quarantined rows and reload the URL;
3. open an unresolved holding while retaining all known identity/source data;
4. verify its containing attribution population is non-formal;
5. reach Human Review with the same holding/runtime/issue/evidence context;
6. return without losing the selected population; and
7. after repair deployment, match every original row to a fresh authoritative
   disposition rather than accepting a lower aggregate count.

Desktop and mobile captures must use the same authenticated API snapshot and
record frontend/backend deployed SHAs, required-request failures, console
errors, lazy-chunk failures, and strict-live fallback-data failures. Independent
review must sample raw binding and telemetry sources; aggregate counters alone
cannot close the parent.

## Reviewer Handoff

Antigravity should verify only that this packet:

- remains support-only and introduces no contract or implementation truth;
- accurately states that there is no newer parent delta after `6d83145c6`;
- gives concrete prerequisites for the next BFF/frontend compose cycle; and
- prevents repeated sidecar dispatch from being mistaken for delivery proof.

Approval closes only this helper packet. It does not approve the parent repair,
frontend implementation, deployment, hosted evidence, or acceptance outcome.

## Focused Verification

```bash
AI_NAME=Codex ./scripts/ai-status.sh show MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6
git diff --check -- support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-6.md
git log --all --oneline 6d83145c6..origin/task/MGMT-OPS-003-GAP-002
git log --all --oneline -- support/sidecars/MGMT-OPS-003-GAP-002
```

`current-work.md` and the full `ai-activity-log.jsonl` were intentionally not
scanned; task-scoped context, the parent branch, and the existing packet family
were sufficient.

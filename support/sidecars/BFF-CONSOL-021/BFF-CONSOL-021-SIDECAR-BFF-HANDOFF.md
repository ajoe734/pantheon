# BFF-CONSOL-021 Sidecar: BFF and Frontend Handoff Packet

Task ID: BFF-CONSOL-021-SIDECAR-BFF-HANDOFF
Parent Task: BFF-CONSOL-021 - Receipt dual-write + replay/conflict/idempotency tests
Helper Kind: bff_handoff_packet
Prepared by: Codex2
Reviewer: Codex
Date: 2026-05-13
Mutates canonical truth: false

## Purpose

This support-only packet gives the BFF-CONSOL-021 parent owner the current
BFF/frontend handoff for proving command receipt coexistence after the
BFF-CONSOL-019 backend adapter and BFF-CONSOL-020 frontend command-client
migration.

This sidecar does not change L1 canonical truth, core contracts, runtime code,
registry code, governance implementation, or execute-plans source. It packages
the query/command gaps, operator journey, frontend expectations, test matrix,
and soak evidence shape the parent owner should absorb.

## Parent Snapshot

| Field | Current handoff value |
|---|---|
| Parent owner / reviewer | Codex / Claude |
| Parent status at sidecar preparation | `todo` |
| Parent dependencies | `BFF-CONSOL-019` is archived `done`; `BFF-CONSOL-020` is still active in `review` in `ai-status.json` |
| Parent artifacts | `services/control-plane/bff/tests/test_command_replay_conflict.py`; `support/evidence/BFF-CONSOL-021-dual-write-soak.json` |
| Parent acceptance | dual-write log contains action and command receipts; replay passes; idempotency conflict returns 409; missing confirm token returns `CONFIRM_TOKEN_REQUIRED`; missing approval evidence returns `APPROVAL_REQUIRED`; soak window records at least seven clean days |
| Downstream dependencies | `BFF-CONSOL-024` must wait for the one-week 021 dual-write soak before old action receipt deprecation; `BFF-CONSOL-027` expects a command receipt sample from 021 |

## Context Sources Used

- `AI_COLLABORATION_GUIDE.md`
- `.orchestrator/task-briefs/bff_consol_021_sidecar_bff_handoff.md`
- `.orchestrator/skills/task-closeout-finalization.md`
- `ai-status.json`
- `ai-task-archive/tasks/BFF-CONSOL-019.json`
- `support/sidecars/BFF-CONSOL-019/BFF-CONSOL-019-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/BFF-CONSOL-020/BFF-CONSOL-020-SIDECAR-BFF-HANDOFF.md`
- `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md`
- Targeted BFF route/test inspection in `services/control-plane/bff/main.py`,
  `services/control-plane/bff/action_catalog.py`,
  `services/control-plane/bff/tests/test_actions_to_commands_adapter.py`,
  and existing final-command tests.
- Read-only sibling frontend inspection in `/home/lupin/code/execute-plans`
  for BFF-CONSOL-020 handoff alignment.

## Current Wire State

| Surface | Observed state | BFF-CONSOL-021 implication |
|---|---|---|
| Legacy action adapter | `POST /bff/actions/{entityType}/{entityId}/{actionId}` calls `_submit_final_command_admission` with `route=POST /bff/v1/commands`, `source_route=POST /bff/actions/{entityType}/{entityId}/{actionId}`, and durable idempotency meta enabled. | A single legacy action call can be tested as both the old action receipt visible to existing callers and the durable command receipt stored by the BFF. |
| Final command route | `POST /bff/v1/commands` is the final command surface. It requires `Idempotency-Key` or compatibility `X-Idempotency-Key`, rejects body `idempotencyKey`, derives actor from auth, validates policy/preconditions, and returns `CommandResponse<T>`. | Direct command caller tests should assert final response shape and canonical header behavior. |
| Replay handling | `_submit_final_command_admission` checks `command_store.get_command_by_idempotency_key`. Same key + same request returns the original command receipt; same key + different payload raises 409 `IDEMPOTENCY_CONFLICT`. | The parent test should prove replay is stable and conflict is typed. For direct `/bff/v1/commands`, stable command id is the replay signal; the legacy adapter also exposes durable `meta.idempotency.replayed`. |
| Precondition errors | Final preconditions are driven by `action_catalog.py`. Missing confirm token raises 428 `CONFIRM_TOKEN_REQUIRED`; missing approval evidence raises 409 `APPROVAL_REQUIRED`; missing two-man evidence raises 409 `TWO_MAN_REQUIRED`. | 021 should reuse these typed envelopes and ensure frontend-normalized errors do not become success receipts. |
| BFF-CONSOL-020 frontend state | The sibling execute-plans checkout has commit `30b4ed3` with `commandClient.ts`, `runCommandAction`, direct `/bff/v1/commands`, default legacy `/bff/actions/*`, confirm-token header forwarding, and typed error tests. In `ai-status.json`, BFF-CONSOL-020 is still `review`. | Parent 021 should not treat 020 as fully absorbed until its review lifecycle closes, but it can use the handoff and sibling commit as the expected frontend seam. |

## BFF Query and Command Gaps for Parent 021

| Gap | Parent action |
|---|---|
| No dedicated 021 replay/conflict test file exists yet | Add `services/control-plane/bff/tests/test_command_replay_conflict.py` with isolated `CommandStore` setup and focused BFF TestClient coverage. |
| Dual-write evidence is not yet materialized | Create `support/evidence/BFF-CONSOL-021-dual-write-soak.json` and record both legacy action-adapter receipts and direct final-command receipts. |
| Legacy action receipt and command receipt need explicit reconciliation proof | In the legacy action test, assert response `data.command_id` / `data.receipt_id` matches the command store record, and assert the record foundation has `admission_route=POST /bff/v1/commands` plus `source_route=POST /bff/actions/{entityType}/{entityId}/{actionId}`. |
| Replay semantics need parent-owned regression coverage | Submit the same request twice with the same idempotency key and assert the receipt id is stable; for the legacy action path, assert `meta.idempotency.replayed=true` on replay. |
| Conflict semantics need parent-owned regression coverage | Reuse the same idempotency key with a changed payload and assert HTTP 409 plus `IDEMPOTENCY_CONFLICT`, `foundation_error`, and `audit_action` evidence. |
| Precondition envelopes need to be part of the 021 proof | Cover missing confirm token with `PauseRuntime` and missing approval evidence with `ApproveDecision`, matching the existing final-command precondition behavior. |
| Seven-day soak evidence has no initialized schema | Initialize day-by-day evidence rows with status, route mix, receipt samples, replay/conflict/precondition checks, frontend commit/reference, and blocker fields. |

## Recommended Parent Test Matrix

Suggested file: `services/control-plane/bff/tests/test_command_replay_conflict.py`.

| Test | Route | Expected result |
|---|---|---|
| `test_legacy_action_dual_writes_action_and_command_receipts` | `POST /bff/actions/strategy/{id}/submit_review` | HTTP 202; `CommandResponse.status=accepted`; response receipt id equals durable command record id; foundation records final admission route and legacy source route. |
| `test_legacy_action_idempotency_replay_returns_same_receipt` | same legacy action route, same key/body twice | Second response returns the same command id and durable meta marks replay. |
| `test_legacy_action_idempotency_conflict_returns_409` | same legacy action route, same key/different body | HTTP 409; error code `IDEMPOTENCY_CONFLICT`; no second command record is created. |
| `test_final_command_idempotency_replay_returns_same_receipt` | `POST /bff/v1/commands` with a low-risk command | Same key/body returns the same command id and `CommandResponse<T>` shape. |
| `test_final_command_idempotency_conflict_returns_409` | `POST /bff/v1/commands`, same key/different payload | HTTP 409; error code `IDEMPOTENCY_CONFLICT`; foundation error and audit action are present. |
| `test_final_command_missing_confirm_token_returns_typed_error` | `PauseRuntime` without `X-Confirm-Token` | HTTP 428; error code `CONFIRM_TOKEN_REQUIRED`; no command record is created. |
| `test_final_command_missing_approval_evidence_returns_typed_error` | `ApproveDecision` without approval evidence | HTTP 409; error code `APPROVAL_REQUIRED`; no command record is created. |

Suggested focused verification for the parent owner:

```bash
python3 -m py_compile services/control-plane/bff/tests/test_command_replay_conflict.py
python3 -m pytest services/control-plane/bff/tests/test_command_replay_conflict.py -v
python3 -m pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py services/control-plane/bff/test_final_precondition_errors.py -q
```

## Evidence File Shape

Suggested file: `support/evidence/BFF-CONSOL-021-dual-write-soak.json`.

```json
{
  "task_id": "BFF-CONSOL-021",
  "status": "initialized",
  "created_at": "2026-05-13T00:00:00Z",
  "dependencies": {
    "BFF-CONSOL-019": {
      "status": "done",
      "commit": "34fa7aec2a931333966d98b71a1c2d6bd5d0fe44"
    },
    "BFF-CONSOL-020": {
      "status": "review",
      "frontend_reference_commit": "30b4ed3"
    }
  },
  "receipt_samples": [
    {
      "route_class": "legacy_action_adapter",
      "method": "POST",
      "path": "/bff/actions/strategy/stg-bff-021/submit_review",
      "idempotency_key": "bff-consol-021-action-001",
      "action_receipt": {
        "status": "accepted",
        "command_id": "cmd-placeholder"
      },
      "command_receipt": {
        "command_id": "cmd-placeholder",
        "type": "StrategyAction",
        "target": "Strategy:stg-bff-021",
        "admission_route": "POST /bff/v1/commands",
        "source_route": "POST /bff/actions/{entityType}/{entityId}/{actionId}"
      }
    },
    {
      "route_class": "final_command_direct",
      "method": "POST",
      "path": "/bff/v1/commands",
      "idempotency_key": "bff-consol-021-command-001",
      "command_receipt": {
        "status": "accepted",
        "command_id": "cmd-placeholder",
        "type": "PauseExecution"
      }
    }
  ],
  "regression_checks": {
    "legacy_action_replay": "pending",
    "final_command_replay": "pending",
    "idempotency_conflict_409": "pending",
    "missing_confirm_token_confirm_token_required": "pending",
    "missing_approval_evidence_approval_required": "pending"
  },
  "soak": {
    "required_clean_days": 7,
    "started_at": null,
    "earliest_completion_at": null,
    "days": []
  },
  "blockers": [
    "BFF-CONSOL-020 must complete review/absorption before frontend soak claims are final."
  ]
}
```

The parent owner should replace placeholders with real command ids and test
timestamps. Keep the evidence compact enough for reviewers to compare route mix,
receipt identity, typed errors, and soak progression without reading raw logs.

## Operator Journey to Preserve

```text
Operator opens a live-write-capable detail surface
  -> UI verifies authenticated session and VITE_BFF_REAL_WRITES=true
  -> Existing callers continue through /bff/actions/* by default
  -> New command caller can submit the same governed action to /bff/v1/commands
  -> BFF derives actor_ref from auth and requires a stable Idempotency-Key
  -> BFF validates role, policy, command params, confirm token, approval evidence,
     and two-man evidence where required
  -> Accepted command writes a durable command record and audit/foundation context
  -> UI receives one normalized CommandResponse-style receipt
  -> Exact retry with the same idempotency key shows the same receipt
  -> Changed retry with the same idempotency key returns 409 IDEMPOTENCY_CONFLICT
  -> Missing confirm/approval evidence returns typed non-2xx errors, not success
```

Do not imply live capital side effects are enabled. BFF-CONSOL-019 explicitly
keeps adapter-only command dispatch with `live_capital_side_effects=false`, and
BFF-CONSOL-022 keeps Lovable strict preview writes off with
`VITE_BFF_REAL_WRITES=false`.

## Frontend Handoff Notes

- Treat `/home/lupin/code/execute-plans/src/lib/bff/commandClient.ts` from
  sibling commit `30b4ed3` as the expected BFF-CONSOL-020 seam only after its
  review lifecycle is accepted.
- `runAction` should remain defaulted to legacy `/bff/actions/*` until
  BFF-CONSOL-024 explicitly deprecates the old receipt path.
- Use `runCommandAction(..., { route: "commands" })` or direct
  `commandClient.submitCommand` only for the final-command caller path.
- Preserve `Idempotency-Key`; do not put `idempotencyKey` in the JSON body.
- Forward `X-Confirm-Token` and explicit approval/two-man evidence fields. The
  backend typed errors are only useful if the frontend does not convert them to
  success receipts.
- In strict Lovable staging (`BFF-CONSOL-022`), `VITE_BFF_REAL_WRITES=false`
  should block live writes. 021 proof should therefore be backend/local or a
  controlled authenticated smoke, not a production-capital exercise.

## Parent Absorption Order

1. Confirm BFF-CONSOL-020 is review-approved or explicitly decide to test
   against the sibling frontend reference commit while 020 is still in review.
2. Add the parent-owned backend test file and run the focused py_compile/pytest
   checks above.
3. Initialize and populate `support/evidence/BFF-CONSOL-021-dual-write-soak.json`
   with real receipt samples from the tests.
4. If remote/staging soak is in scope for the parent closeout, record seven
   elapsed clean days before requesting final review. If credentials or preview
   reachability are missing, block the parent task with the concrete missing
   dependency instead of marking the soak complete.
5. Hand the parent task to Claude with the evidence JSON, exact verification
   commands, and any remaining BFF-CONSOL-020 dependency caveat.

## Reviewer Checklist for This Sidecar

- This packet only edits
  `support/sidecars/BFF-CONSOL-021/BFF-CONSOL-021-SIDECAR-BFF-HANDOFF.md`.
- It does not modify L1 canonical truth, BFF runtime code, BFF contract truth,
  registry code, governance code, or execute-plans source.
- It clearly separates sidecar support from parent implementation.
- It identifies the parent artifact gaps and the downstream BFF-CONSOL-024 and
  BFF-CONSOL-027 dependencies.

## Owner Closeout Verification

Codex2 closeout on 2026-05-13 confirmed the approved support-only scope still
holds. The task-owned artifact remains limited to this packet; no L1 canonical
truth, BFF runtime code, BFF contract truth, registry code, governance code, or
execute-plans source is part of this sidecar closeout.

Focused closeout checks:

```bash
git diff --check -- support/sidecars/BFF-CONSOL-021/BFF-CONSOL-021-SIDECAR-BFF-HANDOFF.md
git diff --no-index --check /dev/null support/sidecars/BFF-CONSOL-021/BFF-CONSOL-021-SIDECAR-BFF-HANDOFF.md
LC_ALL=C grep -nP '[^\x00-\x7F]' support/sidecars/BFF-CONSOL-021/BFF-CONSOL-021-SIDECAR-BFF-HANDOFF.md
python3 -m py_compile services/control-plane/bff/tests/test_actions_to_commands_adapter.py
python3 -m pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py -q
```

Results: no whitespace-error output; no non-ASCII matches; py_compile passed;
pytest passed with 3 tests.

## Verification for This Sidecar

Performed as read-only context checks plus support-artifact update:

```bash
sed -n '1,220p' AI_COLLABORATION_GUIDE.md
sed -n '1,260p' .orchestrator/task-briefs/bff_consol_021_sidecar_bff_handoff.md
sed -n '1,260p' .orchestrator/skills/task-closeout-finalization.md
sed -n '1,220p' ai-status.json
jq '.tasks[] | select(.id=="BFF-CONSOL-021-SIDECAR-BFF-HANDOFF")' ai-status.json
sed -n '1,220p' ai-task-archive/tasks/BFF-CONSOL-019.json
sed -n '1,280p' support/sidecars/BFF-CONSOL-020/BFF-CONSOL-020-SIDECAR-BFF-HANDOFF.md
rg -n 'v1/commands|actions/\\{entityType\\}|IDEMPOTENCY_CONFLICT|CONFIRM_TOKEN_REQUIRED|APPROVAL_REQUIRED' services/control-plane/bff
rg -n 'submitCommand|runCommandAction|Idempotency-Key|X-Confirm-Token' /home/lupin/code/execute-plans/src/lib/bff
```

Final sidecar formatting check should be run before handoff:

```bash
git diff --check -- support/sidecars/BFF-CONSOL-021/BFF-CONSOL-021-SIDECAR-BFF-HANDOFF.md
```

Initial sidecar preparation did not require runtime tests because this is a
support packet only. Owner closeout additionally ran the existing adapter test
listed above to preserve the reviewer-verified command adapter evidence.

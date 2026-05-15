# BFF-CONSOL-019 Sidecar: BFF And Frontend Handoff Packet

| Field | Value |
|---|---|
| Task | `BFF-CONSOL-019-SIDECAR-BFF-HANDOFF` |
| Parent task | `BFF-CONSOL-019` |
| Helper kind | `bff_handoff_packet` |
| Owner | `Codex2` |
| Reviewer | `Codex` |
| Status | ready for Codex review |

## Scope Guard

This sidecar is support-only. It does not modify L1 canonical truth, core contract
truth, runtime implementation, registry implementation, or governance
implementation. Its only task-owned artifact is this handoff packet.

The parent implementation is already archived as `done` in
`ai-task-archive/tasks/BFF-CONSOL-019.json`. The parent delivery commit is
`34fa7aec2a931333966d98b71a1c2d6bd5d0fe44` with subject
`BFF-CONSOL-019 adapt actions to command admission`.

## Evidence Sources

| Source | Use in this packet |
|---|---|
| `.orchestrator/task-briefs/bff_consol_019_sidecar_bff_handoff.md` | Sidecar scope, owner/reviewer, artifact list |
| `ai-task-archive/tasks/BFF-CONSOL-019.json` | Parent terminal state, commit, review, push/gate notes |
| `.orchestrator/reviews/BFF-CONSOL-019-review-claude.md` | Parent review findings and focused verification |
| `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md` | Command facade contract and section 8 action mapping rules |
| `services/control-plane/bff/main.py` | Current action adapter and final command admission implementation |
| `services/control-plane/bff/command_executor.py` | Adapter-only executor behavior |
| `services/control-plane/bff/tests/test_actions_to_commands_adapter.py` | Regression coverage for parent acceptance |

## Parent State Snapshot

| Field | Current value |
|---|---|
| Parent status | `done` |
| Parent owner / reviewer | `Codex2` / `Claude` |
| Parent review file | `.orchestrator/reviews/BFF-CONSOL-019-review-claude.md` |
| Parent verification | `py_compile` OK; focused pytest passed 3 tests |
| Parent merge gate | EP5 paper-canary closeout gate still applies before merge to `main` |
| Parent delivery publication | Archive records upstream `origin/feat/bff-consol-022-staging-strict-cutover`, ahead by 1 at archive time |

## BFF Query / Action Gap

Before BFF-CONSOL-019, frontend `runAction` style calls could use
`POST /bff/actions/{entityType}/{entityId}/{actionId}` as a direct action surface.
The production gap was that this write path was not consistently represented as
a governed final-contract command with durable idempotency, trace, policy, audit,
and typed target evidence.

BFF-CONSOL-019 closes the backend part of that gap by routing generic action
calls through final command admission:

```text
POST /bff/actions/{entityType}/{entityId}/{actionId}
  -> _action_adapter_command_payload(...)
  -> _submit_final_command_admission(route="POST /bff/v1/commands",
                                     source_route="POST /bff/actions/{entityType}/{entityId}/{actionId}",
                                     enqueue=False,
                                     include_durable_meta=True)
```

The adapter now records:

| Contract point | Handoff detail |
|---|---|
| Actor | Extracted from authenticated operator identity before admission |
| Idempotency | `Idempotency-Key` is required; `X-Idempotency-Key` remains a compatibility alias |
| Trace | `X-Trace-Id`, `X-Correlation-Id`, and `X-Request-Id` propagate into foundation context |
| Policy / audit | Admission records `policy_decision` and audit metadata, including `source_route` |
| Target | `entityType` maps to a typed command target such as `Strategy`, `Runtime`, `Incident`, or `ApprovalDecision` |
| Response | Legacy action callers receive final-style `CommandResponse` data plus durable meta |
| Execution | `enqueue=False`; adapter-only executor records `live_capital_side_effects: false` |

## Operator Journey

1. Operator initiates a write from an execute-plans detail surface, such as
   submitting a strategy review or triggering a runtime action.
2. The frontend sends either the temporary legacy action route or, after
   BFF-CONSOL-020 absorption, the final `/bff/v1/commands` route.
3. The browser supplies a stable `Idempotency-Key`, auth/session evidence, trace
   headers, and any required high-risk confirmation token.
4. BFF normalizes the action into a command envelope, derives actor and target
   references, writes foundation command context, and records audit/policy data.
5. The operator receives `CommandResponse<T>` with `status: accepted`, command
   receipt identifiers, and durable meta. This means the command was admitted;
   it does not mean downstream live execution completed.
6. The UI should poll or subscribe to the command/status projection when it needs
   completion state. Do not infer live completion from the initial 202 response.

Expected negative-path behavior:

| Scenario | Expected handling |
|---|---|
| Missing idempotency header | Non-2xx `BffErrorEnvelope`; focused test expects 400 `INVALID_PARAMS` with `precondition_failed: idempotency_key` |
| Reused idempotency key with different payload | Non-2xx conflict from shared final admission path |
| Unauthorized role | Non-2xx policy denial with foundation error and deny decision evidence |
| Missing confirm token for high-risk action | Non-2xx `CONFIRM_TOKEN_REQUIRED` per command contract |
| Missing approval / two-man evidence | Non-2xx `APPROVAL_REQUIRED` or `TWO_MAN_REQUIRED` per command contract |

## Frontend Handoff

BFF-CONSOL-020 owns the frontend migration. This packet should be used as the
backend handoff source for that work.

Frontend implementation notes:

| Area | Required behavior |
|---|---|
| New callers | Prefer direct `POST /bff/v1/commands` through `commandClient` |
| Existing callers | Keep `/bff/actions/*` working until the explicit retirement task; the backend adapter now admits those calls as commands |
| Idempotency | Mint one stable `Idempotency-Key` per operator intent and reuse it for retries of the same intent |
| Trace | Preserve or generate `X-Trace-Id`, `X-Correlation-Id`, and `X-Request-Id` |
| Confirmation | Pass `X-Confirm-Token` and matching high-risk action/body evidence when the modal flow requires it |
| Response model | Treat success as `CommandResponse<T>` with required `status` and `data`; typed precondition failures are non-2xx errors, not pseudo-success statuses |
| Receipt display | Show the command/receipt id from response data; use status polling or SSE when completion matters |
| Operator copy | Say "admitted" or equivalent for the initial 202, not "completed" or "executed live" |

Do not add frontend-only fallback state that claims a command succeeded when BFF
returned a typed error. BFF-CONSOL-021 is the receipt dual-write/replay slice and
should own reconciliation between old action receipts and new command receipts.

## Parent Absorption Notes

The parent task can absorb this packet as a support note only. No canonical
document promotion is required for this sidecar.

Open gates and follow-on dependencies:

| Item | Owner task | Note |
|---|---|---|
| EP5 merge gate | `BFF-CONSOL-019` parent / release owner | Do not merge the runtime change to `main` until EP5 paper-canary closeout is confirmed |
| Frontend direct command caller | `BFF-CONSOL-020` | Migrate new runAction callers to `/bff/v1/commands` while preserving legacy adapter compatibility |
| Receipt replay/conflict soak | `BFF-CONSOL-021` | Validate same-key replay, conflict, confirm-token, approval, and dual-write behavior |
| End-to-end acceptance packet | `BFF-CONSOL-027` | Update any stale reference that still describes BFF-CONSOL-019 as pending |
| Publication gap | Parent delivery metadata | Parent archive recorded branch ahead by 1; parent owner/release owner should verify push state before relying on remote availability |

## Sidecar Verification

Commands run for this packet refresh:

```bash
python3 -m py_compile services/control-plane/bff/tests/test_actions_to_commands_adapter.py
python3 -m pytest services/control-plane/bff/tests/test_actions_to_commands_adapter.py -v
git diff --check -- support/sidecars/BFF-CONSOL-019/BFF-CONSOL-019-SIDECAR-BFF-HANDOFF.md
```

Result: `py_compile` passed, focused pytest passed `3` tests, and the sidecar
artifact diff has no whitespace errors.

## Reviewer Checklist For Codex

- Confirm this sidecar only changes support material.
- Confirm the parent state, commit, and review references match current task truth.
- Confirm frontend handoff notes stay within BFF-CONSOL-019/020/021 boundaries.
- Confirm no L1 canonical truth, core contract truth, runtime, registry, or governance implementation changed in this sidecar.
- If accepted, move this sidecar to `review_approved`; owner will perform normal closeout before `done`.

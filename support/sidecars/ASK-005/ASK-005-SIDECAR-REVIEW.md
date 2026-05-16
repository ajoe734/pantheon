# ASK-005 Sidecar Review Packet

**Sidecar task:** `ASK-005-SIDECAR-REVIEW`
**Helper parent:** `ASK-005`
**Helper kind:** `review_packet`
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Claude`
**Generated:** `2026-05-16T11:48:00Z`
**Status:** `review-approved`

> Scope constraint: support artifact only. This packet summarizes observed
> ASK-005 review state, evidence, verification, and reviewer handoff notes. It
> does not edit canonical truth, L1 policy, runtime code, registry code, or
> governance implementation.

## Executive Summary

`ASK-005` is the parent task for approval and ask SSE event publishing in the
BFF. The parent is currently `in_progress` after a Codex review requested two
blocking fixes:

1. `escalate` and `freeze` decisions must publish `approval.stage.changed`
   instead of being reported as terminal `approval.decided` events.
2. Approval decision idempotency replay must not publish duplicate SSE events.

The current observed parent worktree and evidence show both review findings
addressed:

- `bff_approvals_decide` defines `_APPROVAL_STAGE_CHANGE_DECISIONS` with
  `request_revision`, `escalate`, and `freeze`.
- approval event publishing now runs behind an idempotency pre-check against
  `_FINAL_CONTRACT_IDEMPOTENCY`.
- the direct ASK-005 test file contains 12 tests at closeout, including coverage for
  `escalate`, `freeze`, and approval replay de-duplication.
- focused local verification passed during closeout: `12 passed in 11.64s`.

Original reviewer attention was around publication state rather than behavior:
the first sidecar run did not yet see a durable parent fix commit after Codex
review commit `afaca235`. Claude's sidecar approval records that the parent fix
commit `73304fe0` was later captured in the subsequent handoff. Parent
`ASK-005` remains a separate task and should be approved or finalized only by
its own owner/reviewer flow.

## Sources Used

| Source | Role |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Collaboration lifecycle and sidecar boundaries |
| `.orchestrator/task-briefs/ask_005_sidecar_review.md` | Sidecar scope, owner/reviewer, artifact target |
| `ai-status.json` / `scripts/ai-status.sh show ASK-005-SIDECAR-REVIEW` | Durable task state and parent status |
| `support/reviews/ASK-005-review-codex.md` | Original Codex blocking review findings |
| `support/sidecars/ASK-005/ASK-005-SIDECAR-ACCEPTANCE.md` | Earlier sidecar acceptance map and reviewer attention points |
| `support/evidence/ASK-005/README.md` | Parent implementation/evidence summary after review fixes |
| `services/control-plane/bff/main.py` | Observed current parent runtime behavior |
| `services/control-plane/bff/test_ask005_sse_event_publishing_contract.py` | Direct ASK-005 verification coverage |
| `services/control-plane/bff/BFF_API_CONTRACT.md` section 11 | SSE channel and event-type catalog |
| `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json` | Task-scoped planning reference; no direct `ASK-005` match, but it records command idempotency/RBAC/audit as relevant BFF command expectations |

No `current-work.md` or full `ai-activity-log.jsonl` scan was used.

## Review Status Against Codex Findings

| Review item | Original state | Current observed state | Sidecar read |
|---|---|---|---|
| `escalate` event type | Codex observed `approval.decided` for `escalate` | `raw_decision in _APPROVAL_STAGE_CHANGE_DECISIONS` publishes `approval.stage.changed`; dedicated test exists | appears addressed |
| `freeze` event type | Codex observed `approval.decided` for `freeze` | `raw_decision in _APPROVAL_STAGE_CHANGE_DECISIONS` publishes `approval.stage.changed`; dedicated test exists | appears addressed |
| approval replay duplicate event | Codex observed replay returned `replayed=True` but approval buffer length became 2 | idempotency pre-check skips publish when `_FINAL_CONTRACT_IDEMPOTENCY` already contains matching request hash; dedicated replay test exists | appears addressed |
| role-gate failure side effect | already covered by original tests | still covered by direct test | no new concern |
| ask session create event | already covered by original tests | still covered by direct test | no new concern |
| ask session replay de-duplication | already covered by original tests | still covered by direct test | no new concern |

## Event Matrix

| Mutation | Channel | Expected event behavior | Current evidence |
|---|---|---|---|
| `POST /bff/agora/ask/sessions` | `ask` | publish `ask.session.started` once | implemented and tested |
| replay ask session create with same idempotency key | `ask` | no second ask event | implemented and tested |
| `POST /bff/approvals/{id}/decide` with `approve` | `approval` | publish `approval.decided`, `outcome=approved` | implemented and tested |
| `POST /bff/approvals/{id}/decide` with `reject` | `approval` | publish `approval.decided`, `outcome=rejected` | implemented and tested |
| `POST /bff/approvals/{id}/decide` with `request_revision` | `approval` | publish `approval.stage.changed`, `current_stage=request_revision` | implemented and tested |
| `POST /bff/approvals/{id}/decide` with `escalate` | `approval` | publish `approval.stage.changed`, `current_stage=escalate` | implemented and tested after review fix |
| `POST /bff/approvals/{id}/decide` with `freeze` | `approval` | publish `approval.stage.changed`, `current_stage=freeze` | implemented and tested after review fix |
| replay approval decide with same idempotency key | `approval` | no second approval event | implemented and tested after review fix |
| approval decide with insufficient role | `approval` | no approval event | implemented and tested |

## Evidence Snapshot

Parent evidence now records:

```text
pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -v
# 9 passed in 28.24s

pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q
# 14 passed

pytest services/control-plane/bff/test_bff_approvals_decide_contract.py \
       services/control-plane/bff/test_ask_001_sessions_contract.py \
       services/control-plane/bff/test_ask_003_committee_lifecycle.py \
       services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 113 passed
```

Sidecar-local verification:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -q
# initial sidecar run: 9 passed in 17.74s
# closeout run 2026-05-16T14:12:30Z: 12 passed in 11.64s

rg -n "ASK-005|ask.session.started|approval.decided|approval.stage.changed|idempot" \
  docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/planning-session.json
# no direct ASK-005 match; generic BFF command idempotency/RBAC/audit references found

AI_NAME=Codex ./scripts/ai-status.sh show ASK-005-SIDECAR-REVIEW
# task is active, owner Codex, reviewer Claude, artifact target matches this file
```

The only task artifact authored by this sidecar is this support packet. Status
commands may also update L0/generated collaboration state.

## Reviewer Attention

1. Parent `ASK-005` is still `in_progress` in durable state. This packet should
   support review but should not be read as parent approval.
2. The original publication watchpoint has been carried into review approval:
   Claude recorded parent fix commit `73304fe0` in the subsequent handoff.
   Require the parent owner to keep the runtime/evidence changes durable before
   parent review approval.
3. The approval replay guard duplicates the request-hash construction used by
   `_sem_command_response`. It passes focused tests, but future refactors should
   consider centralizing that hash calculation to avoid drift.
4. The test module header still names the original smaller coverage set. This is
   documentation drift only; the executable tests cover the review fixes.

## Handoff

To `Claude`, sidecar reviewer and parent owner:

- Review this packet as support-only material for `ASK-005`.
- If accurate, accept the sidecar packet and use it to prepare the parent task's
  updated review handoff.
- Before parent `ASK-005` returns to review, ensure the parent-owned runtime
  patch, tests, and evidence are committed or otherwise made durable, then rerun
  the focused and adjacent test commands listed in parent evidence.

Recommended sidecar disposition: approve this packet as a truthful support
summary. Parent disposition should remain separate and should depend on the
parent task's committed fix state.

## Closeout Addendum

Claude approved this sidecar packet and returned it to Codex for owner closeout.
The approval confirms that the packet accurately records the Codex blocking
findings, observed ASK-005 fixes, and the publication watchpoint. Support-only
scope remains intact: no L1 canonical truth, runtime, registry, or governance
implementation files were changed by this sidecar.

Finalization verification:

```text
AI_NAME=Codex ./scripts/ai-status.sh show ASK-005-SIDECAR-REVIEW
# status review_approved; owner Codex; reviewer Claude; artifact target matches

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -q
# 12 passed in 11.64s
```

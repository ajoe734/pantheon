# ASK-005 Sidecar Acceptance Packet

**Sidecar task:** `ASK-005-SIDECAR-ACCEPTANCE`
**Helper parent:** `ASK-005`
**Helper kind:** `acceptance_packet`
**Parent owner:** `Claude`
**Parent reviewer:** `Codex2`
**Sidecar owner:** `Codex`
**Sidecar reviewer:** `Claude`
**Generated:** `2026-05-16T11:13:57Z`
**Owner closeout verification:** `2026-05-16T14:08:27Z` by `Codex`
**Status:** `review-approved / closeout-ready`

> Scope constraint: support artifact only. This packet summarizes acceptance
> criteria, dependency routing, verification evidence, and reviewer attention
> points for `ASK-005`. It does not modify canonical truth, L1 policy,
> runtime code, registry code, or governance implementation.

## Executive Summary

`ASK-005` entered parent review after commit `6c7484c1` added BFF SSE
publishing for ask session creation and approval decisions. This sidecar packet
identified two narrow reviewer attention items: `escalate`/`freeze` event
typing and approval replay de-duplication.

Closeout update: the sidecar reviewer approved this packet on
`2026-05-16T11:47:10Z` and noted that both attention items were addressed in
parent commit `73304fe0`. Current parent evidence records 10 direct `ASK-005`
contract tests, including `escalate`, `freeze`, approval replay de-duplication,
and body `idempotencyKey` rejection before any approval SSE publish. The
current worktree now collects and passes 12 direct `ASK-005` tests, adding a
durable approval replay de-duplication guard.

The delivered slice has strong direct coverage for:

1. `POST /bff/agora/ask/sessions` publishing `ask.session.started` on the
   `ask` channel.
2. Ask-session idempotency replay not double-publishing the started event.
3. `POST /bff/approvals/{id}/decide` publishing `approval.decided` for
   approve and reject decisions.
4. Approval role-gate failures not publishing events.
5. `request_revision` publishing `approval.stage.changed`.

The original reviewer attention points, as of the first packet, were:

1. Parent status text says `escalate` and `freeze` publish
   `approval.stage.changed`, but the then-current code mapped those decisions to
   `CommandType.APPROVE_DECISION`, which publishes `approval.decided`.
2. Approval decide event publishing happens before `_sem_command_response`
   performs final-contract idempotency replay checks, so approval replay
   de-duplication is not established by the current ASK-005 tests.

This packet is therefore a support handoff and closeout record: the original
acceptance map was useful for parent owner/reviewer triage, and the two flagged
items have since been repaired in the parent line.

## Closeout Addendum

| Item | Closeout read | Evidence |
|---|---|---|
| Sidecar review | approved | `ai-status.json` review notes and task brief both record Claude approval at `2026-05-16T11:47:10Z` |
| `escalate` event type | repaired in parent | `main.py` now classifies `escalate` as `approval.stage.changed`; direct ASK-005 test covers it |
| `freeze` event type | repaired in parent | `main.py` now classifies `freeze` as `approval.stage.changed`; direct ASK-005 test covers it |
| Approval replay de-dup | repaired in parent | `bff_approvals_decide` now pre-checks `_FINAL_CONTRACT_IDEMPOTENCY` before publish; direct ASK-005 test covers replay |
| Body idempotency rejection before publish | repaired in parent | Direct ASK-005 test asserts 400 and zero approval events when body `idempotencyKey` is supplied |
| Sidecar scope | unchanged | This closeout only updates `support/sidecars/ASK-005/ASK-005-SIDECAR-ACCEPTANCE.md` |

## Sources Used

| Source | Role |
|---|---|
| `ai-status.json` | Durable task state for `ASK-005` and this sidecar |
| `.orchestrator/task-briefs/ask_005_sidecar_acceptance.md` | Sidecar scope, owner/reviewer, artifact target |
| `support/evidence/ASK-005/README.md` | Parent implementation and verification summary |
| `services/control-plane/bff/main.py` | Current runtime behavior for ask and approval publishing |
| `services/control-plane/bff/test_ask005_sse_event_publishing_contract.py` | Direct ASK-005 contract coverage |
| `services/control-plane/bff/BFF_API_CONTRACT.md` section 11 | Channel and event-type inventory |

The task-scoped planning session listed in the brief did not contain a direct
`ASK-005` string match, so this packet uses the current task state, evidence
file, implementation, and tests as the concrete review sources.

## Acceptance Checklist

| Criterion | Current read | Evidence | Reviewer action |
|---|---|---|---|
| Support artifact only | pass | This file is under `support/sidecars/ASK-005/` and no canonical/runtime files were edited by the sidecar | Confirm scope remains support-only |
| Ask session create publishes started event | pass | `main.py:24509` publishes `ask.session.started`; test asserts one ask event with `session_id` and `mode=quick_ask` | Parent reviewer can accept if event payload shape is sufficient |
| Ask session idempotency replay does not double publish | pass | Test reuses an idempotency key and asserts one ask event remains | No extra parent action required |
| Approval approve publishes terminal decision event | pass | Test asserts `approval.decided`, `approval_id`, `outcome=approved`, and `decided_by` | No extra parent action required |
| Approval reject publishes terminal decision event | pass | Test asserts `approval.decided`, `outcome=rejected`, and `approval_id` | No extra parent action required |
| Approval request_revision publishes stage event | pass | Test asserts `approval.stage.changed`, `current_stage=request_revision`, and `actor_id` | No extra parent action required |
| Approval role-gate failure does not publish | pass | Test posts with only `operator` role and asserts 403 plus zero approval events | No extra parent action required |
| Approval escalate/freeze event type matches parent handoff | pass | Parent line now classifies `escalate` and `freeze` as `approval.stage.changed`; direct tests cover both decisions | No extra parent action required |
| Approval idempotency replay does not double publish | pass | Parent line now pre-checks final-contract idempotency before approval SSE publish; direct tests cover replay de-duplication | No extra parent action required |

## Event Matrix

| Mutation | Channel | Expected event | Current status |
|---|---|---|---|
| `POST /bff/agora/ask/sessions` | `ask` | `ask.session.started` | implemented and directly tested |
| replay `POST /bff/agora/ask/sessions` with same key | `ask` | no second event | directly tested |
| `POST /bff/approvals/{id}/decide` with `approve` | `approval` | `approval.decided` with `outcome=approved` | implemented and directly tested |
| `POST /bff/approvals/{id}/decide` with `reject` | `approval` | `approval.decided` with `outcome=rejected` | implemented and directly tested |
| `POST /bff/approvals/{id}/decide` with `request_revision` | `approval` | `approval.stage.changed` | implemented and directly tested |
| `POST /bff/approvals/{id}/decide` with insufficient role | `approval` | no event | implemented and directly tested |
| `POST /bff/approvals/{id}/decide` with `escalate` | `approval` | `approval.stage.changed` | implemented and directly tested |
| `POST /bff/approvals/{id}/decide` with `freeze` | `approval` | `approval.stage.changed` | implemented and directly tested |
| replay `POST /bff/approvals/{id}/decide` with same key | `approval` | no second event | implemented and directly tested |

## Dependency Map

| Dependency | Direction | Why it matters for ASK-005 review |
|---|---|---|
| `services/control-plane/bff/BFF_API_CONTRACT.md` section 11 | upstream contract inventory | Lists `approval.stage.changed`, `approval.decided`, and `ask.session.started` as valid BFF SSE event types |
| `services/control-plane/bff/main.py` SSE substrate | runtime dependency | `_sse_buffers`, `_sse_subscribers`, and `_publish_event` are the in-memory event path ASK-005 now uses |
| `services/control-plane/bff/main.py` `sem_agora_ask_create_session` | parent implementation | Publishes `ask.session.started` after creating quick-ask sessions and after the ask idempotency early-return guard |
| `services/control-plane/bff/main.py` `bff_approvals_decide` | parent implementation | Publishes approval events after role/shape checks and before command-response idempotency replay handling |
| `services/control-plane/bff/test_ask005_sse_event_publishing_contract.py` | direct verification | Current worktree collects 12 direct ASK-005 tests for ask started, approval decided/stage changed, role-gate, ask replay, approval replay, and body idempotency rejection |
| `services/control-plane/bff/test_pkt005_sse_substrate_contract.py` | substrate regression guard | Parent evidence reports 14 passing tests for SSE replay/substrate behavior |
| `services/control-plane/bff/test_bff_approvals_decide_contract.py` | adjacent approval command guard | Parent evidence reports this remained green with ASK-001/003/004 tests in the 113-test bundle |
| `support/evidence/ASK-005/README.md` | parent evidence packet | Records the parent implementation summary, reviewer fixes, and verification commands |

## Verification Snapshot

Original parent evidence recorded:

```text
pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -v
# 6 passed

pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q
# 14 passed

pytest services/control-plane/bff/test_bff_approvals_decide_contract.py \
       services/control-plane/bff/test_ask_001_sessions_contract.py \
       services/control-plane/bff/test_ask_003_committee_lifecycle.py \
       services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 113 passed
```

Current parent evidence now records:

```text
pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -v
# 10 passed in 12.44s

pytest services/control-plane/bff/test_pkt005_sse_substrate_contract.py -q
# 14 passed

pytest services/control-plane/bff/test_bff_approvals_decide_contract.py \
       services/control-plane/bff/test_ask_001_sessions_contract.py \
       services/control-plane/bff/test_ask_003_committee_lifecycle.py \
       services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 113 passed
```

Sidecar closeout verification for this packet:

```text
rg -n "ASK-005|ask.session.started|approval.decided|approval.stage.changed"
sed -n focused reads of the ASK-005 evidence, test file, BFF contract section 11,
and relevant `main.py` implementation ranges.

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_ask005_sse_event_publishing_contract.py -q
# 12 passed in 37.18s
```

No runtime, registry, governance, or L1 canonical files were modified by this
sidecar.

## Historical Reviewer Handoff

This handoff text is retained as the reviewed support packet. The closeout
addendum above supersedes the action items because the parent line has since
repaired the flagged points.

To `Claude`, sidecar reviewer and parent owner:

1. Use this packet to decide whether the parent `ASK-005` review handoff should
   be amended before `Codex2` approval.
2. If `escalate` and `freeze` are intentionally pass-through approve commands
   for now, update the parent review/evidence language so it does not claim
   `approval.stage.changed` for those decisions.
3. If approval-event idempotency de-duplication is required, add or request a
   targeted test and implementation adjustment so replay cannot publish a
   second approval event.
4. If those two points are accepted as out of scope, the directly tested ASK-005
   behavior appears ready for parent reviewer inspection.

Recommended sidecar disposition: approve this support packet as a truthful
dependency and acceptance map, then let the parent owner decide whether to patch
the parent task before `Codex2` completes the main review.

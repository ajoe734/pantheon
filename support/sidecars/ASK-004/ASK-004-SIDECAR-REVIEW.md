# ASK-004 Sidecar Review Packet

**Task:** ASK-004-SIDECAR-REVIEW
**Parent task:** ASK-004
**Helper kind:** review_packet
**Owner:** Codex
**Reviewer:** Claude
**Date:** 2026-05-16
**Canonical/runtime mutation:** none

## Purpose

This sidecar records the review packet for ASK-004 after the parent task was already approved and closed. It is support material only; it does not change ASK-004 contract truth, BFF runtime behavior, registry implementation, or governance policy.

## Parent State

- `AI_NAME=Codex ./scripts/ai-status.sh show ASK-004` resolves to archive source `ai-task-archive/tasks/ASK-004.json`.
- Parent terminal status: `done`.
- Parent terminal outcome: `completed`.
- Parent closeout commit: `439f7dbec1c8da56beb6b7026be4761b3645f46c` (`ASK-004: finalize memo publish review`).
- Parent reviewer approval: `support/reviews/ASK-004-review-claude2.md`.
- Parent evidence packet: `support/evidence/ASK-004/README.md`.

## Reviewed Scope Summary

ASK-004 implemented committee-session memo review and publish behavior on top of the ASK-003 committee lifecycle:

- `GET /bff/agora/committee/sessions/{sessionId}/memos`
- `POST /bff/agora/committee/sessions/{sessionId}/memos`
- `GET /bff/agora/committee/sessions/{sessionId}/memos/{memoId}`
- `POST /bff/agora/committee/sessions/{sessionId}/memos/{memoId}/publish`

The accepted boundary is advisory/local-registry only:

- Publish makes the memo visible through the consult memo registry read model.
- Publish emits `ask.memo.published` only on first publish.
- Repeated publish keeps `published_at` stable.
- Duplicate explicit `memoId` submissions are rejected unless replaying the same request with the same idempotency key.
- No deployment, broker, capital, or runtime side effects are introduced.

## Evidence Map

| Evidence | Location | Notes |
|---|---|---|
| Implementation scope and test commands | `support/evidence/ASK-004/README.md` | Owner-written evidence packet for routes, read-store helpers, registry projection, advisory boundary, and closeout verification. |
| Reviewer decision | `support/reviews/ASK-004-review-claude2.md` | Claude2 approved ASK-004, including advisory boundary, timestamp stability, first-publish event semantics, registry visibility, and no OpenAPI duplicate issue. |
| Archive record | `ai-task-archive/tasks/ASK-004.json` | Durable terminal state, review notes, handoffs, delivery commit, and verification summary. |
| Contract tests | `services/control-plane/bff/test_ask_004_memo_publish_contract.py` | 31 ASK-004 contract tests recorded as passing by owner and reviewer. |
| Regression coverage | `services/control-plane/bff/test_ask_003_committee_lifecycle.py`, `services/control-plane/bff/test_cw04_redteam_memo_contract.py`, `services/control-plane/bff/test_pkt015_consultation_workbench_contract.py` | 38 regression tests recorded as passing during ASK-004 closeout. |

## Verification Already Recorded For Parent

From the parent evidence and review artifacts:

```bash
python3 -m py_compile services/control-plane/bff/main.py services/control-plane/bff/read_store.py services/control-plane/bff/test_ask_004_memo_publish_contract.py
# OK

python3 -m pytest services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 31 passed

python3 -m pytest services/control-plane/bff/test_ask_003_committee_lifecycle.py services/control-plane/bff/test_cw04_redteam_memo_contract.py services/control-plane/bff/test_pkt015_consultation_workbench_contract.py -q
# 38 passed
```

This sidecar did not rerun runtime tests because it only collates support evidence and does not change runtime code.

## Sidecar Verification

Read-only/context checks run by this sidecar:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show ASK-004-SIDECAR-REVIEW
AI_NAME=Codex ./scripts/ai-status.sh show ASK-004
sed -n '1,260p' support/evidence/ASK-004/README.md
sed -n '1,220p' support/reviews/ASK-004-review-claude2.md
sed -n '1,180p' ai-task-archive/tasks/ASK-004.json
git status --short
```

## Reviewer Handoff

Claude should treat this packet as support-only review material. The parent ASK-004 task is already done, and there is no additional canonical change to absorb unless the parent owner later asks for a retrospective documentation promotion.

Recommended review decision for this sidecar:

- Confirm this artifact accurately summarizes the existing ASK-004 evidence/review/archive state.
- Confirm no L1 canonical truth, core contract truth, runtime implementation, registry behavior, or governance implementation was changed by the sidecar.
- If accepted, approve the sidecar so Codex can perform normal closeout for `ASK-004-SIDECAR-REVIEW`.

## Non-Goals

- Do not reopen ASK-004 implementation.
- Do not broaden the consult memo registry contract.
- Do not alter ASK-005 SSE/governance workflow ownership.
- Do not promote this sidecar into canonical truth without a separate owner decision.

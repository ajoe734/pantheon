# Review Packet: ASK-007

**Sidecar Kind:** review_packet
**Sidecar Task:** ASK-007-SIDECAR-REVIEW
**Parent Task:** ASK-007
**Prepared by:** Codex
**Prepared at:** 2026-05-17
**Reviewer:** Claude
**Parent Reviewer:** Claude2
**Canonical/runtime mutation by this sidecar:** none

---

## Purpose

This sidecar records a support-only review packet for ASK-007. It summarizes the parent task's archived delivery, acceptance evidence, reviewer decision, and a narrow re-run of the ASK-007 regression test.

It does not change canonical truth, core contract truth, runtime behavior, registry behavior, or governance implementation. The parent owner decides whether any of this packet should later be promoted into mainline documentation.

## Parent State

- `AI_NAME=Codex ./scripts/ai-status.sh show ASK-007` resolves to archive source `ai-task-archive/tasks/ASK-007.json`.
- Parent terminal status: `done`.
- Parent terminal outcome: `completed`.
- Parent archive timestamp: `2026-05-17T01:52:54Z`.
- Parent closeout commit: `80b170fa0ca4864b6a857cff26c2a25e11b4a00d` (`ASK-007: redact consult memo review evidence`).
- Parent reviewer approval: Claude2, recorded before owner closeout.
- Note: `.orchestrator/task-briefs/ask_007.md` still shows the earlier `review_approved` handoff state, but the durable archive has the finalized `done` state.

## Reviewed Scope Summary

ASK-007 added a regression guard for review-facing consult memo evidence redaction. The accepted parent behavior is:

- Review-facing consult memo payloads remove persona-internal state fields.
- Review-facing consult memo payloads remove secret credential fields.
- Review-facing consult memo payloads remove capability map internals.
- `authorRef` keeps only review-safe identity fields: `type`, `id`, and `role`.
- Redaction is applied to draft memo creation and memo detail projection before review-facing consumers observe the payload.

The parent implementation changed:

- `services/control-plane/bff/read_store.py`
  - added `_redact_consult_memo_review_payload`
  - applies redaction in committee memo submission and consult memo detail projection
- `services/consultation/test_evidence_redaction.py`
  - adds a single pytest regression covering the publish-to-review flow and all three redaction categories

## Acceptance Criteria Verification

| Criterion | Result | Evidence |
|---|---|---|
| Test asserts memo body does not contain redacted fields when published to review queue | PASS | `_assert_review_payload_redacted` checks `draft_memo`, `published_memo`, `memo_detail`, and `handoffs[0]`. |
| Test covers persona internal state, secret credentials, and capability map internals | PASS | `SENSITIVE_NEEDLES` and fixture payload include `policy_internals`, `memory_trace`, `internal_score`, `secret_credentials`, `api_key`, `secretRef`, `capability_map_internals`, `effective_tools`, and `effective_skills`. |
| Test uses `pytest -q -x` exit 0 | PASS | Sidecar re-run: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/consultation/test_evidence_redaction.py -q -x` -> 1 passed. |
| Test fixture mirrors a real persona consult session | PASS | Test creates an ask session, committee session, opens the committee, submits a persona-authored committee memo, publishes it, and verifies the management review handoff. |

All ASK-007 acceptance criteria: PASS.

## Parent Review Decision

**Decision:** APPROVED

**Reviewer:** Claude2

Recorded review notes from `ai-task-archive/tasks/ASK-007.json`:

- ASK-007 covers three redaction categories: persona internal state, secret credentials, and capability map internals.
- `authorRef` is stripped to `{type, id, role}` during memo creation.
- `read_store._redact_consult_memo_review_payload` protects create/draft and memo detail paths.
- `_assert_review_payload_redacted` verifies draft memo, published memo, memo detail, and handoff payload surfaces.
- Reviewer verification recorded `pytest -q -x services/consultation/test_evidence_redaction.py` as passing.

## Delivery Metadata

| Field | Value |
|---|---|
| Repository | `ajoe734/pantheon` |
| Parent branch at archive | `bff-luv-fe-006-dev-deploy` |
| Parent commit | `80b170fa0ca4864b6a857cff26c2a25e11b4a00d` |
| Parent commit author | Codex |
| Parent commit metadata | `LLM-Agent: Codex`, `Task-ID: ASK-007`, `Reviewer: Claude2`, `Wave: 2026-W20` |
| Parent push status at archive | `ahead` of `origin/bff-luv-fe-006-dev-deploy` |
| Parent dirty worktree at archive | yes, 36 entries; parent task-owned files were committed |

Parent recorded verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/consultation/test_evidence_redaction.py -q -x
# 1 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/bff/test_ask_004_memo_publish_contract.py -q
# 31 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/consultation/test_e2e_consult_review.py -q
# 1 passed

git diff --cached --check
# clean
```

## Sidecar Verification

Commands run by this sidecar:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show ASK-007
AI_NAME=Codex ./scripts/ai-status.sh show ASK-007-SIDECAR-REVIEW
sed -n '1,260p' ai-task-archive/tasks/ASK-007.json
sed -n '1,260p' services/consultation/test_evidence_redaction.py
rg -n "redact|secret_credentials|capability_map|authorRef|memory_trace|policy_internals|internal_score" services/control-plane/bff/read_store.py services/consultation/test_evidence_redaction.py
git show --stat --format=fuller 80b170fa0ca4864b6a857cff26c2a25e11b4a00d
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/consultation/test_evidence_redaction.py -q -x
```

Sidecar test result:

```bash
.                                                                        [100%]
1 passed in 4.89s
```

## Reviewer Handoff

Claude should review this sidecar as support-only material for an already archived parent task.

Recommended review checks:

- Confirm this packet accurately summarizes ASK-007 parent archive, acceptance evidence, and Claude2 approval.
- Confirm the sidecar changed only `support/sidecars/ASK-007/ASK-007-SIDECAR-REVIEW.md`.
- Confirm no L1 canonical truth, core contract truth, runtime implementation, registry behavior, or governance implementation was changed by this sidecar.

If accepted, approve `ASK-007-SIDECAR-REVIEW` so Codex can perform normal closeout.

## Sidecar Review Approval

**Decision:** APPROVED

**Reviewer:** Claude

**Approved at:** 2026-05-17T02:55:54Z

Recorded review result:

- Packet accurately summarizes the archived ASK-007 parent task, commit `80b170fa0ca4864b6a857cff26c2a25e11b4a00d`, Claude2 parent approval, and all four parent acceptance criteria.
- Support-only boundary confirmed: this sidecar changed only `support/sidecars/ASK-007/ASK-007-SIDECAR-REVIEW.md` and did not edit canonical truth, core contract truth, runtime implementation, registry behavior, or governance implementation.
- Reviewer spot-check verification: `PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/consultation/test_evidence_redaction.py -q -x` -> 1 passed.

## Owner Closeout Verification

Commands run during owner finalization:

```bash
AI_NAME=Codex ./scripts/ai-status.sh show ASK-007
AI_NAME=Codex ./scripts/ai-status.sh show ASK-007-SIDECAR-REVIEW
git status --short -- support/sidecars/ASK-007/ASK-007-SIDECAR-REVIEW.md
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/consultation/test_evidence_redaction.py -q -x
```

Closeout results:

- Parent `ASK-007` resolves from `ai-task-archive/tasks/ASK-007.json` with terminal status `done` and terminal outcome `completed`.
- Sidecar `ASK-007-SIDECAR-REVIEW` is active with status `review_approved`, owner `Codex`, and reviewer `Claude`.
- Task-owned worktree scope is limited to `support/sidecars/ASK-007/ASK-007-SIDECAR-REVIEW.md`; unrelated dirty files remain unstaged.
- Finalization pytest re-run: 1 passed in 3.86s.

## Non-Goals

- Do not reopen ASK-007 implementation.
- Do not broaden consult memo redaction semantics beyond the archived parent commit.
- Do not promote this support packet into canonical truth without a separate parent-owner decision.
- Do not treat the parent push-status note as part of this sidecar's runtime scope.

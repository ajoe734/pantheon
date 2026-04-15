# BP5-SVC-008 Acceptance Packet

**Sidecar kind:** `acceptance_packet`  
**Sidecar task:** `BP5-SVC-008-SIDECAR-ACCEPTANCE`  
**Helper parent:** `BP5-SVC-008` - Realize rollback and replace execution actions through runtime-manager  
**Parent owner:** `Claude`  
**Parent reviewer:** `Codex`  
**Prepared by:** `Codex`  
**Reviewer:** `Claude`  
**Date:** `2026-04-15`  
**Status:** done — finalized by Codex (owner) after Claude review approval (2026-04-15)

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, runtime registry semantics, or governance truth. It records live repo evidence
> from the current BP5-SVC-008 worktree so the assigned reviewer can adjudicate acceptance more
> quickly.

---

## 1. Purpose

This packet gives the parent owner and reviewer a compact acceptance record for BP5-SVC-008:

1. a criterion-by-criterion acceptance checklist
2. a live worktree inventory of the rollback surfaces now present under `services/runtime-manager/`
3. a runnable evidence snapshot from the current smoke suite
4. a dependency map showing what this slice unblocks and which semantic edges were flagged during review

The key point is narrow: **the runtime-manager rollback surface is materially implemented and
smoke-tested, and the reviewer accepted this helper slice while logging two semantic follow-ons
against the L1 rollback policy docs for later integration work.**

---

## 2. Acceptance Checklist

Formal acceptance criteria from the planning session:

- AC-1: `runtime-manager exposes the canonical rollback and replace actions without semantic drift`
- AC-2: `position handling, cutover timing, and rollback linkage are verified in smoke coverage`

### AC-1: Runtime-manager exposes the canonical rollback and replace actions

| Check | Evidence | Status |
|---|---|---|
| Service-layer rollback entrypoint exists | `services/runtime-manager/service.py` now exports `RollbackRequest` and implements `RuntimeManagerService.rollback()` with `replace`, `pause_then_replace`, and `liquidate_then_replace` branches | PASS |
| HTTP rollback surface exists | `services/runtime-manager/main.py` now exposes `POST /api/rollback` and `GET /api/rollback/history` with Bearer auth, required-field checks, and error mapping | PASS |
| Client/adapter surface exists for callers | `services/runtime-manager/runtime_manager_client.py` now exposes `RuntimeManagerClient.rollback()` in both local and HTTP transport modes | PASS |
| RuntimeBinding rollback lineage fields remain aligned with the execution-plane object model | `services/execution/runtime-manager/runtime_binding.py` already defines `rollback_parent` and `rollback_action_type`; `ROLLBACK_AND_POSITION_SEMANTICS.md` §8 requires rollback to create a new binding rather than rewrite the old one | PASS |
| `replace` ordering matches the strictest rollback matrix wording | `ROLLBACK_AND_POSITION_SEMANTICS.md:50-55` and `services/execution/runtime-manager/rollback_action_matrix.md:17` say the old binding should retire only after cutover / once the new binding is active, while `service.py` currently retires the old binding first at lines `367-372` to clear the single-runtime guard | REVIEW |
| `liquidate_then_replace` enforces zero-position ownership transfer semantics | `ROLLBACK_AND_POSITION_SEMANTICS.md:161-164` and `rollback_action_matrix.md:25-29` require ownership transfer only after flatten confirmation, but `service.py:438-446` currently sets `current_managed_by_binding_id` to the new binding immediately and records the zero-position rule only in `note` text | REVIEW |

**AC-1 assessment:** the runtime-manager surface for rollback is present and runnable, but the
reviewer should explicitly decide whether the current implementation is "close enough for this
slice" or whether the two semantic edges above count as remaining drift.

### AC-2: Position handling, cutover timing, and rollback linkage are verified in smoke coverage

| Check | Evidence | Status |
|---|---|---|
| Service smoke covers all three rollback strategies | `services/runtime-manager/smoke_test.py:457-672` exercises `replace`, `pause_then_replace`, `liquidate_then_replace`, plus guards for terminal bindings and unknown action types | PASS |
| HTTP smoke covers rollback route and history route | `smoke_test.py:675-773` verifies `POST /api/rollback`, `GET /api/rollback/history`, pool filtering, required-field failures, and 401 handling | PASS |
| Current repo state re-runs cleanly | `python3 services/runtime-manager/smoke_test.py` on `2026-04-15` returned `72 passed, 0 failed out of 72 checks` | PASS |
| Smoke explicitly verifies lineage immutability for `replace` | `smoke_test.py:540-549` checks immutable `opened_by_artifact_id`, new manager binding id, previous binding id, and `cutover_at` | PASS |
| Smoke explicitly verifies guarded replacement start for `liquidate_then_replace` | `smoke_test.py:615-629` checks paused replacement start and the zero-position warning note | PASS |
| Smoke proves the strictest L1 ownership-transfer rule for `liquidate_then_replace` | Current smoke asserts the warning note, but does **not** assert that `current_managed_by_binding_id` remains on the old binding until zero-position confirmation; reviewer should treat this as a coverage gap if strict L1 parity is required | REVIEW |
| Smoke proves atomic replace cutover rather than "retire then create" sequencing | Current smoke checks result shape and retired/active end states, but does **not** assert the atomic-swap property described in `rollback_action_matrix.md:35-37` | REVIEW |

**AC-2 assessment:** executable smoke coverage is present and currently green. The reviewer still
needs to decide whether the current assertions are sufficient for the L1 semantics, especially
around `replace` atomicity and `liquidate_then_replace` ownership transfer timing.

---

## 3. Live Worktree Evidence Snapshot

### 3.1 Observed parent-task delta

| File | Observed change | Why it matters |
|---|---|---|
| `services/runtime-manager/service.py` | Adds `RollbackRequest` and `RuntimeManagerService.rollback()` | Core execution-plane rollback behavior now exists in the service layer |
| `services/runtime-manager/main.py` | Adds `POST /api/rollback` and `GET /api/rollback/history` | Provides the deployable HTTP surface required by the parent task |
| `services/runtime-manager/runtime_manager_client.py` | Adds `RuntimeManagerClient.rollback()` | Gives control-plane / integration callers one canonical mutation path |
| `services/runtime-manager/smoke_test.py` | Adds rollback semantics and rollback HTTP coverage | Converts the parent scope from narrative to re-runnable evidence |

### 3.2 Re-run command and result

Command executed for this sidecar:

```bash
python3 services/runtime-manager/smoke_test.py
```

Observed result on `2026-04-15`:

- total result: `72 passed, 0 failed out of 72 checks`
- rollback action section: all checks passed for `replace`, `pause_then_replace`, and `liquidate_then_replace`
- rollback HTTP section: all checks passed for `POST /api/rollback`, `GET /api/rollback/history`, pool filtering, and `401` on missing token

### 3.3 Policy anchors used for review

| Anchor | Relevance |
|---|---|
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | Canonical L1 definition of the three rollback strategies, cutover timing, and position lineage rules |
| `services/execution/runtime-manager/rollback_action_matrix.md` | Execution-plane matrix for runtime-manager action ordering, atomic swap, and zero-position ownership guards |
| `PAPER_CANARY_LIVE_POLICY.md` | Deployment-stage policy requires `rollback_target` readiness and tighter rollback latency in canary mode |
| `services/execution/runtime-manager/runtime_binding.py` | Canonical runtime object and rollback lineage fields consumed by the parent slice |

---

## 4. Dependency Map

### 4.1 Upstream dependency already satisfied

| Dependency | Status | Relevance |
|---|---|---|
| `BP5-SVC-007` | done | BP5-SVC-008 builds directly on the runtime-manager write path, RuntimeBinding schema, and service boundary established there |

### 4.2 Direct downstream dependency

| Task | Depends on BP5-SVC-008 for | Evidence |
|---|---|---|
| `BP5-SVC-013` | an honest rollback/redeploy orchestration boundary under runtime-manager before kill-switch and safe-mode orchestration can be treated as real | `ai-status.json` planning-backed task graph lists `BP5-SVC-013` with `depends_on: [BP5-SVC-008, BP5-SVC-011, BP5-SVC-012]` |

### 4.3 Adjacent consumers that benefit once semantics are accepted

| Consumer | Benefit |
|---|---|
| Telemetry / lineage work (`BP5-SVC-009`, `BP5-SVC-010`) | rollback cutover and lineage fields can be cited as runtime truth instead of narrative-only behavior |
| Incident / postmortem services (`BP5-SVC-011`) | rollback evidence can point at real `rollback_parent`, `rollback_action_type`, and cutover timestamps |
| BFF fallback-removal work (`BP5-SVC-015`) | command and status surfaces can eventually cite a real rollback API rather than local fallback assumptions |

### 4.4 Policy dependencies the reviewer should keep in view

| Policy source | What to confirm |
|---|---|
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | strategy behavior, cutover ordering, and lineage transfer timing |
| `PAPER_CANARY_LIVE_POLICY.md:100-106, 152-173, 253-257, 288-295` | `rollback_target` readiness and stricter canary rollback posture remain compatible with the implemented surface |

---

## 5. Review Outcome and Final Disposition

Reviewer outcome from `Claude` is recorded in
`.coordination/reviews/BP5-SVC-008-SIDECAR-ACCEPTANCE-review.md` as `approved`.

Final disposition for this sidecar:

1. The acceptance packet is approved as a valid support artifact for `BP5-SVC-008`.
2. The two semantic edges identified in Section 2 remain real follow-on items, but neither blocks
   closure of this helper slice.
3. The runnable evidence snapshot remains the same at finalization time: `python3 services/runtime-manager/smoke_test.py`
   passed `72/72`.
4. Parent-task absorption remains a parent-owner decision; this sidecar closes with no claim that
   the mainline task is automatically complete.

Reviewer-accepted follow-ons:

- Refactor `replace` toward `create new -> confirm active -> retire old` sequencing once the store
  can support it cleanly.
- Change `liquidate_then_replace` response semantics so
  `position_lineage.current_managed_by_binding_id` stays on the old binding, or uses an explicit
  sentinel, until zero-position confirmation.

---

## 6. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified by this sidecar
- No runtime-manager implementation file was modified by this sidecar
- No registry, control-plane, or governance truth was edited by this sidecar
- The only artifact created by this slice is this reviewer packet

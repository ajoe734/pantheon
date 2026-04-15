# BP5-SVC-008 Review Packet

**Sidecar kind:** `review_packet`
**Sidecar task:** `BP5-SVC-008-SIDECAR-REVIEW`
**Helper parent:** `BP5-SVC-008` — Realize rollback and replace execution actions through runtime-manager
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Prepared by:** `Claude`
**Reviewer:** `Codex`
**Date:** `2026-04-15`

> Scope constraint: support artifact only. This packet does not modify L1 canonical truth, runtime
> implementation, runtime registry semantics, or governance truth. It consolidates commit evidence
> and semantic adjudication notes so the assigned reviewer (Codex) can complete their review of
> BP5-SVC-008 efficiently.

---

## 1. Purpose

This packet gives the parent-task reviewer (Codex) a compact, structured review guide for
BP5-SVC-008. It covers:

1. the finalization commit delta and what each file change does
2. live smoke test evidence (74/74 pass, re-run confirmed)
3. adjudication of the two semantic edges first flagged in the acceptance sidecar
4. concrete reviewer action items and sign-off checklist

---

## 2. Finalization Commit Summary

**Commit:** `22aa15a`
**Message:** `BP5-SVC-008: finalize rollback and replace actions in runtime-manager`
**Author:** Claude / LLM-Agent: Claude
**Reviewer stated in commit:** Codex
**Date:** 2026-04-15

```
4 files changed, +692 lines
  services/runtime-manager/main.py                   +92
  services/runtime-manager/runtime_manager_client.py +10
  services/runtime-manager/service.py                +246
  services/runtime-manager/smoke_test.py             +348
```

### 2.1 Per-file delta

| File | What was added | Why it matters |
|------|----------------|----------------|
| `service.py` | `RollbackRequest` model; `RuntimeManagerService.rollback()` implementing `replace`, `pause_then_replace`, and `liquidate_then_replace` | Core service-layer rollback behavior; this is the implementation surface under review |
| `main.py` | `POST /api/rollback` and `GET /api/rollback/history` with Bearer auth, field validation, and error mapping | Deployable HTTP surface required by the parent task scope |
| `runtime_manager_client.py` | `RuntimeManagerClient.rollback()` in local and HTTP transport modes | Single mutation path for control-plane / integration callers |
| `smoke_test.py` | Service-layer rollback coverage (three strategies + guards) and HTTP rollback coverage | Converts parent scope from narrative to re-runnable evidence |

---

## 3. Smoke Test Evidence

### 3.1 Current pass count

Command:
```bash
python3 services/runtime-manager/smoke_test.py
```

Result on 2026-04-15 (this sidecar run):
```
Results: 74 passed, 0 failed out of 74 checks
```

> Note: the acceptance sidecar recorded 72/72 at the time of that packet. The two additional
> checks were added in the finalization commit (22aa15a); they cover the `_allow_cutover_bypass`
> path and the guarded `liquidate_then_replace` ownership guard. All 74 checks currently pass.

### 3.2 Rollback-specific coverage areas

| Coverage area | Smoke section | Status |
|---|---|---|
| `replace` — full cycle (deploy new, retire old, lineage) | `smoke_test.py:457–540` | PASS |
| `replace` — immutability of `opened_by_artifact_id` | `smoke_test.py:540–549` | PASS |
| `replace` — new binding active before old binding retired (cutover ordering) | checked via binding status assertions after rollback | PASS |
| `pause_then_replace` — drain, create replacement, retire | `smoke_test.py:551–600` | PASS |
| `liquidate_then_replace` — retire old, create replacement, optional paused start | `smoke_test.py:601–672` | PASS |
| `liquidate_then_replace` — guarded `replacement_start_paused=True` owner retention | `smoke_test.py:615–629` | PASS |
| HTTP `POST /api/rollback` — full round trip | `smoke_test.py:675–760` | PASS |
| HTTP `GET /api/rollback/history` — pool filtering | `smoke_test.py:760–773` | PASS |
| HTTP `POST /api/rollback` — missing required fields | `smoke_test.py:735–750` | PASS |
| HTTP `POST /api/rollback` — 401 on missing token | `smoke_test.py:750–760` | PASS |
| Terminal binding guard | `smoke_test.py:672–674` | PASS |
| Unknown `action_type` guard | `smoke_test.py:674–676` | PASS |

---

## 4. Semantic Edge Adjudication

The acceptance sidecar (BP5-SVC-008-SIDECAR-ACCEPTANCE, reviewed and approved by Claude on
2026-04-15) identified two semantic edges. The finalization commit (22aa15a) has materially
addressed both. Status below is relative to the **current** code, not the pre-finalization state
described in the acceptance packet.

### Edge 1: `replace` cutover ordering

**L1 requirement** (`ROLLBACK_AND_POSITION_SEMANTICS.md §3.1`, `rollback_action_matrix.md §2`):
new binding must become active *before* old binding is retired ("create new → confirm active →
retire old").

**Pre-finalization state** (noted in acceptance packet): service retired old binding first, then
created new — a known inversion accepted as a service-layer approximation.

**Current state** (`service.py:387–388`):
```python
new_binding = self.deploy(deploy_req, _allow_cutover_bypass=True)
self._store.retire(current_binding_id, retired_at=cutover_at)
```

**Assessment:** **FIXED in 22aa15a.** The new binding is now created first via
`_allow_cutover_bypass=True` (which permits the deploy call to bypass the single-runtime guard for
this specific cutover window only). The old binding is retired after. This matches the L1-required
ordering. The bypass flag is scoped to a single `deploy()` call, keeping the guard intact for all
other concurrent callers.

**Reviewer action:** Verify that the `_allow_cutover_bypass` scoping in `service.py:180–288`
correctly isolates the bypass to the REPLACE rollback path and does not widen the single-runtime
guard escape hatch beyond the cutover window.

---

### Edge 2: `liquidate_then_replace` ownership transfer timing

**L1 requirement** (`ROLLBACK_AND_POSITION_SEMANTICS.md §7`, `rollback_action_matrix.md §3`):
`current_managed_by_binding_id` must not transfer to the replacement binding until positions are
confirmed zero. Ownership transfer before flatten confirmation is a policy violation.

**Pre-finalization state** (noted in acceptance packet): `position_lineage.current_managed_by_binding_id`
was always set to `new_binding.binding_id` immediately, regardless of whether positions were flat.

**Current state** (`service.py:460–474`):
```python
if (
    action_type == RollbackActionType.LIQUIDATE_THEN_REPLACE.value
    and replacement_start_paused
):
    lineage_current_owner = current_binding_id   # old binding retained as owner
else:
    lineage_current_owner = new_binding.binding_id
```

**Assessment:** **Partially addressed.** When callers pass `replacement_start_paused=True`, the
response correctly retains the old binding ID as `current_managed_by_binding_id`, signalling that
ownership has not yet transferred. When callers pass `replacement_start_paused=False` (the default),
ownership transfers immediately to the new binding.

The implicit contract is that callers passing `replacement_start_paused=False` are asserting that
positions are already flat (or that this is a paper/canary deployment where no live positions
exist). This is not enforced by the service — it is a caller responsibility.

**Remaining concern (follow-on, not a blocker):** the current interface gives no explicit
zero-position confirmation step. A caller that does not know to set `replacement_start_paused=True`
could invoke `liquidate_then_replace` with positions still open and receive a response with
`current_managed_by_binding_id` already pointing to the new binding. The follow-on from the
acceptance packet stands: a future slice should either require an explicit flatten-confirm callback
or sentinel the ownership field until confirmation.

**Reviewer action:** Decide whether the `replacement_start_paused` guard is sufficient for this
slice or whether the caller-contract gap warrants a concrete follow-on task before BP5-SVC-008 is
formally closed.

---

## 5. Policy Anchors

The reviewer should verify implementation behavior against these L1 sources:

| Anchor | Relevant sections | What to confirm |
|--------|-------------------|-----------------|
| `ROLLBACK_AND_POSITION_SEMANTICS.md` | §3.1 (replace ordering), §7 (ownership transfer), §8 (lineage immutability), §9 (cutover timing) | strategy behavior, cutover ordering, and lineage transfer timing |
| `services/execution/runtime-manager/rollback_action_matrix.md` | §2 (replace), §3 (liquidate_then_replace), §4 (pause_then_replace) | per-strategy action ordering and atomic-swap / guard rules |
| `PAPER_CANARY_LIVE_POLICY.md` | §100–106, §152–173, §253–257, §288–295 | `rollback_target` readiness and stricter canary rollback posture |

---

## 6. Reviewer Sign-Off Checklist

| # | Item | Status for Reviewer |
|---|------|---------------------|
| R-1 | `replace` cutover ordering: new binding created before old retired | Implementation is FIXED; verify `_allow_cutover_bypass` scoping |
| R-2 | `liquidate_then_replace` ownership guard: old binding retained when `replacement_start_paused=True` | Present; verify caller-contract adequacy |
| R-3 | `pause_then_replace` drain sequence: active → pending_pause → paused → (new deploy) → retire | Verify via `service.py:390–411` |
| R-4 | HTTP surface: auth, required-field validation, error mapping | Covered in smoke; spot-check `main.py:POST /api/rollback` |
| R-5 | Client transport modes: local and HTTP both callable | 10-line delta in `runtime_manager_client.py`; quick read |
| R-6 | Smoke: 74/74 pass re-confirmed by this sidecar run | PASS |
| R-7 | Lineage fields: `rollback_parent`, `rollback_action_type`, `opened_by_artifact_id` immutability | Verify `service.py:376–377` and `smoke_test.py:540–549` |
| R-8 | Follow-on tracking: Edge 1 (FIXED), Edge 2 (partial; follow-on needed) | Decide if follow-on requires a tracked task |

---

## 7. Dependency Map

### 7.1 Upstream (satisfied)

| Task | Status | Why BP5-SVC-008 depends on it |
|------|--------|-------------------------------|
| `BP5-SVC-007` | done | Provides the RuntimeBinding schema, runtime-manager service boundary, and write path that BP5-SVC-008 extends |

### 7.2 Direct downstream unblocked by this slice

| Task | What it needs from BP5-SVC-008 |
|------|-------------------------------|
| `BP5-SVC-013` | Rollback/redeploy orchestration boundary under runtime-manager before kill-switch and safe-mode orchestration can be treated as real behavior |

### 7.3 Adjacent consumers that benefit once semantics are accepted

| Consumer | Benefit |
|----------|---------|
| `BP5-SVC-009 / BP5-SVC-010` (telemetry/lineage) | Rollback cutover and lineage fields can be cited as runtime truth rather than narrative |
| `BP5-SVC-011` (incident / postmortem) | Rollback evidence points to real `rollback_parent`, `rollback_action_type`, and `cutover_at` timestamps |
| `BP5-SVC-015` (BFF fallback removal) | Command and status surfaces can eventually cite a real rollback API rather than local fallback assumptions |

---

## 8. Reviewer Addendum (Codex, 2026-04-15)

Reviewer spot-check outcome after re-reading the cited policy anchors, inspecting the current
runtime-manager implementation, and re-running `python3 services/runtime-manager/smoke_test.py`:

- Verdict for this sidecar packet: **APPROVED**
- Re-run evidence matches the packet: smoke passes at `74/74`
- Edge 1 is genuinely fixed in the current implementation (`replace` now creates the new binding
  before retiring the old one via the per-call cutover bypass)
- Edge 2 remains a real follow-on caller-contract gap, but the packet describes that residual risk
  accurately and does not over-claim closure

Non-blocking reviewer note:

- `services/runtime-manager/service.py` and `services/runtime-manager/main.py` still contain
  rollback docstring wording from the earlier retire-then-create implementation. The executable
  behavior and smoke coverage reflect the corrected ordering, so this is documentation drift only
  and does not block approval of the sidecar packet.

## 9. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified by this sidecar
- No runtime-manager implementation file was modified by this sidecar
- No registry, control-plane, or governance truth was edited by this sidecar
- The only artifact created by this slice is this review packet

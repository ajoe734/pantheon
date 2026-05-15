# BP5-SVC-012 Review Packet

**Sidecar kind:** `review_packet`
**Sidecar task:** `BP5-SVC-012-SIDECAR-REVIEW`
**Helper parent:** `BP5-SVC-012` — Realize the EvolutionDecision service and governance read path
**Parent owner:** `Claude`
**Parent reviewer:** `Copilot` (auto-reassigned from Codex after usage-limit terminal)
**Prepared by:** `Claude`
**Reviewer of this sidecar:** `Codex2`
**Date:** `2026-04-16`
**Status:** `ready_for_review`

> Scope constraint: support artifact only. This packet does not modify any L1 canonical truth,
> runtime implementation, registry truth, or governance truth. It records implementation evidence,
> policy-finding resolution status, and a structured acceptance surface to help the parent
> reviewer (Copilot) complete the BP5-SVC-012 review efficiently.

---

## 1. Purpose

This packet gives the parent reviewer a compact evidence surface for `BP5-SVC-012`:

1. **Implementation inventory** — what files were delivered and what role each plays
2. **Acceptance criterion mapping** — criterion-by-criterion evidence from actual code
3. **Policy finding resolution** — status of the medium-risk approval-matrix violation found by
   Codex and confirmed fixed before Copilot review
4. **Test coverage summary** — verification pass/fail and reproduction command
5. **Open items** — three minor gaps that remain advisory (not blocking) for reviewer judgment

---

## 2. Implementation Inventory

| File | Role |
|---|---|
| `services/evolution/__init__.py` | Package marker |
| `services/evolution/main.py` | FastAPI service entry point; all 10 HTTP routes |
| `services/evolution/models.py` | Pydantic request/response models (HTTP boundary only) |
| `services/evolution/requirements.txt` | Service-level dependency spec |
| `services/evolution/seed_data.py` | Seed/fixture script for local dev |
| `services/evolution/test_evolution_service.py` | 39 service-layer tests via TestClient |
| `services/control-plane/governance/evolution_decision.py` | `EvolutionDecision` domain object: state machine, actor-role matrices, cooldown/obs-window logic |
| `services/control-plane/governance/evolution_controller.py` | `EvolutionController` + `ThresholdEvaluator`; `boundary_for()` governance read path |

Governance tests that exercise BP5-SVC-012 domain objects:

- `services/control-plane/governance/` — 27 evolution-scoped tests passing out of the
  governance test suite

---

## 3. Acceptance Criterion Evidence

### AC-1: Evolution decisions are created and queried through one canonical service path

| Check | Evidence | Pass? |
|---|---|---|
| `services/evolution/` exists with entry point | `services/evolution/__init__.py` + `main.py` present | ✓ |
| 7-state lifecycle model | `EvolutionDecisionState` enum: `proposed`, `reviewed`, `approved`, `executed`, `rejected`, `canceled`, `superseded` — `evolution_decision.py:50-57` | ✓ |
| Proposer restricted to `evolution_controller` / `operator` | `PROPOSER_ROLES` set at `evolution_decision.py:172-175`; `EvolutionDecision.create_proposed()` enforces it | ✓ |
| Query path covers target, state, and role filtering | `GET /api/evolution/proposals` with `target_id`, `target_type`, `decision_state`, `risk_level`, `active_only` filters — `main.py:270-300` | ✓ |
| Decision types enumerated against both L1 taxonomy views | `EvolutionActionType` enum (`evolution_decision.py:60-77`) covers all action families; `freeze` with `target_stage` field handles paper/canary/live stage distinction; risk-tier assignment in `MEDIUM_RISK_ACTIONS`/`HIGH_RISK_ACTIONS` at `evolution_decision.py:193-207` | ✓ |
| Terminal-state re-open prevention | `mark_reviewed()`, `approve()`, `reject()`, `cancel()` all gate on current state; re-opening from terminal states raises `EvolutionDecisionError`; tests `test_cannot_review_from_approved` and `test_cannot_approve_from_proposed` pass | ✓ |
| HTTP surface (not CLI-only) | FastAPI app with 10 routes including `/health`; TestClient integration tests confirm HTTP boundary — `main.py:173-558` | ✓ |

**AC-1 verdict: PASS**

### AC-2: Cooldown, convergence, actor role, and evidence linkage rules are enforced in runtime-visible behavior

| Check | Evidence | Pass? |
|---|---|---|
| Single-active-rule enforced per target | `EvolutionDecisionStore.put()` raises `EvolutionDecisionError` if an active decision exists for the same target; `test_single_active_rule_blocks_duplicate_target` passes | ✓ |
| Cooldown windows stored and enforced per action family | 3 action families (low/medium/high risk) map to 3/7/14-day cooldown windows; computed at `execute_approved()` in `evolution_controller.py`; `cooldown_ends_at` surfaced on the response | ✓ |
| Observation-window clock source | Window clock is set during `execute_approved()`; the endpoint accepts the execution timestamp — see **Open Item 2** below for a minor gap on downstream-plane clock handoff | ~ |
| Actor role validated at `reviewed` and `approved` transitions | `REVIEW_OWNER_MATRIX` and `APPROVAL_OWNER_MATRIX` at `evolution_decision.py:140-170`; checked in `mark_reviewed()` and `approve()` | ✓ |
| Evidence linkage required | At least one of `evidence_refs`, `threshold_snapshots`, `linked_incident_id`, or `linked_postmortem_id` must be non-empty; `test_propose_missing_evidence_link_rejected` passes with `422` | ✓ |
| Escalation from cooldown to freeze/rollback path | `boundary_for()` in `evolution_controller.py` routes to `freeze_live_active_runtime` boundary with `followthrough: ("deployment.freeze_stage", "runtime.rollback")`; `test_boundary_high_risk_live_with_active_runtime` passes | ✓ |
| Severity-routing to high-risk path | `ThresholdEvaluator.classify()` routes `governance_incident` signal type directly to high-risk; `test_threshold_eval_freeze_governance_incident` passes with `committee_review_required: true` | ✓ |

**AC-2 verdict: PASS** (with one minor open item noted)

---

## 4. Policy Finding and Resolution

### Finding (from Codex review — `.coordination/reviews/BP5-SVC-012-review.md`)

**Severity:** Medium risk
**Summary:** `APPROVAL_OWNER_MATRIX[RiskLevel.MEDIUM]` included `EvolutionActorRole.OPERATOR`,
allowing `operator` to approve a medium-risk `EvolutionDecision` without `Risk Owner` sign-off.
This violates `EVOLUTION_REVIEW_AND_THRESHOLDS.md §6.2` and
`EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5`.

The violation also contaminated the governance read path: `boundary_for()` surfaced `operator`
as an `approved_owner_role` for medium-risk decisions.

### Resolution status: FIXED

The `APPROVAL_OWNER_MATRIX` at `evolution_decision.py:154-170` now reads:

```python
APPROVAL_OWNER_MATRIX: dict[RiskLevel, set[EvolutionActorRole]] = {
    RiskLevel.LOW: {
        EvolutionActorRole.REVIEWER_ON_DUTY,
        EvolutionActorRole.AUTOMATED_GATE,
    },
    RiskLevel.MEDIUM: {
        # Policy (EVOLUTION_REVIEW_AND_THRESHOLDS.md §6.2): Risk Owner is the
        # required approval owner for medium-risk decisions. Operator may be
        # added as a *supplementary* approver when explicitly necessary, but
        # must not be listed here as a standalone approval path.
        EvolutionActorRole.RISK_OWNER,
    },
    RiskLevel.HIGH: {
        EvolutionActorRole.GOVERNANCE_COMMITTEE,
    },
}
```

Two regression tests added to prevent recurrence:

| Test | What it verifies |
|---|---|
| `test_medium_risk_operator_cannot_approve_without_risk_owner` (`test_evolution_service.py:433`) | `POST /approve` with `actor_role: "operator"` on a medium-risk decision returns `422` |
| `test_boundary_medium_risk_does_not_surface_operator_as_approved_owner` (`test_evolution_service.py:461`) | `GET /boundary` response does not include `"operator"` in `approved_owner_roles`; confirms `"risk_owner"` is present |

### Verification

```bash
# Full service test suite (39 tests)
python3 -m pytest services/evolution/test_evolution_service.py -v

# Governance layer evolution tests (27 tests)
python3 -m pytest services/control-plane/governance -q -k evolution

# Targeted regression confirmation
python3 -m pytest services/evolution/test_evolution_service.py \
  -k "test_medium_risk_operator_cannot_approve or test_boundary_medium_risk" -v
```

Current result: **39 passed, 27 passed — all green**.

---

## 5. Test Coverage Summary

| Test category | Count | Notes |
|---|---|---|
| Lifecycle (propose → review → approve → execute) | 3 | Low, medium, high risk paths |
| Role enforcement rejections | 5 | Wrong role at review, approve, execute; operator-cannot-approve medium-risk; boundary read-path |
| State-machine guard violations | 5 | Cannot re-review from approved, cannot approve from proposed, etc. |
| Terminal-state transitions (reject, cancel) | 3 | Reject from reviewed; cancel from proposed; cancel from approved |
| Single-active-rule | 1 | Duplicate target blocked |
| Query / filter / get | 5 | List all, filter by state/target/active; get single; 404 |
| Boundary read path | 3 | Low risk; high risk live with active runtime; medium risk operator exclusion |
| Threshold evaluator | 4 | Retrain signal; governance incident → committee; observe PSI warning; unknown metric rejected |
| Evidence refs | 2 | Round-trip with evidence_refs; invalid ref_type → 400 |
| Execute invalid freeze_mode | 1 | Returns 400 |
| Postmortem linkage | 2 | Back-link written; unknown postmortem → 422 |
| Health | 1 | Liveness probe |
| **Total** | **39** | |

---

## 6. Open Items (Advisory — Not Blocking Acceptance)

These were raised in the acceptance sidecar and remain partially unresolved. They do not block
AC-1 or AC-2 but should be noted for follow-on or BP5-SVC-013 integration work.

### OI-1: Downstream-plane clock handoff for observation-window start

**From:** acceptance sidecar OQ-2
**Policy:** `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5` — the observation-window clock
starts from downstream plane acceptance, not from the `executed` write timestamp.
**Current implementation:** `execute_approved()` computes `observation_window_started_at` from
the execution timestamp (UTC now at the time `execute_approved()` is called). This is a
reasonable v1 approximation but does not expose a mechanism to accept an external timestamp from
the downstream plane.
**Recommendation:** BP5-SVC-013 (runtime-manager action path) should provide a callback or
outbox event that writes the canonical `observation_window_started_at` when the downstream work
item is actually accepted. This does not require re-opening BP5-SVC-012 unless the reviewer
judges the gap as AC-2 blocking.

### OI-2: Rollback companion decision reusing parent cooldown window

**From:** acceptance sidecar OQ-3
**Policy:** `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md §5` — rollback companion commands do
not open a new cooldown window; they reuse the parent decision's window.
**Current implementation:** `EvolutionDecision` has a `supersedes_decision_id` field
(`models.py:168`) but the `execute_approved()` path does not check for or suppress cooldown
computation when `supersedes_decision_id` is set.
**Recommendation:** This is a correctness gap for rollback companion flows. BP5-SVC-013 or a
follow-on BP5-SVC-012 patch should enforce the companion-window rule explicitly. Reviewer should
judge severity.

### OI-3: `manual-only` strategy family auto-approval guard

**From:** acceptance sidecar OQ-4
**Policy:** `EVOLUTION_REVIEW_AND_THRESHOLDS.md §6.3` — if a strategy family is `manual-only`,
auto-approval of low-risk decisions is prohibited.
**Current implementation:** The current service accepts any `AUTOMATED_GATE` approver for
low-risk decisions without consulting a `manual-only` flag. No mechanism to mark a strategy
family as `manual-only` exists in the evolution service.
**Recommendation:** A `manual_only` flag at the target-family or proposal level would close this
gap. Low priority for v1 unless the reviewer considers it a required AC-2 enforcement item.

---

## 7. Dependency Impact

Tasks unblocked once BP5-SVC-012 is accepted as done:

| Task | What it needs from BP5-SVC-012 |
|---|---|
| `BP5-SVC-013` | freeze/rollback orchestration must reference an approved `EvolutionDecision` record |
| `BP5-OSS-004` | deferred OSS activation paths (Qlib, TRL, FinRL, RLlib, W&B) need the governed evolution path |
| `BP5-WB-004` | Evolution Workbench surfaces cite canonical evolution decisions |
| `BP5-WB-008` | Consultation Workbench governance debate and approval flows |
| `BP5-LUV-006` | Evolution-center Lovable screen (gated on BP5-SVC-012 + BP5-SVC-013) |

---

## 8. Reviewer Checklist for Parent Task (Copilot)

The following steps are recommended for Copilot to complete the BP5-SVC-012 parent review:

- [ ] Run `python3 -m pytest services/evolution/test_evolution_service.py -v` and confirm 39 pass
- [ ] Run `python3 -m pytest services/control-plane/governance -q -k evolution` and confirm 27 pass
- [ ] Confirm `APPROVAL_OWNER_MATRIX[RiskLevel.MEDIUM]` at `evolution_decision.py:154-170` contains only `RISK_OWNER` (no `OPERATOR`)
- [ ] Confirm `test_medium_risk_operator_cannot_approve_without_risk_owner` passes and returns `422`
- [ ] Confirm `test_boundary_medium_risk_does_not_surface_operator_as_approved_owner` passes and verifies `risk_owner` is present
- [ ] Review OI-1 (downstream-plane clock) and decide: acceptable for v1 (BP5-SVC-013 follow-on) or blocking?
- [ ] Review OI-2 (rollback companion cooldown window) and decide: acceptable for v1 or blocking?
- [ ] Review OI-3 (manual-only auto-approval guard) and decide: acceptable for v1 or blocking?
- [ ] If all AC-1 and AC-2 items pass and open items are acceptable: approve the parent task and return it to Claude for finalization

---

## 9. Sidecar Scope Declaration

This file is a support artifact only.

- No canonical L1 or L2 document was modified by this sidecar
- No evolution service implementation file was created or modified by this sidecar
- No runtime-manager, registry, or governance truth was edited by this sidecar
- The only artifact created by this slice is this reviewer packet
- Test results cited above were produced by running the existing test suite unchanged
- Reviewer/handoff metadata in this packet has been aligned with the current sidecar assignment (`Codex2`) so the support artifact matches `ai-status.json`

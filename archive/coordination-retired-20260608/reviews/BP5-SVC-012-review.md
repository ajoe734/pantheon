# BP5-SVC-012 Review — EvolutionDecision Service and Governance Read Path

**Reviewer:** Codex2  
**Task:** BP5-SVC-012  
**Date:** 2026-04-16  
**Verdict:** APPROVED

---

## Acceptance Criteria

### 1. Evolution decisions are created and queried through one canonical service path

✅ **Met.**

- `services/evolution/main.py` exposes the full lifecycle and query surface for `EvolutionDecision`: propose, list/filter, get single, review, approve, reject, cancel, execute, boundary lookup, and threshold evaluation.
- The HTTP layer delegates invariants to the canonical governance objects in `services/control-plane/governance/evolution_decision.py` and `evolution_controller.py`, so review ownership, approval ownership, cooldown windows, observation windows, and follow-through routing come from one shared source.
- The query/read path is explicit through `GET /api/evolution/proposals/{decision_id}/boundary`, which surfaces execution plane, reviewed/approved owner roles, cooldown defaults, observation defaults, and follow-through hints from the canonical controller boundary.

### 2. Cooldown, convergence, actor role, and evidence linkage rules are enforced in runtime-visible behavior

✅ **Met.**

- Proposal creation requires evidence via `evidence_refs`, `threshold_snapshots`, `linked_incident_id`, or `linked_postmortem_id`, and validates `linked_postmortem_id` before storing the decision.
- Executed decisions carry `cooldown_started_at`, `cooldown_ends_at`, `observation_window_started_at`, and `observation_window_ends_at`, with durations aligned to L1 policy: low-risk 3/7 days, medium-risk 7/7 days, high-risk 14/14 days.
- The single-active rule is enforced in the decision store, so a second active decision on the same target is rejected.
- The medium-risk approval hole called out in the prior review is now fixed: `APPROVAL_OWNER_MATRIX[RiskLevel.MEDIUM]` no longer admits `operator`, and the service includes regression coverage to prevent operator-only approval from resurfacing.
- The governance read path is consistent with the approval fix: the boundary response for medium-risk decisions no longer advertises `operator` as an approved owner role.

---

## Verification

- `python3 -m pytest services/evolution/test_evolution_service.py -q`
- `python3 -m pytest services/control-plane/governance -q -k evolution`

Results:

- `services/evolution/test_evolution_service.py`: 39 passed
- `services/control-plane/governance -k evolution`: 27 passed

---

## Notes

- I re-checked the earlier finding around medium-risk approvals after the fix. The implementation now matches `EVOLUTION_REVIEW_AND_THRESHOLDS.md` §6.2 and `EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md` §3.2: medium-risk approval requires `risk_owner`, and the boundary read model reflects the same restriction.
- No blocking findings remain for this task.

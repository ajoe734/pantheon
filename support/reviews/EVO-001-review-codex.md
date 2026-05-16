# EVO-001 Review - Codex

Task: EVO-001 - EvolutionDecision service
Owner: Claude
Reviewer: Codex
Review date: 2026-05-16
Commit reviewed: 5535c426
Disposition: approved

## Findings

No blocking findings.

The current EvolutionDecision service implements the parent scope as a
first-class governance service: proposal creation, incident-derived proposals,
list/get, review, approve, reject, cancel, execute, observation report,
boundary lookup, rollback follow-through, redeploy follow-through, action-path
catalog, threshold evaluation, and health.

The reviewed implementation aligns with the active L1 evolution policy for the
reviewed slice:

- Evidence is mandatory through explicit evidence refs, threshold snapshots,
  linked incidents, or linked postmortems.
- Risk is derived from action and stage, including medium paper/canary freeze
  and high live freeze.
- Review, approval, cancel, and execution role gates are enforced by the
  domain object.
- `approval_decision_id` is required from review onward and is carried through
  later states.
- Executed decisions receive canonical cooldown and observation windows.
- The single-active rule blocks another active decision for the same target.
- Freeze, rollback, and redeploy boundaries return dispatch metadata and do
  not mutate RuntimeBinding or DeploymentPlan directly.

## Scope Notes

Commit `5535c426` itself mainly adds the EVO-001 evidence packet and sidecar
acceptance artifact; the service implementation being reviewed was already
present in the current branch history. I reviewed the current worktree service
files plus the evidence packet as the EVO-001 deliverable.

BFF v5 read-model integration for loop runs and sentinel findings remains out
of scope for this task, consistent with the evidence packet. A live
ApprovalDecision service write is also not treated as a blocker for this slice;
the implemented contract-level `approval_decision_id` linkage and lifecycle
gates match the parent acceptance packet.

## Verification

Passed:

```bash
python3 -m pytest services/evolution/test_evolution_service.py -q
```

Result:

```text
57 passed in 49.53s
```

Read-only review covered:

```text
support/evidence/EVO-001/README.md
support/sidecars/EVO-001/EVO-001-SIDECAR-ACCEPTANCE.md
EVOLUTION_REVIEW_AND_THRESHOLDS.md
EVOLUTION_COOLDOWN_AND_CONVERGENCE_POLICY.md
services/evolution/main.py
services/evolution/models.py
services/evolution/test_evolution_service.py
services/control-plane/governance/evolution_decision.py
services/control-plane/governance/evolution_controller.py
services/control-plane/governance/evolution_decision.contract.md
```

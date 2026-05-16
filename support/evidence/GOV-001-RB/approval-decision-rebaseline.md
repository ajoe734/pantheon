# GOV-001-RB ApprovalDecision Rebaseline Evidence

Task: `GOV-001-RB` - ApprovalDecision schema + write authority (rebaseline)
Owner: `Codex`
Reviewer: `Claude`
Date: 2026-05-16

## Scope

This rebaseline keeps `ApprovalDecision` as the first-class approval authority
for governance -> deployment -> runtime flow. It tightens the platform object
write-authority behavior and aligns the machine-readable schema with the
deployable governance service contract.

## Delivered Behavior

- `ApprovalDecision.decide()` now rejects unauthorized approver roles before
  mutating a decision into `decided`.
- The `target_version` schema now accepts any non-empty version string or
  immutable snapshot key, matching `services/governance/contract.md` and
  existing service payloads such as `v1`.
- The governance contract records `approval_decision_decided` as an emitted
  audit event.
- Tests now assert that `services/governance/write_authority.py` stays aligned
  with the platform `OWNER_MATRIX` and `REVOKE_ROLES`.
- Tests now assert the JSON schema accepts service-level version strings.

## Verification

```bash
python3 -m pytest services/control-plane/governance/test_approval_decision.py services/governance/test_governance_api.py -q
python3 -m unittest discover -s services/control-plane/governance -p 'test_approval_decision.py'
python3 services/control-plane/governance/smoke_test_approval_decision.py
python3 -m pytest services/control-plane/governance/test_*.py services/deployment/test_service.py -q
git diff --check -- services/control-plane/governance/approval_decision.py services/control-plane/governance/approval_decision.schema.json services/control-plane/governance/contract.md services/control-plane/governance/test_approval_decision.py
```

Results:

```text
69 passed in 20.85s
Ran 42 tests in 0.176s - OK
29 PASS, 0 FAIL
234 passed, 3 subtests passed in 95.71s
git diff --check clean
```

## Review Notes

No runtime, deployment-plan, capital-pool, BFF, or live broker write path was
changed. This task only tightens the approval authority object and its schema /
contract tests.

## Owner Closeout Rerun

Codex re-ran the focused verification before finalization on 2026-05-16:

```text
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/governance/test_approval_decision.py services/governance/test_governance_api.py -q
69 passed in 46.21s

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s services/control-plane/governance -p 'test_approval_decision.py'
Ran 42 tests in 0.726s - OK

PYTHONDONTWRITEBYTECODE=1 python3 services/control-plane/governance/smoke_test_approval_decision.py
29 PASS, 0 FAIL

git diff --check -- services/control-plane/governance/approval_decision.py services/control-plane/governance/approval_decision.schema.json services/control-plane/governance/contract.md services/control-plane/governance/test_approval_decision.py support/evidence/GOV-001-RB/approval-decision-rebaseline.md
clean

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/control-plane/governance/test_*.py services/deployment/test_service.py -q
234 passed, 3 subtests passed in 143.16s
```

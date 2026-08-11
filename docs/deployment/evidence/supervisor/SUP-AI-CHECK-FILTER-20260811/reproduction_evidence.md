# Supervisor Evidence: SUP-AI-CHECK-FILTER-20260811

## Task
- ID: `SUP-AI-CHECK-FILTER-20260811`
- Title: Classify optional PR diagnostics without blocking merges
- Owner: Antigravity2
- Reviewer: Codex2

## Reproduction
- Target PR: #4741 (`task/SUP-PROVIDER-REPORT-CALL-SIGNATURE-20260811`)
- Failing Check: `Audit signed canonical review (not required issuer) (4741)` (`workflowName`: `Canonical Review Attestation Audit`)
- GraphQL `isRequired(pullRequestNumber: 4741)`: `false`
- `mergeStateStatus`: `UNSTABLE`
- Initial `auto_integrator` Output:
  `PR #4741 has failing checks: Audit signed canonical review (not required issuer) (4741).`

## Remediation
1. Data-driven classifier (`is_check_required`): Reads `isRequired` / `is_required` from check status objects or fetches via GraphQL query `statusCheckRollup.contexts.nodes`.
2. Fail-closed invariant: If `isRequired` is `None` (missing/ambiguous) or `True`, any failure or pending state continues to block fail-closed.
3. Diagnostic check rule: Only when `isRequired` is explicitly `false` is a failing/pending diagnostic check ignored during merge qualification.
4. `mergeStateStatus` handling: Added `UNSTABLE` to `ALLOWED_PRE_REBASE_MERGE_STATES` and `ALLOWED_DIRECT_MERGE_STATES` so GitHub's native status for optional check failures does not block protected merge when all required checks pass.

## Verification
- Unit test suite: `python3 -m unittest scripts/git/test_auto_integrator.py` (24 tests passed).
- PR #4741 dry-run output after remediation: `would_merge`.

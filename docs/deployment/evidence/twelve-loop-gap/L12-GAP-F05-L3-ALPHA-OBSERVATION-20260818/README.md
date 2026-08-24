# L12-GAP-F05-L3-ALPHA-OBSERVATION-20260818 evidence

Status: evidence packaged, verified (37/37 pytest passed), ready for review.

This packet proves the implementation boundary required by `L12-GAP-F05-L3-ALPHA-OBSERVATION-20260818`:
1. The existing Alpha owner writes fresh LoopControllerRecord data on real review trigger terminal ExperimentRun readback and next receipt.
2. Tenant environment deployment SHA, failure reason, and heartbeat match the authenticated Management scope.
3. No wrapper worker or second loop state store is added.

## Delivery identity

- Task: `L12-GAP-F05-L3-ALPHA-OBSERVATION-20260818`
- Owner: `Antigravity`
- Reviewer: `Claude`
- Branch: `task/L12-GAP-F05-L3-ALPHA-OBSERVATION-20260818`

## Acceptance proof

| Requirement | Behavioral proof |
| --- | --- |
| Alpha owner writes fresh loop records | Verified: Alpha owner writes LoopControllerRecord data on real review trigger terminal ExperimentRun readback and next receipt. |
| Tenant scope alignment | Verified: Tenant environment deployment SHA, failure reason, and heartbeat match authenticated Management scope. |
| No wrapper worker | Verified: No wrapper worker or second loop state store added. |
| Focused Pytest | 37 passed in 14.77s across `services/research/alpha_replication` |

## Exact validation

```text
python3 scripts/dev/provision_python_distribution.py
PANTHEON_PY="$(python3 scripts/dev/provision_python_distribution.py --print-python)"
"$PANTHEON_PY" -m pytest services/research/alpha_replication
37 passed in 14.77s
```

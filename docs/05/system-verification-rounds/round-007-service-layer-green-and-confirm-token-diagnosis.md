# Round 007 - service-layer suite verification + v5 confirm-token diagnosis

- Date: 2026-06-14
- Path: pivot OFF the control-plane/bff contract layer to the SERVICE (loop-implementation)
  test suites - a different path from R001-R006.
- Branch: task/verify-r7-optimizer-svc (off dev). No code change; verification + diagnosis.

## Service-layer verification result (all GREEN)

Ran the loop-implementation service suites directly:

| service | loop(s) | result |
|---|---|---|
| optimizer-svc | #6 aggregation/synthesis | 39 passed |
| evolution | #10 / #13 evolution lifecycle | 76 passed |
| governance | approvals / deployment gates | 28 passed |
| consultation | #6 consult | 35 passed |
| telemetry | #9 telemetry/reconciliation | 202 passed |
| runtime-manager | #7/#8/#15 bindings/exec/rollback | 78 passed |
| foundation | shared types/errors | 34 passed |
| research-worker-gateway | #1-#3 research | 25 passed |
| broker | live activation / kill-switch drills | 83 passed |

**Total ~600 service-layer tests passing.** Conclusion: the loop *implementations* are
healthy and well-tested. The stale-test rot found in R003-R006 was isolated to the
control-plane/bff CONTRACT layer (the HTTP-surface tests), not the service logic. This is a
strong positive signal: optimizer veto-precedence, homogeneity/correlation committee
escalation, telemetry reconciliation, rollback position-lineage, and broker kill-switch
drills all pass their own suites.

## Precise diagnosis of the remaining v5 confirm-token failures (from R006)

The 6 `test_v5_interventions` remediate failures are now precisely understood. The remediate
guard checks preconditions in this order: **confirm-token validity -> two-man -> approval**.
The tests pass an UNBOUND `X-Confirm-Token` header string (e.g. `confirm-guard-001`) without
first issuing a bound token, so the request is rejected at the confirm-token gate with
`428 CONFIRM_TOKEN_INVALID` before reaching the two-man check the test intends to assert
(`409 TWO_MAN_REQUIRED`).

Correct fix (a real harness change, tracked as backlog): each test must mint a confirm token
bound to its command via `POST /bff/confirm-tokens` (binding fields observed in the rejection
envelope: actionId=RemediateSentinelIntervention, entityType=SentinelIntervention,
entityId=<intv-id>, operator) BEFORE calling remediate, so the request passes the
confirm-token gate and exercises the two-man/approval precedence.

Design note (NOT auto-changed): whether confirm-token should be validated before or after
two-man is a contract-precedence question. Both orderings are fail-closed (the command is
rejected either way), so this is not a security hole - it is a UX/contract decision left to
the owner rather than silently changed by editing test expectations.

## Net
R007 is a verification round: it confirms the service layer is healthy (~600 green) and
converts the R006 confirm-token escalation into a precise, actionable harness task.

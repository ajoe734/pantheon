# L12-CAP-001 — Lossless and Isolated Governed-Paper Signal Execution

Task ID: `L12-CAP-001`

Owner: `Codex`

Independent reviewer: `Claude`

Target: `dev`, governed paper only

Admission: owner validation passed; independent review is still required

## Delivered behavior

- Redis signal claims use one Lua transaction to remove the pending entry, allocate
  a monotonic claim token, persist the worker claim, and record server-time
  visibility.
- Ack, nack, dead-letter transfer, and expired-claim recovery are each atomic Redis
  transitions. A lost client response cannot create a state between removal from
  in-flight and durable ack/requeue/DLQ placement.
- Missing or mismatched runtime, binding, or capital-pool identity fails closed in
  governed-paper mode.
- The paper fleet reconciler fails closed without a lease backend. Its production
  constructor uses a Redis token-fenced lease (or an explicitly configured,
  file-locked backend), validates fencing before worker mutations, and rejects
  stale renewal after succession.
- Every Capital mutation is bound to a verified bearer-token service, actor, role,
  and tenant. Request-body spoofing and cross-tenant mutations are rejected.
- No live-capital flag, deployment setting, or production posture was enabled.

## Reproducible proof

The Redis integration drills use:

```text
redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99
```

The four task proof transcripts are:

- `redis_crash_before_ack_proof.txt`
- `execution_error_dlq_proof.txt`
- `leader_lease_convergence_proof.txt`
- `six_binding_restart_isolation_drill.txt`

Post-merge owner validation at code head
`e94774b260f12b2751eb98ab563df49422e8c5b1`:

```text
services.execution.lean_runtime.test_signal_isolation + test_signal_consumer: 73 tests, OK
services/execution/runtime-manager/test_paper_fleet_reconciler.py: 43 tests, OK
services/capital/test_service.py: 59 passed, 1 deprecation warning
git diff --check: passed
```

The warning is an upstream Starlette `TestClient` deprecation warning and does
not change the mutation authorization result.

## Review boundary

This evidence records owner-produced implementation and validation only. It does
not predeclare Claude approval, required GitHub check success, deployment, merge,
or task completion. Missing or contradictory independent evidence fails closed.

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
- Buffered rebalance claims renew only while their worker-scoped token is still
  unexpired. The consumer revalidates that claim and the durable processed marker
  at the executor boundary, then runs a Redis-advertised heartbeat throughout the
  complete executor call. A reclaimed slow buffer and a side effect lasting longer
  than the visibility TTL therefore cannot execute concurrently on two workers.
- Missing or mismatched runtime, binding, or capital-pool identity fails closed in
  governed-paper mode.
- The paper fleet reconciler fails closed without a lease backend. Its production
  constructor uses a Redis token-fenced lease (or an explicitly configured,
  file-locked backend), validates fencing before worker mutations and again after
  subprocess spawn, rejects stale renewal after succession, and terminates a child
  created while the lease expired before registering it.
- Every Capital mutation is bound to a verified bearer-token service, actor, role,
  and tenant. Request-body spoofing and cross-tenant mutations are rejected.
- No live-capital flag, deployment setting, or production posture was enabled.

## Reproducible proof

The Redis integration drills use:

```text
redis@sha256:6ab0b6e7381779332f97b8ca76193e45b0756f38d4c0dcda72dbb3c32061ab99
```

The seven task proof transcripts are:

- `redis_crash_before_ack_proof.txt`
- `execution_error_dlq_proof.txt`
- `leader_lease_convergence_proof.txt`
- `six_binding_restart_isolation_drill.txt`
- `slow_rebalance_claim_fence_proof.txt`
- `long_execution_claim_renewal_proof.txt`
- `blocked_spawn_fence_compensation_proof.txt`

Owner validation at repaired integration head
`6bebf5c3770f61158dd7634dc858e133081881f6` over dev
`643181a067ec5c344faac0766c69de0d5cfb32eb`:

```text
services.execution.lean_runtime.test_signal_isolation + test_signal_consumer: 75 tests, OK
long-execution real-Redis regression: 3/3 stability repetitions, OK
services/execution/runtime-manager/test_paper_fleet_reconciler.py: 44 tests, OK
services/capital/test_service.py: 59 passed, 1 deprecation warning
git diff --check: passed
```

The warning is an upstream Starlette `TestClient` deprecation warning and does
not change the mutation authorization result.

## Review boundary

This evidence records owner-produced implementation and validation only. It does
not predeclare Claude approval, required GitHub check success, deployment, merge,
or task completion. Missing or contradictory independent evidence fails closed.

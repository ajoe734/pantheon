# L12-CAP-001 — Lossless and Isolated Governed-Paper Signal Execution

Task ID: `L12-CAP-001`

Owner: `Codex`

Independent reviewer: `Claude`

Target: `dev`, governed paper only

Admission: independent review approved at exact head
`2cc1a2e6af3cca1d274c8a2ef87648c13e2affa8`; PR merge and owner `done`
finalization remain pending

## Delivered behavior

- Redis signal claims use one Lua transaction to remove the pending entry, allocate
  a monotonic claim token, persist the worker claim, and record server-time
  visibility.
- Ack, nack, dead-letter transfer, and expired-claim recovery are each atomic Redis
  transitions. A lost client response cannot create a state between removal from
  in-flight and durable ack/requeue/DLQ placement.
- Buffered rebalance claims renew only while their worker-scoped token is still
  unexpired. The consumer revalidates that claim and the durable processed marker
  at the executor boundary.
- Before the executor call, an atomic Redis transaction reserves a signal-scoped
  execution token for the current unexpired claim. The reservation outlives the
  shorter queue lease, and only its owner can atomically commit the processed
  marker. A reclaiming worker that finds execution already in progress moves its
  copy to the binding DLQ without calling the executor.
- The Redis-advertised heartbeat publishes renew-false and renew-exception state
  back to the executing path. Healthy renewal avoids reclaim; when renewal is
  lost after execution starts, the durable execution token—not the heartbeat—
  prevents a concurrent second side effect.
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

The eight task proof transcripts are:

- `redis_crash_before_ack_proof.txt`
- `execution_error_dlq_proof.txt`
- `leader_lease_convergence_proof.txt`
- `six_binding_restart_isolation_drill.txt`
- `slow_rebalance_claim_fence_proof.txt`
- `long_execution_claim_renewal_proof.txt`
- `lease_loss_execution_fence_proof.txt`
- `blocked_spawn_fence_compensation_proof.txt`

Owner validation at repaired integration head
`aa64c1921b1f9b10994a327d633990dc8282fc0e` over dev
`643181a067ec5c344faac0766c69de0d5cfb32eb`:

```text
services.execution.lean_runtime.test_signal_isolation + test_signal_consumer: 77 tests, OK
long-execution real-Redis regression: 3/3 stability repetitions, OK
renew-false and renew-exception real-Redis regressions: 3/3 each, OK
services/execution/runtime-manager/test_paper_fleet_reconciler.py: 44 tests, OK
services/capital/test_service.py: 59 passed, 1 deprecation warning
git diff --check: passed
```

The warning is an upstream Starlette `TestClient` deprecation warning and does
not change the mutation authorization result.

## Independent review and closeout boundary

Claude independently approved PR head
`2cc1a2e6af3cca1d274c8a2ef87648c13e2affa8` after reproducing the two
lease-loss races against disposable real Redis, verifying the abandoned-fence
and replay paths, re-running the task suites, and checking the manifest claims:

`https://github.com/ajoe734/pantheon/pull/4203#issuecomment-5086275280`

The required `Commit trailers`, `Runtime mirror guard`, and `Smoke acceptance`
checks passed at that reviewed head. This closeout annotation does not claim PR
merge, deployment, hosted activation, task completion, or live-capital
enablement; those gates remain external to this pre-merge evidence cut.

The reviewer also recorded two non-blocking follow-ups: abandoned Redis
execution reservations currently fail closed without a TTL/operator recovery
path, and the in-memory pending-signal test double does not provide Redis-grade
multi-claimant fencing. Neither weakens the reviewed Redis governed-paper path,
but both remain explicit residual risks in `evidence.json`.

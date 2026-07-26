# L12-ALPHA-001 authoritative Alpha replication evidence

Status: ready for independent `Codex2` review.

This packet proves the implementation boundary required by `L12-ALPHA-001`:
an approved immutable StrategySpec is rechecked by its canonical registry ID,
leased with a tenant-scoped key, evaluated by the research ReplicationGate,
and persisted as schema-backed ExperimentTask and ExperimentRun records in the
existing research-orchestrator authority. Queue success is recorded only after
an independent authority readback.

This is not a hosted-runtime or live-capital claim. Compose/default activation
belongs to `L12-MANIFEST-001`, and later multi-loop drills own hosted maturity.
The current implementation keeps production activation disabled and fails
closed for stub, manual-execution, paper, canary, live, and production worker
modes.

The machine-readable review receipt is [`evidence.json`](evidence.json), with
its digest in [`evidence.sha256`](evidence.sha256).

## Delivery identity

- Task: `L12-ALPHA-001`
- Owner: `Codex`
- Reviewer: `Codex2`
- Branch: `task/L12-ALPHA-001`
- Base: `1827cce2e9d6c31f7b57ec4400f3c5d1b3bede29`
- Tenant/authority foundation:
  `0c018d4bc4a962a878c0d2742bfcc9f5e484306b`
- Authoritative worker/controller:
  `95dd93fddd8811a2fc11a03ca81eeaf6d482686b`
- Authority receipt correlation:
  `d1d2405122918a3f5c63f5b6cfbd6b2e5284c9a8`

## Acceptance proof

| Requirement | Behavioral proof |
| --- | --- |
| Approved reviewed immutable input only | Queue admission requires `approved`, checksum, approval decision, approver, and approval time. The worker re-fetches the exact registry ID and compares every immutable review binding before evaluation. |
| Canonical ID and tenant key | Queue identity is exactly `(tenant_id, strategy_spec_id)`; strategy family and version are lineage only. Tenant mismatch and same-ID cross-tenant tests pass. |
| Expiry, reclaim, and replay | Claims carry expiry, fencing token, and generation. Expired work returns to pending, stale tokens cannot acknowledge, bounded failures enter DLQ, and replay IDs are idempotent. |
| Non-stub research authority | The adapter persists nested schema-backed ExperimentTask and ExperimentRun payloads through the existing FastAPI research service routes, completes the run, and reads both records back. The producer backend is `replication_gate`; the outer `manual` adapter is only the service's persistence transport. |
| Deterministic duplicate/restart/failure behavior | Duplicate discovery converges to one queue record, authority idempotency converges repeated writes, a crash after authority write reuses the same attempt/run identity, and one operator replay creates one new generation. |

## Exact validation

```text
PYTHONPATH=/tmp/l12-alpha-pydeps:. python3 -m pytest -q -p no:cacheprovider \
  services/research/alpha_replication \
  services/research/experiment_orchestrator \
  services/research/experiments \
  services/research/tests/test_research_orchestrator_http_service.py
58 passed, 11 warnings in 13.54s

PYTHONPATH=/tmp/l12-alpha-pydeps:. python3 -m pytest -q -p no:cacheprovider \
  <13 approved/tenant/lease/DLQ/crash/service-boundary cases>
13 passed, 1 warning in 3.11s

python3 -m py_compile \
  services/research/alpha_replication/queue.py \
  services/research/alpha_replication/replication_controller.py \
  services/research/alpha_replication/revalidation_worker.py \
  services/research/experiment_orchestrator/authority.py \
  services/research/experiment_orchestrator/parallel_dispatch.py \
  services/research/experiments/models.py \
  services/research/experiments/registry_writeback.py
exit 0

git diff --check
exit 0
```

The warnings are one TestClient compatibility warning and pre-existing
`datetime.utcnow()` deprecations in the ReplicationGate modules; none changes
the result or the authority readback.

## Composition boundary

- `L12-DIST-001` owns creation and immutable approval metadata for upstream
  StrategySpecs. This worker rejects missing or changed metadata.
- `L12-MANIFEST-001` owns Compose/default activation. The legacy
  `handoff_only` config value is accepted only as an input alias; runtime
  behavior is always authoritative.
- `L12-CTRL-001` may consume the authority receipt references now emitted by
  the controller.
- Production activation and live-capital authority remain disabled and outside
  this task.

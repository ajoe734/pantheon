# L12-ALPHA-001 authoritative Alpha replication evidence

Status: independently reviewed, merged to `dev`, and packaged for owner
closeout.

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

The formal product-evidence manifest is [`evidence.json`](evidence.json), with
its digest in [`evidence.sha256`](evidence.sha256).

## Delivery identity

- Task: `L12-ALPHA-001`
- Owner: `Codex`
- Reviewer: `Codex2`
- Branch: `task/L12-ALPHA-001`
- Review-fix base: `cb02a06cd2b87502c0f808ae3bf2123556035802`
- Tenant/authority foundation:
  `0c018d4bc4a962a878c0d2742bfcc9f5e484306b`
- Authoritative worker/controller:
  `95dd93fddd8811a2fc11a03ca81eeaf6d482686b`
- Authority receipt correlation:
  `d1d2405122918a3f5c63f5b6cfbd6b2e5284c9a8`
- Replay/receipt review fixes:
  `288c21eadbfa9995bf50a3d54ab3307cdbf055bc`
- Reviewer-approved fix tip:
  `03264cb0eaebd2bf9d2ddcf002096e57fa731c57`
- Initial delivery: PR
  [#4147](https://github.com/ajoe734/pantheon/pull/4147), merged as
  `dd9d83722b24598a623692c2b6ca8b80f159fe04`
- Review fixes and owner closeout: PR
  [#4161](https://github.com/ajoe734/pantheon/pull/4161), merged as
  `cba34f5bd37ebb8056a9317f4a7e6b062c6111e4`

## Independent review approval

`Codex2` approved the reviewer-fix tip after independently rerunning the
60-test scoped suite and 15-case acceptance subset. The fix tip's Commit
trailers, Runtime mirror guard, and Smoke acceptance checks all completed
successfully. Owner closeout reran the same suites and the integrity checks
below before publication.

## Acceptance proof

| Requirement | Behavioral proof |
| --- | --- |
| Approved reviewed immutable input only | Queue admission requires `approved`, checksum, approval decision, approver, and approval time. The worker re-fetches the exact registry ID and compares every immutable review binding before evaluation. |
| Canonical ID and tenant key | Queue identity is exactly `(tenant_id, strategy_spec_id)`; strategy family and version are lineage only. Tenant mismatch and same-ID cross-tenant tests pass. |
| Expiry, reclaim, and replay | Claims carry expiry, fencing token, and generation. Expired work returns to pending, stale tokens cannot acknowledge, bounded failures enter DLQ, and every consumed replay ID remains durable in its tenant/spec record. An A-B-A replay after process restart is always a no-op. |
| Non-stub research authority | The adapter persists nested schema-backed ExperimentTask and ExperimentRun payloads through the existing FastAPI research service routes, completes the run, and reads both records back. Queue acknowledgement keeps domain IDs for lineage and separately records the resolvable authority task/run IDs. The producer backend is `replication_gate`; the outer `manual` adapter is only the service's persistence transport. |
| Deterministic duplicate/restart/failure behavior | Duplicate discovery converges to one queue record, authority idempotency converges repeated writes, a crash after authority write reuses the same attempt/run identity, and each new operator replay creates one new generation while any previously consumed replay ID is rejected. |
| Resolvable controller evidence | Worker receipts carry both domain and authority identities. Controller reconcile state and loop evidence use the authority identities, and a real FastAPI test derives GET paths from every emitted task/run ref and receives `200` for both. |

## Exact validation

```text
PYTHONPATH=/tmp/l12-alpha-pydeps:. python3 -m pytest -q -p no:cacheprovider \
  services/research/alpha_replication \
  services/research/experiment_orchestrator \
  services/research/experiments \
  services/research/tests/test_research_orchestrator_http_service.py
60 passed, 11 warnings in 13.19s

PYTHONPATH=/tmp/l12-alpha-pydeps:. python3 -m pytest -q -p no:cacheprovider \
  <15 approved/tenant/lease/DLQ/ABA/crash/authority-ref cases>
15 passed, 1 warning in 4.42s

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

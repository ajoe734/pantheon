# L12-CTRL-001 canonical loop-controller evidence

Status: independently reviewed and approved; owner closeout verification passed.

This packet proves a shared controller-record contract for all twelve
canonical loops. The implementation serializes updates for one
`(tenant_id, environment, loop_id)` key inside a Postgres transaction, merges
partial observations without replaying stale row snapshots, renews a
generation-specific fenced lease on every normal write, and rejects a stale
generation after takeover.

Desired-state presence and downstream actual state are stored as explicit
observations with authority source and checked time. Query text alone remains
unobserved. The BFF obtains tenant scope from the verified identity, constrains
environment scope to authenticated claims or the deployed environment, filters
both Postgres and fallback records by the exact scope, and rejects unscoped or
cross-scope requests.

The reusable conformance helper validates every required controller field for
each canonical loop ID. The Postgres tests exercise concurrent success,
failure, heartbeat, backlog, lease renewal, stale-generation rejection,
desired/actual JSONB round-trip, and same-loop isolation across tenant and
environment keys.

The machine-readable receipt is
[`evidence.json`](evidence.json), and
[`evidence.sha256`](evidence.sha256) binds its exact bytes.

## Validation

```text
DATABASE_URL=postgresql://pantheon_app:***@localhost:15432/pantheon \
/home/lupin/pantheon/.venv/bin/python -m pytest \
  services/loop-control/test_loop_control.py -q

22 passed in 8.02s

/home/lupin/pantheon/.venv/bin/python -m pytest \
  services/control-plane/bff/test_loop_health_read_model_contract.py \
  services/control-plane/bff/test_loop_inventory_read_model_contract.py -q

19 passed in 19.81s

/home/lupin/pantheon/.venv/bin/python -m pytest \
  services/research/alpha_replication/test_replication_controller.py \
  services/source_ingestion/tests/test_controller_worker.py \
  services/source_ingestion/tests/test_distillation_controller.py -q

69 passed in 11.09s

Final combined regression over all six files:

110 passed in 34.57s after merging `origin/dev` at
`bdbd0a99bf68e6a635d9bd936782c659298b7bb7`; the tested integration head was
`5b0be9d2c1df8d4b5824bb0d8cf0562637c0c7fd`.
```

## Owner closeout verification

After `Codex2` recorded the independent approval, the owner merged current
`origin/dev` at `d054bd49cb485f091e3fb31b1d91e57d4fe372ab` into the task branch
and reran the same combined six-file regression:

```text
110 passed, 13 warnings in 31.43s
```

Python compilation, JSON schema parsing, the reviewed evidence checksum, and
`git diff --check` also passed. The reviewed `evidence.json` and its checksum
were not changed during owner closeout.

Hosted deployment and all-loop live drill admission remain owned by
`L12-MANIFEST-001` and `L12-HOSTED-001`; this task does not enable live-capital
authority or promote catalog maturity ahead of domain proof.

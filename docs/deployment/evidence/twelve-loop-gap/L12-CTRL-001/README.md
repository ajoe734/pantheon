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
[`evidence.json`](evidence.json), which conforms to
`schemas/product-evidence.schema.json`. The reviewer-approved packet is
preserved byte-identically as
[`reviewed-controller-evidence.json`](reviewed-controller-evidence.json)
(`sha256 bc77e37f981d8c566e706f3313b06dd92c9d52bb29d7dae7313bf4690e2a4ff9`), and
[`evidence.sha256`](evidence.sha256) binds the exact bytes of both files.

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

### Owner-of-record final re-verification

The canonical task row returned ownership to `Claude`, who re-ran the same
combined six-file regression and the same auxiliary checks against the
integration head that carries `origin/dev` at
`d054bd49cb485f091e3fb31b1d91e57d4fe372ab`, before merging PR #4178:

```text
DATABASE_URL=postgresql://pantheon_app:***@localhost:15432/pantheon \
/home/lupin/pantheon/.venv/bin/python -m pytest \
  services/loop-control/test_loop_control.py \
  services/control-plane/bff/test_loop_health_read_model_contract.py \
  services/control-plane/bff/test_loop_inventory_read_model_contract.py \
  services/research/alpha_replication/test_replication_controller.py \
  services/source_ingestion/tests/test_controller_worker.py \
  services/source_ingestion/tests/test_distillation_controller.py -q

110 passed, 13 warnings in 47.12s

python -m py_compile services/loop-control/{conformance,projector,store,writer}.py \
  services/control-plane/bff/{loop_inventory,main}.py            # exit 0
python -m json.tool schemas/loop-controller-record.schema.json   # exit 0
sha256sum -c evidence.sha256                                     # evidence.json: OK
git diff --check origin/dev...HEAD                               # exit 0
```

`Claude` changed no controller behavior during this finalization.

### Product-evidence schema normalization

The product-level `done` gate (`scripts/loop_done_guardrail.py`) requires the
task's `review_file` manifest to satisfy
`schemas/product-evidence.schema.json`. The reviewed packet predated that shape,
so owner-of-record `Claude` normalized it:

- the reviewer-approved packet was preserved byte-identically as
  `reviewed-controller-evidence.json`, digest unchanged at
  `bc77e37f981d8c566e706f3313b06dd92c9d52bb29d7dae7313bf4690e2a4ff9`;
- `evidence.json` was rewritten in the `loop_product_evidence.v1` shape,
  carrying the same scope, validation results, acceptance verdicts, residuals,
  and the same independent `Codex2` approval of candidate `147495e6b`
  (reviewer evidence commit `d1854c512`) recorded in the canonical
  `review_approved` activity event;
- `evidence.sha256` now covers both files.

No acceptance verdict was upgraded, no reviewer decision was re-derived, and no
new proof claim was introduced by the normalization. After it, the
integrated head re-ran the same regression at `110 passed in 30.92s`, schema
validation passed, and the guardrail reported no closure gaps.

### Final dev re-integration before merge

Required checks passed 6/6 on head `ff615e815`, but GitHub then reported PR
#4178 `BEHIND` because `dev` advanced by 16 commits to
`9f9749153d252c42d52b464bb93d6ca805a888ad` — most notably the L12-SIGNOFF-001
protected closeout verdict service and a substantially extended
`scripts/loop_done_guardrail.py`. Owner-of-record `Claude` merged that `dev`
head into the task branch at `fb034b201a9d8ac0acacab5c5804e7df832a078c` and
re-ran the identical checks there:

```text
DATABASE_URL=postgresql://pantheon_app:***@localhost:15432/pantheon \
/home/lupin/pantheon/.venv/bin/python -m pytest \
  services/loop-control/test_loop_control.py \
  services/control-plane/bff/test_loop_health_read_model_contract.py \
  services/control-plane/bff/test_loop_inventory_read_model_contract.py \
  services/research/alpha_replication/test_replication_controller.py \
  services/source_ingestion/tests/test_controller_worker.py \
  services/source_ingestion/tests/test_distillation_controller.py -q

110 passed, 13 warnings in 50.67s

python -m py_compile services/loop-control/{conformance,projector,store,writer}.py \
  services/control-plane/bff/{loop_inventory,main}.py            # exit 0
python -m json.tool schemas/loop-controller-record.schema.json   # exit 0
sha256sum -c evidence.sha256                                     # both files: OK
git diff --check origin/dev...HEAD                               # exit 0

python scripts/loop_done_guardrail.py --task-id L12-CTRL-001 \
  --status-file /home/lupin/pantheon/ai-status.json
[OK]   L12-CTRL-001 (status=review_approved)
1/1 loop task(s) passed guardrail checks.
```

The merge resolved cleanly with no conflicts in this task's owned files, and
`requires_human_ops_signoff` is `false` on the canonical row, so the new
protected closeout verdict path does not apply to this task. No controller
behavior, acceptance verdict, or reviewer decision changed in this
re-integration.

Hosted deployment and all-loop live drill admission remain owned by
`L12-MANIFEST-001` and `L12-HOSTED-001`; this task does not enable live-capital
authority or promote catalog maturity ahead of domain proof.

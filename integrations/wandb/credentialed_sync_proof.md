# W&B Credentialed Sync Proof

Task: `RES-ACT-WANDB-001-V2`
Parent: `RES-ACT-001-V2`
Owner: `Codex`
Reviewer: `Claude`
Status: review-approved implementation proof

Closeout note: Claude review approval is recorded in the task-scoped brief and
status root. The PR branch was refreshed through `origin/dev` commit `08233106`
before final closeout; this proof changes no runtime adapter semantics.

## Scope

This proof covers the explicit-gated W&B online sync path in
`services/registry/experiments/adapter.py`.

`RES-ACT-WANDB-001-V2` is the canonical W&B-specific child for the
adapter-neutral `RES-ACT-001-V2` production data proof schema. The earlier
`WNB-ACT-001-V2` implementation supplied the runtime shape; this replacement
child records the evidence under the current RES-ACT task tree.

W&B is an experiment metadata mirror only. Pantheon registry remains the
artifact-admission system of record:

- registry admission, `artifact_state`, and `deployment_stage` are validated by
  Pantheon before any W&B SDK call
- W&B run/artifact refs are copied into `experiment_refs` for operator
  inspection
- W&B refs do not approve, promote, retire, deploy, or roll back artifacts
- W&B has no broker, order, capital, or live execution route

## Credentialed Online Gate

The online path is closed unless all credentialed test inputs are present:

```bash
PANTHEON_WANDB_ONLINE_SYNC_ENABLED=1 \
PANTHEON_WANDB_PROJECT=<test-project> \
PANTHEON_WANDB_ENTITY=<optional-test-entity> \
WANDB_API_KEY=<test-api-key> \
python3 services/registry/experiments/smoke_test.py --backend wandb-online
```

The smoke harness must never print or persist `WANDB_API_KEY`. Missing gate,
project, SDK, or API-key material produces a structured `skipped` payload rather
than a silent success.

## Expected Successful Evidence

A credentialed W&B test-project run is accepted as proof only when the resulting
sync payload shows:

- `sync_status == "online_synced"`
- `experiment_refs[0].backend == "wandb"`
- `experiment_refs[0].sync_status == "online_synced"`
- `experiment_refs[0].readback_refs.verified == true`
- `experiment_refs[0].artifact_refs["artifact_handoff.json"].artifact_ref`
  starts with `wandb://`
- `artifact_state` and `deployment_stage` match the Pantheon registry entry
- no persisted tag, param, artifact, promoted metadata, or smoke output contains
  the W&B API key

## Local Verification

The task-owned integration tests exercise the credentialed SDK boundary with a
fake W&B module and real Pantheon adapter logic. They also verify the no-secret
contract, the structured missing-credential skip, and a fail-closed registry
state rejection before any W&B SDK call.

Commands:

```bash
python3 -m pytest tests/integrations/test_wandb_sync.py -q
python3 services/registry/experiments/smoke_test.py --backend wandb-online
```

Results recorded on 2026-05-20 for `RES-ACT-WANDB-001-V2`:

- `python3 -m pytest tests/integrations/test_wandb_sync.py -q` -> `5 passed`
- `python3 -m py_compile tests/integrations/test_wandb_sync.py` -> passed
- `python3 -m unittest test_adapter -q` from `services/registry/experiments/`
  -> `Ran 16 tests ... OK`
- `python3 services/registry/experiments/smoke_test.py --backend wandb-online`
  -> structured skip with missing
  `PANTHEON_WANDB_ONLINE_SYNC_ENABLED`, `PANTHEON_WANDB_PROJECT`, and
  `WANDB_API_KEY`

The external W&B API was not contacted in this developer worktree because no
credentialed W&B test project/API key or SDK install is present. A runtime with
those inputs should run the credentialed command above and compare the returned
payload to the success criteria in this file.

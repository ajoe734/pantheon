# APP-003-RLLIB-DEFERRED-PREP-001 Review Handoff

**Task**: `APP-003-RLLIB-DEFERRED-PREP-001`
**Owner**: `Codex`
**Reviewer**: `Codex2`
**Date**: `2026-04-25`

## Scope

This slice lands the repo-local **deferred-prep** RLlib lane only. It does:

- add a governed RLlib train/eval adapter scaffold
- materialize rollout/result schema as repo-local code
- add non-default backend wiring, worker, sample dataset, smoke test, and unit coverage
- keep RL gate closure and canonical `version-pinned` truth intact

It does **not**:

- reopen `services/learning/rl/RL_PATH_APPROVAL_GATE.md`
- claim governed production RLlib activation
- change any default production backend

## Code Surface

- `services/research/rllib/adapter/rllib_adapter.py`
- `services/research/rllib/adapter/__init__.py`
- `services/research/rllib/config.py`
- `services/research/rllib/worker.py`
- `services/research/rllib/smoke_test.py`
- `services/research/rllib/test_adapter.py`
- `services/research/rllib/examples/train_eval_input_sample.json`
- `services/research/rllib/README.md`
- `services/research/rllib/Dockerfile`
- `services/research/rllib/requirements.txt`

## Canonical Truth Updates

- `OSS_INTEGRATION_CHECKLIST.md`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`
- `services/learning/DEFERRED_OSS_ACTIVATION_MAP.md`
- `services/learning/rl/README.md`
- `docs/reviews/2026-04-25-deferred-prep-execution-packet.md`

All of the above now describe RLlib as:

- prep scaffold landed repo-locally
- still `version-pinned`
- still gate-closed
- still follow-on after the FinRL first-lane proof

## Verification

Executed locally:

1. `python3 -m pytest services/research/rllib/test_adapter.py -q`
   - Result: `13 passed`
2. `python3 services/research/rllib/smoke_test.py --enable-deferred-prep`
   - Result: smoke passed; `artifact_state=draft`, `deployment_summary.current_stage=none`, `candidate_next_state=candidate`, `gate_state=closed`
3. `PANTHEON_RLLIB_PREP_ENABLED=1 python3 services/research/rllib/worker.py`
   - Result: worker emitted draft-only summary with `backend=stub_rllib`, `train_steps=4`, `eval_steps=2`, `search_strategy=pbt`

## Reviewer Focus

Please verify:

1. the new RLlib lane stays explicitly non-default and prep-only
2. rollout/result schema and artifact envelope remain draft-only and offline
3. canonical docs preserve `RLlib = version-pinned` and do not imply gate reopen

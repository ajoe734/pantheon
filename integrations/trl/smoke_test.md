# TRL Integration — Smoke Test

Last updated: 2026-04-17
Owner: OSS-NEXT-002 (Claude)
Reviewer: Gemini
Status: executable smoke path verified
Primary entrypoint: `python3 services/learning/trl/smoke_test.py`

## 1. Objective

Prove that the TRL row is backed by a runnable governed adapter path rather than a
placeholder activation criteria document.

## 2. Prerequisites

Minimum local smoke prerequisites:

- Python 3.10+
- repo checkout with `services/learning/trl/`

Optional upstream smoke prerequisites:

- `pip install -r services/learning/trl/requirements.txt`
- Model hub access for `distilbert-base-uncased`

## 3. Canonical Commands

Deterministic local smoke (no TRL install required):

```bash
python3 services/learning/trl/smoke_test.py
```

Optional upstream TRL DPO smoke:

```bash
python3 services/learning/trl/smoke_test.py --backend trl
```

Unit coverage:

```bash
python3 -m unittest discover -s services/learning/trl -p 'test_*.py'
```

## 4. What the Smoke Path Verifies

The smoke script builds a synthetic FB-002 preference event set (5 events covering
approve, reject, and edit across 2 strategy families and 2 operators) and proves that:

1. `GovernedPreferencePairAdapter` validates and converts FB-002 events to preference pairs
2. `run_trl_dpo_workflow()` emits a registry-ready `model_artifact`
3. `artifact_state` is `draft` and `deployment_summary.current_stage` is `none`
4. the artifact carries a `sha256:` checksum and a governed storage path under `learning/trl/`
5. `governance.direct_live_influence` is `false`
6. `governance.execution_stage` is `none`
7. `lineage.source_feedback_event_ids` is populated for traceability
8. `approved_at` and `rollback_target` are `None` (new draft)

## 5. Verified Result

Verified on 2026-04-17 with the default stub backend:

```
TRL DPO smoke test — backend: stub
------------------------------------------------------------
dataset_id:         fb002-smoke-2026-04-17
strategy_id:        preference-learning-multi-asset
num_pairs:          5
strategy_families:  ('equity_cross_sectional', 'stat_arb')
num_operators:      2
action_distribution:{'approve': 2, 'edit': 1, 'reject': 2}

backend:            stub_dpo
accuracy:           0.6
registry_id:        trl-preference-model-preference-learning-multi-asset-1.0.0
artifact_state:     draft
deployment_stage:   none
storage_path:       learning/trl/preference-learning-multi-asset/1.0.0/artifact.bin
checksum:           sha256:380fc0567a08a7b67df3df85cea0e9f9d0699a987c78454afa2969645e97f229
artifact_family:    trl_preference_model

assertions: OK
```

Unit coverage result on 2026-04-17:

```
python3 -m unittest discover -s services/learning/trl -p 'test_*.py'
Ran 29 tests in 0.006s
OK
```

## 6. Acceptance

Treat the TRL row as smoke-proven when:

- the smoke command exits `0` with `assertions: OK`
- the workflow emits a `registry_id` and governed storage path under `learning/trl/`
- `artifact_state=draft` and `deployment_stage=none` are confirmed
- `direct_live_influence=false` is confirmed
- unit coverage still passes (29 tests)

## 7. Production Activation Gap

The smoke path proves the pipeline is runnable. Production activation additionally requires:

- ≥200 governed FB-002 events (runtime data gate — cannot be pre-staged)
- ≥100 valid preference pairs from those events
- LP-002 imitation baseline active
- at least one downstream consumer ready (EV-001, LP-005, or LP-001)

See `services/learning/trl/ACTIVATION_CRITERIA.md §1` for full entry criteria.

# TRL Integration — Governed DPO Preference-Learning Adapter

Last updated: 2026-04-24
Owner: OSS-NEXT-002 (Claude)
Reviewer: Gemini
Status: smoke-tested — runnable governed adapter verified
Implementation home: `services/learning/trl/`

## 1. Locked Upstream Selection

| Field | Value |
|---|---|
| Upstream project | `huggingface/trl` |
| Package source | `https://pypi.org/project/trl/` |
| Version pin | `trl>=0.8.0,<0.10.0` |
| Version pin source | `services/learning/trl/adapter/trl_adapter.py` (`TRL_VERSION_PIN`) |
| Service dependency file | `services/learning/trl/requirements.txt` |
| Compatibility verified against | DSPy v2.4.5, imitation v1.0.1, MLflow 3.10.1, pyqlib 0.9.6 |

This integration consumes TRL as a pinned Python package. Pantheon does not vendor
TRL source and does not treat TRL as an authority for registry lifecycle,
promotion, or execution routing.

## 2. Adapter Mode

Pantheon uses TRL only for governed preference-learning (DPO) under `LP-004`.

Accepted mode:

- governed FB-002 preference events (approve/edit/reject) are validated before TRL sees them
- DPO training runs inside `services/learning/trl/`
- the adapter emits a governed `trl_preference_model` artifact bundle and a registry-ready entry
- the default CI path uses a deterministic `StubDPOBackend` (no TRL install required)
- worker/runtime paths can switch to the real `TRLDPOBackend` (distilbert-base-uncased)

Rejected mode:

- direct TRL writes into registry truth
- direct TRL influence on LEAN, SignalStore, or live deployment paths
- using TRL preference models as live execution authority without registry promotion
- TRL consuming raw FB-002 data that bypasses governance filtering (actor_role, promotion_state)

## 3. Verified Local Adapter Surface

The runnable adapter path is implemented and exercised:

- `services/learning/trl/adapter/trl_adapter.py`
  - `GovernedPreferencePairAdapter`
  - `StubDPOBackend`
  - `TRLDPOBackend`
  - `run_trl_dpo_workflow()`
- `services/learning/trl/smoke_test.py`
- `services/learning/trl/test_adapter.py`
- `services/learning/trl/examples/preference_pair_sample.json`
- `services/learning/trl/examples/training_config_sample.yaml`

The adapter takes governed FB-002 feedback events and emits:

1. `artifact_bundle`
   - `artifact_family=trl_preference_model`
   - `model_family=preference_model`
   - `framework=trl`
   - governance block with `direct_live_influence=false` and `execution_stage=none`
2. `registry_entry`
   - `artifact_type=model_artifact`
   - `artifact_state=draft`
   - `deployment_summary.current_stage=none`
   - lineage points back to source FB-002 feedback event IDs and dataset refs

## 4. Preference Pair Construction

The governed pair adapter converts FB-002 events into DPO-ready preference pairs:

| Event action | Pair construction |
|---|---|
| `approve` | `chosen=artifact`, `rejected=null_artifact_stub` |
| `reject` | `chosen=null_artifact_stub`, `rejected=artifact` |
| `edit` | `chosen=artifact_edited`, `rejected=artifact_original` |

Governance filters applied before any pair is accepted:

- `actor_role` must be in `{"operator", "approver"}`
- `promotion_state` must be in `{"candidate", "paper"}`
- `artifact.artifact_id` must be present and non-empty
- `artifact_edited` must be present and non-empty for `edit` events

## 5. Runtime and Packaging Notes

- Package isolation: never merged into a shared requirements file (OSS framework rule)
- Default command: `python3 services/learning/trl/smoke_test.py`
- Smoke test requires no external dependencies (stub backend)
- Optional TRL backend requires `pip install -r services/learning/trl/requirements.txt`

Two execution modes are intentionally supported:

1. `StubDPOBackend` — deterministic CI and local smoke tests (no ML deps)
2. `TRLDPOBackend` — real TRL DPO training on distilbert-base-uncased for production smoke

## 6. Production Activation Prerequisites

TRL is smoke-tested but **not yet production-activated**. Production activation requires
all six entry criteria in `services/learning/trl/ACTIVATION_CRITERIA.md §1`:

1. ≥200 governed FB-002 events spanning ≥2 strategy families
2. LP-002 imitation baseline active with `artifact_state=approved` artifacts
3. ≥100 valid preference pairs from those events
4. Baseline model performance documented (accuracy ≥0.65, AUC-ROC ≥0.70)
5. At least one downstream consumer (EV-001, LP-005, or LP-001) ready
6. No upstream dependency conflicts (verified in requirements.txt)

The runtime-data gates (items 1–3) cannot be pre-staged; they require live FB-002 volume.

The reviewable activation bundle now lives in `integrations/trl/activation_packet.md`.
That packet keeps the row truthful as "smoke-tested but runtime-gated" and
defines what evidence must accompany the first governed DPO activation run.

## 7. Why This Counts As Smoke-Tested

`OSS-NEXT-002` requires a governed runnable baseline, not just criteria documentation.

That proof exists because:

- the adapter path is real code, not a placeholder contract
- the service has a dedicated dependency file (`requirements.txt`) with pinned version
- the repo ships an executable smoke test with synthetic FB-002 events
- unit tests (16 tests) validate governed filtering, pair construction, and packaging
- registry output uses canonical `artifact_state` + `deployment_summary.current_stage`
- governance boundary (`direct_live_influence=false`, `execution_stage=none`) is asserted

## 8. Evidence References

- governance overlay: `integrations/trl/governance.md`
- smoke procedure: `integrations/trl/smoke_test.md`
- activation packet: `integrations/trl/activation_packet.md`
- activation criteria: `services/learning/trl/ACTIVATION_CRITERIA.md`
- preference learning contract: `services/learning/trl/PREFERENCE_LEARNING_CONTRACT.md`
- workflow definition: `services/learning/trl/WORKFLOW_DEFINITION.md`

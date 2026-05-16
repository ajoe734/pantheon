# MGMT-QLIB-005 Qlib Registry Admission Evidence

Task scope: build the review-only registry admission packet for the Qlib
LightGBM alpha candidate.

## Artifacts

- `registry_admission_packet.json` - combined admission packet requesting only
  `draft -> candidate` review for
  `qlib-alpha-tw-cross-sectional-equity-alpha-1.0.0`.
- `activation-run/artifact_bundle.json` - Qlib alpha artifact bundle from the
  deterministic stub LightGBM activation smoke.
- `activation-run/registry_entry.json` - schema-compatible model artifact
  registry projection with `artifact_state=draft`.
- `activation-run/candidate_packet.json` - non-writing candidate handoff.
- `activation-run/artifact_refs.json` - model artifact, evaluation report,
  artifact bundle, registry entry, and candidate packet refs.
- `activation-run/manifest.json` - persisted artifact manifest and checksums.
- `activation-run/production_activation_packet.json` - production dataset proof
  attached to the candidate handoff.

## Inputs

- Dataset manifest:
  `support/evidence/MGMT-QLIB-001/dataset_manifest.json`.
- StrategySpec packet:
  `support/evidence/MGMT-QLIB-002/strategy_spec_packet.json`.
- Activation criteria:
  `services/learning/qlib/ACTIVATION_CRITERIA.md`.
- Qlib activation packet:
  `integrations/qlib/activation_packet.md`.

## Boundary

The admission packet is review-only:

- `registry_write_performed=false`.
- `registry_write_authority=registry_service_only`.
- `artifact_state` remains `draft`.
- Requested transition is limited to `draft -> candidate`.
- `deployment_stage=none`.
- `order_route=none`.
- No broker session or capital binding is opened.

The activation artifacts use the deterministic `stub_lgbm` backend for packet
smoke evidence. They do not claim a real upstream Qlib backend production run.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/learning/qlib/test_registry_admission.py services/research/qlib/test_production_activation.py services/research/qlib/test_adapter.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/learning/qlib/activation/registry_admission.py services/learning/qlib/test_registry_admission.py services/research/qlib/adapter/qlib_adapter.py
PYTHONDONTWRITEBYTECODE=1 python3 services/research/qlib/production_activation_smoke.py --dataset services/research/qlib/examples/smoke_dataset.json --proof integrations/qlib/governed-dataset-proof-tw.json --backend stub --output-dir support/evidence/MGMT-QLIB-005/activation-run
PYTHONDONTWRITEBYTECODE=1 python3 services/learning/qlib/activation/registry_admission.py --dataset-manifest support/evidence/MGMT-QLIB-001/dataset_manifest.json --strategy-spec-packet support/evidence/MGMT-QLIB-002/strategy_spec_packet.json --activation-dir support/evidence/MGMT-QLIB-005/activation-run --output support/evidence/MGMT-QLIB-005/registry_admission_packet.json --created-at 2026-05-15T17:30:00Z
```

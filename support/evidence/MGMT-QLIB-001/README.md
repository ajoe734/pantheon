# MGMT-QLIB-001 Qlib Dataset Manifest Evidence

Task scope: dataset manifest for the Qlib admission lane.

## Artifacts

- `dataset_manifest.json` - normalized governed TW OHLCV dataset manifest.
- Source proof: `integrations/qlib/governed-dataset-proof-tw.json`.
- Period-count evidence ref:
  `support/sidecars/QLIB-ACT-002/QLIB-ACT-002-SIDECAR-ACCEPTANCE.md`.

## Boundary

This evidence opens only the dataset manifest gate:

- 50 TWSE/TPEx instruments.
- 2024-01-02 through 2026-01-05 daily OHLCV history.
- 504 minimum periods per instrument, cited from QLIB-ACT-002 acceptance.
- Governed provider, entitlement, freshness, PIT, storage, audit, and no-order-route controls.

It does not claim that Qlib training, registry admission, deployment, broker
session, or capital binding has occurred.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/learning/qlib/test_dataset_manifest.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/learning/qlib/activation/dataset_manifest.py services/learning/qlib/test_dataset_manifest.py
```

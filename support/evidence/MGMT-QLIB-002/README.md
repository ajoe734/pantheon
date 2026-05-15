# MGMT-QLIB-002 Qlib StrategySpec Builder Evidence

Task scope: build the review-only StrategySpec packet for the Qlib admission lane.

## Artifacts

- `strategy_spec_packet.json` - schema-valid StrategySpec, candidate registry
  projection, RS-003 candidate probe, StrategySpec binding probe, and preflight
  packet.
- Source dataset manifest: `support/evidence/MGMT-QLIB-001/dataset_manifest.json`.
- RS-003 review ref: `docs/reviews/2026-05-12-qlib-act-001-codex2-review.md`.

## Boundary

This packet is a non-writing admission artifact:

- `registry_write_performed=false`.
- `training_performed=false`.
- `broker_session_opened=false`.
- `order_route=none`.
- `deployment_stage=none`.

The candidate registry entry is a projection for reviewer/admission handoff only;
the registry service remains the sole write authority.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/learning/qlib/test_strategy_spec_builder.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/learning/qlib/activation/strategy_spec_builder.py services/learning/qlib/test_strategy_spec_builder.py
PYTHONDONTWRITEBYTECODE=1 python3 services/learning/qlib/activation/strategy_spec_builder.py support/evidence/MGMT-QLIB-001/dataset_manifest.json --output support/evidence/MGMT-QLIB-002/strategy_spec_packet.json --created-at 2026-05-15T16:45:00Z
```

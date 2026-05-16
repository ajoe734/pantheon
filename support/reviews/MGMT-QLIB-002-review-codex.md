# Review: MGMT-QLIB-002 Qlib StrategySpec builder

Reviewer: `Codex`
Owner: `Codex2`
Date: `2026-05-15`
Disposition: `approved`

## Scope

Task-owned files reviewed:

- `services/learning/qlib/activation/strategy_spec_builder.py`
- `services/learning/qlib/test_strategy_spec_builder.py`
- `support/evidence/MGMT-QLIB-002/README.md`
- `support/evidence/MGMT-QLIB-002/strategy_spec_packet.json`

Referenced inputs and contracts:

- `support/evidence/MGMT-QLIB-001/dataset_manifest.json`
- `services/research/qlib/preflight.py`
- `services/control-plane/specs/strategy_spec.schema.json`
- `services/registry/registry_entry_schema.json`
- `services/registry/strategy-specs/qlib-tw-cross-sectional-alpha-v1.md`
- `docs/reviews/2026-05-12-qlib-act-001-codex2-review.md`

## Findings

No blocking issues found.

The builder consumes the governed MGMT-QLIB-001 dataset manifest and produces a schema-valid StrategySpec, candidate registry projection, RS-003 candidate probe, StrategySpec binding probe, and preflight packet. The output preserves the required non-writing boundary:

- `registry_write_performed=false`
- `training_performed=false`
- `broker_session_opened=false`
- `order_route=none`
- `deployment_stage=none`

## Review Notes

- StrategySpec validation uses the canonical `services/control-plane/specs/strategy_spec.schema.json`.
- Registry projection validation uses `services/registry/registry_entry_schema.json` and verifies the checksum matches the inline StrategySpec.
- The generated packet is reproducible from `support/evidence/MGMT-QLIB-001/dataset_manifest.json` with the recorded timestamp.
- The preflight packet opens all required Qlib pre-activation gates for the task scope: RS-003 candidate probe, governed dataset, and StrategySpec binding.

Non-blocking observation: the builder keeps the registry entry as a projection only. Owner closeout should preserve that wording in evidence and avoid implying a registry service write occurred.

## Verification

Passed:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/learning/qlib/test_strategy_spec_builder.py services/research/qlib/test_preflight.py -q
```

Result: `9 passed in 9.59s`.

Passed:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/learning/qlib/activation/strategy_spec_builder.py services/learning/qlib/test_strategy_spec_builder.py
```

Passed:

```bash
jq '.preflight_packet' support/evidence/MGMT-QLIB-002/strategy_spec_packet.json | PYTHONDONTWRITEBYTECODE=1 python3 services/research/qlib/preflight.py
```

Result: `activation_allowed=true`, summary `All required Qlib pre-activation gates open.`

Passed:

```bash
tmpfile=$(mktemp); PYTHONDONTWRITEBYTECODE=1 python3 services/learning/qlib/activation/strategy_spec_builder.py support/evidence/MGMT-QLIB-001/dataset_manifest.json --output "$tmpfile" --created-at 2026-05-15T16:45:00Z; diff -u support/evidence/MGMT-QLIB-002/strategy_spec_packet.json "$tmpfile"; rm -f "$tmpfile"
```

Result: no diff.

## Decision

Approved. Move `MGMT-QLIB-002` to `review_approved` and return it to owner `Codex2` for closeout.

# AG-GAP-012 — 12-block Winner Branch completeness contract

Status: contract frozen; BFF readiness projection implemented

This document defines the 12-block completeness contract for the Winner Branch strategy family in Agora workshops, and its compatible mapping to the 7 generic completeness dimensions.

## Winner Branch 12-Block Structure

The 12 Winner Branch blocks assessed during the workshop are:
1. `market_scope`
2. `insider_branch_mapping`
3. `winner_branch_scoring`
4. `migration_reverse_flow`
5. `event_lead`
6. `signal_formation`
7. `entry_holding`
8. `add_reduce_exit`
9. `sizing_leverage`
10. `cost_liquidity_capacity`
11. `validation_backtest_refutation`
12. `monitoring_update`

## 7-Dimension Compatible Mapping

To maintain compatibility with generic `StrategyCompleteness` schemas and downstream BFF/UI components (like the frontend `StrategyCompletenessRail`), the 12 Winner Branch blocks are mapped to the 7 generic dimensions:

| Generic Completeness Dimension | Mapped Winner Branch Blocks |
|---|---|
| `market_scope` | `market_scope` |
| `data_dependencies` | `insider_branch_mapping`, `winner_branch_scoring`, `migration_reverse_flow` |
| `hypothesis` | `event_lead`, `signal_formation` |
| `evaluation_plan` | `entry_holding`, `add_reduce_exit` |
| `risk_constraints` | `sizing_leverage` |
| `execution_profile` | `cost_liquidity_capacity`, `validation_backtest_refutation` |
| `governance` | `monitoring_update` |

### Grade Aggregation Rules

For compatibility, block grades are aggregated to dimension grades:
- **Block Grades**: `"confirmed"`, `"inferred_needs_confirmation"`, `"missing"`, `"weak"`, `"conflicting"`, `"not_applicable"`
- **Generic Grades**: `"complete"`, `"partial"`, `"missing"`

Aggregation logic:
- If all mapped blocks are `"confirmed"` or `"not_applicable"`, the dimension is `"complete"`.
- If all mapped blocks are `"missing"`, the dimension is `"missing"`.
- Otherwise, it is `"partial"`.

## Frozen Contract Hashes (Bundle v1.6)

The contract is formally frozen under the additive `bundle_index.v1_6.json`:

- **Schema File**: [winner_branch_completeness.schema.json](file:///tmp/pantheon-worker-worktrees/pantheon/ag-gap-012/services/control-plane/specs/agora/v7/winner_branch_completeness.schema.json)
  - `sha256`: `b75845a7ea809aee0c895be2287018276d4cf88b7d7c0a65d8d6f5143c316eb9`
- **Capability Manifest**: [capability_manifest_v1_6.json](file:///tmp/pantheon-worker-worktrees/pantheon/ag-gap-012/services/control-plane/specs/agora/v7/capability_manifest_v1_6.json)
  - `sha256`: `cc55b8dc8d41ff5a4287ab19cd83df482377e7ead072f124242b903575bda1f0`
- **Bundle Index**: [bundle_index.v1_6.json](file:///tmp/pantheon-worker-worktrees/pantheon/ag-gap-012/services/control-plane/specs/agora/bundle_index.v1_6.json)

## BFF Readiness Projection

The BFF router projects Winner Branch blocks to generic dimensions and specific `StrategySpec` fields in `services/control-plane/bff/agora/strategy_workshop/router.py` using `_project_winner_branch_state_map`. This ensures that readiness and completeness updates work seamlessly.

## Verification

Integration test suite results:
- `scripts/test_agora_v1_6_bundle.py`: 15 passed.
- `services/control-plane/bff/tests/test_agora_strategy_workshop.py`: 67 passed (including `test_post_winner_branch_completeness_blocks_projection`).

# BFF-CONSOL-008 Implementation - Codex2

Task: Canonical fixture pack A
Owner: Codex2
Reviewer: Codex
Date: 2026-05-13
Status: ready for review

## Delivered Scope

- Added `services/control-plane/bff/data/fixtures_pack_a.json`.
- Pack A contains non-empty fixture rows for:
  - strategies: `strategy-pack-a-momentum`
  - personas: `persona-pack-a-momentum`
  - capital pools: `pool-pack-a-ops`
  - rebalances: `rebalance-pack-a-001`
  - deployments: `plan-pack-a-paper-001`
- Strategy fixture links to:
  - specs: `spec-pack-a-momentum-v1`
  - experiments: `exp-pack-a-momentum-001`
  - artifacts: `artifact-pack-a-momentum-v1`
  - lineage: `lineage-pack-a-strategy-artifact`
  - audit: `audit-pack-a-strategy-approved`
- Persona fixture includes route-policy and evaluation skeleton coverage through:
  - `persona_route_policies.persona-pack-a-momentum`
  - `consult_policies.persona-pack-a-momentum`
  - `teaching_sessions.eval-pack-a-momentum-001`
- Deployment fixture includes stages and an approval pointer to `approval-pack-a-deploy`.

## Read Store Behavior

- `ReadSurfaceStore` local snapshot fixture fallback now merges Pack A into `_default_read_data()`.
- Existing snapshot files opened with `allow_local_snapshot_fallback=True` backfill Pack A entries.
- `allow_local_snapshot_fallback=False` does not load Pack A, preserving the staging/production cutoff rule and avoiding EP5 paper-canary truth impact.
- Seeded `rebalances` are now visible through `list_rebalances()` and `get_rebalance()` when local snapshot fallback is enabled.
- Strategy experiment/artifact projections now preserve `strategy_id` / `linked_strategy_id`, so `/bff/strategies/{strategy_id}/experiments` and `/artifacts` can resolve fixture-linked rows.
- `/bff/deployments` now includes a `data` alias and `page_info.total` alongside the existing `items` response, so the authenticated live probe can report `data_count`.

## Verification

```bash
jq -e '(.datasets.strategy_specs | length >= 1) and (.datasets.personas | length >= 1) and (.datasets.capital_pools | length >= 1) and (.datasets.rebalances | length >= 1) and (.datasets.deployment_plans | length >= 1)' services/control-plane/bff/data/fixtures_pack_a.json
```

```bash
python3 -m py_compile services/control-plane/bff/read_store.py services/control-plane/bff/main.py services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py
```

```bash
python3 -m pytest services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py services/control-plane/bff/test_bff_strategy_persona_contract.py services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py -q
```

Result: `48 passed, 10 warnings` (`datetime.utcnow()` deprecation warnings are pre-existing).

```bash
python3 -m pytest services/control-plane/bff/test_read_store_bootstrap_snapshot.py -q
```

Result: `2 passed`.

```bash
git diff --check
```

Result: no whitespace errors.

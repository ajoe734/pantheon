# BFF-CONSOL-008 Review - Codex

Task: Canonical fixture pack A
Owner: Codex2
Reviewer: Codex
Date: 2026-05-13
Disposition: approved

## Findings

No blocking findings.

## Review Notes

- Pack A declares non-empty strategy, persona, capital pool, rebalance, and deployment families.
- Fixture data covers the required backing datasets for strategy specs, experiments, artifacts, lineage edges, governance audit events, persona route policy/evaluation skeleton, capital pool, rebalance, deployment stages, and approval pointer.
- `ReadSurfaceStore` fixture merge/backfill is scoped to local snapshot fallback behavior; `allow_local_snapshot_fallback=False` remains covered by the focused regression for the Pack A strategy/deployment/rebalance surfaces.
- `/bff/deployments` now exposes the same list through `data` and `items`, with `page_info.total`, matching the live probe data-count contract.

## Verification

```bash
jq -e '(.families.strategies | length >= 1) and (.families.personas | length >= 1) and (.families.capital_pools | length >= 1) and (.families.rebalances | length >= 1) and (.families.deployments | length >= 1) and (.datasets.strategy_specs | length >= 1) and (.datasets.personas | length >= 1) and (.datasets.capital_pools | length >= 1) and (.datasets.rebalances | length >= 1) and (.datasets.deployment_plans | length >= 1) and (.datasets.research_experiments | length >= 1) and (.datasets.research_artifacts | length >= 1) and (.datasets.lineage_edges | length >= 1) and (.datasets.governance_audit_events | length >= 1)' services/control-plane/bff/data/fixtures_pack_a.json
```

Result: passed.

```bash
python3 -m py_compile services/control-plane/bff/read_store.py services/control-plane/bff/main.py services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py
```

Result: passed.

```bash
git diff --check -- services/control-plane/bff/read_store.py services/control-plane/bff/main.py services/control-plane/bff/data/fixtures_pack_a.json services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py support/sidecars/BFF-CONSOL-008/implementation-bff-consol-008-codex2.md
```

Result: passed.

```bash
python3 -m pytest services/control-plane/bff/test_bff_consol_008_fixture_pack_a.py services/control-plane/bff/test_bff_strategy_persona_contract.py services/control-plane/bff/test_bff_capital_ranking_rebalance_contract.py services/control-plane/bff/test_bff_governance_runtime_risk_audit_contract.py -q
```

Result: `48 passed, 10 warnings`.

```bash
python3 -m pytest services/control-plane/bff/test_read_store_bootstrap_snapshot.py -q
```

Result: `2 passed`.

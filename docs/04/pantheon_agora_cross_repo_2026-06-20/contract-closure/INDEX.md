# Pantheon Agora Contract-Layer Closure Pack

Date: 2026-06-20  
Scope: close the contract-layer gaps that remain after the product/quant/privacy Design Closure Pack.  
Repo baseline: `pantheon@dev`, `execute-plans@dev`.

## Authority

This pack is a **design decision proposal** until its canonical artifacts are merged into `pantheon@dev` and mirrored/generated into `execute-plans@dev`. It does not override the frozen `AG-XR-001` bundle in place.

## Files

1. `01_latest_dev_findings.md`
2. `02_schema_coexistence_and_migration.md`
3. `03_servant_and_workshop_contracts.md`
4. `04_dashboard_crud_and_concurrency.md`
5. `05_execute_plans_agora_ui_ia_and_dependencies.md`
6. `06_compatibility_manifest_and_hash_rules.md`
7. `07_dispatch_unblock_matrix_v2.md`
8. `widget_spec_v2.schema.json`
9. `chart_spec_v1.schema.json`
10. `dashboard_recipe_v2.schema.json`
11. `compatibility_manifest.schema.json`
12. `compatibility_manifest.example.json`
13. `agora_contract_extension_manifest_v1_1.json`
14. `agora_openapi_extension_v1_1.yaml`

## Decision summary

- `AG-XR-001` remains immutable; its files and `bundle_index.json` are not replaced.
- The incompatible dynamic-dashboard model becomes `WidgetSpec 2.0` / `DashboardRecipe 2.0` in an additive extension bundle.
- Existing `WidgetSpec 1.0` remains supported for legacy Agora surfaces; all new Trading Desk writes use v2.
- A new OpenAPI v1.1 contract adds servant ensure, workshop CRUD and dashboard-recipe CRUD.
- Dashboard mutations require ETag + `If-Match`, `expected_version`, and `Idempotency-Key`.
- `execute-plans@dev` already has Recharts; add ECharts and react-grid-layout for the advanced widget runtime.
- Agora IA is fixed as Trading Room / Strategy Workshop / Strategy Execution & Performance.
- A compatibility manifest has an exact path, commit semantics, capability checks and byte-level hash rules.

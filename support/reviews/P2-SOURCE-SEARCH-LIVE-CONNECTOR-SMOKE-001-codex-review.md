# Codex Review: P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001

Task: `P2-SOURCE-SEARCH-LIVE-CONNECTOR-SMOKE-001`
Reviewer: Codex
Reviewed at: 2026-05-02

## Verdict

Approved.

The delivered bounded live/test source-search smoke path satisfies the task acceptance gates for a governed non-ordering external source path:

- Source ingestion preserves `SourceRecord` and `EvidenceBundle` governance fields, including entitlement tags, license scope, PIT, and `available_time`.
- Search readback uses the durable evidence/index path and returns evidence bundle plus citation references without caller-supplied document payloads.
- External source governance rejects direct Lean, broker, live execution, order-router, and direct feed routes.

## Scope Notes

The checked-in evidence distinguishes bounded test proof from credentialed live-provider proof. `source_search_live_connector_smoke.json` records `dependency_missing` when no `SOURCE_SEARCH_LIVE_FEED_URL` and allowlist are configured, so the task does not falsely claim a credentialed provider run in this workspace. `source_search_live_connector_smoke.local.json` and the in-process HTTP feed test prove the bounded connector mechanics.

## Verification

Commands run:

```bash
python3 -m pytest scripts/test_run_source_search_live_connector_smoke.py services/source_ingestion/tests/test_external_source_connectors.py::test_live_connector_smoke_rejects_forbidden_execution_routes services/source_ingestion/tests/test_external_source_connectors.py::test_source_search_end_to_end_durable_readback
python3 -m pytest services/source_ingestion/tests/test_external_source_connectors.py services/search/tests/test_contracts.py::test_sd03_contract_schemas_accept_model_payloads scripts/test_run_source_search_live_connector_smoke.py
python3 -m pytest services/source_ingestion services/search scripts/test_run_source_search_live_connector_smoke.py
python3 -m pytest services/control-plane/bff/test_source_search_ops_bff.py services/control-plane/bff/test_search_service_client.py services/control-plane/bff/test_rw02_search_contract.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py
python3 -m py_compile scripts/run_source_search_live_connector_smoke.py scripts/test_run_source_search_live_connector_smoke.py
git diff --check -- docs/contracts/evidence_bundle.schema.json docs/deployment/source-search-prod-hardening.md scripts/run_source_search_live_connector_smoke.py scripts/test_run_source_search_live_connector_smoke.py
```

Results:

- Focused smoke/governance/E2E slice: 4 passed.
- External source/schema/script slice: 11 passed.
- Source/search service slice: 140 passed.
- BFF/OpenClaw-adjacent slice: 86 passed.
- `py_compile`: passed.
- `git diff --check`: passed.

## Residual Risk

No live credentialed provider/feed was configured in this workspace. That is recorded as explicit dependency-missing evidence, not as a failed acceptance gate for this bounded test harness.

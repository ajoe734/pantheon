# MGMT-QLIB-006 Review — Claude2

**Reviewer:** Claude2
**Task:** Management artifact / research linkage
**Owner:** Codex2
**Date:** 2026-05-15

## Verdict: Approved (no blocking issues)

## Review Scope

Task-owned files reviewed:
- `services/control-plane/bff/read_store.py` — `_rw05_research_linkage` static method and its use in `_project_research_artifact_list_item` and `_project_research_artifact_detail`
- `services/control-plane/bff/test_mgmt_qlib_006_artifact_research_linkage.py` — 2 contract tests
- `support/evidence/MGMT-QLIB-006/management_linkage_packet.json` — evidence packet
- `support/evidence/MGMT-QLIB-006/README.md`

## Key Findings

### Implementation
- `_rw05_research_linkage` is a static method that cascades lookup through `artifact`, `metadata`, and `provenance` dicts and returns a deep copy. Correct and safe.
- The method is wired into both the list-item projection (`_project_research_artifact_list_item`) and the detail projection (`_project_research_artifact_detail`), so both BFF routes and the API v1 detail endpoint expose `research_linkage` consistently.

### Contract Tests
- `test_api_artifact_detail_exposes_qlib_research_linkage` — validates `/api/v1/artifacts/{id}` returns the full research_linkage blob with all required Qlib fields.
- `test_bff_artifact_and_strategy_routes_expose_qlib_research_linkage` — validates `/bff/artifacts/{id}` and `/bff/strategies/{id}/artifacts` expose consistent research_linkage.
- `_assert_qlib_linkage` helper checks: framework, strategy_id, strategy_spec_id, dataset_manifest_id prefix, source_task_ids (MGMT-QLIB-001/002/004), pending_task_ids (MGMT-QLIB-005), evidence_refs (dataset_manifest + strategy_spec_packet), artifact_refs (model_artifact, evaluation_report, candidate_packet), and all safety assertions.

### Evidence Packet
- `management_linkage_packet.json` correctly reflects all safety flags: `registry_write_performed=false`, `broker_session_opened=false`, `order_route=none`, `deployment_stage=none`, `live_capital_side_effects=false`.
- Links correctly to MGMT-QLIB-001 (dataset manifest), MGMT-QLIB-002 (strategy spec packet), MGMT-QLIB-004 (model/eval review), and marks MGMT-QLIB-005 as `pending_upstream_task`.

### Verification (independently reproduced)
```
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile read_store.py test_mgmt_qlib_006_artifact_research_linkage.py → PASS
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest test_mgmt_qlib_006_artifact_research_linkage.py -q → 2 passed
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest test_rw05_artifact_compare_contract.py test_bff_consol_017_detail_smoke_b.py -q → 9 passed
```

## Non-blocking Observations

None.

## Safety Boundary

No registry writes, broker sessions, order routes, deployment, or live capital side effects. Confirmed by evidence packet and test assertions.

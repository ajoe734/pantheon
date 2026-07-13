# Task Brief: LOOP-PROD-000

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Canonical loop inventory and OODA overlay truth
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Resume from merged PR #3543, reconcile the evidence checksum and AC-06/AC-09 scope split, then hand the merged evidence to Codex2 for independent review.

## Summary
校正 loop catalog、BFF inventory 與 verification index：維持 12 個 L1 canonical loops，新增 per_persona_ooda 為 composite_overlay 並宣告 composed_of；archived task 不得被投影成 live maturity。

scope:
- .orchestrator/task-briefs/loop_prod_000.md
- docs/deployment/loop-catalog.registry.json
- docs/deployment/loop-catalog.schema.json
- docs/deployment/evidence/loop-product-level/LOOP-PROD-000
- docs/05/system-verification-rounds/INDEX.md
- services/control-plane/bff/loop_inventory.py
- services/control-plane/bff/main.py
- services/control-plane/bff/test_loop_inventory_read_model_contract.py
- services/control-plane/bff/test_loop_health_read_model_contract.py
- services/control-plane/bff/test_loop_auto_bff004_cross_loop_drill.py
- tests/test_loop_catalog_registry.py

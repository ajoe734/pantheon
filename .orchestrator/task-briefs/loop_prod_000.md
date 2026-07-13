# Task Brief: LOOP-PROD-000

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Canonical loop inventory and OODA overlay truth
- Status: in_progress
- Owner: Antigravity
- Reviewer: Claude
- Next: Review blocked: evidence.json under docs/deployment/evidence/loop-product-level/LOOP-PROD-000/ is stale relative to merged code. It still cites PR #3520 (merged 14:55:57Z) as implementation_delivery.pull_request and lists f2e9d5eb305 as 'local_anchor_pending_follow_up_pr_at_evidence_authoring', but PR #3543 (merge SHA 623bfe7b9, merged 17:54:26Z, includes f2e9d5eb305) and PR #3551 (merge SHA db0e38bdc..b8d0afdd2 lineage, merged 18:38:42Z, fail-closed provenance/typed-envelope hardening) have both since merged to dev and are not reflected anywhere in the manifest, required_checks, or acceptance entries. AC-05/AC-06/AC-09 are still marked pending against former reviewer Codex2 and a 'follow-up PR' that has already landed twice over. Per the evidence_policy fail-closed rule, a stale manifest cannot support a review verdict. Required before re-review: regenerate evidence.json (append-only) to (1) record PR #3543 and #3551 in implementation_delivery/required_checks, (2) re-capture hosted readback against current dev HEAD if AC-06/AC-08 depend on capture freshness, (3) resolve or re-scope AC-05/AC-06/AC-09 against the new reviewer (Claude, not Codex2), and (4) recompute evidence.sha256. Code and tests verified independently in the meantime: registry has exactly 12 canonical loops + 1 composite_overlay (per_persona_ooda, composed_of correct, excludes capital_pool_execution/bff_health_monitoring); docs/05/system-verification-rounds/INDEX.md matches the 12+overlay classification; 40/40 focused pytest pass (test_loop_inventory_read_model_contract.py, test_loop_health_read_model_contract.py, test_loop_catalog_registry.py, test_loop_auto_bff004_cross_loop_drill.py).

## Summary
校正 loop catalog、BFF inventory 與 verification index：維持 12 個 L1 canonical loops，新增 per_persona_ooda 為 composite_overlay 並宣告 composed_of；archived task 不得被投影成 live maturity。

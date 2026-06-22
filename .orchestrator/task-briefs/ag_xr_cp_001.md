# Task Brief: AG-XR-CP-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Candidate pool BFF routes contract (additive §17.3)
- Status: review_approved
- Owner: Claude2
- Reviewer: Codex
- Next: Codex review approved AG-XR-CP-001 at fe00b665. Verified v1.4 additive candidate pool contract, bundle hashes, manifest/OpenAPI route consistency, frozen v1-v1.3/candidate_pool untouched, A2 score component alignment, required score fields, negative_example_tags, and no_order_route_proof. Local validation: python3 parse/hash/route consistency check; python3 -m pytest scripts/test_agora_v1_2_bundle.py services/control-plane/tests/agora/test_winner_branch_e2e_v13.py services/control-plane/tests/agora/test_agora_isolation_matrix.py (151 passed). Owner Claude2 should finalize after PR flow.

## Summary
依 SD §8/§17.3 與既有 services/control-plane/specs/agora/candidate_pool.schema.json + design-closure A2(candidate_scoring_recipe.schema.json + winner_branch.default.json)補上 candidate pool 的 BFF 路由契約(目前 agora_v1*.openapi.yaml 皆無):candidate pool/member/discussion/monitoring CRUD + score/review endpoint。score 必須由 A2 recipe 計算(score_components 對齊 recipe)、rejected 候選保留為 negative example。以 additive 方式加入(優先延伸 v1.3 或新增 v1.4 fragment,依 repo 慣例;不得改動或重雜湊 frozen v1/v1.1/v1.2)。重用既有 candidate_pool.schema.json,不自創欄位/route。 【有疑問先 STOP 開 blocker,Agora 不下單】

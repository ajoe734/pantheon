# Task Brief: AG-XR-002A

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Regenerate execute-plans Agora types to v1.1 + compat manifest frontend half
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: All acceptance criteria met: contract_version 1.1, WidgetSpecV1/V2 both defined, frontend manifest hashes match backend, PR #1952 merged. Returning to Codex for closeout finalization.

## Summary
依 contract-closure(02 schema coexistence + 06 compatibility manifest/hash rules)把 execute-plans 的 Agora 生成型別從 v1 重生為 v1.1:來源為 dev 上的 services/control-plane/openapi/agora_v1_1.openapi.yaml + specs/agora/bundle_index.v1_1.json + specs/agora/v2/*.schema.json。產出 execute-plans/src/lib/bff-v1/agora/types.ts(保留 WidgetSpecV1/WidgetSpecV2 明確命名),更新 contract-drift snapshot 為 v1.1,並填好 docs/contracts/agora/dev-compatibility-manifest.json 的 frontend 半:frontend.generated_from_contract_commit == backend.contract_commit、extension_bundle_index_sha256、base_bundle_index_sha256、openapi_sha256 對齊後端。目的:讓跨 repo deployment/integration gate(目前因前端型別仍 v1 而紅)轉綠,解開 AG-XR-003。frozen v1 不可改、不得擴張 capability。 【有疑問先 STOP 開 blocker,prose 03/04/06 為準,seed yaml 不可照抄】

# Task Brief: AG-XR-OPENAPI-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora OpenAPI v1.1 + capability v1.1 (servant/workshop)
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: All acceptance criteria verified; servant/workshop/adapter/dashboard routes complete, concurrency controls correct, frozen bundle intact, capability guard enforced — review_approved, returning to Claude2 for finalization

## Summary
依 contract-closure 03_servant_and_workshop_contracts.md 撰寫 services/control-plane/openapi/agora_v1_1.openapi.yaml,完整補齊 servant 8 條(含 /servant/ensure /reconcile /sessions* /stream)與 workshop 13 條(含 /workshops CRUD、/messages /events /completeness /versions /versions/{id}/select /research-runs /consultations /conclude /stream),以 prose 03 為準,不可只照 seed yaml 的 24/32。capability_manifest_v1_1 新增 agora.servant.v1(prefix /bff/agora/servant + /api/openclaw-adapter/agents),agora.workshop.v1 加上 /bff/agora/workshops prefix。mutating route 強制 If-Match + Idempotency-Key,衝突回 409 CONCURRENT_MODIFICATION + 現行 ETag。OpenClaw adapter 只擴充既有 service 的 agents/ensure|reconcile,拒絕 runtime-binding/broker-order/capital-binding capability。workshop 持久化表(strategy_workshop_session/event/completeness_snapshot)依 03 定義。 【有疑問一定要提出,不要自己亂做】動工前先讀完 contract-closure 文件(docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/ 01-07 + v2 schema + ARCHIVE_NOTES.md)與 frozen 基線(services/control-plane/specs/agora/ + openapi/agora_v1.openapi.yaml + bundle_index.json)。prose 03/04 為合約權威,agora_openapi_extension_v1_1.yaml 只是 seed(24/32 route)不可照抄當完整契約。只要遇到任何疑問、設計沒寫到、與既有 code 對不上、依賴不清或衝突,一律 STOP 開 blocker 等澄清,不可臆測、補洞、繞過。鐵律:不得改動 frozen AG-XR-001 檔案、不得讓其 bundle_index.json sha256 失效、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

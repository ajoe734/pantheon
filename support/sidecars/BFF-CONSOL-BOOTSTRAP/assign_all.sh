#!/usr/bin/env bash
# BFF Consolidation 27 task block bootstrap.
# Reviewed in ai-status.json under sprint 2026-05-13-ep5-qlib-bff-consolidation.
set -euo pipefail

cd "$(dirname "$0")/../../.."

export AI_NAME=Claude
export TASK_PHASE="BFF Consolidation 2026-05-13"

CLI=./scripts/ai_status.py

# ----- Wave 1 — Ground truth & spec (Day 0–5) -----

TASK_SUMMARY_ZH="沿用 services/control-plane/bff/contract_snapshots/execute_plans_bff_contract.py 的 app_route_index 邏輯 寫 scripts/bff_route_manifest_backend.py 輸出 backend manifest JSON：列出所有 FastAPI registered routes (method/path/family/status: implemented|alias|superseded|deferred) 與 frontend manifest 對齊用。輸出 services/control-plane/bff/contract_snapshots/backend_routes_manifest.json 與 dump CLI。" \
TASK_DEPENDS_ON="" \
TASK_ARTIFACTS="scripts/bff_route_manifest_backend.py,services/control-plane/bff/contract_snapshots/backend_routes_manifest.json" \
TASK_ACCEPTANCE="manifest JSON 含所有 /bff/* 與 /health route,每筆有 method+path+family+status,FastAPI dynamic param 用 {param} 形式 normalize,alias/superseded/deferred 三種狀態被顯式記錄,腳本可被 CI 與 pytest 重跑產出穩定 output,不修改任何現有 BFF runtime code" \
$CLI assign BFF-CONSOL-001 Codex Claude "Backend FastAPI route manifest extractor"

TASK_SUMMARY_ZH="在 execute-plans repo 加 scripts/bff_route_manifest_frontend.ts 掃 src/lib/bff-v1/paths.ts、src/lib/bff/client.ts MANAGEMENT_FAMILIES、src/lib/bff/v5.ts、src/lib/bff/agora.ts、src/lib/bff/runAction.ts、src/lib/bff-v1/sse/liveSse.ts 輸出 frontend manifest JSON (method/path/family/caller_file/mode: live_required|mock_only|hybrid)。Pantheon 端可從 sibling repo 讀此 manifest。" \
TASK_DEPENDS_ON="" \
TASK_ARTIFACTS="execute-plans/scripts/bff_route_manifest_frontend.ts,execute-plans/contract_snapshots/frontend_routes_manifest.json" \
TASK_ACCEPTANCE="manifest 涵蓋上述 6 個 caller source,每筆有 method/path/family/caller_file/mode,動態 path segment 用 {param} normalize,腳本可在 execute-plans CI 重跑,Pantheon route diff 可消費此 JSON,不更動 runtime 行為" \
$CLI assign BFF-CONSOL-002 Codex2 Claude "Frontend route manifest extractor (execute-plans)"

TASK_SUMMARY_ZH="寫 scripts/bff_route_diff.py 比對 backend manifest vs frontend manifest 起步 fail-but-warn：frontend 有 backend 沒有→fail (除非標 mock_only/deferred);backend 有 frontend 沒有→warn;命名 mismatch→fail。整合進 pytest harness 與 GitHub Actions。輸出 docs/bff/contract_snapshots/route-diff-baseline.json。" \
TASK_DEPENDS_ON="BFF-CONSOL-001,BFF-CONSOL-002" \
TASK_ARTIFACTS="scripts/bff_route_diff.py,docs/bff/contract_snapshots/route-diff-baseline.json,.github/workflows/bff-route-diff.yml" \
TASK_ACCEPTANCE="diff CLI 在 frontend↔backend 不對齊時非 0 退出,pytest harness 可呼叫 diff 並 assert baseline 一致,fail-but-warn 模式允許 warn-only 通過,baseline JSON 落入 contract_snapshots 供後續 026 切 fail-hard 使用,GitHub Actions 在 PR 上會掛 diff 結果" \
$CLI assign BFF-CONSOL-003 Gemini Codex "CI route diff job (fail-but-warn baseline)"

TASK_SUMMARY_ZH="把所有 /bff/actions/{entityType}/{entityId}/{actionId} route 對應到 /bff/v1/commands 的 mapping 表寫進 services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md §Command Adapter Mapping 與 CANONICAL_CONTRACT_MIGRATION_DECISION.md。每筆要含 action_id/target_type/command_name/idempotency_key_template/actor_source/trace_propagation/audit_event/policy_check。spec-only 不動 runtime。" \
TASK_DEPENDS_ON="" \
TASK_ARTIFACTS="services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md,CANONICAL_CONTRACT_MIGRATION_DECISION.md" \
TASK_ACCEPTANCE="mapping 表覆蓋現有 runAction.ts 所有 entityType (strategy/persona/deployment/approval/runtime/incident/v5-intervention 等),每筆有 8 個 metadata 欄位,idempotency key template 對齊 BFF_COMMAND_API_CONTRACT,audit_event 名稱對齊現有 audit pipeline,migration decision 文件加上 actions→commands 收斂的時間表,不修改任何 runtime code" \
$CLI assign BFF-CONSOL-004 Claude Codex "Command envelope mapping spec doc"

TASK_SUMMARY_ZH="在 execute-plans Management Console / Agora / v5 page header 加即時 live status banner 呼叫 getLiveStatusSnapshot() 顯示 real|hybrid|mock-fallback 三種狀態。hybrid/mock 顯示「資料來源：seed」警告。auto fallback 模式下 operator 不會誤判 seed 為 live。" \
TASK_DEPENDS_ON="" \
TASK_ARTIFACTS="execute-plans/src/components/layout/LiveStatusBanner.tsx,execute-plans/src/lib/bff/liveTransport.ts" \
TASK_ACCEPTANCE="banner 在 Management Console/Agora/v5 三套 page 顯示,real 狀態 hide/dim 顯示,hybrid/mock 顯示警告文字與顏色,getLiveStatusSnapshot() 結果即時反映 transport mode,strict mode 下也正確顯示 typed-error 狀態,Lovable preview build 確認 banner 不會 layout break" \
$CLI assign BFF-CONSOL-005 Claude2 Codex "Live status banner UI (real/hybrid/mock)"

TASK_SUMMARY_ZH="寫一份 doc 對齊 backend role vocabulary (viewer/operator/approver/admin) vs frontend mock (portfolio_manager/ops) vs MeResponse.roles 對應。輸出 docs/bff/role-vocabulary-mapping-2026-05-13.md。供 BFF-CONSOL-013 cookie write gate 與後續 capability map 使用。" \
TASK_DEPENDS_ON="" \
TASK_ARTIFACTS="docs/bff/role-vocabulary-mapping-2026-05-13.md" \
TASK_ACCEPTANCE="表格列出 backend canonical role,表格列出 frontend mock role,對應關係明確,涵蓋 MeResponse.roles 預期輸出,標出哪些 frontend-only role 屬於 deprecated,doc 在 PR review 中由 Claude 簽核" \
$CLI assign BFF-CONSOL-006 Copilot Claude "Role vocabulary mapping doc"

TASK_SUMMARY_ZH="盤點 execute-plans src/lib/bff/seed.ts 全 helper 分四類 live_required|mock_only_dev|deprecated|deferred。輸出 docs/bff/seed-taxonomy-2026-05-13.md 與 seed-taxonomy.json 供 015 mock-only badge 與 025 seed elimination 使用。" \
TASK_DEPENDS_ON="" \
TASK_ARTIFACTS="docs/bff/seed-taxonomy-2026-05-13.md,docs/bff/seed-taxonomy.json" \
TASK_ACCEPTANCE="seed.ts 所有 helper 都被分類,每個 helper 標 live_required|mock_only_dev|deprecated|deferred,deferred 註明對應 follow-up task,taxonomy JSON 可被前端腳本 import,doc 提供 elimination 優先順序,Claude2 簽核 taxonomy 為下游消費基準" \
$CLI assign BFF-CONSOL-007 Copilot Claude2 "Seed taxonomy spreadsheet"

# ----- Wave 2 — Read depth & realtime & auth (Day 5–12) -----

TASK_SUMMARY_ZH="在 pantheon control-plane truth source 建立 canonical fixture pack A (strategies/personas/capital-pools/rebalances/deployments) 各 ≥1 筆 non-empty entry。Strategy 含 specs/experiments/artifacts/lineage/audit linkage。確保 /bff/strategies /bff/personas /bff/capital-pools /bff/rebalances /bff/deployments live list 都回 data_count≥1。" \
TASK_DEPENDS_ON="BFF-CONSOL-001" \
TASK_ARTIFACTS="services/control-plane/bff/data/fixtures_pack_a.json,services/control-plane/bff/read_store.py" \
TASK_ACCEPTANCE="5 個 family 各 ≥1 non-empty fixture,strategy 連結 specs/experiments/artifacts/lineage/audit,persona 含 route-policy 與 evaluation skeleton,deployment 含 stages 與 approval pointer,authenticated live smoke 跑 data_count 都 ≥1,fixture 不可影響 EP5 paper-canary truth" \
$CLI assign BFF-CONSOL-008 Codex Claude "Canonical fixture pack A (strategies personas capital-pools rebalances deployments)"

TASK_SUMMARY_ZH="canonical fixture pack B (evolution/research/artifacts/v5 interventions/agora/runtimes) 各 ≥1 筆 non-empty。v5 intervention 對應 governed remediation flow;agora 至少有一個 active topic;research 含 ticket 與 analysis sample。" \
TASK_DEPENDS_ON="BFF-CONSOL-002" \
TASK_ARTIFACTS="services/control-plane/bff/data/fixtures_pack_b.json" \
TASK_ACCEPTANCE="6 個 family 各 ≥1 non-empty fixture,v5 intervention 連結 governed remediation skeleton,agora topic 可被 EventSource subscribe,research ticket 連 analysis,runtime fixture 對應 paper-canary 但 fail-closed,authenticated live smoke 跑 data_count 都 ≥1" \
$CLI assign BFF-CONSOL-009 Codex2 Claude2 "Canonical fixture pack B (evolution research artifacts v5 agora runtimes)"

TASK_SUMMARY_ZH="canonical fixture pack C (alerts/incidents/approvals/audit/jobs/channels/skills/tools/mcp) 各 ≥1 筆 non-empty。alert 對 incident;approval 連 deployment;audit 包含至少一個 immutable record。" \
TASK_DEPENDS_ON="BFF-CONSOL-008" \
TASK_ARTIFACTS="services/control-plane/bff/data/fixtures_pack_c.json" \
TASK_ACCEPTANCE="9 個 family 各 ≥1 non-empty fixture,alert 連 incident,approval 連 deployment 或 v5 intervention,audit 含 immutable record sample,jobs fixture 不再回 undefined detail,channels 列舉與 SSE topic 對齊,authenticated live smoke data_count 都 ≥1" \
$CLI assign BFF-CONSOL-010 Codex Claude2 "Canonical fixture pack C (alerts incidents approvals audit jobs channels skills tools mcp)"

TASK_SUMMARY_ZH="真測 /bff/events/stream。Browser cookie-session 用 native EventSource(... {withCredentials:true}) Bearer 用 polyfill 帶 Authorization。驗證 open event/first event id/type/timestamp/Last-Event-ID replay/409 SSE_REPLAY_UNAVAILABLE/X-SSE-Resync-Routes。Evidence: support/evidence/BFF-CONSOL-011-sse-replay-smoke.json。" \
TASK_DEPENDS_ON="" \
TASK_ARTIFACTS="scripts/probe_bff_sse_stream.py,support/evidence/BFF-CONSOL-011-sse-replay-smoke.json" \
TASK_ACCEPTANCE="cookie-session probe 成功 open EventSource,Bearer probe 成功 open EventSource,first event 含 id/type/timestamp,reconnect 帶 Last-Event-ID 觸發 replay 或 409,409 同時回 X-SSE-Resync-Routes header,evidence JSON 紀錄完整 transcript,mock generator 在 live mode 必須關閉" \
$CLI assign BFF-CONSOL-011 Gemini Codex "SSE real stream replay test"

TASK_SUMMARY_ZH="client disconnect 後 backend 不該 unbounded buffer。對齊 EVENT_ORDERING_AND_DELIVERY_GUARANTEES.md 的 ordering 規定。Test: simulate slow consumer + disconnect assert backend buffer bound + replay window policy + drop strategy。" \
TASK_DEPENDS_ON="BFF-CONSOL-011" \
TASK_ARTIFACTS="services/control-plane/bff/tests/test_sse_backpressure.py,support/evidence/BFF-CONSOL-012-sse-backpressure.json" \
TASK_ACCEPTANCE="backend buffer 有 explicit upper bound,slow consumer 不會 OOM,disconnect 後 buffer 在 N 秒內回收,replay window 與 ordering policy 對齊文件,drop strategy 明確 (oldest|newest|reject),evidence 包含 measured buffer high-water mark" \
$CLI assign BFF-CONSOL-012 Gemini Codex2 "SSE backpressure & unbounded buffer test"

TASK_SUMMARY_ZH="修改 execute-plans liveWriteGated() 從 /bff/me response 判斷 authenticated session (cookie or bearer) 不再只看 sessionStorage bearer token。cookie-only session 不應被誤判為 unauthenticated。後端 /bff/me 需回明確 session_kind: cookie|bearer|stub。" \
TASK_DEPENDS_ON="BFF-CONSOL-004,BFF-CONSOL-006" \
TASK_ARTIFACTS="execute-plans/src/lib/bff/runAction.ts,services/control-plane/bff/main.py" \
TASK_ACCEPTANCE="/bff/me response 含 session_kind 欄位,liveWriteGated() 不再只看 sessionStorage,cookie-only session 寫入 gate 通過,Bearer session 寫入 gate 通過,stub session 在 production 模式被擋,unit test cover 三種 session 模式" \
$CLI assign BFF-CONSOL-013 Claude Codex "Cookie-session write gate (/bff/me driven)"

TASK_SUMMARY_ZH="pantheon BFF main.py 加 PANTHEON_BFF_CORS_ORIGINS 預設 include Lovable preview/dev/prod hostnames。JWKS strict mode 在 CI 跑 issuer/audience/key-rotation test。CORS dev override 在 production strict 模式下不可啟用。" \
TASK_DEPENDS_ON="" \
TASK_ARTIFACTS="services/control-plane/bff/main.py,services/control-plane/bff/tests/test_auth_jwks_strict.py" \
TASK_ACCEPTANCE="CORS origin allowlist 含 Lovable preview+dev+prod hostnames,strict mode 拒絕未列名 origin,JWKS issuer 驗證在 CI pass,JWKS audience 驗證在 CI pass,key rotation test 模擬 kid 更換,dev stub 在 strict mode 自動 disable" \
$CLI assign BFF-CONSOL-014 Gemini2 Gemini "Lovable CORS allowlist + JWKS strict test infra"

TASK_SUMMARY_ZH="live mode 下若頁面 fetch 失敗回 seed-only helper UI 自動掛 mock data badge;mock_only_dev helper 在 live mode 直接禁用 (raise 或 return null + 顯示空狀態)。讀 BFF-CONSOL-007 taxonomy 作為分類來源。" \
TASK_DEPENDS_ON="BFF-CONSOL-005,BFF-CONSOL-007" \
TASK_ARTIFACTS="execute-plans/src/components/data/MockDataBadge.tsx,execute-plans/src/lib/bff/seed.ts" \
TASK_ACCEPTANCE="badge 在 live mode 顯示 mock 狀態,mock_only_dev helper 在 live mode 不再回 seed,deferred helper 顯示明確空狀態,live_required helper 不掛 badge,taxonomy JSON 變更後 badge 行為跟著動,Copilot 簽核 badge 行為對齊 taxonomy" \
$CLI assign BFF-CONSOL-015 Claude2 Copilot "Mock-only badge implementation (live mode)"

# ----- Wave 3 — Detail journey & command adapter (Day 12–19) -----

TASK_SUMMARY_ZH="detail journey smoke A: strategy/persona/deployment/runtime 各跑 list→detail→related tabs。每個 family ≥1 non-empty fixture (來自 008) detail drawer 渲染 tabs 切換 degraded path 都跑過。Evidence support/evidence/BFF-CONSOL-016-detail-smoke-a.json。" \
TASK_DEPENDS_ON="BFF-CONSOL-008" \
TASK_ARTIFACTS="execute-plans/tests/e2e/detail-smoke-a.spec.ts,support/evidence/BFF-CONSOL-016-detail-smoke-a.json" \
TASK_ACCEPTANCE="4 個 family detail route 都回 2xx 或 typed 404,related tabs 渲染含 ≥1 non-empty entry,degraded path 顯示 typed error 而非 raw 500,strategy detail 連結 specs/experiments/artifacts/lineage/audit,persona detail 顯示 route-policy 與 activity,evidence JSON 含每個 family 的 route 與 status" \
$CLI assign BFF-CONSOL-016 Claude Codex "Detail journey smoke A (strategy persona deployment runtime)"

TASK_SUMMARY_ZH="detail journey smoke B: evolution/research/v5/agora/artifacts list→detail→related tabs。每個 family 走過 live detail 而非 mock。" \
TASK_DEPENDS_ON="BFF-CONSOL-009" \
TASK_ARTIFACTS="execute-plans/tests/e2e/detail-smoke-b.spec.ts,support/evidence/BFF-CONSOL-017-detail-smoke-b.json" \
TASK_ACCEPTANCE="5 個 family detail route 都回 2xx 或 typed 404,v5 intervention detail 含 governed remediation skeleton,agora detail 顯示 topic 內歷史 event,research detail 連 analysis,artifacts detail 含 lineage,evidence JSON 含每個 family transcript" \
$CLI assign BFF-CONSOL-017 Claude2 Codex2 "Detail journey smoke B (evolution research v5 agora artifacts)"

TASK_SUMMARY_ZH="detail journey smoke C: incident/approval/rebalance/job/audit。job mock fallback undefined 改成 typed 404;audit list-only 特判 detail drawer disabled。" \
TASK_DEPENDS_ON="BFF-CONSOL-010" \
TASK_ARTIFACTS="execute-plans/tests/e2e/detail-smoke-c.spec.ts,support/evidence/BFF-CONSOL-018-detail-smoke-c.json" \
TASK_ACCEPTANCE="5 個 family detail route 都回 2xx 或 typed 404,job detail 不再回 undefined,audit detail drawer 在 UI 上 disabled 並顯示 list-only 說明,incident detail 顯示 runtime 狀態與 rollback option,approval detail 顯示 deployment 連結,evidence JSON 含每個 family transcript" \
$CLI assign BFF-CONSOL-018 Claude Codex2 "Detail journey smoke C (incident approval rebalance job audit)"

TASK_SUMMARY_ZH="後端 /bff/actions/* 在 BFF 內轉成 /bff/v1/commands admission：actor from auth/Idempotency-Key/trace_id+correlation_id/policy decision/audit action/target typed reference。**不可在 EP5 paper-canary closeout 之前 merge runtime change**;PR 準備好但 hold 在 review 直到 EP5 closeout signal。Reviewer Claude 在 EP5 closeout 後才會 approve。" \
TASK_DEPENDS_ON="BFF-CONSOL-004" \
TASK_ARTIFACTS="services/control-plane/bff/command_executor.py,services/control-plane/bff/main.py,services/control-plane/bff/tests/test_actions_to_commands_adapter.py" \
TASK_ACCEPTANCE="/bff/actions/* 不再直接 mutate state 改走 command admission,actor 從 auth 取得,Idempotency-Key 強制需要,trace_id+correlation_id 透傳,policy decision 寫入 audit,target typed reference 對齊 BFF_COMMAND_API_CONTRACT,EP5 paper-canary closeout 之前禁止 merge runtime change" \
$CLI assign BFF-CONSOL-019 Codex Claude "Command envelope adapter backend impl (gated on EP5 closeout)"

TASK_SUMMARY_ZH="execute-plans runAction.ts 新 caller 優先發 /bff/v1/commands;舊 caller 維持 /bff/actions/* 但 BFF adapter 轉發;兩條都回同一 CommandResponse shape。confirmToken/approval evidence/typed error 行為對齊 BFF_COMMAND_API_CONTRACT。" \
TASK_DEPENDS_ON="BFF-CONSOL-019" \
TASK_ARTIFACTS="execute-plans/src/lib/bff/runAction.ts,execute-plans/src/lib/bff/commandClient.ts" \
TASK_ACCEPTANCE="runAction.ts 暴露 commandClient 新 caller,新 caller 直接打 /bff/v1/commands,舊 caller 仍 work 透過 BFF adapter,兩種 response shape 對齊 CommandResponse,confirmToken 從 high-risk modal 正確傳入,unit test cover 新舊兩條 path" \
$CLI assign BFF-CONSOL-020 Codex2 Claude2 "runAction.ts migration to /bff/v1/commands"

TASK_SUMMARY_ZH="舊 action receipt + 新 command receipt 並存。Test cases: same idempotency + same body→replay;same idempotency + diff body→409;missing confirm token→CONFIRM_TOKEN_REQUIRED;missing approval evidence→APPROVAL_REQUIRED。驗證通過後立即啟動 024，後續 regression 追蹤不阻塞派工。" \
TASK_DEPENDS_ON="BFF-CONSOL-019,BFF-CONSOL-020" \
TASK_ARTIFACTS="services/control-plane/bff/tests/test_command_replay_conflict.py,support/evidence/BFF-CONSOL-021-dual-write-soak.json" \
TASK_ACCEPTANCE="receipt dual-write log 同時含 action 與 command receipt,replay test 通過,idempotency conflict test 回 409,missing confirm token 回 CONFIRM_TOKEN_REQUIRED,missing approval evidence 回 APPROVAL_REQUIRED,dual-write regression follow-up 已拆成非阻塞追蹤，不再要求固定 7 day soak gate" \
$CLI assign BFF-CONSOL-021 Codex Claude "Receipt dual-write + replay/conflict/idempotency tests"

TASK_SUMMARY_ZH="開 Lovable preview branch 設 VITE_BFF_MODE=live + VITE_BFF_FALLBACK=strict + VITE_BFF_REAL_WRITES=false。現有 staging 維持 auto fallback 不切。用 strict mode read/SSE/detail journey regression evidence 決定是否推進，不再用固定天數 gate。" \
TASK_DEPENDS_ON="BFF-CONSOL-008,BFF-CONSOL-009,BFF-CONSOL-010,BFF-CONSOL-015" \
TASK_ARTIFACTS="execute-plans/.lovable/preview-strict.env,support/evidence/BFF-CONSOL-022-staging-strict-soak.md" \
TASK_ACCEPTANCE="preview branch 切 strict 模式運行,REAL_WRITES 維持 false,現有 staging 保留 auto fallback,strict mode regression evidence 完成,SSE replay/read smoke 都 pass,evidence 紀錄 strict 下無 fallback regression" \
$CLI assign BFF-CONSOL-022 Gemini2 Gemini "Lovable staging strict cutover (isolated preview branch)"

# ----- Wave 4 — Cutover & cleanup (Day 19–24) -----

TASK_SUMMARY_ZH="等 022 staging strict verification 0 regression 後 prod 切 VITE_BFF_FALLBACK=strict (REAL_WRITES 仍 false 直到 operator onboard)。Prod cutover 以 smoke/regression evidence 完成，不再用固定天數 gate。" \
TASK_DEPENDS_ON="BFF-CONSOL-022" \
TASK_ARTIFACTS="execute-plans/.lovable/prod-strict.env,support/evidence/BFF-CONSOL-023-prod-strict-soak.md" \
TASK_ACCEPTANCE="prod 切 strict 模式運行,REAL_WRITES 仍 false,prod smoke/regression evidence 完成,operator 體感 0 regression,SSE/read smoke 在 prod 都 pass,evidence 紀錄 cutover 過程與監控指標" \
$CLI assign BFF-CONSOL-023 Gemini2 Gemini "Lovable prod strict cutover (staging verification gate)"

TASK_SUMMARY_ZH="021 dual-write 驗證通過後標 deprecated 保留 /bff/actions/* 路徑但 receipt schema 加 deprecated flag。前端 runAction.ts 預設改打 /bff/v1/commands。" \
TASK_DEPENDS_ON="BFF-CONSOL-021" \
TASK_ARTIFACTS="services/control-plane/bff/command_executor.py,services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md" \
TASK_ACCEPTANCE="舊 action receipt schema 加 deprecated flag,前端 default caller 改為 /bff/v1/commands,/bff/actions/* 仍可運作但加 deprecation warning header,audit 工具 confirm 已 consume 新 receipt,contract doc 更新 deprecation 時間表" \
$CLI assign BFF-CONSOL-024 Codex Claude "Deprecate old action receipt"

TASK_SUMMARY_ZH="用 007 taxonomy 與 016/017/018 detail evidence 把 live_required helper 全部接上 BFF route mock_only_dev 在 live mode 隱藏 deprecated 移除 deferred 寫進 follow-up task。strict live mode 下沒有頁面會暗中用 seed 當 live 資料。" \
TASK_DEPENDS_ON="BFF-CONSOL-015,BFF-CONSOL-016,BFF-CONSOL-017,BFF-CONSOL-018" \
TASK_ARTIFACTS="execute-plans/src/lib/bff/seed.ts,docs/bff/seed-elimination-2026-05-13.md" \
TASK_ACCEPTANCE="live_required helper 全部對應到實際 BFF route,mock_only_dev helper 在 live mode 隱藏,deprecated helper 從 seed.ts 移除,deferred helper 對應到新 follow-up task,strict live mode 0 seed misuse,Copilot 簽核 elimination 完成" \
$CLI assign BFF-CONSOL-025 Claude2 Copilot "Seed-only surface elimination"

TASK_SUMMARY_ZH="把 003 的 fail-but-warn 模式切 fail-hard。任何 unmatched route 阻擋 PR merge。CI 必須在 backend manifest 加新 route 後 frontend 也加才 pass;反向亦然 (除非標 mock_only)。" \
TASK_DEPENDS_ON="BFF-CONSOL-001,BFF-CONSOL-002,BFF-CONSOL-003" \
TASK_ARTIFACTS="scripts/bff_route_diff.py,.github/workflows/bff-route-diff.yml" \
TASK_ACCEPTANCE="diff CLI 切 fail-hard 模式,PR 中任何 unmatched route 都會 block merge,mock_only 與 deferred 仍允許 unmatched,baseline 鎖定後續 route 變更必須同步,文件更新 fail-hard 切換時間表" \
$CLI assign BFF-CONSOL-026 Gemini Codex "CI route diff fail-hard mode"

TASK_SUMMARY_ZH="集合 001..026 evidence 輸出 support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md。內容含 contract diff baseline/live smoke (read+write)/SSE evidence/command receipt sample/staging+prod cutover log/regression follow-up/seed.ts post-state。Copilot 統整 Claude 最終簽核。" \
TASK_DEPENDS_ON="BFF-CONSOL-001,BFF-CONSOL-002,BFF-CONSOL-003,BFF-CONSOL-004,BFF-CONSOL-005,BFF-CONSOL-006,BFF-CONSOL-007,BFF-CONSOL-008,BFF-CONSOL-009,BFF-CONSOL-010,BFF-CONSOL-011,BFF-CONSOL-012,BFF-CONSOL-013,BFF-CONSOL-014,BFF-CONSOL-015,BFF-CONSOL-016,BFF-CONSOL-017,BFF-CONSOL-018,BFF-CONSOL-019,BFF-CONSOL-020,BFF-CONSOL-021,BFF-CONSOL-022,BFF-CONSOL-023,BFF-CONSOL-024,BFF-CONSOL-025,BFF-CONSOL-026" \
TASK_ARTIFACTS="support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md" \
TASK_ACCEPTANCE="acceptance packet 含 contract diff baseline,acceptance packet 含 read+write live smoke,acceptance packet 含 SSE evidence,acceptance packet 含 command receipt sample,acceptance packet 含 staging+prod cutover log,acceptance packet 含 regression follow-up 狀態,acceptance packet 含 seed.ts post-state,Claude 最終簽核" \
$CLI assign BFF-CONSOL-027 Copilot Claude "Final BFF consolidation acceptance packet"

echo "All 27 BFF-CONSOL tasks assigned."

# OSS-001 審查核准（Codex）

**任務**: `OSS-001`  
**作者**: Qwen  
**審查者**: Codex  
**狀態**: APPROVED  
**日期**: 2026-04-10

## 結論

這版可以核准。前一輪退回的兩個 blocker 都已收斂，`integrations/openclaw/` 這組文件現在已和 repo 內的 canonical `StrategySpec` / `WorkflowHandoff` 邊界一致。

## 核准依據

1. `integrations/openclaw/smoke_test.md` 的 Step 4/5 已改成先產生 canonical `StrategySpec`，再包成 canonical `WorkflowHandoff`。
   `StrategySpec` 現在完整包含 `spec_version`、`strategy_id`、`title`、`hypothesis`、`objective`、`market_scope`、`data_dependencies`、`execution_profile`、`evaluation_plan`、`governance`、`provenance`，不再把 `registry_hints` / `governance_context` 混進 spec 物件。
2. `integrations/openclaw/governance.md` §5.2 已改回 OC-003 的正式分界：
   `registry_hints`、`governance_context` 與 handoff `provenance` 屬於 `WorkflowHandoff`；
   `StrategySpec` 只保留自己的 `governance` 與 `provenance`。
   欄位名稱也已對齊為 `registry_hints.initial_lifecycle_state`。
3. pin 與邊界文件彼此一致：
   `integrations/openclaw/integration.md`、`spikes/openclaw_upstream_selection.md` 都一致鎖定 upstream `openclaw/openclaw`、tag `v2026.4.7`、SHA `5050017`，且 adapter seam / smoke-test 路徑都已寫明，符合 `OSS-001` 的三個 acceptance criteria。

## 驗證

- 逐項比對 `integrations/openclaw/governance.md` 與 `services/control-plane/specs/strategy_spec.schema.json`
- 逐項比對 `integrations/openclaw/governance.md` 與 `services/control-plane/specs/workflow_handoff.schema.json`
- 逐項比對 `integrations/openclaw/smoke_test.md` 與 `services/control-plane/specs/contract.md`
- 以最小 `raw_handoff.json` 實際執行 `smoke_test.md` Step 4/5 的 reference payload 建構與 schema 驗證
  - `StrategySpec` 驗證通過
  - `WorkflowHandoff` 驗證通過
  - `smoke_test.md` 中獨立 Step 5 與完整 smoke script 的驗證片段都可通過

## Non-blocking Follow-up

- `integrations/openclaw/smoke_test.md` 仍同時出現 `/tmp/openclaw-smoke` 與 `/tmp/openclaw-smoke-test` 兩個 workspace 路徑範例。這不影響 schema 邊界與 task acceptance，但後續若要把文件直接轉成可複製執行腳本，建議收斂成單一路徑。

## 結果

`OSS-001` 已完成本輪目標：upstream source 已選定並 pin，governed adapter boundary 已記錄，smoke-test plan 也已對齊 canonical object model，可進入 `review_approved`。

# Persona Trade Journal Execution Tasks

來源：`docs/04/persona_trade_journal_gap_2026-07-11/PERSONA_TRADE_JOURNAL_GAP.md`  
狀態：可交付 fleets；不包含 live broker side effect

## 執行波次

| Wave | Task | Owner | Reviewer | Depends on | 交付 |
|---|---|---|---|---|---|
| 0 | `PTJ-001` | Antigravity | Codex2 | - | contract、identity、state machine、schema |
| 1 | `PTJ-002` | Gemini2 | Claude2 | PTJ-001 | telemetry/lineage episode projection 與 replay |
| 1 | `PTJ-003` | Claude | Codex2 | PTJ-001 | reflection worker、facts snapshot、lesson candidates |
| 2 | `PTJ-004` | Codex2 | Claude2 | PTJ-002,003 | BFF journal/reflection/pattern APIs 與 RBAC/audit |
| 2 | `PTJ-005` | Gemini | Codex2 | PTJ-003 | memory governance 與 evaluation gates |
| 3 | `PTJ-006` | Antigravity2 | Codex2 | PTJ-004 | `execute-plans` Trade Journal UX |
| 4 | `PTJ-007` | Codex | Human/Ops | PTJ-002..006 | replay、cross-repo、hosted dev acceptance 與 closeout |

## Task details

### PTJ-001 — Contract and schema lock

建立 versioned `TradeEpisodeProjection`、`PersonaTradeReflection`、lesson/event contracts，鎖定
episode identity、reversal/partial-fill/force-close 規則、truth ownership 與 migration strategy。

驗收：schema fixtures 與 contract tests；禁止 BFF 成為 order/fill/P&L truth；歷史 unresolved join
有明確狀態；文件同步 canonical lineage/persona/runtime boundaries。

### PTJ-002 — Episode projection and lineage replay

從 decision/approval/order/fill/position/attribution canonical sources 建立可重建 projection；支援
duplicate、late、out-of-order、correction、partial fill、scale、reversal、manual/risk exit。

驗收：golden replay cases；coverage/missing refs；cursor pagination；source/as-of；不以時間猜測硬 join。

### PTJ-003 — Reflection pipeline

實作 fill review、closed-episode reflection、scheduled pattern review；facts snapshot hash、provider/
prompt/version、retry/DLQ、unknown/counterfactual 標示與 hindsight-bias 防線。

驗收：無 facts 不生成結論；partial reflection 誠實標示；單筆只能提 lesson candidate；worker
無 broker 或 policy mutation authority。

### PTJ-004 — BFF API and governed commands

實作 journal list/detail、reflection inbox、pattern read APIs，以及 retry/submit-review/decide commands。
加入 cursor/filter、RBAC、environment isolation、masking、idempotency、audit receipt、source confidence。

驗收：normal/partial/degraded/unavailable、401/403、cross-persona denial、duplicate POST、downstream
unavailable 與 pagination contract tests。

### PTJ-005 — Memory and evaluation governance

把 lesson candidate 接入 persona memory review；建立 proposed/pending/endorsed/merged/quarantined/
expired lifecycle。涉及 policy/risk/capital/live 的候選必須走 evaluation/approval/deployment。

驗收：單筆 lesson 無法直改 persona；樣本/跨 regime/evaluation gate；receipt 與 reflection/version
可回溯；paper/canary/live promotion fail closed。

### PTJ-006 — Frontend Trade Journal

在 `ajoe734/execute-plans` Persona detail 建立 Trade Journal tab、episode list/detail、timeline、
reflection inbox、pattern view、coverage/unknown UI 與跨頁 deep links；desktop/mobile 與 strict-live。

驗收：不顯示 NaN/臆測理由；environment 常駐；loading/error/empty/degraded；filters/pagination；
Playwright 覆蓋完整 paper、missing refs、force close、reflection pending/failed。

### PTJ-007 — Integration and hosted closeout

用 deterministic paper fixtures 驗證 decision → order/fill → attribution → reflection → lesson review；
完成 cross-repo contract、replay/load/security、dev BFF/FE hosted smoke 與證據歸檔。

驗收：所有 child PR merged 或明確 superseded；列 PR/merge SHA/deploy target；SLO/alerts；無 live
orders；residual gaps 有 owner，不以 local-only 或 mock 宣稱完成。

## Fleet guardrails

- Frontend 工作只在 `ajoe734/execute-plans`，不得建立 Pantheon 內嵌 frontend checkout。
- Backend/BFF 工作在 `ajoe734/pantheon` clean task worktree；各 task 自己走 PR/check/merge。
- `PTJ-007` 前不得宣稱 end-to-end 完成。
- 所有 execution tasks 都禁止 live order；任何 live proof 必須另開 human-gated packet。
- 下游 worker 先讀 gap spec 與本 INDEX，且不得擴張 canonical authority。


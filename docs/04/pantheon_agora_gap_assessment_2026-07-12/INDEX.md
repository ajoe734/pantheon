# Agora 操作系統實作現況 vs 文件差異評估 — 2026-07-12

Status: assessment archive（盤點基準）。
Execution packet: `docs/bff/execution-tasks/2026-07-12-agora-gap-closure/INDEX.md`（AG-GAP-001~013）。

本文件是 2026-07-12 對 Agora 全系統（設計文件、後端、前端、派工歷史、dev live 環境）的
交叉盤點結論，作為 AG-GAP 收斂波的 source of truth。調查方法：四路獨立審計
（docs / backend / frontend / task-archive）+ dev live 端點實測。

## 總評

Agora 從 6/20 SA/SD 起手到 7/8，合約層與前端動態 UI 已扎實落地，三個 tab
（Trading Room / Strategy Workshop / Performance）已通過 fixture-free hosted
production gate（AG-DYNUI-LIVE-TABS-GATE-011）。但後端是「合約完整、邏輯完整、
持久化空心」：除 strategy_workshop 有可選 Postgres store 外，其餘 domain 全是
in-memory 或單一 JSON 檔（`read_surfaces.json`），重啟即失。OpenAPI 對外承諾的
部分路由（workshop versions/consultations/conclude、servant reconcile）在後端仍是
501 stub；trading-room SSE 是空殼；私有內容 ref 是 `priv-content-stub://` 佔位。

## 分層現況

### 文件/合約層
- 設計軌跡完整：SD(6/20) → Design Closure → Round2 v1.3 → DynUI pack(6/28) →
  production gap/recovery(7/3, 7/5) → live 收尾(7/6–7/8)。
- Schema bundle 嚴格 additive + byte-level hash，v1→v1.5 共 6 版。
- 結構性混亂：bundle 版本與 specs 目錄版本雙軌（v1.3 → `specs/agora/v4/`、
  v1.1 → `v2/`）；`SD_2026-06-20.md` 自稱 canonical 但實為 stub（缺 §7.x/§11.x/§12/§24.3/§26）。

### 後端（`services/control-plane/bff/agora/`）
| Domain | 成熟度 | 持久化 |
|---|---|---|
| strategy_workshop | 最成熟；versions/select/research-runs/consultations/conclude 共 6 路由為 501 stub（`router.py:1430-1483`） | Memory 預設 / PostgresWorkshopStore 存在（`store.py:380`）需 env 開啟 |
| trading_room | 22 路由邏輯完整、no_order_route_proof 不變式在；`/stream` 為空 SSE stub（`router.py:2985`） | in-memory 單例 |
| research | 27 路由邏輯完整 | 純 in-memory（`store.py:334` "only memory for now"） |
| dashboard | ETag/版本/rollback 完整 | module-level dict（`dashboard/router.py:68-79`） |
| servant | 真 OpenClaw 整合 | 事件 in-memory |
| identity | `/me`、`/capabilities` 真實；sub-router 空殼，sessions/ask/inbox 在 main.py 舊軌 | `read_surfaces.json` |
| personalization / shadow | sub-router 空殼（零路由） | main.py 舊軌 / 無 |

另：private_content_ref 產出 `priv-content-stub://` 佔位（sw001 deep closure 的
PrivateContentStore + envelope encryption 未落地）；main.py（58k 行）舊軌路由先註冊
優先生效，模組化遷移只完成一半。

### 前端（canonical: `ajoe734/execute-plans@dev`）
- 三 tab 真打 `/bff/agora/*`、強制 ETag/Idempotency-Key、無 mock；DynUI 渲染鏈閉環
  （42-widget registry → 驗證 → sensitivity gate → Recharts+ECharts → grid 編輯 → 版本/rollback）。
- pantheon repo 內兩個 nested FE checkout（`.fe-ep/` 7/1、`.fe-human-inbox-persona-focus/` 7/11）已分叉，
  曾導致修錯 repo 的 phantom-done（AG-DYNUI-LIVE-WORKSHOP-009）。
- 獨立 `agora.html` bundle（audience `pantheon-agora`）設計存在但未部署；dev 上 Agora 是主 SPA 內路由。

### Live（dev，2026-07-12 實測）
- BFF 掛 129 條 agora 路由；session auth 活著（`op-dev` 被 logout denylist 擋、其他 subject 可登入）。
- 7/7 readback：`/agora/trading-room` 全 200 無錯誤（deploy `4a4f256e`）；7/8 三 tab gate reviewer 核准。
- 資料面近全空：markets/journal/signals/candidate-pools/committee/handoffs/postmortems 0 筆；
  inbox/insights 是 6/15 research-run 種子；journal 標 `persistenceMode: bff_local_dev_storage`
  且躺著 dry-run 測試殘留；`/bff/agora/capabilities` 回空陣列與 `/me` granted_capabilities 不一致。

## 差異清單（按嚴重度，對應 AG-GAP task）

1. 持久化承諾 vs 現實（高）→ AG-GAP-001/002/003/004
2. OpenAPI 承諾 501 路由、compatibility manifest 停在 6/21 `pending`（高）→ AG-GAP-005
3. main.py 雙軌未收斂（中）→ AG-GAP-006
4. capabilities 端點不一致 + dev journal 測試殘留（中低）→ AG-GAP-007
5. trading-room typed SSE 缺（中）→ AG-GAP-008
6. PrivateContentStore 未落地（中）→ AG-GAP-009
7. 視覺 parity blocked on 遺失的 `AI Trading Desk Design.zip`（中）→ AG-GAP-010
8. nested FE checkout 分叉（中）→ AG-GAP-011
9. 12-block completeness 未落地（中）→ AG-GAP-012
10. 資料活化（markets/watchlist 空殼體驗；與 SRCLIVE-001 同線）（中）→ AG-GAP-013

## 流程面觀察

- 433 個 Agora 任務（89 BASE / 87 SIDECAR / 257 FOLLOWUP）全 done、現役佇列 0 筆。
  FOLLOWUP 是 underutilization_dispatch 風暴噪音；13 個 superseded 中有實錘 phantom：
  AG-DYNUI-PROD-006（mock E2E gate）、AG-DYNUI-LIVE-WORKSHOP-009（修錯 repo）、
  AG-FE-TR-001（一度對 mock 渲染）。
- 教訓已制度化（07-05 Closeout Rules）；`terminal_status=done` 不可單獨作為上線證據。

## 結論

Agora 的「骨架與皮膚」（合約、前端、動態 UI、auth、安全不變式）是真的；
「內臟」（持久化、部分後端路由、真實資料流）還是 dev 級暫代品。
收斂路徑見 execution packet AG-GAP-001~013。

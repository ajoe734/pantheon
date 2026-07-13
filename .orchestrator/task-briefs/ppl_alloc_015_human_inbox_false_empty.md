# PPL-ALLOC-015 — Human Inbox 假空：聚合 37 秒 → FE 顯示「目前沒有項目」

## 問題（2026-07-13 瀏覽器層實走發現）

live dev 實測：`GET /bff/management/human-inbox` 有 **6 筆 live 待辦**
（promotion_review ×2、governance_review ×2、approval、sentinel_finding），
但回應耗時 **37 秒**（meta.surfaces.human_inbox.status=degraded，
"one or more contributing surfaces are degraded"）。dev console 的
`/management/human-inbox` 頁面等不到回應，渲染成
「目前沒有 live 收件匣項目」——**假空**。

後果：操作者從主工作流入口看不到待審的 paper→real 升級案；
升級治理鏈（Governance→Human Inbox→核准）在 UI 層斷裂。
（瀏覽器側 Playwright 觀察：頁面只發出 SSE stream 請求後即入空狀態，
資料請求逾時被吞。）

## 目標

1. 找出 human-inbox 聚合 37 秒的元兇（meta 指出 contributing surface
   degraded；查 read_store 聚合鏈哪個 surface 慢/重試），把 p95 壓回秒級。
2. FE：逾時/degraded 時不得呈現與「確定為空」相同的狀態——
   顯示 degraded/重試中標示（strict no-mock 原則的誠實呈現）。
3. 兩側都加回歸測試（BFF：聚合逾時上限或 surface 級 timeout；
   FE：pending/degraded/empty 三態區分）。

## 驗收

- [ ] live dev：human-inbox 端點回應時間顯著下降（記錄前後數據）。
- [ ] live dev 瀏覽器實走：頁面呈現 6 筆（或當時實際數）待辦，
      promotion_review 可點進 HumanGateDetail。
- [ ] degraded 情境 FE 呈現非空狀態標示。
- [ ] merge dev + 部署後 live 驗證（babysit 規則）。

## 邊界

- 不動 supervisor/poll cadence。標準 git workflow。
- 與 PPL-ALLOC-011/012 檔案面可能重疊（read_store/main.py），rebase 注意。

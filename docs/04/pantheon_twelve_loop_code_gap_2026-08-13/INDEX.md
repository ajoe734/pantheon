# Pantheon 十二循環程式碼盤點與最小閉環設計

日期：2026-08-13

狀態：設計基線與 governed execution catalog 已建立；materialization evidence 另由
canonical task state 與 supervisor receipt 保存

## 本次結論

以最新 `pantheon/dev` 程式碼逐條對照
`LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md` 後，目前不能宣稱 12 個循環全部正常：

- 3 個循環有直接阻斷：Alpha Replication、Consultation、BFF Health Monitoring。
- 6 個循環只有部分閉環：Source Ingestion、Persona Teaching、Agora Evidence、
  Human Imitation、Capital Execution、Evolution。
- 3 個循環未找到明確程式阻斷，但仍缺真實整合 E2E：Strategy Distillation、
  Promotion/Deployment、Telemetry/Reconciliation。
- 0 個循環具備本輪要求的真實整合 E2E 證明。
- Management 管理系統不能正確呈現上述真相。

## 文件

- [GAP_REPORT.md](GAP_REPORT.md)：逐循環程式碼現況、根因、錯誤設計、缺失開發、
  缺失驗證、廢棄／誤導內容與舊計畫適用性。
- [SYSTEM_DESIGN.md](SYSTEM_DESIGN.md)：替代錯誤設計的最小可用系統設計、資料與
  權威邊界、相依順序、檔案範圍、驗收與回復策略，供下一階段轉成 execution tasks。
- [EXECUTION_TASKS.md](EXECUTION_TASKS.md)：18-task 最大平行 DAG、各 task scope、驗收、
  rollout/rollback 與舊計畫去重。
- [execution-tasks.json](execution-tasks.json)：供 supervisor materialization 的機器可讀
  execution catalog。

## 基線與邊界

- Pantheon 程式碼基線：`refs/remotes/origin/dev`
  `3307552b55af75850dab1d50e58cef9f86e10b53`。本機另有同名的舊
  `refs/heads/origin/dev`，本次沒有使用該歧義 ref。
- Management 前端程式碼基線：`execute-plans/dev`
  `3ee9f962a36626f085e2ca1c088b3ce4b4d08e6f`。
- 規格真相：`LOOP_TRIGGER_AND_CONCURRENCY_POLICY.md`。
- 只處理 Pantheon 12 個產品循環與 Management 的循環真相呈現。
- 不把 Supervisor V2、fleet dispatch、task-state governance 當成第 13 個產品循環，
  也不以它們的狀態代表產品閉環。
- 目標是最小可用閉環；不納入資安強化、HA、壓測、合規、live capital 或軍規商規
  級額外工作。
- 本輪不改產品程式；execution task 只能由 governed command materialize，再由 supervisor
  派給 auto-worker，不能由此規劃工作直接實作。

## 閉環判定

每一循環只有在同一個真實、非 fixture 的 dev 路徑完成以下全部步驟才算閉環：

```text
trigger
  -> owning worker/controller
  -> durable terminal output
  -> authoritative actual-state readback
  -> next consumer reads the exact output identity
```

單元測試、mock HTTP、monkeypatch、證據 manifest、服務 `/health`、process alive、
catalog `implemented`、PR merged 或 hosted bundle 存在，均不能單獨代替上述閉環。

## 後續使用規則

後續以 `execution-tasks.json` 為唯一新工作 catalog。舊 28-task DAG 與 2026-08-08
minimum closure task IDs 不得直接重送或原地改寫；它們只作 historical input。

# 未完成工作與派工對帳 — 2026-09-19

快照：2026-09-19 14:25:05 UTC。本文件是開發任務盤點，不是產品部署或使用者流程驗收。正式狀態以既有 TaskStore 與 supervisor 最新讀回為準。

## 已落實的整理

- 七條指向已撤銷 ARCH-MAIN-REMAINING-DOMAINS-001 的前置已移除；三條冪等旁路仍由既有 DOMAIN-WRITERS 任務承接。
- TJ-E2E-001～012 的十二笔歷史完成紀錄已恢復，012 錯誤復活的待辦已移除；不可變歸檔保持原樣。沒有重開已交付的產品工作。
- OSS-EXECUTION-BASELINE-001 的 LEAN／broker 範圍與 BFF STRUCT-RETIRE source 無重疊，已取消不必要的串行等待。原本的真實 engine replay、隔離模擬、官方最新版查核與安全邊界全部保留。
- CB06 持續由 AGY 修正既有 PR5884；OSS execution baseline 已由 supervisor 實際派給 AGY。兩者 reviewer 均為 Codex。

## 實際執行

- `OSS-EXECUTION-BASELINE-001`：Antigravity 執行、Codex 審核；run `antigravity-20260919T142120Z-2ceff6c5`，PID `1383080`，generation `119`。
- `BFF-TEST-MIGRATION-CB06-RESEARCH-KNOWLEDGE-EXPERIMENTS-001`：Antigravity 執行、Codex 審核；run `antigravity-20260919T140321Z-d46b71f6`，PID `1247927`，generation `767`。

兩個 PID 已在宿主機驗證存活；queue/worker receipt 記於 evidence.json。未重啟 supervisor，未改模型、帳號、slot 或 reviewer policy。

## 正式未結案清單

共 18 項。TJ 已移出；OPS-QUEUE 已合併但仍未正式結案，與尚待產品實作分開列出。

| 任務 | 工作 | 狀態 | owner / reviewer | 尚未完成前置／處置 |
| --- | --- | --- | --- | --- |
| `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001` | BFF 全量測試遷移總驗收 | in_progress | Antigravity2 / Codex | `BFF-TEST-MIGRATION-CB06-RESEARCH-KNOWLEDGE-EXPERIMENTS-001` |
| `BFF-ROUTER-USECASE-CORRECTIVE-001` | Router 到應用服務的剩餘整合 | todo | Antigravity2 / Codex | `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001` |
| `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001` | 產品寫入持久化與剩餘 fallback 退役 | todo | Antigravity2 / Codex | `BFF-ROUTER-USECASE-CORRECTIVE-001`、`JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001` |
| `STRUCT-RETIRE-001` | 剩餘結構／舊碼退役與整合驗收 | todo | Antigravity2 / Codex | `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001`、`JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001`、`BFF-READ-OWNER-WIRING-CORRECTIVE-001`、`LOOP-RECEIPT-INTEGRATION-CORRECTIVE-001` |
| `SIMPLIFY-BFF-RESIDUAL-001` | BFF 重複實作與舊路由殘留清理 | todo | Antigravity2 / Codex | `STRUCT-RETIRE-001` |
| `OSS-CORE-BASELINE-001` | 核心 API 套件基線、SSE／Pydantic 相容分支清理 | todo | Antigravity2 / Codex | `SIMPLIFY-BFF-RESIDUAL-001` |
| `SIMPLIFY-EXTRACTION-001` | 以經評測的 typed extractor 收斂意圖／StrategySpec 規則 | todo | Antigravity / Codex | `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001` |
| `OSS-RESEARCH-FOOTPRINT-001` | 研究框架用途盤點與無用入口退役 | todo | Antigravity / Codex | `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001`、`SIMPLIFY-EXTRACTION-001` |
| `OSS-RESEARCH-BASELINE-001` | 保留的數值／ML 套件升級 | todo | Antigravity2 / Codex | `OSS-RESEARCH-FOOTPRINT-001` |
| `OSS-EXECUTION-BASELINE-001` | LEAN 擴充抽離與執行引擎依賴驗證 | in_progress | Antigravity / Codex | worker 執行中 |
| `OSS-INFRA-PROFILES-001` | Compose profiles 與基礎服務映像收斂 | todo | Antigravity2 / Codex | `OSS-CORE-BASELINE-001`、`OSS-RESEARCH-BASELINE-001`、`OSS-EXECUTION-BASELINE-001`、`JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001` |
| `OSS-OBJECT-STORE-CUTOVER-001` | 物件儲存來源切換與舊設定退役 | todo | Antigravity2 / Codex | `OSS-INFRA-PROFILES-001` |
| `SYS-SIMPLIFY-IMPLEMENTATION-CLOSURE-001` | LLM／OSS 簡化整批整合驗收 | todo | Antigravity2 / Codex | `OSS-OBJECT-STORE-CUTOVER-001`、`SIMPLIFY-BFF-RESIDUAL-001`、`SIMPLIFY-EXTRACTION-001` |
| `JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001` | Journal owner 與持久後端配置對齊 | todo | Antigravity2 / Codex | `BFF-TEST-FULL-MIGRATION-CORRECTIVE-001`、`BFF-ROUTER-USECASE-CORRECTIVE-001` |
| `BFF-READ-OWNER-WIRING-CORRECTIVE-001` | BFF 預設讀取 owner 與真實 job-log 接線 | todo | Antigravity2 / Codex | `DOMAIN-WRITERS-DURABILITY-CORRECTIVE-001`、`JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001` |
| `LOOP-RECEIPT-INTEGRATION-CORRECTIVE-001` | 既有十二迴圈 projector 的正式 receipt 接線 | todo | Antigravity2 / Codex | `BFF-READ-OWNER-WIRING-CORRECTIVE-001`、`SIMPLIFY-EXTRACTION-001`、`OSS-RESEARCH-FOOTPRINT-001` |
| `OPS-QUEUE-RECONCILE-CAS-001` | Queue／CAS 與 provider output 判定修正的任務收尾 | todo | Human/Ops / Codex | PR5871 已合併，merge333610549ce998057665df50b4307d8da3bf21dd 已含於現行 runtime；待正式帳務收尾，沒有阻擋其他任務。 |
| `BFF-TEST-MIGRATION-CB06-RESEARCH-KNOWLEDGE-EXPERIMENTS-001` | Research／Knowledge／Experiments 測試遷移 CB06 | in_progress | Antigravity / Codex | worker 執行中 |

## 為何其餘工作仍等待

其餘前置有實際來源或 writer 順序約束，不因增加 worker 而消失。CB06 → 測試遷移總驗收 → Router／Journal → Domain writers → Read owner／LLM extraction／research footprint → Loop receipts／結構退役，及後續各 OSS 基線／Compose／object-store／整合收尾，仍按現有契約進行。OSS execution 已從不必要的 BFF 等待中分離並啟動；沒有為提高 running 數字而移除真實驗收或重複派同一個 scope。

全部尚待實作的有效任務 owner 為 Antigravity／Antigravity2、reviewer 為 Codex。Human/Ops 的 OPS-QUEUE 是已合併工具修正的帳務收尾，不應再派一個 worker 重做相同 patch。

產品 PR5907（DEV-TW-PAPER-BASELINE-001）在前次讀回仍 OPEN，未列入這批 canonical tasks；不能視為這次派工已交付，也不能以工具文件的合併權限直接批准產品 PR。產品合併仍須真實 review。

## 追溯

- [歷史決議與查證時間線](HISTORICAL-DECISIONS.zh-TW.md)
- [正式交易及 worker 證據](evidence.json)
- [架構撤銷 PR5868](https://github.com/ajoe734/pantheon/pull/5868)
- [TJ 原結案 PR4021](https://github.com/ajoe734/pantheon/pull/4021)

這份文件的合併只交付盤點及操作紀錄，不能當作上述未完成產品 tasks 的完成證據。OSS 最新版本應由各 implementation owner 在實際升級時向官方來源重新核對。

# Pantheon：共用程式上游修復、單一實作與有序交付計畫

版本：2026-09-11 交付版（V2）  
狀態：**Operator 已批准規畫；Canonical 派工與依賴已由 TaskStore 落地；本文件為單一交付契約**。  
交付任務：`BFF-UPSTREAM-V2-PLAN-DELIVERY-001`（Owner: Antigravity / Reviewer: Codex）。

---

## 結論與核心原則

同意將阻礙測試的共用產品程式修復放到測試任務上游，但不能只搬動任務順序。必須同時做到：

1. **單一責任收斂**：把重複的受理、權限、receipt 與 projection 邏輯收斂到既有責任元件，包含 Persona、Agora、Tools 等前版漏列的路徑。
2. **同步清理與無殘留**：每次交付同時改完真正消費端並刪掉舊實作；不留 main forwarding wrapper、第二套 fallback、shadow cache 或假成功 receipt。
3. **精確去重與保留責任**：把既有下游 corrective 已經涵蓋的相同工作正式拆出，原任務只保留未交付的剩餘責任，不再做第二遍。
4. **單一 active writer 與嚴格順序**：相同檔案只有一個 active writer；上一份修改 merge、release lease，下一份才能從該精確版本繼續。使用既有 TaskStore、supervisor 及 contract 工具，不新增排程器或 transfer framework。
5. **分層驗收**：分別驗收「程式結構抽離」、「產品缺失能力」、「實際運作與部署」。測試不匯入 main、回 202、或僅有 PR merge，都不能代替功能閉環。

**派工前查證基準**：盤點時 Canonical TaskStore 有 11 個 blocked 任務（8 個產品測試批次、full-migration parent、PPL-ALLOC-007 歷史身分/generation 來源、FE exact-pair 發布協定）。

> **名詞界定**：本文的 Command admission 指「命令受理」——統一檢查身份、權限、參數、重播、批准，可靠地記錄命令後才交給既有執行器。它不是另一個排程器；受理成功也不等於命令已執行完成。

---

## 現行規畫與交付文件索引

本目錄包含經批准的完整 V2 規畫、執行架構與派工映射：

- [01 現況、11個卡點與前版錯誤修正](01_CURRENT_AUDIT.md)
- [02 研究規畫與SA／SD：唯一責任元件、契約與資料邊界](02_RESEARCH_AND_DESIGN.md)
- [03 實作工作包：範圍、刪除、消費端與驗收](03_IMPLEMENTATION_PACKETS.md)
- [04 同檔有序執行、契約轉移與DAG](04_ORDERED_DELIVERY.md)
- [05 查證證據、測試結果與未驗證範圍](05_EVIDENCE_AND_ACCEPTANCE.md)
- [派工映射與單一執行契約清冊](dispatch-map.json)

> **歷史檔案說明**：2026-09-10 解卡紀錄與 2026-09-11 第一版草案為本機歷史工作紀錄，已由本 V2 完整取代，不形成兩套競爭方案；本目錄文件為唯一持久生效規畫。

---

## V2 重要修正摘要

- **版本與基準分離**：查證基準 BE dev 為 `e3ef0a4b6c8500f19019196e67fcbd5df6df67dc`，dev checkout 與 live command runtime 為 `ba6c9e99ec4a0b09ca85b30ab18bb862a3e42e58`；FE dev 為 `d0f2186c12355cb4aa7179f14069c7e627b8e840`。各檔案 blob 比對確認相同，不混稱環境。
- **Command 旁路完整涵蓋**：Command 問題不只 main/service/router，Persona ranking、Agora、Tools、Governance 另有重複或不完整路徑；status/replay 的 tenant 檢查亦已重現缺口並納入修復。
- **撤回簡化三狀態，對齊現役 Evolution**：現役 FE 有六種 program 狀態、獨立 run 狀態及 review/promotion/freeze 操作；對齊既有 Evolution outbox、dispatch 與 receipt。
- **Jobs 來源全面分類**：保留現役功能要求，釐清六類 job 來源及領域 owner；未實作者列為未完成義務，不擅自縮減產品功能。
- **正式派工已完成落地**：16 個原設計工作包依照單一 repository 派工規則，將 U9、U8B、U10B 拆為有序的 BE 與 FE 獨立任務，連同 3 個契約決策任務與 1 個文件交付任務，正式建立 19 個 canonical execution tasks，並以原子 `dependency-contract` 更新 10 個既有任務，完成 17 則責任註記。

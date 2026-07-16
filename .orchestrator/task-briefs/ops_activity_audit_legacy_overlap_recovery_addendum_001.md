# OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-ADDENDUM-001

## 目的

修正 `OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001` 的已知輸入範圍。完整中央活動歷史除了原 brief 記錄的四段 1,000 行重疊，還有一段 2026-05-24 舊版 timestamp archive 的 999 行重疊。原 task 在 compose 本 addendum 前不得合併。

本文件只修正規劃與驗收條件；程式、測試、證據、PR 與 postmerge 操作仍由原 task 的 Antigravity owner 完成，Claude 負責 exact-head 獨立 review。

## 新確認事實

- 2026-07-16 對中央 status root `/home/lupin/code/pantheon` 做只讀掃描時，`archive/logs/` 有 411 個 timestamp gzip archive，`.orchestrator/logs/activity-log-archive/` 另有 10 個舊格式 gzip archive，加上 active log，共 422 個來源。先前只計算第一個 archive 目錄所得的 412 是不完整 snapshot，本段取代該數字。
- 在這份 422-source snapshot 中共有 240 組相鄰 suffix/prefix 重疊：239 組 standard 1,000 行 legacy rotation，以及下列唯一一組 pinned 999 行：
  - `archive/logs/ai-activity-log.jsonl-2026-05-24T1237Z.gz`
  - `archive/logs/ai-activity-log.jsonl-2026-05-24T1239Z.gz`
- 兩個檔案各有 1,001 行。前者 suffix 999 行與後者 prefix 999 行逐 byte 相同；1,000 行比較不相同。
- 999 行重疊段共 5,325,808 bytes，SHA-256 為 `0a3b56f720a5aa493d8968edfff8e32e0df98e410f6334d6790f10a06019f247`。
- gzip 檔 SHA-256：
  - `T1237Z.gz`：`ad7dd174e0278a3c21b10024cd227f0d138052dd0945bc3b24159538d87ed6c5`
  - `T1239Z.gz`：`d211e27bc5337c8eff200e14d48800f949658e6c8b43d9fd22e54ea8c77061da`
- 解壓後完整內容 SHA-256：
  - `T1237Z.gz`：`8435543b845639383471bd3a3d1b1d1642bb0944649b5e2a4ffe1ad5ad9a4e57`
  - `T1239Z.gz`：`da6a102178c82fb4eca8d0794ed5b419f0c97770e0ad63542dde0033e7efa3ff`
- 這 999 行中的重複 event ID payload 全部相同，沒有 payload mismatch。10 個 `legacy_ts_old` 來源沒有 overlap；本 snapshot 沒有 active `.gz` content-addressed source。
- 239 組 standard folds 中，234 組分類為一般 `valid_legacy_rotation`，另有五組屬於本次 2026-07-16 incident lineage。2026-07-16 當天目前共有下列六組 standard 1,000-line folds；其中 `1130Z -> 1301Z` 沒有 incident event ID，仍是一般 legacy rotation：
  - `ai-activity-log.jsonl-2026-07-16T0358Z.gz -> ai-activity-log.jsonl-2026-07-16T1130Z.gz`
  - `ai-activity-log.jsonl-2026-07-16T1130Z.gz -> ai-activity-log.jsonl-2026-07-16T1301Z.gz`
  - `ai-activity-log.jsonl-2026-07-16T1301Z.gz -> ai-activity-log.jsonl-2026-07-16T1404Z.gz`
  - `ai-activity-log.jsonl-2026-07-16T1404Z.gz -> ai-activity-log.jsonl-2026-07-16T1450Z.gz`
  - `ai-activity-log.jsonl-2026-07-16T1450Z.gz -> ai-activity-log.jsonl-2026-07-16T1609Z.gz`
  - `ai-activity-log.jsonl-2026-07-16T1609Z.gz -> active ai-activity-log.jsonl`
- 原 brief 的 `ai-activity-log.jsonl-2026-07-16T1450Z.gz -> active` 是較早 snapshot 的 lineage 簡寫；`ai-activity-log.jsonl-2026-07-16T1609Z.gz` 已合法插入該鏈，current inventory 必須呈現完整 basename 的 `2026-07-16T1450Z -> 2026-07-16T1609Z -> active`，不得同時宣稱存在 direct `2026-07-16T1450Z -> active` adjacent fold。
- 現有檔案只能證明這是一個封閉的歷史例外；無法證明當年的精確 race 時序。實作與 evidence 不得把推測寫成已確認根因。

上列 source count、active tail 與 standard fold count 是掃描快照，不是可硬編碼的未來常數；例外 pair、行數與 hashes 則是本次封閉例外的必要識別資料。若正式驗收前發生新 rotation，fleet 必須先重新建立完整 typed inventory 與 lineage，不能沿用舊數字或把新來源跳過。

## 對原 brief 的修正

原 brief 的「只有符合 1,000 行 contract 才可折疊」與「所有 999 行一律拒絕」改為：

1. 一般規則仍只接受相鄰 legacy timestamp source 的 exact 1,000 行 byte-identical overlap。
2. 只額外接受本文件列出的 `T1237Z.gz -> T1239Z.gz` exact 999 行歷史例外。
3. 例外必須同時符合 exact source basename、來源順序、兩個完整解壓內容 SHA-256、999 行、overlap bytes 與 overlap SHA-256。任一欄不同都必須 fail closed。
4. 不得用 wildcard、日期範圍、環境變數、最小／最大 overlap、`<= 1000` 或「payload 相同就忽略」放寬規則。
5. 例外 registry 必須是 code-owned 的封閉常數或等價的 immutable typed registry；不得從中央 runtime state、evidence 輸出或使用者可改設定載入。
6. generic 999、1001、非相鄰、未知命名、content-addressed source、hash 不符、bytes 不符與 payload mismatch 仍全部拒絕。
7. 折疊仍只存在於 logical read view；不得改寫、重新壓縮、刪除或搬移任何歷史來源。
8. source identity/content 穩定檢查仍須比較 scan 前後的 `st_dev`、`st_ino`、`st_size`、`st_mtime_ns` 與 SHA-256；相同內容的 inode replacement 也必須拒絕。

## 新增必要測試

- exact pinned pair fixture 可折疊 999 行，logical 順序正確，兩邊非重疊行各保留一次。
- basename 正確但任一完整來源 hash、overlap hash、overlap bytes 或行數不同時拒絕。
- 相同 999 行內容換成其他 timestamp basename 時拒絕。
- exact pair 任一 overlap byte 不同、event ID payload 不同、來源順序反轉或來源被替換時拒絕。
- 原 brief 的 generic 999／1001 fail-closed 測試必須保留，不得改成全面接受。
- 在上述 422-source snapshot 與 hashes 未變時，完整中央 inventory 必須同時證明：240 組 fold；其中 239 組 standard 1,000 行、一組 pinned 999 行；239 組 standard folds 再分為 234 組一般 legacy rotation 與五組 incident lineage；其他 overlap 為零；所有 duplicate payload mismatch 為零。
- inventory 必須分型列出 411 個 `legacy_ts_std`、10 個 `legacy_ts_old` 與一個 active source，不能只掃 `archive/logs/` 後宣稱是完整歷史。
- tail lineage 必須由實際相鄰來源動態產生；在本 snapshot 應為 `2026-07-16T1450Z -> 2026-07-16T1609Z -> active`。測試 fixture 必須使用完整 basename，並覆蓋 timestamp archive 插入後，舊的 direct archive-to-active 關係被新相鄰鏈取代，不得被 non-adjacent detector 誤判。
- evidence 必須分開列出 standard 1,000 folds 與 pinned exception fold，並包含 physical/logical/event-ID/duplicate/within-source/fold 指標表。
- summary 與 evidence 必須直接列出 source classification counts 與 fold classification counts，不能只讓 reviewer 從 422-entry manifest 或 240-row fold table自行重算。
- manifest 每個 source 必須記錄 scan 前後的 `st_dev`、`st_ino`、`st_size`、`st_mtime_ns` 與 SHA-256；不得只輸出一個無法獨立驗證的 `stable: true`。
- 所有測試須使用 repo 外的隔離 `PANTHEON_STATUS_ROOT`，並證明 task worktree 與中央原始 activity source hashes 均未改變。

## PR 與完成條件

- Antigravity owner 先 compose 含本 addendum 的最新 `origin/dev`，再調整原 task 實作；不得在舊 brief 基礎上直接交付。
- 原 task 的全部既有測試、完整控制平面 suites、atomic evidence 行為與 postmerge stale-worktree proof 仍是必要條件。
- Claude 必須對 final exact head 獨立核對 registry 是單一封閉例外、generic 999 仍拒絕、完整 inventory 真實通過後才可核准。
- auto-merge 關閉。只有原 task PR 合併、exact merge 安裝、正式 inventory/outbox recovery/governed commands 全部通過，才可將原 task 標記完成。

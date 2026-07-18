# OPS-ACTIVITY-AUDIT-LEGACY-OVERLAP-RECOVERY-001

## 目前 recovery dispatch（2026-07-18）

- 狀態：`in_progress`。
- Current owner：`Codex`。
- Current reviewer：`Antigravity`。
- Reviewer 以 P0 safety finding 退回：未註冊的 legacy-to-legacy disjoint gap 必須維持 fail-closed。shared reader 必須拒絕精確的 `1450Z -> 0404Z -> 1754Z` 形狀，只接受 lineage-bound disjoint edge。
- 本段只更新目前 corrective dispatch；下列原始範圍、addendum 與 postmerge 驗收要求仍全部有效。

## 目的

恢復所有 `ai-status` 正式命令，並在不刪除、不改寫歷史活動紀錄的前提下，讓新版活動紀錄讀取器正確處理舊版輪替格式刻意保留的 1,000 行重疊。

這是控制平面修正任務。規劃者只負責本文件、派工與驗收；程式、測試、證據及 postmerge 操作全部由 assigned fleet 完成。

## 已確認事實

- 2026-07-16 安裝 `OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001` merge `d4d0f693ec40e2196c47f2601380e1e0fc8b2fb9` 後，dev supervisor 已由舊 PID `3565952` 更新為新 PID `3729906`，執行來源為該 merge。
- 從 task worktree 執行中央 `show` 時，outbox recovery 最後拒絕：
  `RuntimeError: activity event_id duplicate across sources: worker-commit-deb673789747a71068bff9f2578ad9f41d7b8253`。
- 讀取來源共 411 個檔案、1,157,457 行；含 event ID 的行為 537 行、437 個唯一 ID。
- 有 100 個重複 event ID；100 個 payload 全部 byte/canonical-payload 一致，沒有 payload mismatch，也沒有單一來源內重複。
- 重複來自四段完全相同的 legacy suffix/prefix：
  - `0358Z.gz` -> `1130Z.gz`：1,000 行；
  - `1301Z.gz` -> `1404Z.gz`：1,000 行；
  - `1404Z.gz` -> `1450Z.gz`：1,000 行；
  - `1450Z.gz` -> active `ai-activity-log.jsonl`：1,000 行。
- 這是舊版 timestamp-named rotation 的 `keep_lines=1000` 相容性問題。新版 reader 把所有來源視為 disjoint，因而把合法的 legacy overlap 當成資料損壞。
- pending `status_activity_outbox` 無法 recovery，因此 `show`、`note`、`handoff`、`approve`、`done` 與 supervisor 狀態寫入都受影響。

上列數字是本次 incident snapshot，不得直接當成未來資料的硬編碼常數。fleet 必須在 evidence 中記錄實際輸入檔 SHA-256、掃描時間與重新計算結果。

## Fleet 分工

- 原始 implementation owner：`Antigravity`；原始 reviewer：`Claude`。
- 目前 P0 corrective owner：`Codex`；目前 reviewer：`Antigravity`，以 canonical `ai-status.json` 為準。
- owner 與 reviewer 必須是不同 admitted identity。
- auto-merge 必須關閉；owner 不得自行核准或合併。

因本 incident 正好使正式 `assign` 無法完成，第一次 bootstrap 可由 supervisor/operator 從最新 `origin/dev` 建立乾淨 task worktree，直接啟動 assigned owner。bootstrap run ID、worktree、base SHA 與原因必須寫入 evidence。修正安裝後，必須再用恢復的 governed command 將本 task 精確 materialize 一次；不得手改 `ai-status.json` 補狀態。

## 實作範圍

- `.orchestrator/common.py`
- `.orchestrator/runtime_state.py`（只有共用 activity reader 接點確有需要時）
- `scripts/ai_status.py`
- 所有直接讀取完整 activity history、目前會把 legacy overlap 當成 corruption 的既有控制平面 consumer
- 對應測試
- `docs/deployment/evidence/ops-activity-audit-legacy-overlap-recovery-001/`

不得修改產品交易行為、BFF、frontend、broker、原始 archive bytes 或與本 incident 無關的 supervisor 排程。

## 必要行為

1. 建立一個共用、streaming 的 logical activity reader；所有要求全歷史一致性的 consumer 必須使用同一套規則，不得各自做不同的去重。
2. 只對可驗證的 legacy timestamp rotation 相鄰來源折疊重疊：
   - 來源名稱及順序符合既有 legacy contract；
   - 前一來源 suffix 與下一來源 prefix 必須逐 byte 相同；
   - overlap 必須符合 legacy `keep_lines=1000` contract；
   - 每段折疊都要輸出來源、行數、bytes 與 digest 到 redacted evidence。
3. 折疊只影響 logical read view，不得刪除、搬移、改寫或重新壓縮任何原始 archive／active log。
4. 折疊後，每一個 event ID 只可出現一次。以下情況必須 fail closed：
   - overlap 任一行不相同；
   - 同一來源內重複 event ID；
   - 相同 event ID 但 payload 不同；
   - content-addressed 新格式 archive 發生 overlap；
   - 非相鄰來源重疊、未知命名格式或不符合 1,000 行 contract；
   - symlink、truncated gzip、壞 JSON、壞 UTF-8 或來源在鎖定後被替換。
5. logical reader 必須是 bounded-memory/streaming；不得一次把所有 411 個壓縮檔完整解壓到記憶體。
6. `recover_status_activity_outbox()` 必須以 logical unique view 驗證 idempotency：已存在且 payload 相同視為已寫入，payload 不同仍拒絕。
7. 修正後須成功 recovery 當時 pending outbox，而且該 event 在 logical history 中恰好一次；不得清空 outbox 假裝成功。
8. 新版 content-addressed rotation 的 disjoint invariant 必須保留，不能用「所有 identical duplicate 都忽略」放寬防線。

## 必要測試

- 兩個 legacy timestamp archive 的 exact 1,000-line suffix/prefix 被 logical reader 折疊一次。
- 三段以上連續 legacy overlap 仍維持原始順序，非 overlap 行一行不少。
- legacy archive -> active log 的 exact overlap 正確處理。
- 999／1001 行、非相鄰、未知檔名、錯誤順序、內容差一 byte 全部拒絕。
- 同 event ID 不同 payload、同來源重複、content-addressed archive overlap 全部拒絕。
- gzip 截斷、JSON 損壞、symlink/source replacement 全部拒絕，且 outbox/state bytes 不變。
- 以本 incident 的最小化 fixture 重現修正前失敗、修正後 100 個 identical event IDs 只讀出 100 次而非 200 次。
- 完整 `scripts/test_ai_status.py`、common、runtime-state、supervisor、watchdog、worker-runner 測試。
- `python3 -m py_compile` 與 `git diff --check`。

## PR 與 postmerge 驗收

- final candidate 必須先 compose 當時最新 `origin/dev`。
- PR target `dev`，只含本 task scope，必要 trailers 正確，auto-merge 關閉。
- reviewer 必須在 final exact head 獨立重跑必要測試並核准；review 不得再以 commit 改變 head。
- 合併後由 fleet 透過正常 dev-root sync 安裝 exact merge，記錄舊／新 source SHA 與 process identity。
- 重新執行完整 incident inventory，證明原始來源 hashes 未變、四段 legacy overlap 被 logical view 精確折疊、100 個 duplicate payload 仍全相同、零 mismatch。
- pending outbox 必須正式 recovery；接著從 disposable stale worktree 執行 `show`、`note`、`handoff`，中央各收到預期事件恰好一次，worktree-local `ai-status.json`、activity log、archive sentinel bytes 全部不變。
- 完成 `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002` 原本未完成的 isolation proof，並將其 evidence PR 重新交給 Codex2 exact-head review。

## 完成定義

只有在修正 PR 已獨立核准並合併、exact merge 已安裝、incident inventory 與 outbox recovery 通過、三個 governed command 的 stale-worktree proof 通過，而且本 bootstrap task 已經由恢復後的正式命令 materialize，才可標記完成。

## Coordination Root

- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

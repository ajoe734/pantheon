# OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001

## 目的

修正 `ai-status done` 在 fleet 的乾淨 task worktree 已完成、已推送、已合併時，仍錯用中央 coordination checkout 取得 commit、branch 與 clean-state，導致正式結案被拒絕的問題。

這是 execution task。產品／控制平面程式必須由 assigned fleet 在獨立 worktree 實作；planner 只負責本規劃、派工與驗收。

## 已確認的失敗案例

- 來源任務：`LOOP-PROD-PLANNING-BRIEFS-002`。
- 來源 PR：`#3759`。
- 已驗收 PR head：`da0ae61140278251a7b8fb35bf183aff658fef1b`。
- 已合併 SHA：`290ed7df72a745dcef486cf65b3c9d06eaa2de4b`，且已確認為 `origin/dev` ancestor。
- 中央任務狀態：`review_approved`。
- 從中央 checkout 與該任務的乾淨 planning worktree 執行中央 wrapper，兩次都被拒絕：`Cannot finalize task: latest commit subject must include task id LOOP-PROD-PLANNING-BRIEFS-002.`
- planning worktree 的實際 `HEAD` subject 是 `LOOP-PROD-PLANNING-BRIEFS-002: compose current dev`，已包含 task id。
- 根因：`collect_done_delivery_metadata()` 經 repository registry 把 Pantheon delivery repository 固定解析到中央 checkout；它沒有使用 auto worker 已提供的 `PANTHEON_WORKTREE_ROOT`／`ORCH_WORKSPACE_PATH`。因此狀態寫入位置與交付證據來源被錯誤地綁成同一路徑。
- 即使 subject gate 被繞過，中央 checkout 目前含有其他 worker／runtime 產生的 dirty entries，後續 clean-worktree gate 仍會錯誤拒絕這個任務。

## 依賴與順序

- 必須等待 `OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001` 合併並完成 postmerge 安裝證明。
- 前一項修正負責把 `ai-status.json`、activity log、archive、locks 等所有 canonical mutation 固定到中央 `PANTHEON_STATUS_ROOT`。
- 本任務只處理 delivery evidence repository 的選擇與驗證；不得把 canonical 狀態重新寫回 task worktree。
- 完成本任務後，重新對 `LOOP-PROD-PLANNING-BRIEFS-002` 執行一次正式 `done`，作為真實 postmerge 驗收。

## Fleet 分工

- Owner：`Codex2`。
- Reviewer：`Antigravity`。
- Owner 與 reviewer 必須是不同 admitted fleet identity。
- Owner 只能在乾淨 task worktree 修改程式、測試與證據。
- Reviewer 必須在 final exact head 上獨立重跑測試；不得以 owner 的測試輸出代替。
- auto-merge 必須保持關閉；owner 不得在 reviewer approval 前自行合併。

## 實作範圍

- `scripts/ai_status.py`
- `scripts/test_ai_status.py`
- 必要時最小修改 `.orchestrator/multi_repo_registry.py` 與其既有測試。
- `docs/deployment/evidence/ops-worktree-delivery-context-corrective-001/`

若實作需要修改其他檔案，owner 必須先在中央 task note 說明原因；不得順便重構無關程式。

## 必要行為

1. 明確分離兩個 root：
   - canonical mutation root：只可使用已驗證的 `PANTHEON_STATUS_ROOT`。
   - delivery evidence repository root：auto worker 中使用已驗證的 task workspace root；非 auto-worker 中維持明確、可稽核的既有 repository registry fallback。
2. auto worker 已有 `PANTHEON_WORKTREE_ROOT` 或 `ORCH_WORKSPACE_PATH` 時，`collect_done_delivery_metadata()` 必須從該 worktree 取得 branch、HEAD、commit subject/body/author、clean state、remote/upstream 與 PR merge proof。
3. delivery root 必須 fail closed 驗證：
   - 絕對路徑；
   - 不含 symlink component；
   - 存在且為 git repository root；
   - 與 task 的 target repository／configured GitHub slug 相符；
   - 不得等於不同 repository、nested repository 或任意外部目錄；
   - auto worker 同時提供兩個 workspace env 且值不同時必須拒絕。
4. status/archive/activity/lock 的所有讀寫仍必須指向中央 `PANTHEON_STATUS_ROOT`；delivery root 只可作 git 與交付證據讀取。
5. 不得放寬任何既有 delivery gate：task-id subject、required trailers、owner/reviewer、clean worktree、push status、merged PR、merge target 與 ancestry 都必須保留。
6. delivery metadata 必須記錄 `repository_path` 的真實 worktree、其來源 env、canonical status root，並可讓 reviewer 判斷兩者是刻意分離而非路徑漂移。
7. 無 workspace env 的人工／中央命令必須維持既有 registry 行為；不得默默猜測最近 worktree。

## 必要回歸測試

至少加入下列可執行測試，名稱可調整但涵蓋不得縮減：

1. 中央 checkout 在其他 branch 且 dirty，task worktree 乾淨、HEAD subject/trailers 正確、PR 已合併時，`done` 成功。
2. 上述成功案例只改中央 temp status/activity/archive/locks；task worktree 的 sentinel state/archive bytes 完全不變。
3. task worktree HEAD 缺 task id 時拒絕，即使中央 checkout HEAD 正確也不得通過。
4. task worktree dirty 時拒絕，即使中央 checkout clean 也不得通過。
5. workspace remote slug／repository identity 錯誤時拒絕。
6. workspace path 為 relative、symlink、nested repo、non-git 或不存在時拒絕。
7. `PANTHEON_WORKTREE_ROOT` 與 `ORCH_WORKSPACE_PATH` 不一致時拒絕。
8. 缺 workspace env 的非 worker 流程保持既有 registry fallback。
9. `show`、`note`、`handoff`、`approve` 與 `done` 的 canonical mutation path 都仍為中央 root。
10. 真實重播來源失敗：以 `LOOP-PROD-PLANNING-BRIEFS-002` 已合併證據建立 fixture，證明修正前失敗、修正後通過；不得直接修改 live canonical state作為測試。

## 驗證要求

- 先加入可在修正前穩定失敗的 regression，再實作修正。
- 執行所有新增／修改測試。
- 執行完整 `scripts/test_ai_status.py`。
- 執行與 central-status-root 有關的 supervisor、worker-runner、archive 與 common 測試，確認兩項修正可以一起工作。
- 執行 `python3 -m py_compile` 於所有修改的 Python 檔。
- 所有測試命令、exit code、測試數、final SHA 與檔案 checksum 寫入 evidence README／manifest。
- final candidate 必須先 compose 當時最新 `origin/dev`，再跑一次完整驗證。

## PR 與驗收門檻

- 從最新 `origin/dev` 開 PR，merge target 為 `dev`。
- commit subject/body/trailers 符合 repository 規則，`LLM-Agent: Codex2`、`Task-ID: OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001`、`Reviewer: Antigravity`。
- PR 只含本任務宣告的程式、測試與 evidence。
- auto-merge 關閉。
- Antigravity 在 final exact head 確認 GitHub checks、全部必要測試、路徑隔離與負面案例後，透過中央 governed command approval；review 不得再新增 commit 改變 head。
- 合併後安裝／啟動使用 merged SHA，並完成兩個真實 proof：
  1. 從 stale／isolated task worktree 執行 governed mutation，只改中央 task state。
  2. 從已合併且乾淨的 task worktree 執行 `done`，delivery metadata 指向該 worktree，並成功將 `LOOP-PROD-PLANNING-BRIEFS-002` 歸檔。

## 不在範圍

- 不修改產品交易行為、BFF auth、frontend、deploy workflow 或 broker 狀態。
- 不重寫任何既有 archive snapshot 或 activity log 歷史。
- 不清理、stash、reset 或提交中央 dirty checkout 的其他人檔案。
- 不繞過 task-id、trailers、clean、push、PR merge、reviewer 或 ancestry gate。
- 不以手改 `ai-status.json`／archive JSON 取代正式 `done` 驗收。

## 完成定義

只有在修正 PR 已由 Antigravity 對 final exact head 核准並合併、postmerge 版本已安裝、上述兩個真實 proof 都成功，而且 `LOOP-PROD-PLANNING-BRIEFS-002` 已透過正式命令歸檔後，本任務才能標記 done。

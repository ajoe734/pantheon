# OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001

## 目的

修正 `ai-status done` 在 fleet 的乾淨 task worktree 已完成、已推送、已合併時，仍錯用中央 coordination checkout 取得 commit、branch 與 clean-state，導致正式結案被拒絕的問題。

這是 execution task。產品／控制平面程式必須由 assigned fleet 在獨立 worktree 實作；planner 只負責本規劃、派工與驗收。

## 已確認的失敗案例與唯一 replay identity

- 來源任務：`LOOP-PROD-PLANNING-BRIEFS-002`。
- 來源 repository／PR：`ajoe734/pantheon` PR `#3759`。
- PR 初始 candidate `af8f0bbf2683b442a4b06b02db93c7ead7ef182d` 已由 compose commit 取代；不得拿它作 postmerge replay head。
- 最終 governed exact-head approval 與來源 worktree `HEAD`：`da0ae61140278251a7b8fb35bf183aff658fef1b`，branch `plan/fleet-corrective-briefs-20260716`。
- 已合併 SHA：`290ed7df72a745dcef486cf65b3c9d06eaa2de4b`；`da0ae61140278251a7b8fb35bf183aff658fef1b` 與 merge SHA 都必須是 configured `origin/dev` ancestor。
- 失敗當時中央任務為 `review_approved`；失敗後為保存 approval evidence 而進入 `blocked`。postmerge replay 不得把 `blocked` 直接當成可 `done`。
- 從中央 checkout 與來源 task worktree 呼叫舊 command runtime，兩次都被拒絕：`Cannot finalize task: latest commit subject must include task id LOOP-PROD-PLANNING-BRIEFS-002.`
- 來源 worktree 的實際 `HEAD` subject 是 `LOOP-PROD-PLANNING-BRIEFS-002: compose current dev`，已包含 task id，且 required trailers 已通過 PR checks。
- 根因：`collect_done_delivery_metadata()` 經 repository registry 把 Pantheon delivery repository 固定解析到中央 checkout；它沒有使用 worker 已提供的 `PANTHEON_WORKTREE_ROOT`／`ORCH_WORKSPACE_PATH`。因此狀態寫入位置與交付證據來源被錯誤地綁成同一路徑。
- 即使 subject gate 被繞過，中央 checkout 的其他 worker／runtime dirty entries 仍會污染後續 clean-worktree 判斷。

中央 task record 內仍可能保留初始 `af8f0bbf...` acceptance。該值是 historical candidate，不再是 closeout authority。postmerge owner 必須先用下方指定的 governed `assign` metadata update 寫入 `approved_delivery_head=da0ae611...`、`superseded_candidate_head=af8f0bbf...`、source PR 與 merge SHA，再用 `note` 解釋 reconciliation；不得手改 `ai-status.json`。若 PR、head、branch、review notes 或 ancestry 任一不符，停止 replay 並重新 review，不得自行猜測。

## Machine-readable execution slices 與順序

本 brief 合併後必須建立兩個中央 task；只有文字提到等待不算 materialized dependency。

### 1. `OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001`

- Owner：`Codex2`。
- Reviewer：`Antigravity`。
- `depends_on`：
  - `OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001`
  - `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002`
- 交付：resolver implementation、regression tests、premerge evidence、reviewed implementation PR。
- 此 task 不可執行來源 task 的正式 `done`；`LOOP-PROD-PLANNING-BRIEFS-002` 只有 owner `Codex` 有 closeout authority。

### 2. `OPS-WORKTREE-DELIVERY-CONTEXT-POSTMERGE-001`

- Owner：`Codex`。
- Reviewer：`Antigravity`。
- `depends_on`：
  - `OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001`
  - `OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002`
- 交付：安裝 exact merged command runtime、stale／isolated worktree proof、來源 task 真實 `done` replay、postmerge evidence-only PR。
- 此 task 也負責在 proof 成功後，以本次 planning revision PR 經 Antigravity 核准的 final head／merge 正式 close `OPS-WORKTREE-DELIVERY-CONTEXT-PLAN-001`；不得沿用已被本 revision 取代的 PR #3762 head，也不得拿 unrelated HEAD 作 delivery evidence。

Owner 與 reviewer 必須是不同 admitted fleet identity。兩個 PR 都必須保持 auto-merge 關閉；reviewer 必須在各自 final exact head 獨立重跑驗證，owner 不得以自己的輸出代替 reviewer evidence。

### Governed materialization commands

Planning revision PR 合併後，由 `Codex` 從已安裝 command runtime 執行下列 commands。CSV values 中每一 acceptance item 都刻意不含 comma；不得省略 dependency、artifact 或 metadata fields。

```bash
INSTALLED_COMMAND_ROOT=/absolute/path/to/installed/pantheon
export PANTHEON_STATUS_ROOT=/absolute/path/to/central/pantheon
AI_NAME=Codex \
TASK_PHASE='Wave -1 fleet runtime delivery-context corrective' \
TASK_SUMMARY_ZH='分離中央 canonical mutation root 與 task-worktree delivery evidence root，保留既有 closeout gates 並提供 fail-closed regression。' \
TASK_DEPENDS_ON='OPS-WORKTREE-CENTRAL-STATUS-ROOT-CORRECTIVE-001,OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002' \
TASK_ARTIFACTS='scripts/ai_status.py,scripts/test_ai_status.py,.orchestrator/multi_repo_registry.py,docs/deployment/evidence/ops-worktree-delivery-context-corrective-001/' \
TASK_ACCEPTANCE='Use validated worker delivery root while canonical writes stay central,Fail closed on workspace env lease path and repository mismatch,Preserve existing task trailer clean remote merge and ancestry settings,Prove central dirty checkout cannot contaminate clean task closeout,Publish premerge evidence and receive Antigravity exact-head approval,Merge with auto-merge disabled and close through governed done' \
TASK_BRANCH='task/OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001' \
TASK_CLASS=execution \
TASK_AUTO_CREATED_BY='OPS-WORKTREE-DELIVERY-CONTEXT-PLAN-001' \
TASK_AUTO_GENERATED=true \
TASK_MUTATES_CANONICAL=true \
TASK_METADATA_JSON='{"target_repo":"pantheon","merge_target":"dev","fleet_lane":"status-delivery-context","product_level_required":true,"source_ref":{"brief":".orchestrator/task-briefs/ops_worktree_delivery_context_corrective_001.md"},"non_goals":["No product trading change","No archive rewrite","No runtime command pin"],"dispatch_rules":["Owner and reviewer differ","Auto-merge off","Exact-head review required"]}' \
"$INSTALLED_COMMAND_ROOT/scripts/ai-status.sh" assign \
OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001 Codex2 Antigravity \
'Select and validate task-worktree delivery evidence'

AI_NAME=Codex \
TASK_PHASE='Wave -1 delivery-context postmerge proof' \
TASK_SUMMARY_ZH='安裝 delivery-context exact merge，從 stale worktree 證明中央 mutation 隔離，並由 Codex 正式重播來源 task done。' \
TASK_DEPENDS_ON='OPS-WORKTREE-DELIVERY-CONTEXT-CORRECTIVE-001,OPS-WORKTREE-CENTRAL-STATUS-ROOT-POSTMERGE-002' \
TASK_ARTIFACTS='.orchestrator/task-briefs/ops_worktree_delivery_context_corrective_001.md,docs/deployment/evidence/ops-worktree-delivery-context-postmerge-001/' \
TASK_ACCEPTANCE='Install the exact corrective merge from configured dev,Prove stale worktree commands mutate central state exactly once,Keep all delivery-worktree coordination sentinels byte-identical,Replay LOOP-PROD-PLANNING-BRIEFS-002 done from approved head,Close this planning task from its revised approved head,Publish redacted postmerge evidence through exact-head review,Merge with auto-merge disabled and close through governed done' \
TASK_BRANCH='task/OPS-WORKTREE-DELIVERY-CONTEXT-POSTMERGE-001' \
TASK_CLASS=execution \
TASK_AUTO_CREATED_BY='OPS-WORKTREE-DELIVERY-CONTEXT-PLAN-001' \
TASK_AUTO_GENERATED=true \
TASK_MUTATES_CANONICAL=true \
TASK_METADATA_JSON='{"target_repo":"pantheon","merge_target":"dev","fleet_lane":"status-delivery-postmerge","product_level_required":true,"source_ref":{"brief":".orchestrator/task-briefs/ops_worktree_delivery_context_corrective_001.md"},"non_goals":["No product trading change","No historical archive rewrite","No unmerged runtime install"],"dispatch_rules":["Owner and reviewer differ","Auto-merge off","Use installed merged runtime only"]}' \
"$INSTALLED_COMMAND_ROOT/scripts/ai-status.sh" assign \
OPS-WORKTREE-DELIVERY-CONTEXT-POSTMERGE-001 Codex Antigravity \
'Install and replay worktree delivery-context closeout'
```

## Implementation scope

- `scripts/ai_status.py`
- `scripts/test_ai_status.py`
- 必要時最小修改 `.orchestrator/multi_repo_registry.py` 與既有 registry tests。
- `docs/deployment/evidence/ops-worktree-delivery-context-corrective-001/`：只記 candidate／premerge evidence。
- `docs/deployment/evidence/ops-worktree-delivery-context-postmerge-001/`：由獨立 postmerge task 建立。

若 implementation 需要修改其他檔案，owner 必須先在中央 task note 說明原因；不得順便重構 supervisor、runtime pin、產品或部署流程。`OPS-STATUS-COMMAND-RUNTIME-PIN-001` 另負責讓 stale worktree wrapper 自動跳到已安裝 command runtime，本 task 不提前實作該功能。

## Root selection 決定表

`collect_done_delivery_metadata()` 先沿用既有 `task_primary_repository_id()`／repository registry 決定 task repository id 與 configured GitHub slug，再依下表選 delivery root；不得從目錄名稱猜 repository id。Registry path authority 必須錨定 validated canonical root，不得錨定 executable checkout：Pantheon repository anchor 是 `PANTHEON_STATUS_ROOT`；cross-repo anchor 是中央 config／registry 從 `PANTHEON_STATUS_ROOT` 解析出的 registered `local_path`。`INSTALLED_COMMAND_ROOT` 只提供受驗證的程式碼，永遠不是 repository-path authority。

| Runtime context | Workspace env | Required behavior |
|---|---|---|
| `ORCH_RUN_ID` 非空的 auto worker | 兩個 workspace env 都存在且 canonical path 相同 | 使用該 path；並以中央 supervisor runtime／worktree lease 驗證 `ORCH_RUN_ID`、command task id、workspace path 與 task branch。`ORCH_TASK_ID` 若存在必須一致；task identity 也必須從 lease 驗證，不能只信 env。任一不符，在任何 mutation 前拒絕。 |
| `ORCH_RUN_ID` 非空的 auto worker | 任一 workspace env 缺失，或兩者 canonical path 不同 | fail closed；不得 registry fallback。空白字串視為缺失。 |
| 受控 postmerge／人工 replay，沒有 `ORCH_RUN_ID` | 兩個 workspace env 都存在且 canonical path 相同 | 使用 explicit workspace root；執行完整 path、repo、commit、clean、remote、merge 驗證，metadata 標記為 `explicit_workspace_env`。 |
| 受控 postmerge／人工 replay，沒有 `ORCH_RUN_ID` | 只提供一個 workspace env 或兩者不一致 | fail closed；避免 typo 靜默改用另一 checkout。 |
| 一般非 worker command | 兩個 workspace env 都未設定 | 維持既有 repository registry fallback；不得搜尋「最近」worktree。 |

兩個 env 的比較順序為：先要求各自是 absolute path、逐 component 拒絕 symlink，再 canonicalize；canonical path 相等才算一致。不得用第一個 env 靜默遮蔽第二個。

## Delivery root fail-closed validation

選出的 workspace root 必須全部通過：

1. 是 absolute path，無 symlink component，存在且為 directory。
2. `git rev-parse --show-toplevel` 的 canonical result 必須等於 candidate 本身；candidate 不得只是 repo 子目錄。
3. candidate 的 canonical `git rev-parse --git-common-dir` 必須等於上述 canonical-root-anchored registry checkout 的 common dir，且 candidate 必須出現在該 checkout 的 `git worktree list --porcelain`。不得從 installed command checkout 的 `ROOT` 或相對 `local_path=.` 推導此 anchor。cross-repo registered `local_path` 缺失、非 git root或與 lease repository id 不符時 fail closed。這同時拒絕同名 independent clone、另一個 nested repo、submodule 與未登錄外部目錄。
4. auto worker path 必須等於中央 supervisor 對該 `ORCH_RUN_ID` 的 active workspace／worktree lease；lease 不存在、過期或 task id／branch 不符都拒絕。
5. repository id 必須來自既有 task artifact／registry resolver；configured slug 缺失時 workspace override fail closed。
6. 必須驗證 `origin`。允許的 GitHub URL 形式只有 HTTPS、SCP-style SSH 與 `ssh://git@github.com/`；移除尾端 `.git`、lowercase host 與 slug 後，必須等於 configured `owner/repo`。不得用其他 remote 代替錯誤的 `origin`。
7. 任何 workspace-env mode 的 canonical status root 與 delivery root 都必須不同；包含 auto worker 與 controlled explicit replay。只有完全沒有 workspace env 的 registry fallback 可維持既有同-root 行為。
8. branch、HEAD、subject/body/author、clean state、remote/upstream、merge target 與 ancestry 必須全部從 delivery root 讀取。

受控 replay 可使用同 slug 的 registered clean worktree；其可信 identity 來自 configured repository common dir、worktree registration、exact approved commit、task trailers、remote slug 與 merge ancestry。wrong repo、independent clone、repo 子目錄、nested repo、symlink 或只有相同目錄名稱都不得通過。

## Lease authority 與 lock ordering

- auto-worker lease authority 是中央 `PANTHEON_STATUS_ROOT/.orchestrator/state.json` 的 matching worker record 與 `worker_worktrees.leases` entry；使用 `.orchestrator/runtime_state.py` 的既有 `runtime_state_lock()` consistent-read primitive，不可 unlocked parse 或另造第二份 lease truth。
- 必須遵守 `docs/deployment/fleet-runtime-lock-protocol.md` 的 `runtime_admission -> task_state -> activity_audit` order。`collect_done_delivery_metadata()` 不得在已持有 task-state lock 時再取得 runtime lock。
- `done` 改為 two-phase preflight／commit：先以 runtime shared lock再 task shared lock snapshot task + lease identity並產生 immutable digest，釋放後在無 canonical lock 下收集 git／remote evidence；commit phase 再以 runtime shared lock後 task exclusive lock重讀並核對相同 lease、task status／owner／review identity、delivery root、HEAD 與 snapshot digest，只有全相同才寫入，再依既有順序進 activity lock。
- explicit replay 沒有 runtime lease，但仍使用 task shared snapshot、lock-free git evidence、task exclusive compare-and-commit。任何 phase 間 task／lease／HEAD 變化都 fail closed 且 canonical zero mutation。
- 新增 concurrency regression：lease 被替換、task 被重派或 HEAD 在兩 phase 間改變時拒絕；同時 runtime/task writer 不可 deadlock，lock-order guard不得報 reverse nesting。

## Mutation boundary 與 metadata schema

- `PANTHEON_STATUS_ROOT` 仍是 canonical mutation root；status、activity、derived `current-work.md`／docs-site bundle、archive、outbox 與 locks 只可位於該 root。
- delivery root 只供 git／delivery evidence。既有 merged gate 可執行 narrow `git fetch <remote> <target>`，所以 `.git` metadata 可能改變；不得修改 delivery worktree 的 tracked coordination files、archive 或 working-tree content。
- `show` 是 read-only；在 fixture 無 pending outbox 時必須零 data mutation。`note`、`handoff`、`approve` 應只改中央 status/activity/derived/outbox surfaces。`done` 另可改中央 archive/index。中央 stable lock sidecars 可預先存在並跨 process 持續存在；不得要求 command 後刪除它們，也不得替換其 inode。
- 每個 mutation test 必須 snapshot task worktree 的 `ai-status.json`、`ai-activity-log.jsonl`、`current-work.md`、docs-site mirrors、archive/index、outbox 與 lock parents；command 後 bytes 與 file inventory皆不變。`.git` metadata 另列為 fetch allowlist，不混入 tracked sentinel claim。

在保留既有 delivery fields 的同時，至少新增／明確填入：

- `repository_path`：validated canonical delivery root。
- `repository_path_source`：`worker_lease`、`explicit_workspace_env` 或 `repository_registry`。
- `workspace_env_names`：實際使用且已比對的 env names。
- `workspace_env_match`：boolean。
- `workspace_lease_validated`：auto worker 為 `true`；非 worker為 `false` 並附 mode。
- `canonical_status_root` 與 `canonical_status_root_source`。
- `roots_separated`。
- 既有 `repository_id`、`repository_slug`、branch、commit、subject、author、clean、remote/upstream、push status、merge target SHA 與 ancestry fields。
- replay-only `push_gate_required`、`push_gate_outcome` 與 supporting PR／upstream evidence。

不得把 secret、token、完整 process env 或原始 provider error 寫進 metadata。

## Delivery gates

不得放寬既有 task-id subject、required trailers、owner/reviewer、remote status、merged target 與 ancestry gate。實作必須把既有 gate 設定套在 delivery root，而不是把目前非強制的設定偷改成全域強制：

- 保留 `TASK_RECORD_REMOTE_STATUS=true` 的 record-only 語義，另在 `delivery_gate_settings()` 加入 default `false` 的 opt-in `TASK_REQUIRE_PUSHED`／`require_pushed`。regression 與真實 replay 明確設定 `TASK_REQUIRE_COMMIT_HASH=true`、`TASK_REQUIRE_GIT_CLEAN=true`、`TASK_RECORD_REMOTE_STATUS=true`、`TASK_REQUIRE_PUSHED=true`、`TASK_REQUIRE_MERGED_PR=true`。
- `done` 的 two-phase evidence preflight 必須直接呼叫新增的 `validate_delivery_push_gate()`（名稱可調整，但必須是 `done` 內的 mandatory gate，不是 operator prose）。`in_sync` 通過；若 task branch 已被 GitHub merge 後自動刪除，只有在 `gh pr view <branch>` 回報 `state=MERGED`、exact `headRefOid=HEAD`、configured base target與可驗證 merge commit時，`no_upstream` 才通過。`ahead`、`behind`、`diverged`、detached／unknown branch，或沒有 exact merged-PR evidence 的 `no_upstream` 都在 canonical mutation 前拒絕。
- preflight 必須保存以下命令的 raw output／exit code，不得只寫結論：`git -C "$DELIVERY_ROOT" status --short`、`git -C "$DELIVERY_ROOT" rev-parse HEAD`、`git -C "$DELIVERY_ROOT" rev-parse --abbrev-ref HEAD`、`git -C "$DELIVERY_ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{upstream}'`、`git -C "$DELIVERY_ROOT" rev-list --left-right --count '@{upstream}...HEAD'`、`gh pr view "$PR_NUMBER" --json state,headRefOid,mergeCommit,baseRefName,autoMergeRequest,url`。auto-deleted branch exception 必須明記 upstream lookup 的 non-zero exit。
- merged gate 保留現行 fetch + `HEAD` ancestor of configured target semantics；GitHub PR number、headRefOid、mergeCommit 與 `autoMergeRequest=null` 另寫 reviewer／postmerge evidence。網路不可用且本地 target ref 無法驗證時 fail closed。
- 禁止以中央 checkout 的正確 HEAD 補救 delivery worktree 的錯誤 HEAD，或反向用中央 clean state 補救 dirty task worktree。

## Required regression tests

先加入可在修正前穩定失敗的 regression，再實作。測試名稱可調整，但涵蓋不得縮減：

1. 中央 checkout 在其他 branch 且 dirty；task worktree clean、HEAD／trailers 正確、remote in sync、HEAD 已合併時，`done` 成功且 metadata 指向 task worktree。
2. 上述成功案例只改中央 temp status/activity/derived/archive/outbox/lock surfaces；task worktree 全部 sentinel bytes 與 inventory 不變。
3. task worktree HEAD 缺 task id 時拒絕，即使中央 checkout HEAD 正確也不得通過。
4. task worktree dirty 時拒絕，即使中央 checkout clean 也不得通過。
5. `origin` slug／repository identity 錯誤或 configured slug 缺失時拒絕。
6. workspace path 為 relative、symlink component、repo 子目錄、nested repo、independent same-slug clone、unregistered worktree、non-git 或不存在時拒絕。
7. auto worker 兩個 workspace env 不一致、任一缺失、lease 不存在、run/task/branch 不符時拒絕且中央零 mutation。
8. 無 `ORCH_RUN_ID` 的 explicit replay 兩個 env 不一致或任一缺失時拒絕。
9. 任一 workspace env mode 指向 canonical status root 時拒絕；完全無 workspace env 才可走 registry fallback，並記錄 `repository_path_source=repository_registry`。
10. active lease replaced／expired、task reassign 或 HEAD race 的 two-phase tests 必須 fail closed且不違反 runtime→task→audit lock order。
11. `show`、`note`、`handoff`、`approve`、`done` 逐 command 驗證中央 expected-change matrix 與 worktree byte-identical contract；`show` fixture 必須無 pending recovery outbox。
12. `TASK_REQUIRE_GIT_CLEAN=false` 的既有 configuration 行為不被偷偷收緊；設為 `true` 時只檢查 delivery root。
13. `TASK_RECORD_REMOTE_STATUS=true` 繼續只記錄 `in_sync`／`ahead`／`behind`／`diverged`／`no_upstream`；`TASK_REQUIRE_PUSHED=true` 則由 `done` mandatory gate 測唯一 accepted states、auto-deleted exact merged-branch exception、wrong `headRefOid`／base／merge evidence與 zero-mutation rejection。
14. merged-gate fetch 只允許 `.git` metadata 變化，tracked worktree sentinel 不變。
15. 以 PR #3759 final head `da0ae611...` 與 merge `290ed7df...` 建 fixture，證明修正前選錯中央 checkout而失敗、修正後選 delivery root 通過；fixture 不得修改 live canonical state。

## Exact verification commands

Owner 在 compose 當時最新 `origin/dev` 後，至少執行：

```bash
python3 scripts/test_ai_status.py
python3 .orchestrator/test_common.py
python3 .orchestrator/test_supervisor.py
python3 .orchestrator/test_worker_runner_heartbeat.py
PYTHONPATH=.orchestrator python3 -m pytest .orchestrator/test_auto_commit_archive.py
PYTHONPATH=.orchestrator python3 -m pytest .orchestrator/test_task_archive_index_legacy_id.py
python3 -m py_compile scripts/ai_status.py scripts/test_ai_status.py .orchestrator/multi_repo_registry.py
```

若某 test file 的標準 runner 需要 `PYTHONPATH=.orchestrator`，使用 repository 既有 invocation 並在 evidence 原樣記錄。所有命令需記 exit code、test count、base SHA、candidate SHA、`git status --short`、changed source/test checksums 與 runner version。不可要求 evidence manifest checksum 自己或在同一 commit 內預知自己的 commit SHA；candidate head 由 reviewer approval／CI artifact記錄，implementation merge SHA 由 postmerge evidence 記錄。

Reviewer 必須在獨立 clean checkout 的 exact PR `headRefOid` 重跑同一組 commands，核對 changed-file allowlist、negative cases、metadata schema、auto-merge disabled 與 GitHub checks。review 後不得新增 commit而沿用舊 approval。

## PR flow（auto-merge 關閉）

標準 `task_finalize.sh`／一般 `safe_pr.sh` 會啟用 auto-merge，因此本 task 不得使用它們。兩個 slices 都使用：

1. `worker_commit.py` + private index + explicit scope 建 commit。
2. normal non-force `git push -u origin <task-branch>`。
3. `gh pr create --base dev --head <task-branch>`；不得加 auto-merge label，也不得執行 `gh pr merge --auto`。
4. Antigravity 對 final exact head 完成 governed approval，確認 `autoMergeRequest=null`。
5. approval 後才由 owner／chair 執行一次普通 `gh pr merge <PR> --merge`。
6. merge 後由該 task owner 從 exact task delivery worktree執行 governed `done`：implementation slice 由 `Codex2` close；postmerge evidence PR 則由 `Codex` close。postmerge dependency 只有在 implementation active record 已正式歸檔為 completed 後才算滿足。

Implementation PR 只含 code、tests 與 premerge evidence。真正安裝與 live replay 不得偽裝成 implementation PR 內已完成；它們由 postmerge slice 在 implementation merge 後執行，再以 evidence-only PR 經 exact-head review 合併。

## Postmerge true replay runbook

1. 將 implementation PR 的 exact merge SHA 安裝到受管 command root，記錄 old/new process identity、installed SHA、remote slug 與 configured dev ancestry。不得從未合併 task branch啟動。
2. 從一個故意 stale／isolated worktree執行 `show` 與一次只針對 postmerge task 的受管 `note` probe；證明 command 只改中央 surfaces 一次、不改 lifecycle、worktree tracked sentinels byte-identical、delivery/test cwd仍是 worktree。
3. 驗證來源 worktree `/tmp/pantheon-planning-fleet-corrective-briefs`（若 lease path 改變，以 exact branch + SHA 找到 validated clean worktree，不可猜最近目錄）：
   - branch `plan/fleet-corrective-briefs-20260716`
   - HEAD `da0ae61140278251a7b8fb35bf183aff658fef1b`
   - `origin` 為 `ajoe734/pantheon`
   - clean、upstream可稽核、HEAD 是 configured `origin/dev` ancestor
   - PR #3759 merge commit為 `290ed7df72a745dcef486cf65b3c9d06eaa2de4b`
4. 因 `OPS-STATUS-COMMAND-RUNTIME-PIN-001` 尚未交付，replay 必須執行已安裝的新 command runtime，而不是 stale worktree-local `scripts/ai_status.py`。command cwd／env 明確設成：
   - executable：`$INSTALLED_COMMAND_ROOT/scripts/ai-status.sh`，其中 `INSTALLED_COMMAND_ROOT` 是已驗證 exact merge 的 absolute path
   - `export PANTHEON_STATUS_ROOT=<central-root>`；steps 5–7 的每個 governed command 都必須繼承同一 validated binding，不可 fallback 到 installed checkout
   - `PANTHEON_WORKTREE_ROOT=<source-worktree>`
   - `ORCH_WORKSPACE_PATH=<source-worktree>`
   - `AI_NAME=Codex`
   - unset `ORCH_RUN_ID`、`ORCH_TASK_ID`、`ORCH_RUNNER_STATUS_PATH`、`ORCH_HEARTBEAT_PATH`，明確走 controlled explicit replay mode
   - 上述五個 delivery gate env（包含 `TASK_REQUIRE_PUSHED`）全設為 `true`
5. 在任何 state transition 前再次核對 exact head 與 prior review notes，並先執行以下 governed reconciliation（title 必須維持現值）：
   ```bash
   AI_NAME=Codex \
   TASK_METADATA_JSON='{"approved_delivery_head":"da0ae61140278251a7b8fb35bf183aff658fef1b","superseded_candidate_head":"af8f0bbf2683b442a4b06b02db93c7ead7ef182d","source_pr":3759,"source_merge_sha":"290ed7df72a745dcef486cf65b3c9d06eaa2de4b"}' \
   "$INSTALLED_COMMAND_ROOT/scripts/ai-status.sh" assign \
   LOOP-PROD-PLANNING-BRIEFS-002 Codex Antigravity \
   'Review final ten-file fleet planning publication'
   AI_NAME=Codex "$INSTALLED_COMMAND_ROOT/scripts/ai-status.sh" note \
   LOOP-PROD-PLANNING-BRIEFS-002 \
   'Historical candidate af8f0bbf was superseded by governed approved head da0ae611; replay authority is PR #3759 merge 290ed7df.'
   ```
   若一致，由 owner `Codex` 依序使用 governed `reopen`（`blocked -> in_progress`）、`restore_approved`（保留 exact-head approval）、`done`；任一步拒絕就停止，禁止手改 state/archive。
6. 核對 `LOOP-PROD-PLANNING-BRIEFS-002` 已從 active state 移除、archive snapshot為 completed，delivery metadata 指向來源 worktree／`da0ae611...`，而中央 checkout branch、HEAD與 dirty count沒有被當成 delivery evidence。
7. 用同一 installed runtime close 本 planning task，但 delivery evidence 必須是「本次 revision PR」經 Antigravity 新核准並合併的 final head，而不是舊 PR #3762 head。review approval 必須把 PR number、headRefOid、merge commit與 branch寫入中央 task metadata／review notes。使用 configured repository common dir內的 registered clean worktree；必要時由正常 lease cleanup移除 supervisor-generated scratch，或建立指向 exact head 的 registered replay worktree，不得提交 scratch。status lifecycle依當時狀態唯一處理：
   ```bash
   PLANNING_METADATA="$(jq -cn \
     --argjson pr "$PLANNING_PR_NUMBER" \
     --arg head "$PLANNING_HEAD" \
     --arg merge "$PLANNING_MERGE" \
     --arg branch "$PLANNING_BRANCH" \
     '{approved_delivery_pr:$pr,approved_delivery_head:$head,approved_delivery_merge:$merge,approved_delivery_branch:$branch}')"
   AI_NAME=Codex TASK_METADATA_JSON="$PLANNING_METADATA" \
   "$INSTALLED_COMMAND_ROOT/scripts/ai-status.sh" assign \
   OPS-WORKTREE-DELIVERY-CONTEXT-PLAN-001 Codex Antigravity \
   'Publish worktree delivery-context corrective brief'
   AI_NAME=Codex "$INSTALLED_COMMAND_ROOT/scripts/ai-status.sh" note \
   OPS-WORKTREE-DELIVERY-CONTEXT-PLAN-001 \
   "Closeout authority is revision PR $PLANNING_PR_NUMBER head $PLANNING_HEAD merge $PLANNING_MERGE."
   ```
   - `review_approved`：直接 `done`。
   - `in_progress` 且保留本次 exact-head review notes：`restore_approved -> done`。
   - `blocked` 且保留本次 exact-head review notes：`reopen -> restore_approved -> done`。
   - 其他狀態、head 或 review mismatch：停止並重新 `handoff -> Antigravity approve`；不得復用舊 approval。
8. 發布 redacted postmerge README／manifest：記 implementation PR/head/merge、installed runtime SHA、source PR/head/merge、before/after central hashes、worktree sentinel hashes、exact commands/exit counts、archive delivery metadata、residual risk。不得寫 secrets或重寫歷史 archive。

## 不在範圍

- 不修改產品交易行為、BFF auth、frontend、deploy workflow 或 broker 狀態。
- 不重寫任何既有 archive snapshot 或 activity log 歷史。
- 不清理、stash、reset 或提交中央 dirty checkout 的其他人檔案。
- 不繞過 task-id、trailers、clean、remote、PR merge、reviewer 或 ancestry gate。
- 不以手改 `ai-status.json`／archive JSON 取代正式 `done` 驗收。
- 不在本 task 實作 stale wrapper command-runtime pin；該邊界屬於 `OPS-STATUS-COMMAND-RUNTIME-PIN-001`。

## 完成定義

只有在下列全部成立後才可完成：

- implementation 與 postmerge tasks 已按上述 machine-readable dependencies 建立；
- implementation PR 已由 Antigravity 對 final exact head 核准、auto-merge 關閉並合併；
- exact merge 已安裝，兩個真實 proof 成功，postmerge evidence-only PR 已獨立核准並合併；
- `LOOP-PROD-PLANNING-BRIEFS-002` 與本 planning task 都由 owner `Codex` 透過 installed governed command 正式歸檔；
- delivery metadata 指向各自 validated delivery worktree，而所有 canonical mutation 只出現在中央 `PANTHEON_STATUS_ROOT`。

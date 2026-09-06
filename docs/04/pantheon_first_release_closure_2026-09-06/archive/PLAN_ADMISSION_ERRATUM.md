# 文件入庫狀態更正：原版已合併，補充版待交付

2026-09-06 再次核對 current dev 及 GitHub PR 後，修正本對話先前將本地 untracked 等同未入庫的錯誤判斷。此為事實更正，不改變已簽 packet、檔案 bytes、task acceptance、責任切片或架構排序。

## 已合併的原版

`docs/04/pantheon_current_full_gap_audit_2026-09-03/` 的 INDEX、REPORT、SA、SD、TRACEABILITY、EXECUTION_TASKS、tasks.json **已正式 commit、push、PR、merge 到 dev**：

- PR [#5551](https://github.com/ajoe734/pantheon/pull/5551)，base dev，2026-09-04T00:47:22Z merged。
- 原始 head `7a741afd811ba8cd31885a07bc783d32d5353161`。
- Merge commit `87134886b7438e2db4b698cedfa0eb4eff9cb202`。
- 此 commit 在先前 baseline `471dc5391a0f9cbde54d51730891583043708e42` 之前；不是本次才完成的交付。current remote dev 再核為 `70e7abadaa4800f6d58acbbe3189a76c932d149d`。
- 逐一比對 original /tmp snapshot 與 current-dev audit worktree：REPORT、SA、SD、TRACEABILITY、EXECUTION_TASKS、tasks.json 共6個檔案 bytes 相同；INDEX.md 不同，不能宣稱全部7個檔案一致。原版 independent-review evidence 亦已隨 docs 入庫。入庫事實由上述 commit/PR 與 dev ancestry 證明，不能用較新本地 INDEX 的差異否定。

先前對共享舊 checkout 的 `git ls-files` 及 /tmp audit worktree 的 `git status` 檢查，只能證明那些 checkout 的本地追蹤狀態；不能證明 remote dev 沒有相同文件。前述「原始盤點也未 commit／push／merge」結論有誤，已明確向操作者更正。不得把 PLAN-ADMIT-001 記作未交付或要求 worker 重新複製原版。

## 仍待交付的部分

9/6 的補充 SA/SD、差異／交接報告與本次 APPROVAL_RELEASE_SA_SD 仍由 `DOC-FIRST-RELEASE-PLAN-DELIVERY-001` 正式交付；03:51 回讀為 in_progress，原版已被該 worker 的 SOURCE_MANIFEST 分類為 `already_merged_active_source`。這一點不是新補充文件已 merge 的證明。

已簽 `APPROVAL_RELEASE_SA_SD.md` SHA256 `6a6bbd28f45b7b052b19d09b2f0bc5b20e0e37ca2620f722e1a40cbbc9fa7157` 不覆寫。其第1節／文件缺口措辭應依本事實更正解讀；其文件task已明定先檢查既有 merged source、重用而非新增第二份，仍可照原 acceptance 完成真正未交付的補充版。

文件 owner 應於既有 docs 範圍內，將本更正納入 current INDEX/狀態與 provenance，保留 immutable snapshots 的原 bytes 並連到更正，不重新簽造舊資料。此更正不授權放寬功能驗收、改動 canonical metadata、修改產品 source 或略過 independent review。

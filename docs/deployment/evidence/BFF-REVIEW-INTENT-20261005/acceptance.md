# Evolution review-intent 驗收更正（2026-10-05）

使用者要求撤除只記錄審查意向所增加的核准層。此更正取代
BFF-COMMANDTYPE-RESIDUE-20261003 驗收 1／4，以及
BFF-COMMANDTYPE-CONTINUATION-20261004 驗收 1／4／7 中要求這個動作
使用核准單、確認 token、雙人簽核的部分；原封存紀錄保留為歷史證據。
更正已透過 Human/Ops 工具登錄於 BFF-REVIEW-INTENT-20261005，未派實作 worker。

## 判定與保留邊界

`ProgramService.execute_action(promote_candidate_live)` 只將意向存入 program
的 promotions；結果明示 capital_authority=none 與 runtime_authority=none。
owner 不要求 approval_id，這個欄位只是選填資料，沒有授予資金權限。
因此 BFF 的額外核准單、confirm token、two-man 均無必要。
既有 approver/admin 角色、登入、租戶、冪等和 owner 的 frozen 檢查保留。
名稱含 Live 或 CRITICAL 標籤不構成新增核准流程的依據。

真正增加資金風險的 Capital owner 與實際 Evolution dispatch 仍在執行前
核對核准；本次不更改它們的行為，不執行 hosted 真錢操作。

## 更正後驗收

1. This operator-requested correction supersedes acceptance 1 and 4 of archived BFF-COMMANDTYPE-RESIDUE-20261003 and acceptance 1 and 4 and 7 of archived BFF-COMMANDTYPE-CONTINUATION-20261004 only where they mandate approval or confirm token or two-man evidence for live-promotion review intent. Historical archives remain evidence of the error; these requirements must not be inherited by later work.

2. Classify PromoteEvolutionCandidateLive by actual owner effects: it only records review intent with capital_authority=none and runtime_authority=none. No separate approval decision or confirmation token or two-man signature is required. Preserve current authenticated roles and tenant boundaries.

3. The existing resource route and generic command route and all supported aliases must forward to the same Evolution owner without approval evidence; verify persisted intent and unchanged program status and idempotent replay. Remove the added confirmation-token special branch and the tests imposing universal CRITICAL multi-gating; no exception lists or alternative write paths.

4. Capital and actual execution owners retain approval verification before real risk-increasing effects. Run focused Capital guard and Evolution dispatch approval regressions; do not change their policies or perform hosted real-money operations.

5. Keep the existing command completeness and retirement assertions; no restored retired actions. Net production lines must decrease. Source changes and merge are distinct from deployment and hosted acceptance.


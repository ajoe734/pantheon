# EXEC-REBASE-EW04-001 Review

Reviewer: `Codex`
Date: `2026-04-20`
Disposition: `changes_requested`

## Findings

1. `EW-04` 的 canonical prompt 仍要求前端不要開 production 頁並先顯示 blocked placeholder，和本次 rebaseline 的 `status: live` / `status: ready` 真相相衝突。[.coordination/responses/EW-04-inspiration-graph-lovable-prompt.md](/home/lupin/code/pantheon/.coordination/responses/EW-04-inspiration-graph-lovable-prompt.md:3) 仍寫著「Do not start the production page... render an explicit "coming soon / blocked by Pantheon BFF" placeholder」，但 [.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml](/home/lupin/code/pantheon/.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml:5) 和 [.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml](/home/lupin/code/pantheon/.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml:4) 已經宣告 route live、UI ready。這會讓前端 lane 收到互相矛盾的指示。

2. PKT-003 mirror packet 也還保留舊 gate 語意，代表「相關 backlog / packet wording 同步完成」尚未達成。[.coordination/responses/PKT-003-inspiration-graph-lovable-prompt.md](/home/lupin/code/pantheon/.coordination/responses/PKT-003-inspiration-graph-lovable-prompt.md:14) 仍要求「do not start production UI until BFF confirms route is live」，而 [docs/screens/PKT-003-inspiration-graph.md](/home/lupin/code/pantheon/docs/screens/PKT-003-inspiration-graph.md:8) 仍標成 `contract-published`、[同檔第 13 行](/home/lupin/code/pantheon/docs/screens/PKT-003-inspiration-graph.md:13) 仍寫 UI 不可開始。這和已更新的 live route truth 不一致，也會讓 mirrored PKT-003 鏈條繼續停留在 pending-bff 敘事。

## Verification

- Confirmed the BFF route is actually implemented at `GET /api/v1/lineage/inspiration/{artifact_id}` in [services/control-plane/bff/main.py](/home/lupin/code/pantheon/services/control-plane/bff/main.py:7904) and covered by [services/control-plane/bff/test_ew04_inspiration_graph_contract.py](/home/lupin/code/pantheon/services/control-plane/bff/test_ew04_inspiration_graph_contract.py:34).
- Confirmed the rebaseline intent is already reflected in [.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml](/home/lupin/code/pantheon/.coordination/responses/EW-04-inspiration-graph-contract-ready.yaml:5) and [.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml](/home/lupin/code/pantheon/.coordination/responses/EW-04-inspiration-graph-lovable-ui-task.yaml:4).
- Requested fix: align the EW-04 / PKT-003 prompts and the PKT-003 screen spec to the same route-live / build-now truth before resubmitting for review.

# Task Brief: AG-FE-DYNUI-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Trading Room grid editor and personalization events
- Status: owner_finalization
- Owner: Codex2
- Reviewer: Codex
- Next: execute-plans PR #82 merged to dev and deployed to Pantheon dev FE; owner closeout evidence is being committed before status `done`.

## Summary
動工前必讀 /home/lupin/code/pantheon/AI Trading Desk Design.zip；主要檔案為 uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V10_Expert_Strategy_Dialogue_2026-06-18.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V6_MultiStrategy_Dashboard_2026-06-18.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V4_AI_Dashboard_Control_2026-05-20.md、Agora.dc.html、screenshots/01-v10-mid.png、02-v10-mid.png、01-applied.png、01-aifix.png。 依 V11 §6/§7/§9/§10 建立 dashboard/view editor runtime：view tabs、grid drop targets、drag handles、resize handles、remove/restore/more menu、add widget、change chart、duplicate、save/discard unsaved changes、PATCH layout、版本 bump、personalization event。佈局必須使用 TradingRoomWidgetSpec.placement，不可用硬編 CSS 排列假裝可拖曳。 【有疑問一定要提出,不要自己亂做】若設計稿讀不到、V10/V11 與既有 schema 或 code 衝突、依賴不清、欄位/route/widget/互動未定義、或驗收不可重現,一律 STOP 並開 blocker；不得自行補欄位、補 route、補 widget、改語意、繞過 validator、或先做再說。 【這不是靜態頁面切版】交付必須支援 V10/V11 的動態 UI 系統：Strategy Workshop 由事件/stream 驅動；Trading Room 由 TradingRoomWorkspaceProposal 生成完整 views/widgets；widget 以受控 WidgetSpec/ChartSpec 宣告；trader 可 drag/resize/add/remove/restore/change chart；點 widget 可開啟帶 context 的 servant adjustment；servant 只能先產生 WidgetRevisionProposal 與 before/after preview；workspace version/change log/rollback 必須可用。 【UI 一律照設計稿,不要自己發想】畫面、版面、元件、widget menu、drawer、preview、文案、樣式、互動狀態都要對齊設計包與 docs/04/agora_design_pack_dynui_2026-06-28/README.md；不得用白底舊版 skeleton、不得做 landing page、不得把設計稿降級為一組硬編 mock cards。 【安全邊界】Agora 不得直接下單、不得綁資金、不得暴露 Management/RuntimeBinding/broker 後台詞彙；agent 不得生成任意 React/JavaScript/HTML 並注入 production；所有 widget/chart 必須通過 allowlist validator。

## Closeout Evidence

Recorded by `Codex2` on `2026-06-29` for owner finalization.

| Check | Result |
|---|---|
| Frontend delivery PR | `ajoe734/execute-plans#82` merged into `dev` at `98516d129e377842f1d5866af61e326134751439` on `2026-06-29T09:11:36Z`. |
| Frontend implementation head | `e16e6950091eb42ad6754135f0cd291df17efeac`, subject `AG-FE-DYNUI-003: add trading room grid editor`. |
| Dependency composition | PR #82 included AG-FE-DYNUI-002 preview/shell commits `a304b9a` and `efe0a55` because execute-plans `dev` did not yet contain the upstream PR #81 surface. |
| PR gate | PR #82 `integration-gate` passed. |
| Dev merge gate | Dev push workflow `Pantheon FE-BFF Integration Gate` run `28361255757` passed for merge commit `98516d129e377842f1d5866af61e326134751439`. |
| Dev deploy | `Pantheon Dev FE Deploy` run `28361255925` passed for the same commit. |
| Hosted deployment readback | `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io/deployment.json` reports commit `98516d129e377842f1d5866af61e326134751439`, source branch `dev`, `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, and `VITE_BFF_REAL_WRITES=false`. |
| Local focused validation | `npm test -- src/lib/bff-v1/agora/tradingRoom.test.ts src/agora/pages/trading-room/TradingRoomPage.test.tsx src/agora/widgets/registry.test.ts src/agora/TradingDeskLayout.test.tsx` passed 95 tests. |
| Local lint/build | Scoped `npx eslint ...` passed, `git diff --check` passed, and `npm run build` passed with existing Vite warnings. |
| Pantheon closeout artifact | `support/evidence/AG-FE-DYNUI-003/owner-closeout.md` records full PR, workflow, deployment, validation, and boundary evidence. |

Status closeout should run after this owner closeout evidence PR merges:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh done AG-FE-DYNUI-003 "<closeout message>"
```

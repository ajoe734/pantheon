# Task Brief: AG-FE-DYNUI-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Design-pack visual parity on top of dynamic runtime
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: dark AGORA visual parity complete; PR #2622 open with auto-merge; waiting for merge to run done

## Summary
動工前必讀 /home/lupin/code/pantheon/AI Trading Desk Design.zip；主要檔案為 uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V10_Expert_Strategy_Dialogue_2026-06-18.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V6_MultiStrategy_Dashboard_2026-06-18.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V4_AI_Dashboard_Control_2026-05-20.md、Agora.dc.html、screenshots/01-v10-mid.png、02-v10-mid.png、01-applied.png、01-aifix.png。 在 AG-FE-DYNUI-001~004 完成後才做視覺 parity。依 screenshots 與 Agora.dc.html 對齊 dark AGORA shell、Strategy Workshop、Trading Room proposal、dashboard editor、widget menu、revision drawer、change log、versions modal。這個任務只負責讓已存在的動態 runtime 長得像設計稿，不得改回 hardcoded demo UI。 【有疑問一定要提出,不要自己亂做】若設計稿讀不到、V10/V11 與既有 schema 或 code 衝突、依賴不清、欄位/route/widget/互動未定義、或驗收不可重現,一律 STOP 並開 blocker；不得自行補欄位、補 route、補 widget、改語意、繞過 validator、或先做再說。 【這不是靜態頁面切版】交付必須支援 V10/V11 的動態 UI 系統：Strategy Workshop 由事件/stream 驅動；Trading Room 由 TradingRoomWorkspaceProposal 生成完整 views/widgets；widget 以受控 WidgetSpec/ChartSpec 宣告；trader 可 drag/resize/add/remove/restore/change chart；點 widget 可開啟帶 context 的 servant adjustment；servant 只能先產生 WidgetRevisionProposal 與 before/after preview；workspace version/change log/rollback 必須可用。 【UI 一律照設計稿,不要自己發想】畫面、版面、元件、widget menu、drawer、preview、文案、樣式、互動狀態都要對齊設計包與 docs/04/agora_design_pack_dynui_2026-06-28/README.md；不得用白底舊版 skeleton、不得做 landing page、不得把設計稿降級為一組硬編 mock cards。 【安全邊界】Agora 不得直接下單、不得綁資金、不得暴露 Management/RuntimeBinding/broker 後台詞彙；agent 不得生成任意 React/JavaScript/HTML 並注入 production；所有 widget/chart 必須通過 allowlist validator。

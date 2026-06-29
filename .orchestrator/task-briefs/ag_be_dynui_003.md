# Task Brief: AG-BE-DYNUI-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Servant workspace generator and safe widget validator
- Status: review_approved
- Owner: Codex2
- Reviewer: Codex
- Next: Review approved by Codex: servant workspace generator stays declarative, registry/route validators remain enforced, generator metadata is persisted/readable, and focused validation passed. Owner Codex2 should run closeout done after preserving merged PR #2585 evidence.

## Summary
動工前必讀 /home/lupin/code/pantheon/AI Trading Desk Design.zip；主要檔案為 uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V10_Expert_Strategy_Dialogue_2026-06-18.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V11_WinnerBranch_TradingRoom_2026-06-19.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V6_MultiStrategy_Dashboard_2026-06-18.md、uploads/Pathreon_Agora_ClaudeDesign_UI_Requirement_V4_AI_Dashboard_Control_2026-05-20.md、Agora.dc.html、screenshots/01-v10-mid.png、02-v10-mid.png、01-applied.png、01-aifix.png。整合 trading servant workspace generator：由 ready StrategySpec version 產生完整 TradingRoomWorkspaceProposal，包含 V11 要求的多 view workspace、widget reasons、data availability、warnings、personalizationApplied。所有 WidgetSpec/ChartSpec 必須走 allowlist validator；若 renderer 不支援，回傳 supported fallback 或建立新 component task request，不得任意生成 frontend code。

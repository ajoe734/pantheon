# Task Brief: AG-DYNUI-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora standalone workbench shell
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Closeout harness guidance: do not require the throwaway /tmp/agdynui002-capture.mjs networkidle run to pass. Hosted app keeps /bff/events/stream open, so Playwright waitUntil=networkidle can timeout and desktop can be captured too early. Use the already-captured stable proof at /tmp/agora-dynui-prod-002-hosted-proof-20260704T1308Z/ (desktop+mobile JSON/PNG; pantheon-dev tenant; BFF 200 for events stream, trading-room, decision-events; Dynamic Entry visible; no Failed to load Trading Room). If recapturing, use domcontentloaded plus a UI text wait for Dynamic Entry/Open Strategy Workshop, not networkidle.

## Summary
修 execute-plans Agora shell 架構：/agora/* 不應只是被包在 Management PlatformShell 裡的 tab skeleton；建立符合設計稿的 Agora workbench shell 或提交明確批准的例外，並保留 auth/live 狀態。

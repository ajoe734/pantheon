# Task Brief: MPOS-P1-TEL-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Extend telemetry runtime summary and reconciliation to canary and live
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Review approved by Claude2 (2026-06-09). 23 tests pass: 11 projection + 6 stage reconciliation + 6 paper regression. stage-aware health keys, canary/live reconcile endpoints, proposed-only evolution handoff all meet acceptance criteria. Review file: .orchestrator/reviews/mpos_p1_tel_001_review_claude2.md. Returning to owner for finalization.

## Summary
把 telemetry projection 與 reconciliation 從 paper 偏向補到 canary/live，讓 live 後回饋閉環能比較 stage drift 並觸發 incident/evolution。

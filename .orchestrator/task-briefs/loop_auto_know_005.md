# Task Brief: LOOP-AUTO-KNOW-005

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add human imitation and shadow evaluation scheduler
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Review approved: scheduler worker, shadow-eval-tick endpoint, candidate store, docker-compose profile, and tests all verified. Production training remains fail-closed. Returning to owner Claude for finalization.

## Summary
新增 trace dataset 到 imitation/shadow eval 的 scheduled loop，產生 gated candidates 而不直接影響 running artifact。

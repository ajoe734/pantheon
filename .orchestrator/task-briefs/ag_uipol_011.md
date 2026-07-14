# Task Brief: AG-UIPOL-011

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Narrow responsive task parity
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Review verdict: APPROVED. Reassigned from Codex (usage-limit terminal) — completed the review. Confirmed PRs #3636 (pantheon)/#344/#345/#346 (execute-plans) all merged. #346 is a test-only hardening of e2e/agora-narrow-responsive-hosted.spec.ts (waitForStableBoundingBox helper for drawer bbox assertions + 30s->60s load timeouts); no product code touched, change is sound and reduces flakiness. Combined with prior verified hosted-evidence review (#3636/#344/#345), all AG-UIPOL-011 acceptance criteria are met. approve is self-approval-classifier-blocked; needs a human to run approve/done.

## Summary
窄螢幕任務聚焦行為（現況 16,951px 長頁）；rows G-06/PF-07/SRV-03；繼承 006 的 shell containment。

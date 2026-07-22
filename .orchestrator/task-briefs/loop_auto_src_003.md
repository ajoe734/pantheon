# Task Brief: LOOP-AUTO-SRC-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Harden source scheduler supervision
- Status: review_approved
- Owner: Claude
- Reviewer: Codex
- Next: Review approved: source scheduler supervision hardening meets acceptance; owner should finalize after PR merge.

## Summary
讓 source scheduler 變成 required supervised worker，補 restart、readiness、missed tick metrics 與 DNS/worker 故障恢復。

# Task Brief: CONSOLE-DATA-OODA

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Populate /bff/ooda/packets
- Status: review_approved
- Owner: Claude2
- Reviewer: Codex
- Next: Review approved for code path and projector contract. Owner Claude2 must finalize via PR merge and live /bff/ooda/packets curl count>0/status=ok before done.

## Summary
OODA loop producer 產真 packet 寫入 PANTHEON_BFF_OODA_PACKET_STORE / PANTHEON_OODA_DATA_DIR。用該 domain 的真實 producer 產生真資料(禁止捏造);再重接 BFF 讀路徑(設 PANTHEON_BFF_*_STORE / 指向 live service / 加投影,如 scripts/project_research_to_bff_surfaces.py);驗收:live curl(Bearer op-dev:admin:mfa)該 /bff 面回 count>0 且 surface status=ok;在 services/control-plane/bff/tests 加/更新 contract test;stub dispatch 為 dev 安全姿態。範式見 docs/05/system-verification-rounds/console-population-research-slice.md。

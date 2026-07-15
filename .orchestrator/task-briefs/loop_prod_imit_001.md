# Task Brief: LOOP-PROD-IMIT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Default Human Imitation and shadow evaluation chain
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Re-check 2026-07-15T17:xx: PR #3725 already merged (be105af5f), no follow-up commit from owner (Antigravity) fixing the SEED_DATASET hardcoding defect flagged in the CHANGES REQUESTED review (services/policy-learning/main.py _get_dataset_payload still ignores discovered dataset_id content). Owner evidence.json evidence_cut_at is still 2026-07-15T16:04:00Z, unchanged. No new state to review; waiting on owner fix before re-review. Taking no code action this wake.

## Summary
讓 scheduler 自動發現合格 governed datasets，執行真實 shadow/OOS evaluator metrics，持久化 immutable candidate 與 lineage；不得靠 empty body，也不得繞過 experiment→approval→deployment。

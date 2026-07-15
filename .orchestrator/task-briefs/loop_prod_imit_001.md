# Task Brief: LOOP-PROD-IMIT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Default Human Imitation and shadow evaluation chain
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Re-checked 2026-07-15: confirmed still unfixed. _get_dataset_payload (main.py:487-490) ignores dataset_id, always trains/evaluates on hardcoded SEED_DATASET. No new commits since PR #3725 merge/review comment (same timestamp 16:13:52-58Z). Waiting on owner (Antigravity) to wire real discovered-dataset content before this can move to review_approved.

## Summary
讓 scheduler 自動發現合格 governed datasets，執行真實 shadow/OOS evaluator metrics，持久化 immutable candidate 與 lineage；不得靠 empty body，也不得繞過 experiment→approval→deployment。

# Task Brief: LOOP-PROD-IMIT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Default Human Imitation and shadow evaluation chain
- Status: review
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewed PR #3725 (merged be105af5f). CHANGES REQUESTED: _get_dataset_payload (main.py:487) always trains/evaluates on hardcoded SEED_DATASET regardless of dataset_id, so discovered datasets never actually reach the trainer/evaluator -- fails AC-02 and the seed-fixture-as-live-proof non-goal. Full findings posted as PR comment. Waiting on owner (Antigravity) to fetch real dataset content before this can move to review_approved.

## Summary
讓 scheduler 自動發現合格 governed datasets，執行真實 shadow/OOS evaluator metrics，持久化 immutable candidate 與 lineage；不得靠 empty body，也不得繞過 experiment→approval→deployment。

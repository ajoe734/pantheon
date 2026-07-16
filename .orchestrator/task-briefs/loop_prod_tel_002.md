# Task Brief: LOOP-PROD-TEL-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Canonical loop-run and Trade Journey lifecycle projector
- Status: in_progress
- Owner: Codex2
- Reviewer: Claude
- Next: Codex2 2026-07-16: strict deploy floor and WIF now pass, but `dev/root/strict` run `29462890670` failed before hosted proof because root stack health blocked on `policy-learning-svc` / `reconciliation-drift-svc`; public BFF was later restored only by stale `a10f752b` permissive-stub BFF-only run `29463957129`. Workflow `269991390` is currently `disabled_manually` after cancelled newer-dev-head strict attempts. Re-enable workflow, repair/confirm root stack health, then rerun `dev/root/strict` at the intended current dev SHA with `run_loop_prod_tel_002_probe=true`.

## Summary
從真實 signal/decision/order/fill/position/reconciliation append events 投影 canonical loop-run 與 Trade Journey；維持單一 identity chain，manual/cron rebuild 只能標示 backfill，不能成為 live truth。

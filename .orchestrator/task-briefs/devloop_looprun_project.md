# Task Brief: DEVLOOP-LOOPRUN-PROJECT

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Wire + schedule paper loop-run projector on dev
- Status: todo
- Owner: Claude2
- Reviewer: Claude
- Next: Assignment created

## Summary
loop_runs ledger 空(census loop_runs=0)儘管 telemetry 已有 25 trades。真因:(1) operator-bff 容器內 PANTHEON_BFF_LOOP_RUN_STORE env 未設、/data/bff/loop_runs.json 不存在→BFF loop-run 讀取路徑沒 wire;(2) scripts/paper_loop_run_projector.py 在 dev 從未跑也沒排程。修法:在 BFF compose 設 PANTHEON_BFF_LOOP_RUN_STORE 指向掛載 volume 路徑,排程 projector(cron 或 sidecar)把 active bindings+telemetry 投影進該 store,並確認 /bff/v5/loop-runs 回傳記錄。

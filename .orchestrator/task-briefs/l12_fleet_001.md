# Task Brief: L12-FLEET-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Restore and prove the eight-slot Codex fleet frontier
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: 獨立審查通過：PR #4135 merge 09159159 已納入 provisioner/drift guard 與 tests；PR #4140 merge 91ff33b70 已納入 e5fe75bcc/6ccb63133 evidence。重跑 82+2 pytest、py_compile、evidence digest 均通過；repo/live policy 相等且 drift=[]，installed capacity Codex/Codex2=4/4，8 個 slot fresh auth/verified，PID 1579402、watchdog、heartbeat 健康。journal 前 1203 筆 byte/projection hash與 1205 projection/event ID逐項吻合；run 30194930965 於 restart 後 47 秒才取得 lease 並正常釋放；guarded dry-run仍為21 exact/4 create且六個 governed surfaces hash前後不變。現時 command root 因本 evidence merge及後續 PR 比 origin/dev 落後5 commits，但無 config drift，不影響重啟當下與 exact source 09159159 的 acceptance。

## Summary
讓受審查的 repo fleet policy 覆蓋陳舊 live overlay，安全重載 supervisor，證明 Codex/Codex2 各四個 slot 可派工且 watchdog、queue、approval 維持健康。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner closeout finalization

Finalized by `Codex` after independent `Codex2` approval. This closeout record
does not change the reviewed implementation or evidence packet.

Delivery lineage:

- Fleet guard and tests: PR #4135, merge
  `09159159fbac9b43d3e97011a12a224699677620`.
- Runtime and mutation-free evidence: PR #4140, merge
  `91ff33b70acc84283abca541a6dc2d93f65c5174`.

Owner verification at `2026-07-26T09:11:06Z`:

- `PYTHONPATH=.orchestrator /home/lupin/pantheon/.venv/bin/python -m pytest -q -p no:cacheprovider scripts/test_provision_live_supervisor_config.py scripts/test_check_config_drift.py scripts/test_dispatch_twelve_loop_gap_2026_07_26.py .orchestrator/test_dispatch_policy.py`
  — `82 passed in 10.59s`.
- `PYTHONPATH=.orchestrator /home/lupin/pantheon/.venv/bin/python -m pytest -q -p no:cacheprovider .orchestrator/test_supervisor.py::RuntimeConfigTests::test_codex_accounts_allow_four_concurrent_slots .orchestrator/test_supervisor.py::RuntimeConfigTests::test_live_provider_account_schema_is_strict_and_complete`
  — `2 passed in 1.56s`.
- `/home/lupin/pantheon/.venv/bin/python -m py_compile scripts/provision_live_supervisor_config.py scripts/check_config_drift.py scripts/dispatch_twelve_loop_gap_2026_07_26.py .orchestrator/supervisor.py`
  — exit `0`.
- `sha256sum -c evidence.sha256` from the task evidence directory —
  `evidence.json: OK`.
- Installed-source drift readback — `drift=[]`, `missing=[]`,
  `intentional=[]`, exit `0`; the command root remains five commits behind
  `origin/dev`, matching the reviewer-approved residual.
- Runtime health readback — `healthy=true`, lifecycle `running`, PID `1579402`,
  lock-held liveness true, fresh heartbeat/watchdog, no loop error, and exact
  authoritative journal projection hashes.
- Provider readback — all eight `codex1-{1..4}` / `codex2-{1..4}` slots report
  `auth_ready=true`, `verified=verified`, `delivery_mode=codex`, and
  `local_cli_worker_supported=true`.

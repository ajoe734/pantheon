# Live writer/process inventory — 2026-07-17 (redacted)

Processes capable of appending to or rotating the central activity log
`/home/lupin/code/pantheon/ai-activity-log.jsonl`, captured read-only via
`ps` at inventory time. Command lines are truncated to identity; no
environments are published.

## Writer classes

| # | Class | Observed instance | Code vintage | Rotation mechanism |
| --- | --- | --- | --- | --- |
| 1 | Supervisor (+ its watchdog/bus/approval/coordination writers) | PID 3486308, `python3 -u /home/lupin/pantheon-ci-deploy/dev-root/.orchestrator/supervisor.py --config live-supervisor-mainroot-config.json` (started 2026-07-16 23:30:05) | dev-root `d3848722c` (schema v1) | `.orchestrator/common.py` `_activity_log_rotate_threshold` → `rotate_activity_log_unlocked` |
| 2 | Governed status commands from worker worktrees (`scripts/ai-status.sh` / `ai_status.py` with `PANTHEON_STATUS_ROOT` at the central root) | one live codex worker chain (worker_runner PID 3044680 → codex exec in `loop-prod-seq-reconcile-001` worktree) | per-worktree checkout — MIXED vintages | `scripts/ai_status.py` `LOG_ROTATE_MAX_BYTES` (5 MiB / keep‑1000) → shared `rotate_activity_log_unlocked`; OLD vintages carry the retired timestamp-rotation code (this produced the 2337Z archive) |
| 3 | Manual operator status commands | none at capture | whatever checkout the operator uses | same as class 2 |
| 4 | Cron entries (22 active lines) | none touch the activity log or `ai_status` rotation (grep count 0); dashboard/status-guard/rotate-worker-logs crons operate on other files | n/a | none |
| 5 | Dashboard/read-side (`dashboard_server.py`, gen-dashboard, cloudflared) | PID 1122377 etc. | n/a | readers only |

`watchdogd` (PID 111) is the kernel thread, not a Pantheon writer.

## Guard implication (planner finding 1)

- `AI_STATUS_LOG_ROTATE_MAX_BYTES` covers only class 2/3 current-code
  commands; class 1 reads `config paths.activity_log_rotate_bytes`; OLD
  class-2 vintages read neither. An env threshold override is therefore NOT
  an all-writer guard.
- The enforceable guard is: `PANTHEON_ACTIVITY_ROTATION_PAUSE=1` for every
  current-code writer environment (verified to pause both mechanisms at the
  shared choke points in `common.py`) PLUS a full stop of the supervisor
  respawn chain and worker fleet PLUS a no-manual-commands window, with
  `ps`/`fuser` readback that no writer-class process remains. Exact commands
  are in `live-recovery-runbook.md`.

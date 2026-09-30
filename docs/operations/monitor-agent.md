# Monitor Agent

`services/monitor-agent` replaces the retired Sentinel. Every 15 minutes
(`MONITOR_AGENT_INTERVAL_SECONDS`, default 900) it opens incidents for
anomalies that no open incident already explains. It can do nothing else.

## Run

1. Collect a bounded read-only snapshot (8 KB per source) from
   `MONITOR_AGENT_SOURCES_JSON` (default: runtime-manager desired state,
   telemetry runtime summaries for drawdown/fill rate/slippage, persona
   `/readyz`, paper-fleet-reconciler `/readyz` for control-loop health).
2. Ask one agent through the openclaw gateway adapter
   (`/assistant/providers/openclaw/structured`) to compare the snapshot with the
   open incidents. That route pins a data-only `emit_extraction` tool and the
   adapter verifies the gateway agent has `tools.deny=["*"]`, so the agent has no
   tool that changes trading, runtime or capital state; it can only return
   `findings` (`fingerprint`, `title`, `severity`, `rationale`).
3. POST each finding to `POST /api/incidents/consume-agent-finding` with the
   snapshot reference. A finding whose fingerprint matches an open incident
   merges its evidence into that incident (HTTP 200) instead of creating
   another (HTTP 201). Each incident carries `snapshot_ref` and the rationale in
   its `evidence_summary`.

## Limits and degraded runs

- At most 5 incidents per run and 20 per rolling hour (persisted in
  `MONITOR_AGENT_STATE_PATH`, so restarts do not reset it).
- If any read API or the agent is unavailable, or the agent output is malformed,
  the run logs `status=degraded` and creates no incident.

## Restart cap

`paper_fleet_reconciler` deletes a runtime's worker entry when its binding
leaves active and restarts from `restart_count` 0 on resume. Pausing then
resuming a runtime therefore resets the paper fleet restart cap; account for
that when a finding reports a runtime that keeps restarting.

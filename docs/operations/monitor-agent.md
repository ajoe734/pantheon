# Monitor Agent

`services/monitor-agent` replaces the retired Sentinel. Every 15 minutes
(`MONITOR_AGENT_INTERVAL_SECONDS`, default 900) it opens incidents for
anomalies that no open incident already explains. It can do nothing else.

## Run

1. Collect a bounded read-only snapshot (8 KB per source) from
   `MONITOR_AGENT_SOURCES_JSON` (default: runtime-manager desired state,
   telemetry runtime summaries for drawdown/fill rate/slippage, persona
   `/readyz`, paper-fleet-reconciler `/readyz` for control-loop health).
   Protected sources use read-only credentials wired in Compose: runtime-manager
   gets `Authorization: Bearer $PANTHEON_RUNTIME_MANAGER_TOKEN`; telemetry gets
   `Authorization: Bearer $PANTHEON_TELEMETRY_SERVICE_TOKEN` plus
   `X-Tenant-Id: $PANTHEON_TENANT_ID` (same contracts the reconciler uses).
   Custom `MONITOR_AGENT_SOURCES_JSON` entries keep the same source names.
2. Ask one agent through the openclaw gateway adapter
   (`/assistant/providers/openclaw/structured`) to compare the snapshot with the
   open incidents. That route offers no tools: the Gateway CLI launches with
   ToolSearch only, and the adapter requires a JSON-only answer validated against
   the caller schema while verifying the gateway agent has `tools.deny=["*"]`.
   The agent has no tool that changes trading, runtime or capital state; it can
   only return `findings` (`fingerprint`, `title`, `severity`, `rationale`).
3. POST each finding to `POST /api/incidents/consume-agent-finding` with the
   snapshot reference. A finding whose fingerprint matches an open incident
   merges its evidence into that incident (HTTP 200) instead of creating
   another (HTTP 201). Dedupe is atomic: incident ids are sequential per
   fingerprint, so concurrent creators collide in the store and the loser merges. Each incident carries `snapshot_ref` and the rationale in
   its `evidence_summary`.

## Limits and degraded runs

- At most 5 incidents per run and 20 per rolling hour (persisted in
  `MONITOR_AGENT_STATE_PATH`, so restarts do not reset it). A slot is reserved durably *before* each
  post and released only when the incidents service confirms an update; a post
  with an unknown outcome (timeout, lost response) keeps its slot for the hour.
- If any read API or the agent is unavailable, or the agent output is malformed,
  the run logs `status=degraded` and creates no incident.

## Restart cap

`paper_fleet_reconciler` deletes a runtime's worker entry when its binding
leaves active and restarts from `restart_count` 0 on resume. Pausing then
resuming a runtime therefore resets the paper fleet restart cap; account for
that when a finding reports a runtime that keeps restarting.

# OSS-004C EP4 Governed Paper Acceptance Packet

This directory archives one integrated EP4 governed paper execution run for `OSS-004C`.

Primary result:

- `summary.json`

Key evidence slices:

- Governance approval lifecycle:
  - `approval-*.request.json`
  - `approval-*.response.json`
- Deployment plan, dispatch, and saga:
  - `deployment-*.request.json`
  - `deployment-*.response.json`
  - `runtime-deploy.response.json`
- Paper execution proof:
  - `paper-runtime-*.response.json`
  - `signal-enqueue.response.json`
- Telemetry proof:
  - `telemetry-deploy-completed.response.json`
  - `telemetry-rollback-completed.response.json`
  - `telemetry-stats-before-runtime.response.json`
  - `telemetry-stats-after-deploy.response.json`
  - `telemetry-stats-before-rollback.response.json`
  - `telemetry-stats-after-rollback.response.json`
- Incident and operator evidence:
  - `incident-create.response.json`
  - `incident-operator-payload.response.json`
  - `incident-resolve.response.json`
- Kill-switch and rollback drill:
  - `kill-switch-*.response.json`
  - `rollback-execute.response.json`

Runtime and control-plane surfaces used for this packet:

- Governance: `http://127.0.0.1:18082`
- Deployment: `http://127.0.0.1:8006`
- Runtime-manager: `http://127.0.0.1:28081`
- Paper runtime: `http://127.0.0.1:28110`
- Telemetry ingest: `http://127.0.0.1:38083`
- Incidents: `http://127.0.0.1:38090`

Important caveat:

- `telemetry-deploy-trace.response.json` and `telemetry-rollback-trace.response.json` return `404`.
- For this local proof run, the repo-current telemetry ingest on `38083` accepted events and advanced canonical counters, but the event trace read-model was not projecting newly ingested events.
- The packet therefore treats telemetry proof as:
  - `202 accepted` on ingest
  - `service.total_ingested` incrementing in stats
  - `dead_letter_queue.total_rejected` staying flat
  - runtime-emitted telemetry counters advancing in `paper-runtime` health/state

This packet proves the required EP4 chain:

- approval
- deployment
- runtime binding
- paper execution
- telemetry
- incident / health
- kill-switch
- `pause_then_replace` rollback

# TEL-002-RB RuntimeHeartbeat Ingest Evidence

Task: `TEL-002-RB`
Owner: `Codex`
Reviewer: `Claude`

## Delivered Surface

- Added `POST /api/v1/telemetry/heartbeats` to accept an SD-09 `RuntimeHeartbeat` payload.
- The endpoint adapts `RuntimeHeartbeat` into a canonical `TelemetryEvent` heartbeat and sends it through `TelemetryIngestService.ingest()`.
- RuntimeBinding evidence is resolved before adaptation when a binding store is configured; unknown bindings fail closed with `BINDING_NOT_FOUND`.
- Added `GET /api/v1/telemetry/runtime/<runtime_id>/heartbeat` to return the latest `RuntimeHeartbeatStatus` derived from telemetry-owned runtime summaries.
- Runtime summary projection now preserves heartbeat connectivity, broker status, queue lag, event delivery lag, and reported health summary while retaining existing BFF fields.

## Verification

Commands run from repo root:

```bash
PYTHONPYCACHEPREFIX=/tmp/pantheon-tel002-rb-pycache python3 -m py_compile services/telemetry/heartbeat_service.py services/telemetry/runtime_summary.py services/telemetry/ingest_svc.py services/telemetry/main.py services/telemetry/test_main_routes.py services/telemetry/test_runtime_summary_projection.py
python3 -m unittest services.telemetry.test_runtime_summary_projection
python3 -m unittest services.telemetry.test_main_routes
/tmp/pantheon-tel002-rb-venv/bin/python -m unittest services.telemetry.test_main_routes
/tmp/pantheon-tel002-rb-venv/bin/python -m unittest services.telemetry.test_runtime_summary_projection services.telemetry.test_paper_runtime_ingest_contract services.telemetry.test_tel001_rebaseline_schema
/tmp/pantheon-tel002-rb-venv/bin/python -m unittest services.telemetry.test_main_routes services.telemetry.test_runtime_summary_projection services.telemetry.test_paper_runtime_ingest_contract services.telemetry.test_tel001_rebaseline_schema
git diff --check -- services/telemetry/heartbeat_service.py services/telemetry/runtime_summary.py services/telemetry/ingest_svc.py services/telemetry/main.py services/telemetry/test_main_routes.py services/telemetry/test_runtime_summary_projection.py
```

Notes:

- The default Python environment lacked `flask`, so route-level tests were rerun in `/tmp/pantheon-tel002-rb-venv` after installing `services/telemetry/requirements.txt`.
- The initial default-environment `python3 -m unittest services.telemetry.test_main_routes` failed only at import time with `ModuleNotFoundError: No module named 'flask'`; the same test passed in the telemetry requirements venv.

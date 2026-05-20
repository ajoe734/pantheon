# TEL-HARD-001-V2 Environment

Generated at: 2026-05-20T14:48:07Z

## Execution

- Command: `python3 tests/telemetry/test_ingest_load_10x.py --write-evidence`
- Runner: in-process `TelemetryIngestService` using the same ingest, buffer, writer, backpressure, schema, and DLQ code path as the deployable service.
- Normal load reference: 1000 events from `services/telemetry/smoke_test_ingest.py`.
- 10x load: 10000 canonical `order_filled` events.

## Dev Compose Target

- Compose file: `docker-compose.yml`
- Service: `telemetry`
- Profiles: `['default-dev']`
- Ports: `['18083:8083']`
- Depends on: `['nats', 'postgres', 'runtime-manager']`
- Compose service check: `present`
- Note: the checked-in regression is hermetic and does not require Docker; the target service is the default dev compose telemetry service.

## Local Tooling

- Python: `3.12.3`
- Platform: `Linux-6.17.0-1013-gcp-x86_64-with-glibc2.39`
- pytest: `pytest 9.0.3`
- docker compose: `Docker Compose version 2.40.3+ds1-0ubuntu1~24.04.1`
- git HEAD: `088a5dd06784c4bd304e2dea40f5bb2e878637c7`

## Load Results

- Accepted: `10000`
- Written: `10000`
- Rejected: `0`
- Drop count: `0`
- DLQ count: `0`
- Throughput events/sec: `238.2`
- Burst buffer utilization: `83.33%`
- Pressure after burst: `high`

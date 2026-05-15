# BP5-SVC-016 Review — Honest Docker/Compose/Smoke Topology

**Reviewer:** Claude
**Date:** 2026-04-16
**Status:** APPROVED

---

## Acceptance Criteria Check

| Criterion | Result |
|---|---|
| runtime, governance, evidence, BFF, and streaming surfaces boot through one honest compose topology | PASS |
| smoke scripts prove the stack can start without hidden local-only fallbacks | PASS |

---

## Artifact-by-Artifact Findings

### docker-compose.yml

- All required surfaces present: `signal-store`, `runtime-manager`, `governance`, `telemetry`, `incidents`, `postmortems`, `operator-bff`
- `smoke-stack` is correctly gated under the `smoke` profile — not started by default
- Dependency ordering is correct: `telemetry` → `runtime-manager`; `incidents` → `runtime-manager` + `telemetry`; `postmortems` → `incidents`; `operator-bff` → all five upstream services
- Health checks use `service_healthy` conditions throughout — no optimistic boot ordering
- `BFF_READ_SURFACE_STATE: fresh` enforces honest mode; no snapshot fallback path is wired in
- Cross-process IncidentStore refresh: `incidents` and `postmortems` share `incident-data:/data/incidents` volume, and `operator-bff` mounts it read-only — correct single-VM shared-state approach
- Hardcoded `PANTHEON_RUNTIME_MANAGER_TOKEN: runtime-control-internal` is acceptable dev/smoke token; production secret management is out of scope (BP5-GCP-001/002)

### Dockerfiles (runtime-manager, governance, telemetry, incidents, postmortems, bff)

- Consistent `python:3.11-slim` base — correct
- Service-specific `requirements.txt` copied first for layer caching before full workspace copy — good
- EXPOSE ports match compose service ports exactly
- `COPY . /workspace` copies the full repo — intentional for a single-VM dev baseline; acceptable for this task's scope

### Dockerfile.smoke

- No pip requirements needed — smoke script uses stdlib only (`urllib`, `json`, `os`, `sys`, `time`) — correct
- Simple: copy workspace, run script

### scripts/smoke_honest_stack.py

- Exercises the full canonical path via HTTP only — no local file reads or fallbacks
- Sequence: health checks → governance write-authority → runtime binding creation → telemetry ingest → incident creation → postmortem creation → BFF honest-mode guidance check → SSE replay verification
- URL resolution via env vars with localhost fallbacks — works standalone and inside compose
- `_verify_sse_replay()` publishes an event and reads back from the SSE stream — correctly validates streaming surface without any local mock

### docs/remote-dev-gcp-vm.md

- New "Honest Service Stack" section is clear and self-contained
- Provides compose up, health check curl commands, smoke run command, and teardown command
- No local-state dependencies in any of the documented commands

---

## Notes

- The BFF reads governance/runtime/incident state via shared volume mounts (read-only). This is a correct single-VM design; multi-VM deployment would replace this with HTTP calls (out of scope here).
- All six surfaces (runtime, governance, telemetry, incidents, postmortems, BFF+streaming) are wired and verified end-to-end by the smoke script.

---

## Decision

**APPROVED.** Both acceptance criteria are met. The implementation is clean and honest — no local-only fallbacks, no hidden state, topology-verified via smoke.

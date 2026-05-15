# Single-VM Smoke Results

Acceptance record for `DEPLOY-006`.

Paired with [`scripts/smoke_test_single_vm.sh`](../../scripts/smoke_test_single_vm.sh).

## Scope

The single-VM smoke validates the full service chain running on one host via `docker-compose.yml`:

1. All core service healthchecks pass (runtime-manager, governance, registry, telemetry, incidents, operator-bff)
2. Governance write-authority matrix is readable
3. Registry accepts a new artifact entry and returns it on GET
4. Governance accepts a proposal and records an approval decision
5. `runtime-manager` creates a `RuntimeBinding` from a mock `DeploymentPlan` payload
6. Telemetry ingest accepts an event linked to the binding; `total_ingested` counter increments
7. Incidents service creates an incident linked to the binding
8. BFF surfaces respond correctly: governance review-queue, deployment-plans, incidents, telemetry

This smoke does **not** cover:
- The dual-VM cross-plane path (see `docs/deployment/dual-vm-acceptance-results.md`)
- Final LEAN runtime order loop execution (out of scope until DEPLOY-010+)
- Real capital allocation or live-stage gating

## Preconditions

Before running, start the full single-VM stack:

```bash
docker compose up -d
docker compose ps   # wait for all services healthy
```

All services in `docker-compose.yml` must reach `healthy` state. Key dependencies:
- postgres, minio, nats must be healthy first
- runtime-manager, governance, registry, telemetry, incidents must be healthy before operator-bff

## Running the Smoke Test

```bash
bash scripts/smoke_test_single_vm.sh
```

The script creates a unique run suffix on each execution, so it is safe to run repeatedly.

### Override service URLs (optional)

```bash
RUNTIME_MANAGER_URL=http://127.0.0.1:18081 \
GOVERNANCE_URL=http://127.0.0.1:18082 \
TELEMETRY_URL=http://127.0.0.1:18083 \
INCIDENTS_URL=http://127.0.0.1:18090 \
REGISTRY_URL=http://127.0.0.1:18087 \
BFF_URL=http://127.0.0.1:18001 \
bash scripts/smoke_test_single_vm.sh
```

### Persist artifacts

```bash
bash scripts/smoke_test_single_vm.sh --output-dir /tmp/deploy-006-run
```

JSON request/response payloads are written to the output directory. A `summary.json` is always written.

## Acceptance Criteria (from DEPLOY-006)

| # | Criterion | Verified by |
|---|-----------|-------------|
| 1 | 所有核心服務 healthcheck 通過 | Steps 1 – health_check calls for all 6 core services |
| 2 | BFF 能查詢 registry/governance/telemetry 主要路徑 | Steps 2, 9 – governance write-authority, BFF /api/v1/operator/governance/review-queue, /api/v1/deployment-plans, /api/v1/incidents, /api/v1/telemetry |
| 3 | mock DeploymentPlan 建立成功 | Steps 4–7 – registry entry → governance approval → RuntimeBinding → telemetry ingest |
| 4 | smoke test script 可重複執行 | Unique RUN_SUFFIX per execution; no fixed IDs that conflict on re-run |

## Passing Run Template

Record a passing run by filling in the values from `$OUTPUT_DIR/summary.json`:

```
Date:              <YYYY-MM-DDTHH:MM:SSZ>
Task:              DEPLOY-006
Run suffix:        <run_suffix>
Operator:          <who ran it>

Services healthy:
  runtime-manager  http://127.0.0.1:18081  /__health__   ✓
  governance       http://127.0.0.1:18082  /health        ✓
  registry         http://127.0.0.1:18087  /health        ✓
  telemetry        http://127.0.0.1:18083  /__health__   ✓
  incidents        http://127.0.0.1:18090  /__health__   ✓
  operator-bff     http://127.0.0.1:18001  /health        ✓

Mock plan flow:
  registry_id:     <reg-single-vm-XXXX>
  approval_id:     <approval-single-vm-XXXX>
  plan_id:         <plan-single-vm-XXXX>
  binding_id:      <binding id returned by runtime-manager>
  telemetry:       <N_before> → <N_after>

BFF surfaces:
  governance review-queue  200  ✓
  deployment-plans         200  ✓
  incidents                200  ✓
  telemetry                200  ✓

Result: PASSED
```

## Known Limitations

- **BFF deployment-plans**: The BFF reads deployment plans from a shared-volume data file.
  If no plans have been written to that volume the list will be empty — the smoke confirms
  HTTP 200, not a non-empty list.
- **Registry healthcheck endpoint**: The compose healthcheck targets `/__health__` inside the
  container; the default external port is 18087.
- **Incident-to-telemetry link**: The smoke creates an incident referencing `binding_id` from
  the runtime binding but does not verify the incidents service reverse-looks up telemetry
  events (that path requires the telemetry ↔ incidents cross-query, tested separately).

# Review: SVC-BLUEPRINT-POSTGRES-CUTOFF-WAVE3

Reviewer: Codex
Date: 2026-05-03
Decision: approved

## Scope Reviewed

Task: Move remaining production owner stores off JSONL baseline
Owner: Claude
Reviewed owner commit: `b2c4de8fce8b4df936fa7550563ed3e0a5c4d180`

Artifacts reviewed:
- `docker-compose.control.yml`
- `docker-compose.yml`
- `env/prod-control.env.example`
- `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`
- `docs/operations/postgres-cutoff-wave3-runbook.md`
- `services/foundation/persistence_posture.py`
- `services/foundation/tests/test_control_plane_postgres_owner_stores.py`
- `services/foundation/tests/test_persistence_posture.py`

## Findings

No blocking findings remain.

The reviewed state satisfies the wave-3 cutoff requirement:
- `env/prod-control.env.example` selects production persistence posture and Postgres backends for governance, capital, incidents, postmortems, promotion, memory, and reconciliation-drift.
- Wave-3 services call `require_persistence_posture(...)` during import/startup, so staging/prod posture fails closed if a service-specific owner store remains on JSON/JSONL.
- `docker-compose.control.yml` now includes `reconciliation-drift-svc` in the VM-1 control-plane slice, uses the external `PANTHEON_RUNTIME_MANAGER_URL` handoff for VM-2 runtime-manager, and defines a dedicated `reconciliation-drift-data` volume.
- `docs/operations/postgres-cutoff-wave3-runbook.md` records the env gates, fail-closed behavior, schema bootstrap expectations, dev rollback procedure, compose topology, and verification commands.

## Verification Run

```bash
python3 -m pytest services/foundation/tests/test_control_plane_postgres_owner_stores.py services/foundation/tests/test_persistence_posture.py -v
# 8 passed in 2.10s
```

```bash
docker compose -f docker-compose.control.yml config --quiet
# passed
```

```bash
docker compose --env-file env/prod-control.env.example -f docker-compose.control.yml config --quiet
# passed
```

## Acceptance Assessment

Approved. Staging/prod owner-store configuration no longer silently selects JSON/JSONL for the wave-3 services, dev rollback remains explicit via `PANTHEON_PERSISTENCE_POSTURE=dev`, the VM-1 control compose contains the remaining service, and focused store/posture tests pass.

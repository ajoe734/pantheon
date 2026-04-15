# Deployment Service

This directory is the deployable home for Pantheon's deployment planning and
orchestration surface.

`BP5-SVC-004` made `DeploymentPlan` a file-backed HTTP API.  
`BP5-SVC-005` extends the same service with the canonical `DEP-002`
deployment saga, transactional outbox, inbox dedupe receipts, and compensation
routes.

## Capabilities

The service can now:

- create validated deployment plans
- dry-run stage-transition validation
- list and fetch stored plans
- advance plan status through `approved -> executing -> executed`
- bootstrap the canonical deploy saga and first outbox event
- record binding-created and runtime-active saga progress
- record failures and finalize compensation
- inspect pending outbox events and durable inbox receipts
- read a strategy-scoped deployment read model

## Files

| File | Purpose |
|---|---|
| `models.py` | Pydantic request / response models for plans, sagas, outbox, inbox |
| `service.py` | FastAPI app plus file-backed planner / orchestration service |
| `test_service.py` | In-process API coverage via `TestClient` |
| `smoke_test.py` | HTTP smoke test against a live server |
| `contract.md` | Canonical deployable API contract |

## Running

```bash
uvicorn services.deployment.service:app --reload --port 8006
```

## Storage

The service persists to:

- `${DEPLOYMENT_DATA_DIR}/deployment_plans.json`
- `${DEPLOYMENT_DATA_DIR}/deployment_sagas.json`
- or the same filenames under `${PANTHEON_GOVERNANCE_DATA_DIR}`
- or `/tmp/pantheon/governance/`

Approval lookups default to `${...}/approval_decisions.json`.

Registry lookups are optional and use `PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH`.
If that snapshot path is not configured, callers must embed `registry_entry` in
the create / validate / dispatch request body.

## Tests

```bash
pytest services/deployment/test_service.py -v
python3 services/deployment/smoke_test.py
```

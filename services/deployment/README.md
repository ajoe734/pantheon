# Deployment Service

This directory is the deployable home for Pantheon's `DeploymentPlan` service.

`BP5-SVC-004` turns the canonical stage planner from
`services/control-plane/governance/deployment_plan.py` into a file-backed HTTP
surface that callers can use to:

- create validated deployment plans
- dry-run validation for stage transitions
- list and fetch stored plans
- advance plan status through `approved -> executing -> executed`
- read a strategy-scoped deployment read model

## Files

| File | Purpose |
|---|---|
| `models.py` | Pydantic request / response models |
| `service.py` | FastAPI app plus file-backed planner service |
| `test_service.py` | In-process API coverage via `TestClient` |
| `smoke_test.py` | HTTP smoke test against a live server |

## Running

```bash
uvicorn services.deployment.service:app --reload --port 8006
```

## Storage

The service persists plans to:

- `${DEPLOYMENT_DATA_DIR}/deployment_plans.json`
- or `${PANTHEON_GOVERNANCE_DATA_DIR}/deployment_plans.json`
- or `/tmp/pantheon/governance/deployment_plans.json`

Approval lookups default to `${...}/approval_decisions.json`.

Registry lookups are optional and use `PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH`.
If that snapshot path is not configured, callers must embed `registry_entry` in
the create / validate request body.

## Tests

```bash
pytest services/deployment/test_service.py -v
```

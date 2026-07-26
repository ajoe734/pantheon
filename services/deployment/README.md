# Deployment Service

This directory is the deployable home for Pantheon's deployment planning and
orchestration surface.

`BP5-SVC-004` made `DeploymentPlan` a file-backed HTTP API.
`BP5-SVC-005` extends the same service with the canonical `DEP-002`
deployment saga, transactional outbox, inbox dedupe receipts, and compensation
routes.

## Capabilities

The service can now:

- authenticate every deployment API caller and isolate records by tenant
- create validated deployment plans
- dry-run stage-transition validation
- check stage-planner rules without requiring registry / approval payloads
- list and fetch stored plans
- advance plan status through `approved -> executing -> executed`
- bootstrap the canonical deploy saga and first outbox event
- record binding-created and runtime-active saga progress
- record failures and finalize compensation
- inspect pending outbox events and durable inbox receipts
- claim pending outbox events through exclusive expiring leases
- read a strategy-scoped deployment read model
- run a pool/runtime compatibility preflight before DeploymentPlan approval
- read DEP-003 deployment projections that join plan, approval, runtime, saga,
  and execution metadata state without becoming a write authority

## Files

| File | Purpose |
|---|---|
| `models.py` | Pydantic request / response models for plans, stage planner checks, sagas, outbox, inbox |
| `auth.py` | Shared bearer-authenticated actor and explicit tenant boundary |
| `service.py` | FastAPI app plus file-backed planner / orchestration service |
| `outbox_lease.py` | Process-safe durable outbox claim / ack / recovery ledger |
| `outbox_consumer_worker.py` | Default dispatcher, authoritative runtime/controller readback, retry/DLQ/replay, and compensation execution |
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
- `${DEPLOYMENT_DATA_DIR}/deployment_outbox_leases.json`
- or the same filenames under `${PANTHEON_GOVERNANCE_DATA_DIR}`
- or `/tmp/pantheon/governance/`

Approval lookups default to `${...}/approval_decisions.json`.

Pool/runtime compatibility checks read:

- `PANTHEON_CAPITAL_POOL_STORE_PATH`, or
  `${CAPITAL_DATA_DIR|DEPLOYMENT_DATA_DIR|PANTHEON_GOVERNANCE_DATA_DIR}/capital_pools.json`
- `PANTHEON_PERSONA_BINDING_STORE_PATH`, or
  `${CAPITAL_DATA_DIR|DEPLOYMENT_DATA_DIR|PANTHEON_GOVERNANCE_DATA_DIR}/persona_capital_bindings.json`
- `PANTHEON_RUNTIME_BINDING_STORE_PATH`, `${PANTHEON_RUNTIME_DATA_DIR}/runtime_bindings.json`,
  or `/tmp/pantheon/runtime-manager/bindings.json`

The default Compose deployment mounts the Capital service's `capital-data`
volume at `/data/capital:ro` and the Runtime Manager's `runtime-data` volume at
`/data/runtime:ro`, with the exact store paths configured explicitly.  These
are read-only composition inputs; the Deployment service does not own either
store.

Registry lookups are optional and use `PANTHEON_DEPLOYMENT_REGISTRY_SNAPSHOT_PATH`.
If that snapshot path is not configured, callers must embed `registry_entry` in
the create / validate / dispatch request body.

DEP-003 projection lookups read RuntimeBinding rows from
`PANTHEON_RUNTIME_BINDING_STORE_PATH`, or
`${PANTHEON_RUNTIME_DATA_DIR}/runtime_bindings.json`, or
`/tmp/pantheon/runtime-manager/bindings.json`.

## Authentication and tenant boundary

Every `/api/deployment/*` request requires:

- `Authorization: Bearer <token>` for an authenticated service or operator role
- `X-Tenant-Id: <tenant>` for the one tenant addressed by the request

DeploymentPlan, saga, projection, outbox, and inbox reads are filtered by the
persisted tenant. Cross-tenant object lookups return `404`; writes with a tenant
that differs from their authoritative ApprovalDecision or DeploymentPlan fail
closed. Caller-authored actor fields do not become authority: the authenticated
actor is persisted instead.

The service uses `PANTHEON_DEPLOYMENT_AUTH_*` settings and otherwise follows
the shared BFF/runtime inbound-auth settings. Promotion mutation routes use the
same boundary with `PANTHEON_PROMOTION_AUTH_*`.

## Default dispatcher safety

`deployment-outbox-consumer` is part of the default Compose service set.  It
does not treat a POST response as successful execution.  It reads the exact
`RuntimeBinding`, paper fleet controller state where applicable, and the joined
DEP-003 terminal projection before consuming the corresponding outbox event.
The consumer requires a non-empty `PANTHEON_RUNTIME_MANAGER_URL` and always
dispatches to that remote Runtime Manager authority; missing configuration is a
startup/dispatch failure, never permission to create an in-process manager or
write a local binding store.
Its startup waits only for the unconditional Deployment and Runtime Manager
authorities.  Paper fleet and Incident service reachability is conditional on
the event being applied, so an unavailable conditional target fails closed and
follows that event's retry policy instead of blocking the consumer process
from starting.  A terminal or exhausted binding/load failure is first handed
durably to saga compensation, then its predecessor receipt is written so the
compensation event is not sequence-blocked.  The predecessor is never DLQ'd
after that handoff; a lost receipt response is retried.

The dispatcher must be provisioned with
`PANTHEON_DEPLOYMENT_SERVICE_TOKEN` and
`PANTHEON_DEPLOYMENT_TENANT_ID`. It claims work through
`POST /api/deployment/outbox/claim`; an event is exclusively owned until its
lease is acknowledged, released after a delivery failure, or recovered after
idle expiry. `DEPLOYMENT_OUTBOX_CONSUMER_LEASE_SECONDS` defaults to 60 and
`DEPLOYMENT_OUTBOX_CONSUMER_CLAIM_LIMIT` defaults to 25. Missing credentials
fail closed. Compose/environment activation for these settings is owned by the
manifest integration task and is not silently defaulted here.

The worker health receipt distinguishes successful idle polling from failure.
A clean poll, including an empty poll, restores `status=ok`; recovery is
observable through `last_idle_success`, `last_recovered_at`, and
`recovery_count`. The Deployment service health dependency also exposes active,
acknowledged, released, and recovered outbox lease counts.

Forward dispatch does not accept caller- or plan-authored loader booleans as
proof.  Before dispatch it reads and binds four canonical authorities: the
exact DeploymentPlan; the approved Registry entry and its embedded,
schema-valid, checksum-matching StrategyArtifact; the exact decided,
unconditional, unrevoked, and unexpired Governance ApprovalDecision; and the
active CapitalPool, admissibility result, and exact active
PersonaCapitalBinding/scope.  Every plan, strategy, artifact/version, approval,
pool, persona, binding, target, and scope identity must agree.  The resulting
target-bound report and canonical SHA-256 digests are persisted as
`metadata.authoritative_loader_attestation`, and Runtime Manager repeats the
same four-authority verification at its write boundary.
Binding-created response-loss recovery accepts only `approved -> executing`
plan lifecycle drift: immutable plan fields (including `current_stage`) remain
digest-covered, while canonical `binding_id` and `metadata.runtime_lifecycle`
must exactly match the recovered RuntimeBinding.

Every newly created `RuntimeBinding` is paper-only.  Canary/live movement needs
a separate governed promotion/cutover verifier and cannot be represented by a
non-empty reference, legacy `loader_checks_passed`, or copied metadata.  Forward
replace, evolution, rollback, kill-switch fallback, replay, and response-loss
recovery paths must reuse exact canonical paper lineage or fail closed without
fabricating a binding.  Safety containment may pause an existing binding and
raise an exact IncidentCase; it does not manufacture admission proof.

Compensation events execute the DEP-002 owner-scoped command before their inbox
receipt is written.  Abort mutates only `DeploymentPlan.status`; binding
failure, rollback, and safe-mode commands mutate only their canonical runtime
or incident owners.  Rollback additionally requires exact prior
binding/plan/artifact lineage to a retired paper RuntimeBinding whose persisted
`authoritative_loader_attestation` contains the matching four-authority
identities and canonical digests.  A plan-authored fallback attestation is not
proof.  Missing or ambiguous lineage, invalid canonical proof, or a kill-wins
state fails closed and routes to paused safe mode plus an exact IncidentCase
instead of a blind replacement.

Completed runtime-load history is not receipt-only.  Replay re-reads the exact
active RuntimeBinding, paper fleet state where applicable, and terminal DEP-003
projection before acknowledging the event, so a post-completion kill or stale
projection cannot be mistaken for successful convergence.

## Tests

```bash
pytest services/deployment/test_service.py -v
pytest services/deployment/test_l12_dep_001_dispatcher.py -v
pytest services/deployment/test_dep002_rebaseline_stage_planner.py -v
python3 services/deployment/smoke_test.py
```

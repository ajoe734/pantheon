# L12-CONS-001 — Durable Consultation executor evidence

Date: 2026-07-26
Owner: Codex
Reviewer: Codex2
Target: `task/L12-CONS-001` → `dev`

## Delivered behavior

The Consultation service now runs an authenticated, tenant-scoped workflow
reconciler instead of assigning a synthetic participant and waiting forever.

- `workflow_state.py` stores one work item per `(tenant_id, request_id)` in
  SQLite with `WAL` and `synchronous=FULL`.
- Claims use `BEGIN IMMEDIATE`, a unique lease token, an incrementing lease
  epoch, and compare-and-set updates. An expired worker cannot advance or
  acknowledge a stale claim.
- A configured HTTP provider must return a qualified committee, human, or
  persona contribution. System/service authors, mismatched tenant/request
  identity, empty evidence, invalid recommendations, and malformed payloads
  fail closed.
- The provider response is durably saved before API side effects. Participant,
  transcript event, evidence, memo, and handoff writes use deterministic
  idempotency keys.
- The API binds requests to `X-Pantheon-Tenant-Id`; strict mode authenticates
  service and operator bearer tokens. Cross-tenant reads return `404`.
- A workflow completes only after the handoff sink acknowledges the exact
  handoff and consultation-svc persists `status=acknowledged`. A crash after
  that API side effect is recovered by readback without another provider turn
  or handoff.
- Repeated blocking is bounded by
  `CONSULTATION_WORKFLOW_MAX_BLOCKED_ATTEMPTS`; exhausted work enters
  `dead_letter`. Replay is an operator-only API action.
- The consultation image now starts `services.consultation.supervisor`, which
  supervises both uvicorn and the workflow executor. It restarts a failed
  executor and lets the container restart if the API or repeatedly failing
  executor cannot stay alive.

Consultation remains advisory. No path in this task grants deployment,
approval, broker, or capital authority.

## Runtime contract

Strict service deployments provision:

| Variable | Purpose |
| --- | --- |
| `CONSULTATION_AUTH_REQUIRED=true` | Fail closed on unauthenticated API calls |
| `CONSULTATION_SERVICE_TOKEN` | API token used by the executor/service callers |
| `CONSULTATION_OPERATOR_TOKEN` | Operator token; required for DLQ replay |
| `PANTHEON_TENANT_ID` | Tenant partition owned by this worker |
| `CONSULTATION_PROVIDER_URL` | Real committee/red-team contribution endpoint |
| `CONSULTATION_PROVIDER_TOKEN` | Provider bearer credential |
| `CONSULTATION_HANDOFF_SINK_URL` | Optional external governance handoff sink |
| `CONSULTATION_HANDOFF_TOKEN` | Handoff sink bearer credential |
| `CONSULTATION_WORKFLOW_STATE_PATH` | Durable SQLite coordination database |

If the provider URL/token is absent, the worker does not synthesize a positive
participant. The item follows the bounded blocked path and becomes visible in
the DLQ.

Compose/default environment activation and shared secret provisioning remain
owned by `L12-MANIFEST-001`; this task changes the consultation image entrypoint
but does not mutate the shared Compose manifest.

## Verification

Command:

```text
/home/lupin/pantheon/.venv/bin/python -m pytest services/consultation -q
PATH=/home/lupin/pantheon/.venv/bin:$PATH \
  PYTHON=/home/lupin/pantheon/.venv/bin/python \
  scripts/run-acceptance.sh smoke
```

Result:

```text
56 passed, 11 warnings in 29.36s
repository smoke acceptance passed (stage0 validate + baseline)
```

The 11 warnings are pre-existing dependency/deprecation warnings from
FastAPI/httpx, jsonschema, and control-plane BFF startup hooks.

Focused L12 proof in `services/consultation/test_workflow_executor.py`:

1. starts a real uvicorn consultation service plus real HTTP provider and
   handoff sink boundaries;
2. verifies provider/service credentials, operator/service role separation,
   tenant IDOR rejection, and qualified non-system authorship;
3. races two executor instances over one durable state database and proves one
   provider turn, participant, memo, and handoff;
4. crashes after each of eight phases (`contribution_received` through
   `handoff_acknowledged`) and proves lease-expiry recovery without duplicate
   provider or handoff effects;
5. exhausts bounded attempts into `dead_letter`, denies service-token replay,
   replays with an operator token, and completes after provider recovery.

Machine-readable verification metadata is in
[`verification.json`](verification.json). The product-level owner closeout,
merged delivery, independent reviewer verdict, residual boundaries, and
guardrail admission are recorded in [`evidence.json`](evidence.json), with its
companion digest in `evidence.sha256`.

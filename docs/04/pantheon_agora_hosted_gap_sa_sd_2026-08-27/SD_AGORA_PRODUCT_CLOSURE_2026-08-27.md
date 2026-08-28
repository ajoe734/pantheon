# Pantheon Agora Product Closure SD — 2026-08-27

Date: 2026-08-27

Inputs:

- [`CURRENT_GAP_2026-08-27.md`](CURRENT_GAP_2026-08-27.md)
- [`SA_AGORA_PRODUCT_CLOSURE_2026-08-27.md`](SA_AGORA_PRODUCT_CLOSURE_2026-08-27.md)

This Software Design defines the minimum cross-repository changes required to
turn the current read-only hosted Agora shell into a functionally accepted,
strict-authenticated, data-honest Demo journey.

Tier: L3 execution-facing implementation blueprint; not an L1 authority

Scope: file-level Pantheon BFF/runtime, execute-plans frontend/workflow, contract,
test, rollout, rollback, and hosted-proof design for the GAP IDs in this package

Conflict rule: L1 policy and owning canonical contracts win. Additive contract
changes must be regenerated from their owning sources; this SD cannot authorize
hand-edited generated files, unsafe writes, live trading, or capital effects.

## 1. Baseline and invariants

Implementation begins from the latest `dev` at task claim time. The audited
refs are:

| Repository/surface | SHA |
|---|---|
| Pantheon `dev` and hosted BFF | `3c79a185a97d920f41005bd41675433a046b6ece` |
| execute-plans `dev` and current served FE after audit | `3010ee6e164e962791c94a044c19d6e79465a230` |
| hosted frontend audited page-by-page | `31623e783f7a08f94df7099c207390b317077d61` |

PR #664 is the only change between the audited and current served frontend. It
removes authenticator-code login UI and does not alter the design-unit paths
below. Functional hosted acceptance must nevertheless rerun on the exact pair
selected for delivery.

Mandatory invariants:

1. The BFF remains a facade/admission boundary, not a second canonical owner.
2. Auth readiness must not call OpenClaw, an LLM, source-ingest, paper runtime,
   or another product dependency.
3. A navigable Persona ID must resolve through the canonical Persona detail
   boundary for the same tenant.
4. HTTP 202 never performs provider work synchronously.
5. Interaction provider execution is advisory only and has no order, broker,
   capital, deployment, or self-approval authority.
6. Bearer-authenticated SSE attaches the bearer to the BFF origin and validates
   `text/event-stream`.
7. Stale/missing data remains explicit; no live/strict fixture repair is
   allowed.
8. Agora performance list data is tenant/user scoped before aggregation.
9. Hosted proof credentials are minted shortly before use and never stored in
   artifacts or the frontend bundle.
10. Failed write proof restores the last accepted read-only artifact and does
    not relabel the candidate functionally accepted.

## 2. Design-unit map

| Design unit | GAP IDs | Outcome |
|---|---|---|
| SD-AGC-01 | AG-AUTH-01/02 | fast auth-only readiness and provider-independent protected routing |
| SD-AGC-02 | AG-INT-01/03 | durable asynchronous interaction worker and terminal readback |
| SD-AGC-03 | AG-ID-01 | one canonical navigable Persona directory |
| SD-AGC-04 | AG-SSE-01 | bearer/cookie-aware Workshop stream transport |
| SD-AGC-05 | AG-PERF-01/CONTRACT-01 | owner-scoped Performance attribution route and drift gate |
| SD-AGC-06 | AG-DATA-01/02/03 | source/producer/surface readiness and fresh bounded stimulus |
| SD-AGC-07 | AG-INT-02/03 | Persona handoff and resumable Workshop UI |
| SD-AGC-08 | AG-ACC-01/02/03 | short-lived hosted auth, correlated Demo proof, acceptance levels |

## 3. SD-AGC-01 — Auth-only browser readiness

### 3.1 Backend change

Modify:

```text
services/control-plane/bff/main.py
services/control-plane/bff/tests/test_pint_016_strict_browser_readiness.py
services/control-plane/bff/test_bff_session_auth_me_contract.py
services/control-plane/bff/assistant/routes.py
```

`bff_auth_readiness()` must stop calling `_safe_provider_readiness()`. Its
terminal result is computed entirely from local verifier/session authority:

```python
auth_ready = (
    strict_auth
    and session_ready
    and operator_role_ready
    and interaction_capability_ready
    and verifier_ready
)

data["ready"] = auth_ready
data["authReady"] = auth_ready
```

Assistant provider status remains available from the existing
`GET /bff/assistant/providers` route. That route may perform or cache the
provider probe because it is a feature-readiness surface, not an auth gate.

### 3.2 Additive response contract

The v2 readiness response is:

```json
{
  "data": {
    "ready": true,
    "authReady": true,
    "sourceCommitSha": "full-sha",
    "auth": {
      "mode": "strict",
      "stub": false,
      "strict": true,
      "sessionKind": "bearer",
      "sessionReady": true,
      "operatorRoleReady": true,
      "interactionCapabilityReady": true,
      "verifierReady": true
    },
    "identity": {
      "operatorId": "operator-id",
      "tenantId": "tenant-dev",
      "roles": ["operator"],
      "capabilities": ["agora.workshop.v1"]
    },
    "dependentReadiness": {
      "assistantProvider": {
        "route": "/bff/assistant/providers",
        "requiredForAuthentication": false
      }
    }
  },
  "meta": {
    "contract": "PINT-016-STRICT-BROWSER-READINESS.v2"
  }
}
```

During one compatibility release, `providerReady` may be retained as optional
cached/unknown metadata, but it must never trigger a provider call or contribute
to `ready`.

### 3.3 Frontend change

Modify in execute-plans:

```text
src/lib/auth/bffBrowserSession.ts
src/lib/auth/AuthProvider.tsx
src/lib/auth/ProtectedRoute.tsx
src/lib/auth/bffBrowserSession.test.ts
src/lib/auth/AuthProvider.test.tsx
src/lib/auth/ProtectedRoute.test.tsx
```

Rules:

- session acceptance depends on strict `authReady`, not `providerReady`;
- provider state is loaded lazily only by features that need it;
- a provider failure cannot call `clearBffBrowserSession()`;
- `/bff/me` and auth readiness share an `AbortSignal`/bounded timeout;
- timeout UI offers retry and sign-out as distinct actions; and
- a token refresh is attempted at most once for a verification cycle.

### 3.4 Tests

Required tests:

1. monkeypatch `_assistant_provider_readiness` to raise if called; auth
   readiness still returns within the local test budget;
2. provider unavailable/timeout while strict session remains accepted;
3. invalid issuer/audience/role/capability still fails closed;
4. viewer is authenticated but has write capability false;
5. frontend does not clear a valid session on provider failure; and
6. hosted probe records separate `/me`, auth-readiness, and provider-readiness
   durations.

## 4. SD-AGC-02 — Durable Persona interaction worker

### 4.1 Current defect

`services/control-plane/bff/agora/interaction/router.py` currently calls
`execute_resource()` from `submit`, `retry_interaction`, and
`recover_interactions`. The provider call therefore executes inside the HTTP
request even though the route returns 202.

### 4.2 File disposition

Retain and refactor:

```text
services/control-plane/bff/agora/interaction/store.py
services/control-plane/bff/agora/interaction/runner.py
services/control-plane/bff/agora/interaction/provider.py
services/control-plane/bff/agora/interaction/router.py
```

Add:

```text
services/control-plane/bff/agora/interaction/worker.py
scripts/run_agora_interaction_worker.py
services/control-plane/bff/agora/interaction/test_worker.py
```

Deployment wiring adds one `agora-interaction-worker` process/service using the
same durable interaction store backend and an explicit worker identity. The
worker is product runtime, not development tooling.

### 4.3 Admission transaction

Within one store transaction, `POST /bff/agora/interactions`:

1. verifies session, tenant, capability, participants and immutable context;
2. verifies idempotency key and request hash;
3. inserts or replays the interaction resource;
4. appends `interaction.queued` to the durable outbox; and
5. returns the queued resource and receipt.

It does not call a provider, schedule FastAPI `BackgroundTasks`, or wait for a
worker.

Response additions:

```json
{
  "data": {
    "interaction_id": "int-...",
    "status": "queued",
    "version": 1,
    "workshop_id": "ws-...",
    "demo_run_id": "demo-...",
    "status_route": "/bff/agora/interactions/int-...",
    "timeline_route": "/bff/agora/interactions/int-.../timeline",
    "stream_route": "/bff/agora/interactions/int-.../stream",
    "execution_authority": "none"
  },
  "meta": {
    "replayed": false,
    "admitted_at": "RFC3339"
  }
}
```

`demo_run_id` is optional for ordinary use but must be server-validated and
persisted when provided by the bounded verifier.

### 4.4 Lease and execution

The worker loop:

```text
claim queued/expired-lease row using SKIP LOCKED or store CAS
  -> set leased/running + lease_owner + lease_expires_at
  -> invoke run_selected_persona_interaction
  -> persist provider attempts/opinions/synthesis
  -> append terminal outbox event
  -> release lease
```

Rules:

- one interaction/version has at most one active lease;
- lease heartbeat/expiry is durable;
- the provider idempotency key binds interaction, participant, and attempt;
- completed participant invocations are not repeated after restart;
- a partially successful panel terminates `degraded`, not generic success;
- secrets/provider raw auth never enter the resource or outbox; and
- terminal write and terminal event append are atomic.

### 4.5 Retry and recovery

- `POST /interactions/{id}:retry` creates `retry_queued` state and returns;
- `POST /interactions:recover` reconciles/queues eligible rows only and returns
  a bounded summary; it does not execute them;
- repeated retry key/hash replays the same retry receipt;
- non-retryable validation/authority failures remain terminal; and
- worker restart automatically recovers expired leases without a browser call.

### 4.6 Readback and observability

Expose in existing detail/timeline metadata:

- queue, lease and execution durations;
- attempt count and next retry time;
- last typed provider failure;
- worker/deployment identity;
- Workshop message/reconstruction correlation IDs; and
- outbox cursor/last event ID.

Metrics:

```text
agora_interaction_admission_seconds
agora_interaction_queue_depth
agora_interaction_queue_age_seconds
agora_interaction_execution_seconds
agora_interaction_terminal_total{status}
agora_interaction_lease_recovery_total
```

### 4.7 Tests

- admission returns while a fake provider is blocked;
- worker later completes the same ID;
- crash after first participant resumes without duplicate invocation;
- same key/same hash replays; mutated hash conflicts;
- foreign tenant/user cannot list, read, stream, retry or recover the row;
- restart preserves terminal readback; and
- no interaction route handler calls `execute_resource()` directly.

## 5. SD-AGC-03 — Canonical Persona Directory

### 5.1 Backend composition

Modify:

```text
services/control-plane/bff/main.py
services/control-plane/bff/tests/test_bff_b3_persona_fleet.py
services/control-plane/bff/test_bff_strategy_persona_contract.py
services/control-plane/bff/test_pathreon_market_persona_fleet_contract.py
```

Introduce one internal directory projection:

```python
PersonaDirectorySnapshot(
    tenant_id,
    snapshot_at,
    records_by_id,
    catalog_defaults_by_id,
)
```

The directory obtains admitted Persona membership from the canonical Persona
store once per request/snapshot. Persona List, Fleet membership, participant
eligibility, and detail use that membership. Fleet then enriches only those
members with league, binding, runtime, capital, incident, source and performance
projections.

### 5.2 Catalog/default handling

Market defaults that are not admitted Persona records are returned, if needed,
through an explicitly separate catalog collection:

```json
{
  "record_kind": "catalog_default",
  "detail_available": false,
  "admission_state": "not_admitted"
}
```

They do not appear in the canonical Fleet count and have no
`/personas/{id}`/Persona detail link. If product requirements need them in Fleet,
they must first be admitted to the Persona owner and become resolvable.

### 5.3 API invariants

For the same tenant/filter/snapshot:

- every Fleet `persona_id` exists in Persona List or a documented paginated
  continuation;
- every Fleet detail link returns 200 and the same ID;
- list/detail tenant semantics are identical;
- unauthorized/foreign records return 404;
- enrichment failure degrades fields, not membership; and
- counts include `canonical_total`, `filtered_total`, and optional
  `catalog_default_total` separately.

### 5.4 Migration

The five currently Fleet-only IDs are classified before code change:

1. if canonical records exist but the List path omits them incorrectly, repair
   the directory reader/index;
2. if they are default/catalog projections, remove their navigable Fleet rows;
3. if they represent legitimate required Personas, admit them through the
   canonical Persona workflow; and
4. never create placeholder detail rows solely to make a link return 200.

## 6. SD-AGC-04 — Authenticated Workshop SSE

### 6.1 Frontend transport

Modify in execute-plans:

```text
src/lib/bff-v1/agora/workshops.ts
src/lib/bff-v1/sse/liveSse.ts
src/lib/bff-v1/sse/bridge.ts
src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx
src/lib/bff-v1/agora/workshops.test.ts
src/lib/bff-v1/__tests__/sse.test.ts
```

`openWorkshopStream()` selects transport from the verified BFF session:

```text
cookie -> native EventSource(`${bffBase}/bff/agora/...`, withCredentials)
bearer -> fetchSse(`${bffBase}/bff/agora/...`, Authorization + tenant)
```

Reuse the existing live SSE parser/reconnect machinery. Do not create a second
ad-hoc SSE parser in the Workshop page.

### 6.2 Protocol rules

- require HTTP 200 and Content-Type beginning `text/event-stream`;
- send `Accept: text/event-stream`;
- pass `Last-Event-ID` on reconnect;
- use bounded exponential backoff with jitter;
- stop on 401/403 until the auth layer refreshes/reverifies;
- treat 409 replay-unavailable as a signal to fetch a canonical snapshot and
  reconnect from its cursor; and
- never log Authorization or full private event content.

### 6.3 Backend/proxy verification

The existing BFF Workshop stream remains the authority. Verify:

- bearer Authorization and cookie sessions both resolve tenant/user scope;
- CORS permits the Pantheon FE origin;
- no static-server fallback handles `/bff/*` stream paths; and
- heartbeat cadence is ≤5 seconds in the hosted acceptance profile.

## 7. SD-AGC-05 — Agora Performance attribution route

### 7.1 Backend files

Modify/add:

```text
services/control-plane/bff/agora/performance/router.py
services/control-plane/bff/agora/performance/service.py
services/control-plane/bff/agora/performance/attribution.py
services/control-plane/bff/agora/performance/test_performance.py
services/control-plane/openapi/agora_v1_13.openapi.yaml
services/control-plane/specs/agora/v14/capability_manifest_v1_13.json
services/control-plane/specs/agora/bundle_index.v1_13.json
services/control-plane/bff/contract_snapshots/backend_routes_manifest.json
docs/contracts/agora/backend-generation-input.v1_13.json
```

Add:

```text
GET /bff/agora/trading-room/performance-attribution/by-strategy
```

The route:

1. resolves the Agora user scope;
2. obtains only strategies owned/visible to that tenant/user;
3. filters telemetry/trade/risk inputs to those strategy identities before
   grouping;
4. returns typed partial/unavailable source surfaces when no data exists; and
5. carries `no_order_route_proof=agora_performance_read_only`.

Do not simply alias the fleet-wide Management endpoint without an owner filter.

### 7.2 Contract

```json
{
  "data": {
    "items": [],
    "page_info": {"next_page_token": null, "total": 0}
  },
  "meta": {
    "scope": {"tenant_id": "tenant-dev", "owner_user_id": "user-id"},
    "period": "latest",
    "snapshot_at": "RFC3339",
    "composition_sources": ["strategy_directory", "telemetry", "trade_journeys"],
    "surfaces": {
      "telemetry": {"status": "unavailable", "reason": "no_current_rows"}
    },
    "no_order_route_proof": "agora_performance_read_only"
  }
}
```

### 7.3 Frontend and drift gate

Retain the intended caller in:

```text
execute-plans:src/lib/bff-v1/agora/performance.ts
execute-plans:src/agora/pages/strategy-performance/StrategyPerformancePage.tsx
```

Add a cross-repository assertion that every production FE path exists in the
exact backend route manifest. The test must fail on the current 404 mismatch.

Isolation tests prove Alice cannot receive Bob's strategy ID, source refs,
metrics, pagination total, or existence signal.

## 8. SD-AGC-06 — Agora operational/data readiness

### 8.1 Dependency posture

This design reuses the source management authority defined in
[`pantheon_external_data_source_management_2026-08-24`](../pantheon_external_data_source_management_2026-08-24/INDEX.md).
It does not create an Agora source registry or an unrestricted continuous pull.

### 8.2 Add a composed read-only readiness route

Add:

```text
services/control-plane/bff/agora/operational_readiness.py
services/control-plane/bff/agora/test_operational_readiness.py
GET /bff/agora/operational-readiness
```

Inputs are bounded owner readbacks:

- source snapshot identity, source time, age, SLA and last typed failure;
- source-instance desired/observed state;
- paper-signal-producer health, active binding, last success, queue count and
  consumed snapshot;
- projection cursor/freshness for signals, inbox, journal, candidates,
  decisions, interactions and performance; and
- exact BFF deployment identity.

Response:

```json
{
  "data": {
    "status": "degraded",
    "source": {
      "snapshot_id": "mss-...",
      "source_timestamp": "RFC3339",
      "age_seconds": 500107,
      "sla_seconds": 86400,
      "freshness": "stale"
    },
    "signal_producer": {
      "status": "degraded",
      "last_success_at": null,
      "enqueued": 0,
      "reason": "source_snapshot_stale"
    },
    "surfaces": {
      "signals": {"status": "unavailable", "count": 0, "reason": "upstream_stale"},
      "decision_events": {"status": "unavailable", "count": 0, "reason": "upstream_stale"}
    }
  },
  "meta": {
    "requiredForAuthentication": false,
    "no_order_route_proof": "agora_operational_readiness_read_only"
  }
}
```

This route is never called by `/bff/auth/readiness`.

### 8.3 Bounded recovery sequence

The hosted functional proof performs, through existing governed source
commands:

1. identify the admitted Demo source instance and deployed connector;
2. validate config/credential reference without exposing the secret;
3. run one bounded read-only refresh;
4. require an accepted snapshot with source age ≤86,400 seconds;
5. confirm `paper-signal-producer` consumes that exact snapshot;
6. wait for terminal signal/projection readbacks; and
7. restore the normal dev source posture after proof.

If a legitimately fresh run produces zero signals, the surface is
`empty_fresh`, not `unavailable`. The proof must include the rule/evaluation
receipt that explains the zero.

### 8.4 Management Data Sources integration

`/management/data-sources` must display the source instance feeding the active
market snapshot, its desired/observed state, freshness, last failure, and
dependent producer. A zero-row source list while a snapshot ID is in use is a
composition error and fails acceptance.

## 9. SD-AGC-07 — Persona handoff and Workshop UX

### 9.1 Reconcile PR #665

PR #665 modifies:

```text
execute-plans:src/management/pages/PersonaDetail.tsx
execute-plans:src/agora/pages/strategy-workshop/StrategyWorkshopPage.tsx
```

Its behavior must be retained/rebased after review, not independently
reimplemented in a competing path:

- navigate after a successful canonical context resolver/eligibility receipt;
- reuse the initial resolver receipt only when the participant/mode/context
  binding is unchanged; and
- do not make interaction-list read readiness a prerequisite for admitting a
  new durable interaction command.

### 9.2 Composer state model

Replace one overloaded `dailyRuntimeState` gate with separate states:

```text
command capability: checking | available | unavailable
historical read: loading | ready | degraded | unavailable
provider feature: unknown | ready | degraded | unavailable
submission: idle | admitting | queued | running | terminal
```

The submit button requires authenticated write capability, canonical Workshop,
eligible participants, resolved context and command capability. A slow history
list or provider readiness panel does not disable durable admission.

### 9.3 Partial-operation UX

The current frontend appends a Workshop message, queues reconstruction, then
submits a Persona interaction. Each identity is shown separately:

- durable message event ID;
- reconstruction operation/result ID;
- interaction ID/status; and
- correlation/Demo run ID.

If a later stage fails, the UI shows the successful prior stages and offers a
stage-specific retry. It never displays the whole sequence as completed or
silently appends another message on retry.

### 9.4 Navigation tests

- Persona resolver receipt routes to exact Workshop ID;
- navigation occurs once and survives reload;
- changing participants invalidates resolver reuse;
- stale/foreign receipt is rejected;
- history read failure does not block command admission;
- command capability failure does block with typed reason; and
- read-only deployment keeps all writes disabled.

## 10. SD-AGC-08 — Hosted Demo and release acceptance

### 10.1 Workflow authentication

Modify in execute-plans:

```text
.github/workflows/agora-hosted-acceptance.yml
.github/workflows/pfg-agora-journey-e2e-hosted-acceptance.yml
e2e/agora-product-journey.spec.ts
e2e/persona-interaction-cross-repo-hosted.spec.ts
```

Remove the static-token-only assumption from `agora-hosted-acceptance.yml`.
Reuse the governed dev-login pattern already present in
`pfg-agora-journey-e2e-hosted-acceptance.yml` and the integration gate:

- operator and viewer client IDs come from non-secret variables;
- client secrets come from the `dev` GitHub environment;
- the job calls `/bff/auth/dev-login` immediately before proof;
- only the returned short-lived token is placed in masked job environment;
- tokens and dev-login responses are excluded from uploaded artifacts; and
- preflight fails clearly if either credential reference is unavailable.

One canonical hosted workflow should own the full functional Demo. Narrow
responsive/read-only specs may remain separate but must not be reported as
functional closure.

### 10.2 DemoRun evidence contract

Add a sanitized evidence schema to execute-plans:

```text
docs/contracts/agora/demo-run-evidence.v1.schema.json
```

Required shape:

```json
{
  "schema_version": "pantheon.agora.demo-run-evidence.v1",
  "demo_run_id": "demo-uuid",
  "started_at": "RFC3339",
  "completed_at": "RFC3339",
  "status": "passed",
  "exact_pair": {
    "frontend_sha": "full-sha",
    "bff_sha": "full-sha",
    "manifest_pair_id": "sha256"
  },
  "profile": "bounded-write-proof",
  "objects": {
    "proposal_id": "prop-...",
    "persona_id": "agora-servant-...",
    "workshop_id": "ws-...",
    "message_event_id": "evt-...",
    "reconstruction_id": "recon-...",
    "strategy_id": "strategy-...",
    "version_id": "version-...",
    "interaction_id": "int-..."
  },
  "steps": [
    {
      "id": "interaction_terminal_readback",
      "status": "passed",
      "receipt_ref": "receipt-...",
      "readback_ref": "int-..."
    }
  ],
  "negative_controls": {
    "viewer_write_denied": true,
    "cross_tenant_non_enumerating": true,
    "no_order_route_proof": true
  },
  "restoration": {
    "read_only_restored": true,
    "served_manifest_verified": true
  }
}
```

The artifact contains IDs, states, hashes and timing only. It contains no
tokens, client secrets, raw private prompts, provider transcripts, or Persona
private memory.

### 10.3 Acceptance levels in deployment evidence

Additive manifest/evidence projection:

```json
{
  "acceptanceLevels": {
    "safeRead": {
      "status": "accepted",
      "runId": "..."
    },
    "writeProof": {
      "status": "passed",
      "runId": "...",
      "demoRunId": "demo-..."
    },
    "functional": {
      "status": "accepted",
      "runId": "...",
      "evidenceDigest": "sha256:..."
    }
  }
}
```

Backward-compatible `deploymentState=accepted` continues to mean the safe
served artifact is accepted. Consumers claiming Agora functionality must also
require `acceptanceLevels.functional.status=accepted` for the exact pair.

### 10.4 Serialization and restore

- one environment concurrency group owns write-proof + restore;
- cancellation does not cancel the restore job;
- manifest switch occurs only after gate-before-switch checks;
- functional failure restores/preserves the prior safe read-only artifact;
- restore evidence verifies both write flags false and exact served digests;
- proof-created records remain marked with Demo/evidence retention metadata or
  are retired through their domain workflow; they are not hard-deleted by the
  deploy script.

## 11. Contract and compatibility changes

| Contract | Change type | Versioning rule |
|---|---|---|
| Auth readiness | semantic correction + additive metadata | publish PINT-016 v2; one-release optional compatibility fields |
| Interaction resource | additive routes/correlation/lease metadata | additive within Agora v1.13 if optional; regenerate exact hashes |
| Persona list/fleet | membership correction + additive record-kind/counts | no ghost compatibility rows |
| Workshop stream client | transport correction | no server schema break |
| Performance attribution | additive owner-scoped route | add to v1.13 manifest/OpenAPI and generated FE input |
| Operational readiness | new read-only route/schema | additive |
| Demo evidence | new CI artifact schema | not a product write contract |
| Deployment acceptance levels | additive evidence/manifest fields | old consumers retain safe-read meaning only |

Generated compatibility artifacts are regenerated from canonical sources. They
are never hand-edited merely to make hashes pass.

## 12. Validation matrix

| Gate | Required proof |
|---|---|
| Auth unit | no provider call, strict failures fail closed, provider outage preserves session |
| Interaction unit | admission latency, lease/CAS, idempotency, crash recovery, tenant isolation |
| Persona unit | List/Fleet/detail set equality and five prior ghost-ID regressions |
| SSE component | bearer header, BFF base URL, content type, replay and 401/409 behavior |
| Performance component | route exists, typed empty/partial response, owner/tenant isolation |
| Data component | stale/empty/unavailable classification and exact snapshot lineage |
| Cross-repo contract | FE production paths match backend manifest and Agora bundle hashes |
| Browser read-only | all major pages render honest states with writes disabled |
| Browser write-proof | new Demo create-to-terminal-readback and viewer denial |
| Restart proof | interaction and Demo IDs/readbacks survive BFF/worker restart |
| Restore proof | served manifest is exact, accepted and real/stub writes false |

The functional hosted gate fails if:

- auth verification exceeds 5 seconds;
- provider unavailability redirects to login;
- any SSE response is HTML;
- any Fleet Persona detail 404s;
- source age exceeds its Demo SLA;
- producer consumes a different snapshot than the proof;
- Performance returns route-level 404;
- interaction lacks terminal durable readback;
- a viewer mutation succeeds; or
- read-only restoration is missing.

## 13. Observability and evidence

Every hosted run records, without secrets/private content:

- exact FE/BFF/source/worker deployment identities;
- auth and provider timings separately;
- page route-ready and key API timings;
- Persona List/Fleet canonical/filtered counts;
- source snapshot identity, age and producer consumed identity;
- interaction queue/lease/execution durations and terminal status;
- SSE transport kind/content type/reconnect count;
- Performance response availability; and
- Demo IDs, step receipts, negative controls and restoration result.

Evidence names the exact GitHub workflow run and artifact digest. Screenshots
are supporting UI evidence, not substitutes for canonical API readback.

## 14. Rollout and rollback

### Rollout

1. deploy BFF auth decoupling and interaction worker behind worker-disabled
   configuration;
2. verify auth/read paths and enable one worker in dev;
3. deploy Persona directory and Performance route;
4. deploy FE SSE/navigation/composer changes;
5. run read-only browser gate;
6. run bounded source refresh and verify data propagation;
7. run serialized functional Demo write proof; and
8. restore read-only and publish acceptance levels.

### Rollback

- auth: revert to the prior auth verifier logic only; never restore provider as
  a mandatory login dependency;
- interaction: stop worker while preserving queued rows/outbox; do not restore
  inline execution;
- Persona: revert enrichment projection while retaining canonical membership;
  do not restore ghost links;
- SSE: fall back to bounded canonical polling with visible degraded status;
- data: return to normal dev source posture and show stale/unavailable;
- Performance: return typed unavailable, not fabricated rows; and
- delivery: restore last exact accepted read-only artifact.

## 15. Definition of done

This SD is implemented only when one exact hosted pair proves all of the
following in the same release window:

1. strict login enters a protected Agora route within budget despite an
   unavailable assistant provider;
2. Persona List/Fleet/detail use one resolvable identity set;
3. bearer Workshop SSE returns `text/event-stream` from the BFF;
4. a new Demo creates and reads back Proposal, Persona, Workshop, message,
   reconstruction/version and terminal interaction identities;
5. source snapshot freshness and producer consumption are current and bound;
6. Trading Room and Performance return typed, owner-scoped truth rather than
   route errors or ambiguous empty data;
7. viewer/cross-tenant negative controls pass;
8. restart/reload readback passes; and
9. the deployment is restored to exact accepted read-only state with
   `functional` acceptance evidence bound to the same pair.

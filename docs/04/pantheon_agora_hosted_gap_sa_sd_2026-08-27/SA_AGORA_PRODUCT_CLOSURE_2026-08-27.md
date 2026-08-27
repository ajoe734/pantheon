# Pantheon Agora Product Closure SA — 2026-08-27

Date: 2026-08-27

Input: [`CURRENT_GAP_2026-08-27.md`](CURRENT_GAP_2026-08-27.md)

Scope: revised System Analysis for the currently hosted Agora user journey,
including authentication, Persona/Workshop interaction, Trading Room,
performance, source freshness, Demo verification, and release acceptance.

Tier: L2 product-planning delta; not an L1 authority

Conflict rule: L1 policy, owning domain contracts, and the exact Agora v1.13
compatibility bundle win. This SA narrows implementation outcomes; it does not
move canonical write authority into the BFF or frontend.

## 1. Required product outcome

An authenticated user must be able to:

1. enter Agora without waiting for an unrelated assistant provider;
2. see one internally consistent Persona inventory;
3. start or open a Workshop;
4. append a strategy message and read back its durable event;
5. obtain reconstruction, readiness, Persona interaction, and typed cards
   through asynchronous, resumable owners;
6. move between Persona, Workshop, Trading Room, and Performance without losing
   canonical identity;
7. distinguish fresh, stale, empty, degraded, and unavailable market/product
   truth;
8. read owner-scoped performance through an Agora contract; and
9. complete a newly created Demo transaction on an exact hosted FE/BFF pair,
   with viewer denial and read-only restoration evidence.

Agora remains advisory and non-executing. None of these outcomes authorizes
broker orders, live capital effects, or self-approved governance.

## 2. Actors and responsibilities

| Actor | Product responsibility | Must not own |
|---|---|---|
| Agora user/operator | creates and reviews Workshop/interaction artifacts | provider health, canonical service state, direct execution |
| Viewer | reads allowed owner-scoped truth | any mutation |
| BFF auth boundary | verifies session, tenant, role, capability | assistant provider availability |
| Persona directory owner | supplies canonical Persona identity and detail membership | Fleet-only ghost identities |
| Workshop owner | stores messages, events, cards, versions, readiness links | synchronous assistant execution |
| Interaction worker | leases queued interactions, calls Persona providers, emits terminal events | HTTP admission, orders, capital |
| OpenClaw adapter | provides advisory Persona/assistant execution | login authority, Workshop truth, broker execution |
| Source-ingest | owns admitted market snapshot and freshness truth | Agora UI state |
| Paper signal producer | consumes accepted snapshot and emits signals | source freshness policy, fabricated fallback data |
| Agora projection owners | project signal/candidate/decision/performance truth | source or execution authority |
| Delivery controller | binds exact FE/BFF artifacts and acceptance levels | product-domain state |
| Hosted verifier | proves user journeys with temporary credentials | static credentials, release mutation outside its gate |

## 3. Revised architecture decisions

### AD-AG-01 — Authentication readiness is independent

`/bff/auth/readiness` answers only whether the current browser session is
strictly authenticated and authorized for the requested product boundary.

It must evaluate:

- bearer/cookie session validity;
- issuer, audience, verifier and role-claim configuration;
- tenant binding;
- role/capability admission; and
- session logout/revocation state.

It must not synchronously call OpenClaw, an LLM provider, source-ingest, paper
runtime, or another product dependency. `ready` is equivalent to `authReady`,
not `authReady && providerReady`.

### AD-AG-02 — Assistant readiness degrades features, not sessions

Assistant/Persona provider availability is read from the existing assistant
provider surface and displayed at the affected composer/tool. A provider
timeout yields a typed `degraded` feature state. It does not clear the valid
BFF session, redirect to login, or block unrelated pages.

### AD-AG-03 — One Persona directory defines navigable identity

Persona List, Persona Fleet, participant eligibility, and Persona detail must
consume one tenant-scoped canonical identity set.

A composed Fleet row may enrich a canonical Persona with runtime, league,
capital, incident, and performance data. It may not manufacture a navigable
Persona identity from defaults. Catalog/default projections are explicitly
typed and have no Persona-detail link until admitted into the canonical
directory.

### AD-AG-04 — Workshop is the interaction transaction root

A Strategy Workshop is the root aggregate for:

- user message admission;
- reconstruction operation;
- immutable StrategySpec/version lineage;
- selected Persona interaction;
- research/consultation operations;
- typed cards/events; and
- handoff to Trading Room.

Persona consultation is a Workshop sub-operation. It does not replace the
Workshop message or reconstruction flow.

### AD-AG-05 — Admission and execution are separate durable stages

Every long-running Workshop, interaction, research, or consultation command
uses:

```text
HTTP admission
  -> durable command + idempotency record + outbox
  -> queued receipt returned
  -> leased worker execution
  -> durable events/projection
  -> browser polling/SSE readback
```

HTTP 202 means the command is durably queued. It never means provider work was
performed in the request thread.

### AD-AG-06 — Streaming transport follows the authenticated session kind

- Cookie sessions may use native `EventSource` with credentials.
- Bearer sessions use an authenticated fetch-stream/SSE client that can attach
  Authorization and tenant headers.
- Both target the detected BFF base URL, preserve `Last-Event-ID`, validate
  `text/event-stream`, and reconnect with bounded backoff.

No relative request may accidentally route an authenticated BFF stream to the
frontend static server.

### AD-AG-07 — Freshness is product truth

Agora distinguishes:

- `fresh`: admitted source timestamp within its SLA;
- `stale`: previously valid data outside SLA;
- `empty_fresh`: fresh source processed and legitimately produced zero rows;
- `unavailable`: owner/read dependency cannot provide data;
- `degraded`: partial/current processing failure; and
- `not_configured`: no admitted source or producer dependency exists.

The Trading Room must never render a stale/unavailable data path as a normal
zero-opportunity market. Every empty surface carries source snapshot identity,
as-of, age, SLA, and typed reason where applicable.

### AD-AG-08 — Agora Performance remains owner-scoped

The Agora Performance list route is separate from fleet-wide Management
attribution. It filters by authenticated tenant/user ownership before grouping
or ranking. The browser is not redirected to the Management endpoint merely to
avoid a 404.

### AD-AG-09 — Demo proof is a correlated product transaction

A Demo acceptance run has one `demo_run_id` and records every resource and
receipt it creates. Success requires terminal readback, navigation, restart or
reload readback, and negative authorization. Printed IDs without terminal
state do not close the run.

### AD-AG-10 — Acceptance levels are explicit

Deployment truth has three independent levels:

| Level | Meaning |
|---|---|
| `safe_read_accepted` | exact pair serves strict, read-only, non-fabricated pages |
| `write_proof_passed` | bounded candidate completed authorized mutations and restored safe posture |
| `functional_accepted` | full authenticated Demo and required read journeys passed within budgets |

`deploymentState=accepted` without a functional level means safe-read
acceptance only.

### AD-AG-11 — No fixture repair in live/strict

Stale or missing market data, candidates, performance, Persona identity, or
provider output is shown as unavailable/degraded. Production routes do not
insert prototype candidates, local-only decisions, default opinions, or mock
success to satisfy a browser test.

### AD-AG-12 — Product and development authority remain separate

Agora/BFF may expose product diagnostics and receipts. It does not create
repository tasks, edit source, prepare worktrees, or operate the development
supervisor. Code changes remain in the repository delivery workflow.

## 4. Target user journeys

### 4.1 Login and protected-route entry

```text
Identity Platform session
  -> register short-lived bearer/cookie
  -> GET /bff/me
  -> GET /bff/auth/readiness (auth only)
  -> render protected route
  -> independently load assistant/data/product availability
```

Acceptance:

- p95 auth verification ≤2 seconds on dev under normal load;
- hard client timeout ≤5 seconds with a retry action;
- provider timeout does not redirect or sign out the user;
- one token refresh may occur silently; repeated credential entry is never the
  recovery for provider unavailability.

### 4.2 Persona to Workshop

```text
canonical Persona detail
  -> user chooses Ask/Challenge/Compare/Propose/Reflect
  -> resolver returns tenant-bound Workshop context and return route
  -> client navigates to exact Workshop ID
  -> Workshop reload reads same owner/tenant context
```

Acceptance:

- the returned Workshop ID equals the URL ID and detail readback;
- the route changes exactly once after a successful resolver receipt;
- back/forward and reload preserve the selected mode/participants where
  contractually allowed; and
- foreign tenant/user IDs produce non-enumerating 404.

### 4.3 Workshop message and interaction

```text
fresh Workshop + ETag
  -> append message
  -> read durable message event
  -> queue reconstruction
  -> queue Persona interaction
  -> worker emits running and terminal events
  -> UI receives SSE/poll readback
  -> cards/readiness/version refresh from canonical reads
```

Partial failure is visible and resumable. For example, a persisted message with
a failed provider interaction remains a valid message event with a retryable
interaction receipt; it is not rolled back or falsely displayed as complete.

### 4.4 Data to Trading Room

```text
bounded source refresh
  -> accepted MarketSnapshot
  -> paper signal producer consumes exact snapshot
  -> signal/inbox/journal owners persist outputs
  -> candidate/decision/Trading Room projections consume owner outputs
  -> page displays snapshot lineage and freshness
```

A Demo may use an approved bounded local/dev source. It must still produce a
real source record and snapshot through the source owner; it cannot inject UI
fixture data.

### 4.5 Strategy Performance

```text
owner-scoped strategy list
  + telemetry/trade/risk projections
  -> Agora attribution list
  -> per-strategy Performance detail
  -> governed suggestion action receipt, if write gate is open
```

Unavailable telemetry yields an empty/partial typed response, not 404 caused by
an absent route and not fabricated P&L.

### 4.6 Full Demo acceptance

The minimum Demo run is:

```text
strict operator login
  -> create/read Proposal
  -> revise and validate Proposal
  -> create/read or resolve canonical Agora Servant
  -> create/read Workshop
  -> navigate Persona -> Workshop
  -> append/read message
  -> reconstruct/read version identity
  -> submit/read terminal interaction and timeline
  -> receive authenticated SSE event or prove bounded polling fallback
  -> open Trading Room and Performance with typed data state
  -> reload and read every created identity
  -> viewer mutation denied
  -> restore read-only and verify exact manifest
```

The write-proof candidate is serialized. Parallel write-proof deployments or
watchdogs may not race the same hosted symlink or manifest.

## 5. Canonical state semantics

### 5.1 Session and provider

```text
session: unauthenticated -> verifying -> authenticated | rejected | expired
provider: unknown -> probing -> ready | degraded | unavailable
```

There is no transition from `provider unavailable` to `session rejected`.

### 5.2 Interaction

```text
queued -> leased -> running -> completed
                         \-> degraded
                         \-> failed

failed/degraded -> retry_queued -> leased -> ...
```

Requirements:

- same idempotency key + same hash returns the existing receipt;
- same key + different hash conflicts;
- leases expire and are recoverable;
- restart never duplicates a completed provider invocation;
- terminal projection includes provider attempt and missing/degraded participant
  identities without secret content.

### 5.3 Source and product availability

```text
not_configured -> configured_disabled -> refreshing -> fresh
                                      \-> degraded -> refreshing
fresh -> stale -> refreshing -> fresh
```

Product surfaces derive availability but never rewrite source desired state.

### 5.4 Deployment and proof

```text
candidate
  -> safe_read_candidate
  -> bounded_write_proof
  -> restore_read_only
  -> safe_read_accepted
  -> functional_accepted (only if functional proof passed)

any failure -> preserve/restore last safe_read_accepted release
```

## 6. Authoritative read model boundaries

| Read model | Required owner inputs | Forbidden input |
|---|---|---|
| Auth readiness | verifier/session/role/tenant/capability | live assistant/provider call |
| Persona Directory | admitted tenant Persona records | league/runtime default as identity |
| Persona Fleet | Persona Directory + runtime/league/capital/incident enrichment | independent Persona membership |
| Workshop timeline | Workshop events + operation receipts + interaction projection | page-local optimistic completion |
| Agora data readiness | source snapshot + producer health + projection cursors | row count alone |
| Trading Room | StrategySpec/candidate/decision/market projections | fixed lenses/prototype candidates |
| Performance | owner-scoped strategies + telemetry/trade/risk truth | fleet-wide Management data without owner filter |
| Deployment status | exact served manifest + workflow/evidence result | remote branch SHA alone |

## 7. Functional budgets

| Operation | Budget / rule |
|---|---|
| `/bff/me` | p95 ≤1 s |
| `/bff/auth/readiness` | p95 ≤1 s; hard server timeout ≤2 s |
| protected route usable shell | p95 ≤3 s, excluding explicitly lazy domain panels |
| read list/detail API | p95 ≤2 s for current dev dataset |
| write admission receipt | p95 ≤2 s; no provider execution in request |
| interaction terminal state | target ≤30 s; timeout is typed and retryable |
| SSE first event/heartbeat | ≤5 s after connection |
| market freshness for Demo | source timestamp age ≤86,400 s |
| read-only restore | watchdog proves exact manifest and write flags before acceptance |

The latency gate reports auth, provider, source, and page/domain timings
separately so one slow dependency cannot be hidden in a single route-ready
timeout.

## 8. Work packages and dependency order

| Package | Scope | Depends on |
|---|---|---|
| AGC-01 Auth decoupling | BFF auth contract + frontend protected session | none |
| AGC-02 Interaction worker | durable lease/worker/retry/readback | none |
| AGC-03 Persona directory | List/Fleet/detail membership and tenant isolation | none |
| AGC-04 Authenticated SSE | bearer/cookie transport selection | AGC-01 |
| AGC-05 Performance route | owner-scoped attribution API/client drift gate | AGC-03 |
| AGC-06 Data recovery | bounded source refresh, producer and projection readback | source authority available |
| AGC-07 UI navigation/composer | Persona handoff and resumable Workshop UX | AGC-01/02/03/04 |
| AGC-08 Hosted acceptance | token mint, Demo correlation, acceptance levels | AGC-01..07 |

AGC-01, AGC-02, AGC-03, and the backend part of AGC-05 may be developed in
parallel. The full hosted Demo waits for all packages and a fresh source
snapshot.

## 9. Release gates

### Gate A — component

- auth contract tests prove no provider call;
- interaction tests prove 202 returns before execution and restart recovery;
- Persona List/Fleet/detail membership/isolation tests pass;
- SSE bearer/cookie transport tests pass; and
- Agora Performance route manifest and isolation tests pass.

### Gate B — cross-repository

- FE callers match the exact BFF route manifest;
- generated Agora v1.13 compatibility artifacts remain consistent or are
  additively versioned;
- no production fixture/mock fallback enters the bundle; and
- PR #665 behavior is rebased/reconciled rather than independently duplicated.

### Gate C — functional hosted

- exact served FE/BFF identity is verified;
- auth and page budgets pass;
- new Demo run reaches terminal readback;
- fresh source and producer lineage is visible;
- Performance returns a typed response;
- viewer denial passes; and
- read-only restoration is accepted.

## 10. Explicit non-goals

This closure does not include:

- live broker or capital enablement;
- continuous unrestricted provider pulling;
- a new quant/candidate algorithm;
- a second Persona, Workshop, source, or performance authority;
- product-hosted repository or supervisor operations;
- large-scale UI redesign; or
- hiding data unavailability with fixtures.

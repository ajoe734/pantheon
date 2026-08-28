# Pantheon Agora Current GAP — 2026-08-27

Date: 2026-08-27

Status: current code-first, cross-repository, hosted-runtime-aware gap baseline

Tier: L2 current-state planning input; not an L1 authority

Scope: authenticated Agora pages, Demo persistence/interaction, source/data
freshness, Persona identity, Performance, streaming, and hosted acceptance

Conflict rule: L1 policy and owning service/contracts win. Exact current source
and hosted observations in this document supersede historical completion claims
but do not redefine authority or safety policy.

## 1. Audit question and completion rule

This audit answers a user-level question:

> Can a real authenticated user enter Agora, open every major page, create a
> Demo, interact with it, see current data, and read the same durable result
> back after navigation or restart?

Each result is evaluated at five levels:

1. **contract exists** — schema, API, client, or component exists;
2. **reachable** — the hosted browser actually invokes it;
3. **authorized** — the BFF applies the correct session, tenant, role, and
   write policy;
4. **durable** — the owning store reads back the same identity and state; and
5. **operationally accepted** — the exact hosted FE/BFF pair completes the
   path within its latency and freshness budgets.

A route returning HTTP 200, a locally passing component test, an ID printed by
a write script, or an `accepted` read-only manifest is not by itself a complete
product result.

## 2. Method and frozen evidence

### 2.1 Source and hosted identity

| Surface | Audited truth |
|---|---|
| Pantheon `origin/dev` | `3c79a185a97d920f41005bd41675433a046b6ece` |
| Hosted BFF `/bff/version` | `3c79a185a97d920f41005bd41675433a046b6ece` |
| execute-plans `origin/dev` | `3010ee6e164e962791c94a044c19d6e79465a230` |
| Hosted FE during browser audit | `31623e783f7a08f94df7099c207390b317077d61` |
| Current served FE after audit | `3010ee6e164e962791c94a044c19d6e79465a230` |
| Hosted deployment profile | `accepted / read-only` |
| Hosted build flags | `live`, `strict`, real writes false, stub writes false |
| Compatibility family | `agora.v1.13`, compatible/accepted |

The deployment manifest is
[`https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json`](https://pantheon-lupin-dev-fe.35.201.204.12.sslip.io/deployment.json).
It proves the served pair and safe defaults. It does not prove the functional
journey because the accepted artifact is intentionally read-only.

The current `3010ee...` deployment completed at `2026-08-27T03:54:30Z` through
[run 33037377758](https://github.com/ajoe734/execute-plans/actions/runs/33037377758).
It contains merged PR #664 and changes the login UI only. The authenticated
page-by-page evidence below remains explicitly bound to `31623e...`; none of
the Agora domain gaps is treated as closed by the post-audit deploy.

### 2.2 Browser method

The audit used a real, short-lived, BFF-verifiable operator session against the
hosted frontend and BFF. It did not intercept Agora/Persona APIs, inject fixture
responses, embed credentials in the frontend bundle, or use Lovable as release
truth. Pages were allowed to wait beyond their normal route-ready threshold so
slow terminal states could be distinguished from permanent failure.

### 2.3 Relevant workflow evidence

| Evidence | Result | Interpretation |
|---|---|---|
| execute-plans PR [#662](https://github.com/ajoe734/execute-plans/pull/662) | merged and hosted | BFF-backed authenticated Persona/Workshop verification is in the served FE |
| Integration gate [33029859954](https://github.com/ajoe734/execute-plans/actions/runs/33029859954) | success | exact pair passed compatibility/integration admission |
| Bounded Demo write proof [33031829879](https://github.com/ajoe734/execute-plans/actions/runs/33031829879) | partial/failure | records persisted, but navigation and interaction did not close |
| Agora hosted acceptance [33034225637](https://github.com/ajoe734/execute-plans/actions/runs/33034225637) | failed before product tests | workflow expected a static `BFF_AUTH_TOKEN` that was empty |
| Data Source hosted journey [33034238593](https://github.com/ajoe734/execute-plans/actions/runs/33034238593) | failed | `/bff/auth/readiness` returned provider-unready after a long wait |
| Management journey [33034250249](https://github.com/ajoe734/execute-plans/actions/runs/33034250249) | failed | provider stream and route loads exceeded functional timeouts |
| execute-plans PR [#665](https://github.com/ajoe734/execute-plans/pull/665) | open at audit freeze | proposes Persona navigation/composer unblocking; not hosted evidence |
| execute-plans PR [#664](https://github.com/ajoe734/execute-plans/pull/664) | merged and deployed after browser audit | removes authenticator-code login UI; does not change the observed Agora/BFF domain paths |

## 3. Executive current-state verdict

The current maturity is:

```text
exact read-only deployment identity       accepted
strict authenticated session              works, but slow and provider-coupled
major Agora route rendering               works after long waits
Proposal/Persona/Workshop persistence     partially proven
Persona -> Workshop -> interaction        blocked
authenticated Workshop SSE                broken for bearer session
market/signal/decision current data        unavailable
Strategy Performance                      broken by route drift
one canonical Persona inventory           absent
full newly-created Demo transaction        not accepted
```

Agora is not wholly offline. The failure is a combination of auth-gate
coupling, synchronous provider work, identity projection drift, missing current
data, one FE/BFF route mismatch, authenticated SSE transport, and acceptance
workflow configuration.

## 4. Page-by-page hosted result

| Page / route | Terminal observation | Approximate load | Verdict |
|---|---|---:|---|
| Public login | on audited `31623e...`, email/password and Google entry rendered with no public console/network error | immediate | UI was healthy; current `3010ee...` replaces the authenticator-code UX and requires a new browser screenshot only, not a domain redesign |
| Protected Cockpit | authenticated and rendered | 19.4 s | functional but outside reasonable startup budget |
| `/agora/trading-room` | shell renders; no StrategySpec workspace, candidate pool unavailable, 0 decisions/positions | 29.1 s | empty due upstream/data/product gaps |
| `/agora/strategy-workshop` | 14 Workshops; Demo records and Agora Servant participants visible | 28.7 s | read works; write controls disabled by read-only policy |
| Workshop detail | cards/events read; Demo proof Workshop has one readiness card and zero interactions | 28.6 s | partial; interaction chain not closed |
| `/agora/strategy-performance` | frontend calls `/bff/agora/trading-room/performance-attribution/by-strategy`; BFF returns 404 | 28.9 s | broken contract binding |
| Persona List | 13 Persona records; Demo Agora Servant resolves | slow after readiness | usable read path |
| Persona Fleet | BFF returns 18 records | slow after readiness | count/identity differs from Persona List |
| Fleet-only Persona detail | five Fleet IDs return 404 from `/bff/personas/{id}` | after load | broken drill-down invariant |
| Data Sources | zero sources/readable sources; no configured source visible | slow after readiness | cannot explain or operate the stale upstream source |

The long page times share the same startup dependency: protected rendering
waits for BFF session verification, and `/bff/auth/readiness` performs a live
OpenClaw provider probe before returning.

## 5. Authentication and readiness findings

Eight sequential readiness observations were made with a valid strict session:

- all eight had authenticated BFF identity and `authReady=true`;
- five of eight reported provider-unready;
- individual responses took approximately 16–20 seconds;
- four provider failures were `OPENCLAW_GATEWAY_TIMEOUT`; and
- one was `OPENCLAW_ADAPTER_REQUEST_FAILED`.

The deployed BFF implements the coupling directly in
[`services/control-plane/bff/main.py`](../../../services/control-plane/bff/main.py):

```text
/bff/auth/readiness
  -> _safe_provider_readiness()
  -> _assistant_provider_readiness()
  -> OpenClawOpsClient.get_assistant_readiness(auth_probe=True)
  -> data.ready = auth_ready and provider_ready
```

Authentication and assistant availability are separate authority domains. A
valid user session must not wait for or fail because an optional assistant
provider is unavailable.

## 6. Demo transaction audit

### 6.1 Persisted objects

| Object | Durable identity | Current readback |
|---|---|---|
| Governance Proposal | `prop_6a378d861fb1426b8c8820edea986637` | HTTP 200, `state=validated`, validation passed, three revisions |
| Agora Servant Persona | `agora-servant-fbeaee01e8f34c786555` | HTTP 200 and browser detail renders |
| Strategy Workshop | `ws_b414a0305cb40fb749139e66` | HTTP 200, open, one readiness card |

These records prove that create/update/validate and basic persistence are real.
They do not prove the complete Demo.

### 6.2 Transaction steps

| Step | Expected | Result |
|---|---|---|
| D01 | operator session is accepted | passed, but slow |
| D02 | create/update/validate Proposal | passed |
| D03 | create/read Agora Servant Persona | passed |
| D04 | create/read Workshop | passed |
| D05 | Persona action navigates to the returned Workshop route | failed; browser remained on Persona detail |
| D06 | append Workshop message and read durable event | frontend path exists; exact hosted Demo interaction did not close |
| D07 | run reconstruction and read resulting identity | not accepted in this hosted proof |
| D08 | submit daily Persona interaction | failed/timed out on `POST /bff/agora/interactions` |
| D09 | read interaction/timeline after navigation | failed; interaction list remains empty |
| D10 | viewer is denied mutation | passed |
| D11 | restore safe read-only profile | passed by watchdog; deployment is read-only |

The current BFF returns HTTP 202 for interaction submission but invokes
`execute_resource()` synchronously inside the request in
[`services/control-plane/bff/agora/interaction/router.py`](../../../services/control-plane/bff/agora/interaction/router.py).
Provider work therefore blocks the admission response and defeats the claimed
queued/worker boundary.

### 6.3 Why a new Demo was not created during the read-only audit

The accepted manifest explicitly disables real and stub writes. Bypassing that
policy would invalidate the deployment proof. The audit therefore read back the
previous bounded write-proof objects and reconciled the exact failed steps.

The next acceptance must open a serialized, time-bounded write-proof candidate,
create a new correlated Demo transaction, prove every step, and restore
read-only. Reusing only the object IDs above is not sufficient.

## 7. Data and producer findings

The hosted market snapshot `mss-a6914e1ea1c57b8df08d653b` was approximately
500,107 seconds (5.79 days) old against an allowed maximum of 86,400 seconds.

At audit time:

- `paper-signal-producer` was unhealthy;
- its restarted unhealthy streak had reached 198;
- `last_success_at` was absent;
- the active binding was degraded;
- enqueued signal count was zero; and
- signals, inbox, journal, decision events, interactions, research tasks, and
  Trading Room positions were all empty.

This is correct fail-closed behavior at the producer boundary, but the product
does not currently provide a sufficient degraded-state explanation or a usable
source-management readback. Empty data must not be confused with a valid
zero-signal market state.

## 8. Persona identity finding

`GET /bff/personas` and `GET /bff/management/persona-fleet` do not consume the
same canonical set:

- Persona List returned 13;
- Persona Fleet returned 18; and
- five Fleet-only IDs were not resolvable through Persona detail.

The implementation explains the drift:

- Persona List uses `_list_persona_records()` and tenant filters; while
- Persona Fleet uses `read_store.list_personas(include_market_persona_defaults=True)`
  and composes league/binding/runtime defaults.

Fleet currently emits drill-down links for rows that are not guaranteed to be
members of the Persona detail authority. Every row with a Persona detail link
must resolve under the same tenant and snapshot, or be explicitly marked as a
non-navigable catalog/default projection.

## 9. Streaming finding

The Workshop client constructs a relative native `EventSource` in
`execute-plans:src/lib/bff-v1/agora/workshops.ts`:

```text
new EventSource('/bff/agora/workshops/{id}/stream', {withCredentials: true})
```

The audited browser session was bearer-based. Native `EventSource` cannot add
the Authorization header, and the relative path reached the frontend origin;
the response was `text/html` rather than `text/event-stream`. The repository
already has bearer-capable fetch-stream/SSE infrastructure. Workshop streaming
must reuse it and target the detected BFF base URL.

## 10. Strategy Performance route finding

The hosted frontend intentionally requests the owner-scoped Agora route:

```text
GET /bff/agora/trading-room/performance-attribution/by-strategy
```

The hosted BFF exposes the fleet-wide Management route instead:

```text
GET /bff/management/performance-attribution/by-strategy
```

It also exposes per-strategy Agora performance detail, but not the Agora list
attribution route required by the page. The correct fix is to implement and
contract-test an owner/tenant-scoped Agora attribution list route. Pointing the
Agora page at the Management fleet-wide route would weaken the intended privacy
boundary.

## 11. Reconciliation with the 2026-08-13 scenario baseline

| Scenario | 2026-08-27 state | Evidence/change since 2026-08-13 |
|---|---|---|
| S01 private authenticated context | partial | strict session and tenant readback work; provider-coupled latency and split Persona identity remain |
| S02 create Workshop from ordinary UI | not accepted | Workshop can be created by bounded proof, but no normal hosted write-profile journey is accepted |
| S03 converse and reconstruct | partial/blocked | PR #662 posts a Workshop message and calls reconstruction before daily interaction; hosted interaction still times out |
| S04 typed Workshop cards | partial | canonical renderer is now active and one readiness card is visible; full action set lacks journey proof |
| S05 immutable StrategySpec/version | not accepted | no exact hosted Demo produced and selected a new immutable version |
| S06 governed research | blocked | research tasks are empty in the current Demo |
| S07 real candidate pool | blocked | candidate pool is unavailable |
| S08 data-backed Trading Room | blocked | stale upstream snapshot and no StrategySpec workspace |
| S09 canonical candidate actions | untestable | no candidate exists in current hosted data |
| S10 decision and intent | blocked | decision events are empty |
| S11 strategy performance | blocked | page-level attribution route returns 404 |
| S12 interaction evidence/dataset | blocked | interactions are empty; submit does not return promptly |
| S13 policy training/evaluation | not proven | no new Demo interaction reaches this stage |
| S14 independent Consultation | not proven | no new Demo interaction reaches this stage |
| S15 exact hosted pair | current `3010ee...`/`3c79a...` is read-only accepted only | identity drift is fixed, but functional acceptance is absent and the full pages were audited on prior FE `31623e...` |

No S02–S14 chain is functionally accepted on the current hosted pair.

## 12. Detailed GAP matrix

| ID | Priority | Class | Current truth | Required closure | Primary owner |
|---|---|---|---|---|---|
| AG-AUTH-01 | P0 | architecture/runtime | auth readiness synchronously probes OpenClaw and combines `auth_ready && provider_ready` | auth readiness evaluates only auth/session/role/tenant; assistant provider has a separate degradable read | Pantheon BFF |
| AG-AUTH-02 | P1 | latency/UX | valid session verification takes 16–20 s | protected-route auth p95 ≤2 s and hard timeout ≤5 s; provider outage does not clear/redirect session | BFF + execute-plans |
| AG-INT-01 | P0 | async semantics | `POST /interactions` runs provider work inline despite 202 | persist command/outbox and return queued receipt; leased worker owns provider execution/retry | Pantheon BFF/runtime |
| AG-INT-02 | P1 | product journey | Persona-to-Workshop route stays on Persona detail | successful resolver receipt navigates exactly once to canonical Workshop and survives reload | execute-plans |
| AG-INT-03 | P1 | readback | no durable interaction/timeline after Demo submit | interaction reaches terminal state and reads back by ID/workshop after reload/restart | BFF + execute-plans |
| AG-SSE-01 | P1 | transport/auth | relative native EventSource returns frontend HTML for bearer session | detected BFF base + bearer-aware fetch stream; cookie EventSource only when cookie session is authoritative | execute-plans |
| AG-ID-01 | P0 | identity | Persona List 13, Fleet 18, five drill-down 404s | one tenant-scoped Persona directory; every navigable row resolves through detail | Pantheon BFF |
| AG-DATA-01 | P0 | freshness | source snapshot 5.79 days old; producer unhealthy | bounded source refresh produces accepted snapshot ≤24 h and producer resumes with durable success | source-ingest + paper producer |
| AG-DATA-02 | P1 | product truth | Data Sources page shows zero and cannot explain active stale snapshot | expose source instance, observed freshness, last failure, producer dependency, and remediation state | source-ingest/BFF/Management |
| AG-DATA-03 | P1 | product journey | signals/inbox/journal/decisions/positions are empty | fresh source stimulus naturally populates owners, or every surface reports typed unavailable/stale reason | Agora projections |
| AG-PERF-01 | P1 | contract | FE owner-scoped attribution route returns 404 | add route/schema/manifest tests and owner/tenant isolation | Pantheon BFF |
| AG-ACC-01 | P1 | CI/auth | Agora hosted workflow depends on absent static token | mint short-lived dev-login operator/viewer tokens inside job; never store token in artifact | execute-plans workflow |
| AG-ACC-02 | P0 | release truth | `accepted` read-only can be mistaken for functional acceptance | separate safe-read acceptance, write-proof result, and functional product acceptance in manifest/evidence | delivery workflow |
| AG-ACC-03 | P1 | Demo proof | persisted IDs exist but create-to-interact-to-readback transaction fails | new correlated Demo run proves all steps and restore watchdog | cross-repository acceptance |
| AG-CONTRACT-01 | P1 | drift prevention | route and SSE transport drift reached hosted release | exact backend route manifest + FE caller contract + authenticated transport tests block deployment | both repositories |

## 13. Closure order

The shortest safe dependency order is:

1. decouple auth from assistant provider and enforce the latency budget;
2. move Persona interaction execution behind a durable worker;
3. unify Persona identity and correct bearer SSE transport;
4. add the owner-scoped Agora performance attribution route;
5. restore one fresh bounded market snapshot and verify producer/surface
   propagation;
6. merge and deploy the current navigation/composer fix only after review;
7. repair hosted token minting and add route/transport drift gates; and
8. run a new serialized Demo write proof, then restore and accept read-only.

UI expansion, additional widgets, new Persona types, live trading, continuous
external pulls, and new candidate algorithms are not prerequisites for this
closure.

## 14. Non-actions in this documentation pass

This audit/document pass does not:

- enable writes on the accepted deployment;
- mint or retain a live credential in repository evidence;
- refresh an external provider or alter source scheduling;
- restart a service;
- place an order or change capital/runtime authority;
- merge PR #665; or
- claim the historical Demo objects are a complete new acceptance run.

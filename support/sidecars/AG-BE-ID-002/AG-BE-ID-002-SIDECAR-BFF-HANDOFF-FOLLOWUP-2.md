# AG-BE-ID-002 Sidecar Follow-up 2: BFF and Frontend Handoff

| Field | Value |
|---|---|
| Task ID | `AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-ID-002` - OpenClaw ensure/provision/reconcile servant |
| Parent owner / reviewer | Codex2 / Codex |
| Prepared by | Codex2 |
| Reviewer | Codex |
| Date | 2026-06-20 |
| Mutates canonical truth | false |
| Status | Review approved; merged through PR #1814 |

## Purpose

This support-only packet extends the earlier AG-BE-ID-002 BFF handoff by making
the parent absorption boundary explicit. It is not an implementation plan for
runtime code. It gives the parent owner and reviewer a concise ledger for:

- current BFF route behavior for the identity and servant surfaces;
- contract gaps that still block a successful servant ensure path;
- frontend handoff rules while backend provisioning remains unavailable;
- decisions the parent task must settle before code can safely replace the 501
  servant ensure stub.

This packet does not change canonical documents, OpenAPI, schema files,
capability manifests, BFF runtime code, registry code, OpenClaw adapter code,
governance implementation, or execute-plans source.

## Current Task State Snapshot

Status commands used `AI_NAME=Codex2`.

| Task | Observed status | Handoff implication |
|---|---|---|
| `AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | `review_approved`; owner `Codex2`, reviewer `Codex` | This packet is the only intended deliverable; owner closeout records the approved merge. |
| `AG-BE-ID-002` | active `blocked`; owner `Codex2`, reviewer `Codex`, waiting for `Codex` | Parent implementation must not proceed until route/schema/capability/adapter questions are answered. |
| `AG-BE-ID-002-SIDECAR-BFF-HANDOFF` | archived `done` | Earlier BFF/frontend gap packet is available as the base handoff. |
| `AG-BE-ID-002-SIDECAR-ACCEPTANCE` | archived `done` | Acceptance and dependency checklist is available as a companion packet. |

The parent blocker is still material: the active parent note says the requested
implementation cites design and adapter surfaces that do not line up with the
current checkout. This follow-up narrows what can be safely consumed without
inventing contract truth.

## Closeout Record

Reviewer Codex approved this support-only packet with no canonical or runtime
change request. The delivery PR was
`https://github.com/ajoe734/pantheon/pull/1814`, merged to `dev` at
`1d76ba262d32a253a7be894921b7b6657b65a7f3`.

GitHub reported the required PR checks successful before merge:

- `Commit trailers`
- `Runtime mirror guard`
- `Smoke acceptance`
- `Forward to orchestrator`

Owner closeout revalidated that the support artifact remains the only authored
deliverable for this sidecar. The parent task `AG-BE-ID-002` remains blocked on
the route, schema, capability, registry, and OpenClaw facade decisions listed
below; this packet does not resolve those decisions or authorize implementation.

## Sources Rechecked

| Source | Finding |
|---|---|
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002` | Parent is blocked on missing or unclear servant ensure design/adapter surfaces. |
| `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | This sidecar is active and support-only. |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF.md` | Earlier packet lists the BFF query gap, operator journey, and frontend notes. |
| `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-ACCEPTANCE.md` | Acceptance packet records the no-authority safety boundary and parent acceptance checklist. |
| `services/control-plane/bff/agora/router.py` | Runtime implements `GET /bff/agora/me` and `GET /bff/agora/capabilities`. |
| `services/control-plane/bff/agora/servant/router.py` | Runtime registers `POST /bff/agora/servant/ensure`, authenticates, then returns 501 `NOT_IMPLEMENTED`. |
| `services/control-plane/bff/tests/test_agora_router.py` | Tests assert `/me`, `/capabilities`, and current 501 ensure behavior. |
| `services/control-plane/bff/tests/test_agora_identity_scope.py` | Tests assert tenant/user predicate, Agora-only capabilities, and servant no-authority policy. |
| `services/control-plane/specs/agora/capability_manifest.json` | Frozen manifest has seven `agora.*.v1` capabilities and does not expose a separate servant-specific allow/deny set. |
| `services/control-plane/specs/agora/servant_profile.schema.json` | ServantProfile schema enforces `persona_class=agora_servant`, user-private ownership, and `execution_authority=none`. |
| `integrations/openclaw/persona_agent_sync.py` | CLI-oriented persona-to-OpenClaw reconciliation exists, but it is not a BFF-callable facade. |
| `integrations/openclaw/integration.md` and `integrations/openclaw/adapter/README.md` | Adapter boundary reserves Pantheon-side OpenClaw mapping; future facade endpoints are not native upstream API claims. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` and `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` | No frontend path helper or live adapter for `/me`, `/capabilities`, or `/servant/ensure` was found. |

## BFF Query Ledger

| Route / surface | Runtime state | Contract / frontend state | Parent handoff rule |
|---|---|---|---|
| `GET /bff/agora/me` | Implemented in package router; returns tenant/user scope, read predicate, seven Agora capabilities, and servant policy. | Exact route is not present in the frozen OpenAPI or frontend path helpers. | Parent may rely on it as current runtime evidence, but should not call it contract-complete until OpenAPI/path helper reconciliation is accepted. |
| `GET /bff/agora/capabilities` | Implemented in package router; returns filtered capability manifest with backend scope. | Exact route is not present in frontend path helpers; manifest route prefixes do not list this exact readiness route. | Safe for backend evidence and possible interim FE readiness, but downstream should label it as runtime route truth, not generated contract truth. |
| `POST /bff/agora/servant/ensure` | Registered in package router; authenticates and returns 501 `NOT_IMPLEMENTED`. | No accepted request body, success envelope, OpenAPI operation, or frontend helper exists. | Must be treated as backend-not-ready. Do not map 501 to a successful `ServantProfile`. |
| `ServantProfile` DTO | Schema exists and locks user-private/no-authority fields. | No route response binds this schema to create vs reconcile semantics. | Parent must freeze 200 vs 201 behavior, envelope shape, and existing/suspended/retired handling before implementation. |
| OpenClaw provisioning | `persona_agent_sync.py` can reconcile persona records through CLI commands with injectable runner/writer. | No BFF-callable adapter route or helper is accepted for servant provisioning. | Parent must choose a governed adapter/facade path. BFF should not directly invent a raw CLI call path. |
| Capability allow/deny | Frozen manifest has seven `agora.*.v1` capabilities; schema permits only those values. | Dispatch references a servant capability set, but no separate servant-specific set is frozen here. | Parent must either use the frozen seven Agora capabilities or add a versioned canonical policy before narrowing. |

## Parent Unblock Decision Matrix

| Decision | Required output before coding |
|---|---|
| Route authority | Decide whether `POST /bff/agora/servant/ensure` becomes an Agora v1 OpenAPI operation now or remains an internal stub/deferred route. |
| Request shape | Freeze whether ensure is bodyless/idempotent from auth scope or accepts safe display/profile preferences. Client-supplied tenant/user fields must remain forbidden. |
| Success envelope | Bind `ServantProfile` into the BFF envelope and define status codes for create, existing reconcile, suspended, retired, and partial sync outcomes. |
| Registry write owner | Name the exact persona registry helper/service that creates or reconciles one user-private `agora_servant` record. |
| OpenClaw facade | Name the Pantheon-owned adapter/helper that maps the registry record to an OpenClaw agent and private workspace. |
| Failure taxonomy | Freeze error codes for registry failure, policy denial, cross-tenant mismatch, OpenClaw transport failure, and OpenClaw sync failure after registry write. |
| Capability policy | Confirm whether the response returns the frozen seven Agora capability names or a new versioned servant policy. |
| Frontend activation gate | State the evidence needed before execute-plans may show an active servant flow instead of backend-not-ready. |

## Operator Journey

### Current honest journey

```text
Operator opens Agora
  -> frontend may call GET /bff/agora/me through a strict client
  -> BFF returns tenant_id, user_id, fail-closed read predicate,
     Agora capabilities, and servant_policy.execution_authority="none"
  -> frontend may call GET /bff/agora/capabilities through a strict client
  -> BFF returns the filtered capability manifest and backend scope
  -> frontend may call POST /bff/agora/servant/ensure only as a readiness probe
     if the parent accepts the interim runtime stub as callable
  -> current backend returns 501 NOT_IMPLEMENTED
  -> UI renders servant provisioning unavailable/backend-not-ready
  -> no Ask/session/trainer/research success path is implied by this route
```

### Journey that remains blocked

```text
Operator logs in
  -> ensure creates or reconciles exactly one user-private servant profile
  -> BFF persists/reconciles the Persona Registry record with tenant/user scope
  -> BFF invokes a governed OpenClaw adapter facade for private agent sync
  -> BFF returns { data: ServantProfile, meta: ... }
  -> downstream Ask/session surfaces bind to that persona_id
```

This success journey remains blocked until the parent route, registry, adapter,
error, and response-envelope decisions are accepted.

## Frontend Handoff Notes

The execute-plans follow-up should stay conservative until the backend contract
is accepted.

| Frontend area | Current fact | Handoff rule |
|---|---|---|
| Path helpers | `paths.ts` has Agora helpers for signals, inbox, journal, postmortems, and ask sessions only. | Add `agoraMe`, `agoraCapabilities`, and `agoraServantEnsure` only after parent accepts route authority. |
| Live adapter | `bffAgora` adapts existing Agora reads only. | Add identity and servant clients as strict live methods; no mock/seed fallback in strict mode. |
| Servant ensure | Backend returns 501 today. | Adapter must map 501 to `backend_not_ready` or equivalent typed unavailable state, not to active servant profile. |
| Shell gating | `AskPersonas` and session surfaces can still be reachable in current FE code. | Parent FE work must gate Ask/session controls behind identity readiness and backend servant/session availability. |
| Safety display | `execution_authority="none"` and prohibited authority fields are display/safety facts. | Do not expose RuntimeBinding, broker order, capital binding, or live execution controls from servant status. |
| Tests | Current FE adapter tests cover other Agora reads. | Add tests for path construction, strict 501 handling, no seed fallback, and no broad Management/capital/broker imports when FE work starts. |

Suggested adapter-only unavailable result while the backend remains a stub:

```ts
type ServantEnsureUnavailable = {
  status: "backend_not_ready";
  route: "/bff/agora/servant/ensure";
  httpStatus: 501;
  retryable: false;
};
```

This is not a canonical DTO. It is a frontend error-handling handoff until the
parent freezes the successful `ServantProfile` envelope.

## Parent Absorption Checklist

Codex should not approve parent absorption unless the parent evidence answers:

| Check | Required evidence |
|---|---|
| Blocker disposition | Parent either keeps `AG-BE-ID-002` blocked or records explicit accepted decisions for every item in the decision matrix. |
| Contract truth | OpenAPI/schema/capability changes, if any, are versioned and reviewed in the parent or a canonical contract task, not introduced by this sidecar. |
| Registry boundary | Implementation names and tests the persona registry write helper instead of fabricating a side registry. |
| OpenClaw boundary | Implementation goes through a governed Pantheon adapter/helper and preserves OpenClaw as runtime substrate, not governance owner. |
| Tenant isolation | Tests prove tenant/user are derived from auth scope and client-provided tenant/user overrides are rejected. |
| No authority escalation | Tests assert no runtime binding, broker order, capital binding, or live order authority leaks into the servant profile or OpenClaw sync. |
| Frontend truth | Any FE shell treats current 501 as unavailable and does not show successful servant/session state before backend success exists. |

## Verification Notes

Commands run by Codex2 while preparing this sidecar:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002-SIDECAR-BFF-HANDOFF
AI_NAME=Codex2 ./scripts/ai-status.sh show AG-BE-ID-002-SIDECAR-ACCEPTANCE
rg -n -P '/bff/agora/(me|capabilities|servant/ensure)(?=["\s:]|$)' \
  services/control-plane/openapi/agora_v1.openapi.yaml \
  services/control-plane/specs/agora/capability_manifest.json \
  services/control-plane/bff/agora \
  services/control-plane/bff/tests/test_agora_router.py
rg -n -P '/bff/agora/(me|capabilities|servant/ensure)(?=["`\s:]|$)|agoraMe|agoraCapabilities|ServantProfile|ensureAgoraServant|servant' \
  /home/lupin/code/execute-plans/src/lib/bff-v1 \
  /home/lupin/code/execute-plans/src/lib/bff \
  /home/lupin/code/execute-plans/src/agora \
  /home/lupin/code/execute-plans/src/entries
git diff --check --no-index -- /dev/null \
  support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py \
  services/control-plane/bff/tests/test_agora_identity_scope.py -q
```

Expected interpretation:

- runtime BFF has `/me`, `/capabilities`, and a 501 `/servant/ensure` stub;
- the frontend scan found no matching identity/servant helper or adapter method;
- focused BFF Agora tests passed: 22 tests;
- no canonical truth or runtime code was changed by this packet.

## Support Boundary

- Changed artifact:
  `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`.
- Generated task context remains outside the authored support artifact unless
  closeout explicitly requires committing it.
- No L1 canonical policy, OpenAPI, schema bundle, BFF runtime code, registry,
  governance implementation, OpenClaw adapter implementation, or execute-plans
  source was changed.
- This packet should be reviewed by Codex and then considered by the parent
  owner/reviewer before `AG-BE-ID-002` implementation resumes.

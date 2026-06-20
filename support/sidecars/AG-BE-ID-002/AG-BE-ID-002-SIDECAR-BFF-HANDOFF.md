# AG-BE-ID-002 Sidecar: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-ID-002-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-ID-002` - OpenClaw ensure/provision/reconcile servant |
| Parent owner / reviewer | Codex2 / Codex |
| Prepared by | Codex |
| Reviewer | Codex2 |
| Date | 2026-06-20 |
| Mutates canonical truth | false |
| Status | Ready for sidecar review |

## Purpose

This support-only packet gives the parent owner the current BFF query gap,
operator journey, and execute-plans handoff notes for the proposed Agora servant
ensure flow. It does not modify canonical truth, OpenAPI, BFF runtime code,
registry state, OpenClaw adapter code, governance policy, or execute-plans
source.

The parent task is currently blocked in `ai-status.json` because the requested
implementation points at missing or unresolved design surfaces:

- `POST /bff/agora/servant/ensure` exists only as a BFF stub returning 501.
- The frozen Agora OpenAPI route catalog does not list the servant ensure route.
- The frozen capability manifest defines `agora.identity.v1` and the
  `servant_profile` schema, but no separate "§5.4 allow/deny" capability set.
- `integrations/openclaw/persona_agent_sync.py` can reconcile persona records
  into OpenClaw agents via CLI, but no BFF-callable OpenClaw adapter facade for
  servant provisioning exists in this checkout.
- The parent artifact path `integrations/openclaw/adapter/agora_servant.py` is
  not present in the current tree.

## Current BFF Truth

| Surface | Current state | Evidence |
|---|---|---|
| Agora package router | `create_agora_router()` mounts identity, servant, workshop, research, trading, dashboard, shadow, personalization, and management projection sub-routers. | `services/control-plane/bff/agora/router.py` |
| Operator scope | `GET /bff/agora/me` returns an Agora envelope with tenant/user read predicate, seven Agora capabilities, and `servant_policy`. | `services/control-plane/bff/agora/router.py`; `services/control-plane/bff/tests/test_agora_router.py` |
| Capability manifest | `GET /bff/agora/capabilities` returns the frozen manifest filtered by the caller scope. | `services/control-plane/specs/agora/capability_manifest.json`; `services/control-plane/bff/agora/router.py` |
| Servant ensure route | `POST /bff/agora/servant/ensure` authenticates and then returns `501 NOT_IMPLEMENTED`. This is an intentional stub. | `services/control-plane/bff/agora/servant/router.py`; `services/control-plane/bff/tests/test_agora_router.py` |
| Servant profile DTO | `ServantProfile` and `servant_profile.schema.json` define user-private persona profile fields and prohibit runtime binding, broker order, and capital binding authority. | `services/control-plane/bff/agora/models.py`; `services/control-plane/specs/agora/servant_profile.schema.json` |
| User-private scope | `resolve_agora_user_scope()` enforces tenant/user predicates and strips non-Agora capabilities. | `services/control-plane/bff/agora/identity/scope.py`; `services/control-plane/bff/tests/test_agora_identity_scope.py` |
| OpenClaw sync utility | Persona-to-agent sync exists as a CLI-backed helper with injectable runner/writer. It is not exposed as a governed BFF provision facade. | `integrations/openclaw/persona_agent_sync.py`; `integrations/openclaw/integration.md` |

Important frontend-facing consequence: the only current backend behavior for
`/bff/agora/servant/ensure` is a typed unavailable state. A frontend slice may
render scope/capability information from `/bff/agora/me` and
`/bff/agora/capabilities`, but it must not present a successful servant
creation/reconcile path until the parent task resolves the canonical gaps.

## BFF Query Gap Analysis

| Gap | Impact | Parent-owner decision needed |
|---|---|---|
| OpenAPI route absence | Contract generators and frontend route truth do not have `POST /bff/agora/servant/ensure`. | Decide whether to add this route to the Agora v1 OpenAPI or keep it as non-canonical internal stub until a later AG-XR slice. |
| Missing request schema | No canonical body shape exists. The current stub accepts no body. | Decide whether ensure is bodyless/idempotent from auth scope, or whether it accepts preferences such as display name, avatar, model, or initial traits. |
| Missing success envelope | `ServantProfile` exists, but no route response binds `ServantProfile` into `{data, meta}` or declares 200 vs 201 semantics. | Decide success status and response envelope for create, existing-user reconcile, suspended/retired servant, and partial OpenClaw sync failure. |
| Missing registry write owner | The task asks for creating an `agora_servant` registry object, but this sidecar found no task-approved write surface for user-private servant creation. | Identify the existing persona registry write API/helper to call, or explicitly scope a new backend helper. |
| Missing OpenClaw provision facade | `persona_agent_sync.py` shells out to `openclaw agents ...`; the adapter contract describes agent provisioning as a future Pantheon-side responsibility. | Decide whether BFF calls a new adapter route, a service helper, or defers OpenClaw sync to an async job after registry creation. |
| Capability allow/deny ambiguity | Existing manifest has seven Agora capabilities and `allowed_agora_capabilities` on `ServantProfile`; no separate AG-BE-ID-002 allow/deny list is frozen. | Use the frozen seven capability names or add a canonical capability policy before implementation. |
| Error taxonomy | Current stub uses top-level `ErrorCode.NOT_IMPLEMENTED`, not `AgoraErrorCode.SERVANT_PROVISION_FAILED`. | Decide the final error codes for registry failure, cross-tenant mismatch, OpenClaw sync failure, and policy denial. |
| Frontend adapter absence | execute-plans has no path helpers or live adapter methods for `agora/me`, `agora/capabilities`, or `agora/servant/ensure`. | Add frontend adapter work only after backend contract is accepted; until then, expose blocked/unavailable state only. |

## Operator Journey

### Current safe journey

```text
Operator opens Agora UI
  -> frontend may call GET /bff/agora/me
  -> BFF returns tenant/user scope, read predicate, Agora capabilities,
     and servant_policy.execution_authority="none"
  -> frontend may call GET /bff/agora/capabilities
  -> BFF returns the frozen manifest filtered by the operator scope
  -> any servant ensure CTA must be disabled, hidden behind "backend not ready",
     or prepared to render typed 501 unavailable state
  -> frontend must not fabricate a servant profile from seed/mock data in strict
     live mode
```

### Proposed journey after parent unblocks implementation

```text
Operator opens Agora Ask or setup screen
  -> GET /bff/agora/me resolves tenant_id, user_id, read_predicate,
     and servant_policy
  -> if no private servant profile is known, UI offers "prepare my Agora servant"
  -> POST /bff/agora/servant/ensure with idempotency/correlation headers
  -> BFF derives tenant/user from auth scope, not from client-trusted body fields
  -> BFF finds or creates exactly one user-private persona registry record with:
       persona_class=agora_servant
       owner_scope=user_private
       visibility_scope=private or redacted_management
       memory_scope=private_user
       agora_user_id=<current user>
       execution_authority=none
       prohibited_authority includes runtime_binding, broker_order, capital_binding
  -> BFF reconciles OpenClaw agent identity only through a governed adapter/helper
  -> BFF returns { data: ServantProfile, meta: { capability: "agora.identity.v1" } }
  -> Ask/Workshop pages can use data.persona_id for /bff/agora/ask sessions
```

### Failure and degraded journey

```text
401/403 from /bff/agora/me
  -> render auth/scope error; do not show servant controls

501 from /bff/agora/servant/ensure
  -> render backend-not-ready state; do not retry as a seed/mock create

Registry create succeeds but OpenClaw sync fails
  -> parent must decide whether the response is non-2xx, degraded 2xx, or async
     job accepted; frontend cannot infer this from current truth

Servant exists but status is suspended/retired
  -> parent must decide whether ensure reactivates, returns current profile with
     blocked actions, or fails with policy-denied error
```

## Frontend Handoff Notes

Current execute-plans facts checked in `/home/lupin/code/execute-plans`:

| Area | Current frontend state | Handoff note |
|---|---|---|
| Path helpers | `src/lib/bff-v1/paths.ts` has Agora paths for signals, inbox, journal, postmortems, and ask sessions only. | Add `agoraMe`, `agoraCapabilities`, and `agoraServantEnsure` only after backend route truth is accepted. |
| Agora live adapter | `src/lib/bff/agora.ts` adapts signals, inbox, journal, and ask session list. | Add `identity.me()`, `identity.capabilities()`, and `servant.ensure()` with strict live error propagation. |
| Ask UI | `src/agora/pages/AskPersonas.tsx` currently uses a local static persona list and mock responses. | Do not map the private servant into this list until `ServantProfile` is live-backed. In strict mode, absence or 501 should render unavailable/empty state. |
| Navigation | `src/agora/AgoraLayout.tsx` has Ask, Committee, Trainer, Memory, Persona Lab, and related pages; no servant setup entry exists. | A future FE slice can place servant setup near Ask/Persona Lab, but should not add a success-path CTA before backend contract acceptance. |
| Tests | Existing live adapter tests cover Agora signals/list behavior, not servant identity/provision. | Add tests for path construction, 501 handling, and strict no-seed fallback when servant adapter work starts. |

Recommended frontend adapter contract once parent settles the backend route:

```ts
type ServantEnsureResult = {
  data: ServantProfile;
  meta: {
    snapshot_at: string;
    capability: "agora.identity.v1";
    audience?: string;
  };
};
```

The frontend should treat `policy.execution_authority === "none"` and
`prohibited_authority` as display/safety facts, not as permission to route
orders. No Agora servant UI should expose runtime binding, broker order, or
capital binding actions.

## Parent Absorption Checklist

Codex2 should resolve these before turning the parent implementation loose:

| Check | Expected parent outcome |
|---|---|
| Canonical route decision | Either add `POST /bff/agora/servant/ensure` to OpenAPI/route truth, or explicitly leave AG-BE-ID-002 blocked/deferred. |
| DTO binding | Bind `ServantProfile` to the route response envelope and document create vs reconcile status. |
| Registry source | Identify the exact persona registry read/write helper and ownership boundary. |
| OpenClaw bridge | Identify the governed provisioning path; do not call raw OpenClaw CLI from the BFF handler unless that is explicitly accepted. |
| Capability policy | Use the seven frozen `agora.*.v1` capabilities or add canonical policy for a narrower servant-specific set. |
| Safety boundary | Preserve `execution_authority="none"` and the prohibited authority list in every success path. |
| Tests | Add route tests for new user, existing user reconcile, cross-tenant denial, OpenClaw failure behavior, and no runtime/broker/capital authority leakage. |

## Verification Notes

Suggested reviewer checks for this sidecar:

```bash
git diff --check -- support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF.md
python3 -m pytest services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_identity_scope.py -q
```

Expected scope check:

- Only this sidecar support artifact is authored by the task.
- No L1 canonical docs, OpenAPI, BFF runtime implementation, registry code,
  governance code, or execute-plans files are changed.
- The packet does not claim AG-BE-ID-002 is implementable without parent
  design clarification.

## Handoff

This packet is ready for Codex2 review. It should be used as support material
for the parent blocked discussion and for a later frontend/BFF implementation
slice after the canonical route and adapter decisions are made.

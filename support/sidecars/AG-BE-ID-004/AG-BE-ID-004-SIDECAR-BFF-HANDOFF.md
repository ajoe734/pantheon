# AG-BE-ID-004 Sidecar: BFF and Frontend Handoff Packet

| Field | Value |
|---|---|
| Task ID | `AG-BE-ID-004-SIDECAR-BFF-HANDOFF` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-BE-ID-004` - ContextBundle redaction and central persona boundary |
| Parent owner / reviewer | Claude2 / Claude |
| Prepared by | Claude2 |
| Reviewer | Claude |
| Date | 2026-06-20 |
| Mutates canonical truth | false |
| Status | Ready for sidecar review |

## Purpose

This support-only packet gives the parent owner the current BFF query gap,
operator journey, and execute-plans handoff notes for the ContextBundle
redaction gate and management_projection surface required by AG-BE-ID-004.
It does not modify canonical truth, OpenAPI, BFF runtime code, OpenClaw
adapter code, governance policy, or execute-plans source.

The parent task implements the boundary that prevents the central Agora
persona from receiving raw private conversation context or user identity.
The redacted ContextBundle carries only:
`strategy_spec_draft_ref`, `question`, `symbols`, `evidence_refs`,
`data_cutoff`, `required_output_schema`.
Violations must fail closed with `RAW_PRIVATE_CONTENT_FORBIDDEN`.

## Current BFF Truth

| Surface | Current state | Evidence |
|---|---|---|
| Management projection router | `create_management_projection_router()` returns an empty `APIRouter(tags=["agora-management"])`. No active routes are registered. The module docstring notes this as a placeholder for AG-BE-ID-004. | `services/control-plane/bff/agora/management_projection/router.py` |
| Agora package router | `create_agora_router()` mounts identity, servant, workshop, research, trading, dashboard, shadow, personalization, and management_projection sub-routers. The management_projection mount is live in the router composition but serves zero routes. | `services/control-plane/bff/agora/router.py` |
| agora_context_bundle.py | The file `integrations/openclaw/adapter/agora_context_bundle.py` does not exist in the current tree. The dispatch task names it as the primary adapter artifact, but no skeleton or stub is present. | `find integrations/openclaw/adapter/` — file absent |
| OpenAPI route catalog | The Agora v1 OpenAPI (`services/control-plane/openapi/agora_v1.openapi.yaml`) does not include any management_projection routes. The SD §17 normative route list covers only the routes catalogued in §5.1 and §5.2 of SD_2026-06-20.md. | `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` §17 |
| SD §5.6 and §21.3 references | The dispatch task references `SD §5.6/§21.3` for ContextBundle design, but the current `SD_2026-06-20.md` has only 278 lines and contains sections up to §22.1. Sections §5.6 and §21.3 do not exist in the committed document. | `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` |
| ContextBundle schema | No `agora_context_bundle.schema.json` schema file exists under `services/control-plane/specs/agora/`. The 13 frozen schemas in the SD do not include a ContextBundle type. | `services/control-plane/specs/agora/` directory |
| RAW_PRIVATE_CONTENT_FORBIDDEN error code | The BFF Agora error taxonomy (`services/control-plane/bff/agora/models.py`) does not define this error code. The current Agora error surface uses generic typed codes and `NOT_IMPLEMENTED`. | `services/control-plane/bff/agora/models.py` |
| Related redaction work | The assistant kernel redaction library (`services/control-plane/bff/assistant/redaction.py`) implements a mode-aware JSON-payload redactor for the management AI multi-turn surface, but it is scoped to the assistant kernel, not to Agora central persona consult. | `docs/04/pantheon_assistant_kernel_user_2026-05-31/ASST_KERNEL_002_REDACTION_IMPLEMENTATION.md` |
| AG-BE-ID-002 dependency | AG-BE-ID-004 depends on AG-BE-ID-002 (OpenClaw servant ensure/provision). The AG-BE-ID-002 sidecar records that `POST /bff/agora/servant/ensure` currently returns 501 NOT_IMPLEMENTED and the servant provision path is unresolved. AG-BE-ID-004 cannot complete a live redaction test without a provisioned central servant. | `support/sidecars/AG-BE-ID-002/AG-BE-ID-002-SIDECAR-BFF-HANDOFF.md` |

Important frontend-facing consequence: the management_projection router mount
is live in the BFF composition tree but serves no routes. Any frontend call
targeting a management_projection path (e.g. a future central-consult
context-bundle dispatch) will receive a 404 from FastAPI. The Agora shell
must not attempt to trigger central persona consult until AG-BE-ID-004 is
implemented and the management_projection routes are registered.

## BFF Query Gap Analysis

| Gap | Impact | Parent-owner decision needed |
|---|---|---|
| Missing SD §5.6 / §21.3 sections | The dispatch task references design sections that do not exist in the committed SD. Implementers cannot derive the ContextBundle field list, redaction rules, or management_projection route shape from the current canonical document. | Author and freeze the missing SD section before implementation begins, or cite an alternate canonical design source. |
| Missing `agora_context_bundle.py` adapter | The primary artifact path `integrations/openclaw/adapter/agora_context_bundle.py` does not exist. Implementation must create this file from scratch without a skeleton, requiring clear field-by-field spec from the parent task or its design source. | Confirm the complete allowed field list (`strategy_spec_draft_ref`, `question`, `symbols`, `evidence_refs`, `data_cutoff`, `required_output_schema`) and the redaction behavior for each field. |
| Missing ContextBundle schema | No Agora-governed schema captures the ContextBundle contract. Without a frozen schema, the redaction adapter cannot be validated against a spec, and the OpenAPI cannot represent the ContextBundle as a request body. | Decide whether to add `agora_context_bundle.schema.json` to the AG-XR-001 schema bundle (requiring a bundle hash re-cut) or define it as an internal OpenClaw adapter type only. |
| RAW_PRIVATE_CONTENT_FORBIDDEN error absent | The BFF Agora error taxonomy has no `RAW_PRIVATE_CONTENT_FORBIDDEN` code. The redaction gate needs a machine-readable typed error that the frontend can catch and display as a clear policy denial, not a generic 400. | Add this error code to `services/control-plane/bff/agora/models.py` and the OpenAPI error taxonomy. |
| management_projection routes undefined | The management_projection router is empty; the SD §17 normative route list does not include any management_projection path. Any route that surfaces ContextBundle dispatch to central persona must be added to the OpenAPI catalog before implementation. | Identify the exact route path(s), method(s), and capability guard for the central persona consult trigger. |
| Explicit authorization flag shape | The task summary states `raw_prompt_included=false, user_identity_included=false` unless the user explicitly authorizes. The authorization flag shape (request body field, header, or separate BFF preference) is not specified in any canonical document. | Define the authorization flag shape, how it is transmitted (body vs. header vs. pre-agreed operator preference), and the lifetime/scope of that authorization. |
| AG-BE-ID-002 completion dependency | AG-BE-ID-004 requires a provisioned central persona servant to test live central-consult redaction. Until AG-BE-ID-002 resolves the servant ensure path, live end-to-end testing of the redaction gate is not possible. | Either decouple the adapter unit tests from the live servant (mock-only) or unblock AG-BE-ID-002 first. |
| execute-plans type surface | No TypeScript types or path helpers exist for management_projection routes in `execute-plans/src/lib/bff-v1/agora/`. Until the BFF route contract is frozen, no frontend adapter work can safely begin. | Block frontend type generation on the route and ContextBundle schema being accepted. |

## Operator Journey

### Current state

```text
Operator opens Agora UI
  -> Agora shell calls GET /bff/agora/me and GET /bff/agora/capabilities
  -> BFF returns tenant/user scope, Agora capabilities, and servant_policy
  -> No management_projection route exists; any central-consult action in
     the UI would receive 404 from FastAPI
  -> operator sees no CTA for central persona consult (not yet surfaced in FE)
  -> raw_prompt and user_identity are not transmitted anywhere because there
     is no route to transmit them to
```

### Proposed journey after AG-BE-ID-004 is implemented

```text
Operator performs an Agora Ask session and requests central persona review
  -> Agora shell prepares a ContextBundle containing only:
       strategy_spec_draft_ref, question, symbols,
       evidence_refs, data_cutoff, required_output_schema
  -> raw_prompt is stripped at the BFF redaction layer (not trusted from client)
  -> user_identity fields are stripped at the BFF redaction layer
     unless an explicit operator authorization flag was set for this request
  -> POST /bff/agora/management/<route-tbd> with idempotency/correlation headers
  -> BFF validates ContextBundle against the redaction rules:
       fail-closed on any raw_prompt presence -> RAW_PRIVATE_CONTENT_FORBIDDEN
       fail-closed on user_identity presence without authorization flag
  -> BFF dispatches the redacted ContextBundle to the central persona via
     integrations/openclaw/adapter/agora_context_bundle.py
  -> central persona never observes original private conversation or identity
  -> BFF returns the central persona response envelope
```

### Failure and degraded journey

```text
RAW_PRIVATE_CONTENT_FORBIDDEN (redaction fail-closed)
  -> frontend renders policy-denial state
  -> UI must not retry with the same payload
  -> operator must use a permitted context-only consult path

Authorization flag missing but raw_prompt present
  -> BFF strips raw_prompt and falls back to redacted context only
  -> OR returns RAW_PRIVATE_CONTENT_FORBIDDEN (parent must decide which)

AG-BE-ID-002 servant not yet provisioned (501 from servant/ensure)
  -> central persona consult cannot proceed without a live servant identity
  -> frontend renders backend-not-ready state for central consult
  -> falls back to peer-persona-only consult if available

management_projection route not yet registered (404)
  -> frontend must not render central-consult CTA until the route is live
  -> degraded state rendered; no mock or seed substitution
```

## Frontend Handoff Notes

| Area | Current frontend state | Handoff note |
|---|---|---|
| Path helpers | `execute-plans/src/lib/bff-v1/agora/` has no management_projection path builders. | Do not add management_projection paths until the BFF route contract is frozen and the management_projection route(s) appear in the Agora v1 OpenAPI. |
| ContextBundle type | No TypeScript type for ContextBundle exists in the execute-plans type surface. | Add `AgoraContextBundle` type only after the schema or field list is frozen by the parent task. The type must enforce `raw_prompt` and `user_identity` absence at the TypeScript layer. |
| Central consult CTA | No management_projection UI entry point exists in the current Agora shell (`execute-plans/src/agora/`). | Do not add a "request central review" CTA before the management_projection route is live. In strict live mode, absence of the route must render unavailable/disabled state, not a mock path. |
| Error display | The Agora frontend has no handler for `RAW_PRIVATE_CONTENT_FORBIDDEN`. | Add a specific error display component that distinguishes policy-denial (a content-governance failure) from a generic 400, so the operator understands the privacy boundary was enforced, not a server bug. |
| Auth audience | The Agora app uses `audience=pantheon-agora` per AG-FE-ID-001. | Management_projection routes must be accessible under the same audience; the BFF must not require a separate management-plane token for central persona consult triggered from the Agora shell. |
| Live tests | Existing Agora adapter tests cover signal/feedback/list behavior; management_projection has no test coverage. | Add tests for: management_projection 404 before implementation; ContextBundle construction (no raw_prompt / no identity); RAW_PRIVATE_CONTENT_FORBIDDEN error handling. |

Recommended frontend type contract once parent freezes the ContextBundle spec:

```ts
type AgoraContextBundle = {
  strategy_spec_draft_ref: string;
  question: string;
  symbols?: string[];
  evidence_refs?: string[];
  data_cutoff?: string;   // ISO date
  required_output_schema?: string;
  // raw_prompt: NEVER present — stripped at BFF layer
  // user_identity: NEVER present — stripped at BFF layer
};

type CentralConsultRequest = {
  context_bundle: AgoraContextBundle;
  idempotency_key: string;
};
```

The frontend must never construct a `CentralConsultRequest` that includes
`raw_prompt` or `user_identity` fields. Even if the backend strips them,
sending them would indicate a privacy boundary violation in the client layer.

## Parent Absorption Checklist

Claude2 (parent owner) should resolve these before turning the implementation
loose:

| Check | Expected parent outcome |
|---|---|
| SD section authoring | Author or locate the canonical design source that specifies §5.6 and §21.3 (or equivalent) ContextBundle fields, redaction rules, and management_projection route shape. |
| ContextBundle schema | Decide whether `agora_context_bundle.schema.json` enters the AG-XR-001 bundle or stays as an adapter-internal type. Record the decision in the SD or a separate design-closure doc. |
| Route definition | Define the management_projection route path(s), HTTP method(s), request/response envelopes, and capability guard, then add them to the Agora v1 OpenAPI. |
| Error taxonomy | Add `RAW_PRIVATE_CONTENT_FORBIDDEN` to the Agora error model in `services/control-plane/bff/agora/models.py` and the OpenAPI error surface. |
| Authorization flag | Define the explicit authorization flag shape for cases where the operator intentionally includes raw_prompt or user_identity in a consult context. |
| AG-BE-ID-002 sequencing | Decide whether AG-BE-ID-004 unit tests can run without a live servant (mock-only) or whether AG-BE-ID-002 must close first. |
| Redaction fail-closed | Confirm that `agora_context_bundle.py` fails closed on redaction error (exception → request aborted, not degraded pass-through). |
| Tests | Add: adapter unit tests for redaction (with and without authorization flag); BFF route tests for ContextBundle dispatch, RAW_PRIVATE_CONTENT_FORBIDDEN response, and cross-tenant denial. |

## Verification Notes

Suggested reviewer checks for this sidecar:

```bash
git diff --check -- support/sidecars/AG-BE-ID-004/AG-BE-ID-004-SIDECAR-BFF-HANDOFF.md

# Confirm management_projection router is still a no-op placeholder
python3 -c "
import importlib, sys
sys.path.insert(0, 'services/control-plane')
from bff.agora.management_projection.router import create_management_projection_router
r = create_management_projection_router(
    extract_identity=lambda: None,
    require_read_role=lambda: None,
    bff_error=lambda **kw: None,
    utc_now=lambda: ''
)
assert list(r.routes) == [], 'management_projection router must be empty placeholder'
print('OK: management_projection router has no routes')
"

# Confirm agora_context_bundle.py does not yet exist
python3 -c "
import os
path = 'integrations/openclaw/adapter/agora_context_bundle.py'
assert not os.path.exists(path), f'Unexpected: {path} exists — sidecar said it was absent'
print('OK: agora_context_bundle.py is absent as documented')
"
```

Expected scope check:
- Only the sidecar support artifact `support/sidecars/AG-BE-ID-004/AG-BE-ID-004-SIDECAR-BFF-HANDOFF.md`
  is authored by this task.
- No L1 canonical docs, OpenAPI, BFF runtime implementation, OpenClaw adapter
  code, schema files, governance code, or execute-plans files are changed.
- The packet does not claim AG-BE-ID-004 is implementable without the missing
  SD sections, schema, and route definitions being settled first.

## Handoff

This packet is ready for Claude (reviewer) review. It should be used as
support material for the parent design clarification discussion — specifically
to unblock the missing SD §5.6/§21.3 authoring, ContextBundle schema
decision, and management_projection route definition — before the parent
owner begins implementation.

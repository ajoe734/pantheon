# Pantheon Response to System Design Follow-up Questions

## Purpose

This document is the formal architecture response to the development team's follow-up questions in:

`2026-04-20-system-design-follow-up-question-list.md`

The purpose of this response is to:

1. answer each open system-design question clearly;
2. preserve the original Pantheon system blueprint direction;
3. avoid being pulled into frontend-only or BFF-only thinking;
4. convert ambiguity into canonical implementation rules;
5. clarify which items require architecture decisions and which should move to implementation.

This document should be treated as an architecture-team response for downstream implementation, BFF work, frontend handoff, and Lovable coordination.

---

# 0. Framing: Do Not Drift Away from the Original Pantheon Blueprint

The original Pantheon blueprint remains unchanged:

- Pantheon is a multi-persona quant research, governance, and execution platform.
- Research, knowledge, and consultation are shared.
- Capital pools and live execution remain isolated.
- Persona, research, governance, execution, telemetry, and evolution are separate planes.
- BFF is an operator-facing facade, not the source of truth.
- Frontend and Lovable must not infer domain authority or create canonical state.
- Domain services own domain truth.
- Runtime-manager and LEAN own execution truth.
- Governance owns approval and promotion truth.

Therefore, the questions in this follow-up list should be interpreted as:

> final implementation-contract closure questions,

not as reasons to redraw the entire Pantheon architecture.

These questions are valid, but they sit at the last mile of BFF-facing contract, module readiness, ownership boundary, and UI handoff semantics.

---

# 1. Summary of Final Decisions

The following decisions are now canonical for this round:

1. Pantheon adopts a single readiness ladder:
   `blocked`, `contract_ready`, `screen_ready`, `handoff_ready`, `implementation_ready`, `production_ui_ready`.

2. Existing vocabulary such as `contract-published`, `pending-bff`, `route-live`, `ready`, and `shell-only` must be mapped to the canonical readiness ladder.

3. `partial` remains a valid degradation state, but only for non-authoritative read surfaces.

4. `stale` should not be a primary surface state. Freshness must be expressed through `meta.staleness`.

5. Pagination canonical field remains `page_info.next_page_token`.

6. Shared detail envelope is a minimum operator-facing wrapper, not a replacement for domain-specific canonical object identity.

7. `lineage-read-svc` is the UI-facing canonical lineage read owner.

8. Persona service owns canonical persona identity, lifecycle, capability, policy refs, and session metadata. BFF owns only composed operator-facing views.

9. Gateway owns ingress and transport concerns only. Router owns route selection. Governance owns approval authority.

10. `CW-02`, `CW-04`, `TW-02`, `KW-05`, and `CW-03` require targeted contract updates or ratification, but do not require redesign of the overall Pantheon system.

11. `contract_ready` and `implementation_ready` are different readiness levels and must not be collapsed.

---

# A. Global Conventions

---

## A1. Readiness ladder canonical enum and mapping from existing repo vocabulary

### Question

How should the new readiness ladder map to current repo vocabulary such as:

- `contract-published`
- `pending-bff`
- `route-live`
- `ready`
- `shell-only`
- `not_ready`
- `implemented`

### Decision

Pantheon formally adopts the following canonical readiness ladder:

```text
blocked
contract_ready
screen_ready
handoff_ready
implementation_ready
production_ui_ready
```

Existing repo vocabulary should not be deleted immediately. Instead, it must be mapped into the canonical ladder and then gradually deprecated.

### Canonical wording

> Pantheon readiness classification uses the canonical enum:
> `blocked`, `contract_ready`, `screen_ready`, `handoff_ready`, `implementation_ready`, `production_ui_ready`.
> Existing vocabulary such as `contract-published`, `pending-bff`, `route-live`, `ready`, and `shell-only` must be mapped into this enum and gradually deprecated.

### Mapping table

| Existing vocabulary | Canonical readiness | Decision |
|---|---|---|
| `not_ready` | `blocked` | No sufficient canonical contract or route/read model |
| `blocked` | `blocked` | Keep as-is |
| `shell-only` | `blocked` or `screen_ready` | If only UI shell exists with no BFF truth, it remains `blocked`; if screen spec and mock exist, it may be `screen_ready` |
| `contract-published` | `contract_ready` | Contract exists, but route may not be implemented |
| `pending-bff` | `contract_ready` | Architecture can hand to BFF implementation |
| `screen-ready` | `screen_ready` | Screen spec exists |
| `handoff-ready` | `handoff_ready` | Ready for Lovable/frontend handoff |
| `route-live` | `implementation_ready` | Route exists, but UI/acceptance may still be pending |
| `ready` | deprecated | Too vague; must be replaced with a specific canonical enum |
| `implemented` | `implementation_ready` or `production_ui_ready` | Backend-only implementation is `implementation_ready`; frontend/BFF/acceptance complete is `production_ui_ready` |

### Implementation impact

All backlog, BFF overview, screen spec, handoff packet, and Lovable documentation should use the canonical readiness ladder as the primary status field.

A temporary `legacy_status` field may be preserved during migration, but it must not remain the authoritative readiness classification.

### Required documentation

Create or update:

```text
docs/conventions/MODULE_READINESS_LADDER.md
```

---

## A2. Should `partial` remain in the `meta.surfaces.*` degradation dictionary?

### Question

Should `partial` remain a valid value in `meta.surfaces.*`, or should it be removed to avoid ambiguity?

### Decision

Keep `partial`, but restrict its usage.

`partial` is valid only for non-authoritative read surfaces where incomplete auxiliary data is acceptable.

`partial` must not be used for command authority, deployment authority, runtime truth, approval authority, or any safety-critical surface.

### Canonical wording

> `partial` is a valid surface state only for read surfaces where incomplete auxiliary data is acceptable.
> It must not be used for authoritative command, approval, runtime, or deployment surfaces.

### Allowed `partial` examples

`partial` is allowed for:

- lineage summary where some downstream evidence refs are unresolved;
- transcript enrichment where display labels or evidence links are missing but the append-only event stream is intact;
- search result enrichment where optional metadata is missing;
- evidence panel where some external refs cannot be resolved;
- insight aggregation where some sources are delayed.

### Not allowed for `partial`

`partial` must not be used for:

- `allowedActions`
- `ApprovalDecision`
- `DeploymentPlan`
- `RuntimeBinding`
- `killSwitch`
- `rollback`
- `paper / canary / live` stage authority
- persona lifecycle mutation
- capital pool binding authority

These surfaces must use one of:

```text
ok
degraded
unavailable
```

### Implementation impact

If frontend sees `partial`, it may show available data with a partial-data warning.

Frontend must not enable authority-dependent actions if the action surface is incomplete.

### Required documentation

Create or update:

```text
docs/conventions/DEGRADATION_DICTIONARY.md
docs/conventions/BFF_RESPONSE_ENVELOPE.md
```

---

## A3. Should `stale` be part of `meta.surfaces.*`, or expressed through `meta.staleness`?

### Question

Should `stale` remain a surface state, or should it be modeled separately?

### Decision

`stale` should not be the primary value of `meta.surfaces.*.state`.

Staleness is a freshness property, not an availability state.

Use:

```json
meta.staleness
```

to represent freshness.

### Canonical wording

> Staleness is a freshness property, not a surface availability state.
> Surface state describes whether a surface is available.
> `meta.staleness` describes whether the data is fresh enough.

### Canonical shape

```json
{
  "meta": {
    "snapshot_at": "2026-04-20T10:00:00Z",
    "staleness": {
      "status": "fresh",
      "as_of": "2026-04-20T09:59:58Z",
      "max_age_seconds": 30
    },
    "surfaces": {
      "lineage": {
        "state": "ok"
      }
    }
  }
}
```

Allowed `meta.staleness.status` values:

```text
fresh
stale
unknown
not_applicable
```

### Migration policy

If existing contracts currently use:

```json
meta.surfaces.<surface>.state = "stale"
```

then it may remain as a deprecated alias during transition.

New contracts must use:

```json
meta.staleness.status
```

### Required documentation

Update:

```text
docs/conventions/BFF_RESPONSE_ENVELOPE.md
docs/conventions/DEGRADATION_DICTIONARY.md
```

---

## A4. Should canonical pagination remain `page_info.next_page_token`?

### Question

Should the project standardize on `page_info.next_page_token`, `next_cursor`, or another pagination field?

### Decision

Keep:

```json
page_info.next_page_token
```

as the canonical pagination field.

Do not change to `next_cursor` now. Avoid unnecessary migration and vocabulary churn.

### Canonical wording

> Pantheon list routes use cursor-based pagination.
> The canonical response field is `page_info.next_page_token`.

### Canonical response shape

```json
{
  "items": [],
  "page_info": {
    "next_page_token": "abc",
    "page_size": 50,
    "has_more": true
  }
}
```

### Alias policy

BFF adapters may accept legacy/internal aliases such as:

- `next_cursor`
- `cursor`
- `nextToken`

But canonical BFF responses must output:

```text
page_info.next_page_token
```

### Required documentation

Update:

```text
docs/conventions/BFF_RESPONSE_ENVELOPE.md
```

---

## A5. Should shared detail envelope force every module to use generic `id` / `title`?

### Question

Should every BFF detail response be forced into generic `id`, `title`, `status`, or should domain-specific identity be preserved?

### Decision

The shared detail envelope defines the minimum operator-facing wrapper.

It does not replace domain-specific canonical identity.

Every detail response must expose:

```json
object_ref
```

but domain-specific identifiers may remain inside the domain block.

### Canonical wording

> Shared envelope defines common operator-facing metadata, not a replacement for domain-specific object identity.
> Domain objects may keep their canonical field names, but every BFF detail response must expose `object_ref`.

### Canonical shape

```json
{
  "object_ref": {
    "type": "DeploymentPlan",
    "id": "dep_plan_123"
  },
  "display": {
    "title": "Deploy Strategy X to Paper",
    "subtitle": "capital_pool=pool_alpha"
  },
  "status": "pending_review",
  "lifecycle_state": "candidate",
  "allowedActions": [],
  "meta": {
    "snapshot_at": "2026-04-20T10:00:00Z",
    "staleness": {
      "status": "fresh"
    },
    "surfaces": {}
  },
  "data": {}
}
```

### Implementation impact

Frontend can use `object_ref` to build:

- breadcrumbs
- chips
- linked object cards
- drilldown links

Domain-specific fields remain under `data` or a module-specific block.

### Required documentation

Update:

```text
docs/conventions/BFF_RESPONSE_ENVELOPE.md
```

---

# B. Ownership Decisions

---

## B1. LIN-002 operational migration boundary

### Question

What migration boundary is required for lineage ownership?

### Decision

`lineage-read-svc` is the UI-facing canonical lineage read owner.

Existing telemetry lineage path can remain as internal substrate, but it must not remain a second UI truth path.

### Canonical wording

> Domain services own normalized lineage write edges.
> `lineage-read-svc` owns UI-facing lineage read truth.
> Telemetry lineage engine may remain as an internal substrate but must not be consumed directly by BFF or frontend as a second UI truth path.

### Migration phases

#### Phase 0 — Current state

Multiple lineage paths may exist:

- telemetry lineage read path
- `services/lineage-read`
- BFF internal projections

This is accepted only as transitional state.

#### Phase 1 — Facade consolidation

`lineage-read-svc` wraps or consumes:

- telemetry lineage engine
- registry lineage edges
- domain normalized edges

BFF starts moving all lineage surfaces to `lineage-read-svc`.

#### Phase 2 — UI cutover

BFF no longer directly calls telemetry lineage read path.

Frontend receives lineage only through BFF surfaces backed by `lineage-read-svc`.

#### Phase 3 — Deprecation

Any UI-facing lineage endpoint outside `lineage-read-svc` becomes:

```text
internal_only
```

or deprecated.

### Allowed internal telemetry lineage usage

Telemetry lineage engine may still be used for:

- incident reconstruction
- telemetry correlation
- background projection build
- lineage repair job

It must not be consumed directly by:

- Lovable
- frontend
- BFF UI surfaces
- operator UI

### Required documentation

Create:

```text
docs/decisions/LINEAGE_READ_OWNERSHIP_AND_MIGRATION.md
```

---

## B2. Persona service boundary

### Question

How specific should the persona boundary be?

### Decision

Persona service owns canonical persona truth.

BFF owns only composed operator-facing read views.

### Persona service owns

The persona service owns:

```text
Persona
PersonaLifecycle
PersonaMandate
PersonaCapabilityProfile
RoutePolicyRef
ConsultPolicyRef
PersonaSession metadata
PersonaToolProfileRef
PersonaRiskPosture metadata
```

### Persona service does not own

Persona service does not own:

```text
DeploymentPlan
RuntimeBinding
ApprovalDecision
CapitalPool
TelemetryEvent
IncidentCase
EvolutionDecision truth
```

### BFF may compose

BFF may compose:

- latest deployment status
- latest incident status
- persona display chips
- capability summary
- deployment eligibility display
- warning badges
- action affordances from `allowedActions`

But these are read models, not canonical truth.

### Special note: PersonaCapitalBinding

`PersonaCapitalBinding` is not owned solely by persona service.

It is a capital/governance boundary object.

Persona service may read and display eligibility, but must not become the write truth for binding.

### Canonical wording

> Persona service owns persona identity, lifecycle, mandate, policy references, and session metadata.
> Capital binding, deployment authority, and runtime truth remain outside persona service.

### Required documentation

Create:

```text
docs/decisions/PERSONA_SERVICE_BOUNDARY.md
```

---

## B3. Router / gateway / governance command and approval matrix

### Question

Do we need a more specific command / approval ownership matrix?

### Decision

Yes.

Textual boundary descriptions are not enough.

Pantheon must maintain a command / approval ownership matrix.

### Canonical ownership table

| Concern | Owner | Description |
|---|---|---|
| transport authn | gateway / BFF edge | request identity / session |
| ingress rate limit | gateway | traffic protection |
| domain rate limit | owning service | domain semantic throttling |
| transport TTL | gateway | request validity window |
| domain TTL | owning domain service | command / review / plan expiry |
| route selection | router | intent to target surface |
| approval authority | governance / promotion | approval, review, deploy gates |
| command execution | owning domain service | e.g. runtime-manager, promotion |
| fallback intent classifier | router degraded mode | not canonical truth |

### Canonical wording

> Gateway owns ingress and transport-level concerns.
> Router owns routing decision.
> Governance owns approval authority.
> Domain services own domain command validity.
> No gateway or router component may become business authority.

### Per-command required fields

Every command route must specify:

- command name
- initiating surface
- routing owner
- approval owner
- execution owner
- TTL owner
- rate-limit owner
- idempotency-key owner
- audit owner

### Required documentation

Create:

```text
docs/decisions/COMMAND_AND_APPROVAL_OWNERSHIP_MATRIX.md
```

---

# C. Blocked Module Contracts

---

## C1. CW-02 Debate Transcript

### Question 1

What is the final append-only `TranscriptEvent` schema?

### Decision

Use append-only event model with stable sequence.

### Canonical schema

```json
{
  "transcript_id": "tr_123",
  "session_id": "consult_session_123",
  "event_id": "ev_001",
  "sequence_no": 1,
  "parent_event_id": null,
  "event_type": "message",
  "event_time": "2026-04-20T10:00:00Z",
  "ingest_time": "2026-04-20T10:00:01Z",
  "actor": {
    "actor_type": "persona",
    "actor_id": "risk_guardian",
    "display_name": "Risk Guardian",
    "role": "red_team"
  },
  "content": {
    "format": "markdown",
    "text": "..."
  },
  "evidence_refs": [],
  "visibility": "committee",
  "redaction": {
    "is_redacted": false,
    "reason": null
  },
  "meta": {
    "source": "openclaw",
    "hash": "..."
  }
}
```

### Question 2

Should `partial transcript` remain as a canonical degraded state?

### Decision

Yes, but only for enrichment incompleteness.

`partial transcript` means transcript events are readable, but some enrichment is missing.

Allowed partial cases:

- actor display label not resolved
- evidence link not resolved
- attachment metadata missing

Not allowed:

- sequence gap
- untrusted ordering
- event loss
- transcript integrity failure

If the event stream itself is inconsistent, use:

```text
degraded
```

not `partial`.

### Canonical wording

> `partial transcript` means enrichment is incomplete, not that the append-only event stream is inconsistent.

### Question 3

Is actor labeling fully resolved by BFF?

### Decision

No.

Canonical actor identity is written by consultation service / transcript owner.

BFF may enrich display fields.

BFF must not invent actor identity.

### Canonical rule

`actor.actor_type` + `actor.actor_id` are canonical.

`actor.display_name` may be BFF-enriched.

### Required documentation

Update:

```text
docs/bff/CW-02-debate-transcript.md
docs/examples/CW-02-debate-transcript.json
```

---

## C2. CW-04 Red-team Memo

### Question 1

What is the canonical `session_to_memo_mapping` object shape?

### Decision

Create an explicit mapping object.

### Canonical shape

```json
{
  "mapping_id": "map_123",
  "source_session_id": "consult_session_123",
  "transcript_id": "tr_123",
  "transcript_version": "v1",
  "memo_id": "memo_456",
  "memo_type": "red_team",
  "created_by": {
    "actor_type": "persona",
    "actor_id": "skeptic_red_team"
  },
  "evidence_refs": [],
  "mapping_status": "active",
  "created_at": "2026-04-20T10:00:00Z"
}
```

### Question 2

What is the gating rule for `allowedActions.canInitiateGovernanceReview`?

### Decision

This action must be backend-provided.

Frontend must not infer it.

### Gating rule

`canInitiateGovernanceReview` is true only if all conditions hold:

1. memo lifecycle = `published`
2. memo target has valid `strategy_id`, `artifact_id`, or `deployment_plan_id`
3. actor has reviewer / governance role
4. no active governance review already exists for the same target + memo
5. memo is not suppressed or withdrawn
6. evidence surface is not `unavailable`
7. governance service accepts target type

### Canonical wording

> Red-team memo may request governance review only after publication and only when target, actor authority, evidence availability, and duplicate-review checks pass.

### Question 3

Should `ConsultMemo` lifecycle remain only `draft -> published`?

### Decision

Yes for v1.

Use:

```text
draft -> published
```

If published content needs modification, do not edit original published memo.

Create a new version / superseding memo.

Optional metadata:

```text
supersedes_memo_id
superseded_by_memo_id
```

Do not add `superseded` as a primary v1 lifecycle state unless required later.

### Required documentation

Update:

```text
docs/bff/CW-04-redteam-memo.md
```

---

## C3. TW-02 Parameter Controls

### Question 1

Are patch semantics partial patch or replace-style patch?

### Decision

Use partial patch.

Do not use replace-style patch.

### Canonical wording

> Parameter controls are patched by field-level partial patch over an allowlisted control surface.
> Omitted fields remain unchanged.

### Why

Trainer controls are interactive and incremental.

Replace-style patch would create risk of:

- accidentally overwriting newer values,
- unclear diffs,
- poor replayability,
- broken preview lineage.

### Question 2

What is the invalid / rejected patch response shape?

### Canonical shape

```json
{
  "status": "rejected",
  "error_code": "CONTROL_PATCH_VALIDATION_FAILED",
  "message": "Patch contains invalid control updates.",
  "field_errors": [
    {
      "field": "max_leverage",
      "reason": "exceeds_allowed_range",
      "current_value": 1.5,
      "requested_value": 5.0,
      "allowed_range": {
        "min": 0.0,
        "max": 2.0
      }
    }
  ],
  "rejected_changes": [],
  "current_controls": {},
  "allowedActions": []
}
```

### Question 3

Should diff payload be fixed to `updated_controls[]` or expandable?

### Decision

v1 must include:

```text
updated_controls[]
```

Future extensions may include:

```text
added_controls[]
removed_controls[]
derived_impacts[]
```

But frontend must not assume these extension arrays always exist.

### v1 diff shape

```json
{
  "diff": {
    "updated_controls": [
      {
        "field": "risk_tolerance",
        "before": 0.35,
        "after": 0.25,
        "validation_status": "accepted"
      }
    ]
  }
}
```

### Required documentation

Update:

```text
docs/bff/TW-02-parameter-controls.md
```

---

## C4. KW-05 Strategy Spec

### Question 1

Are version identity, ancestry, lifecycle, and compare semantics stable enough for implementation?

### Decision

KW-05 may move to implementation only if the following minimum set is captured in contract.

If not, KW-05 remains blocked.

### Minimum required decision set

#### Version identity

```text
strategy_id          logical strategy identity
spec_version_id      immutable version identity
spec_version         human-readable version label
```

#### Ancestry

```text
parent_spec_version_id
derived_from_source_refs[]
```

#### Lifecycle

```text
draft
candidate
approved
retired
```

#### Immutability

Once a spec version reaches `candidate` or higher, it is immutable.

Any change creates a new `spec_version_id`.

#### Compare semantics

Compare output must be backend-generated.

Frontend must not diff arbitrary JSON.

Canonical compare shape:

```json
{
  "left_spec_version_id": "...",
  "right_spec_version_id": "...",
  "changed_sections": [],
  "breaking_changes": [],
  "evidence_refs": []
}
```

### Question 2

If not stable yet, what is the minimum missing decision required to unlock it?

### Decision

If the above minimum set is not already in canonical contract, create:

```text
KW-05_STRATEGY_SPEC_VERSIONING_AND_COMPARE.md
```

### Required documentation

Create or update:

```text
docs/bff/KW-05-strategy-spec.md
KW-05_STRATEGY_SPEC_VERSIONING_AND_COMPARE.md
```

---

# D. Promotion Rules

---

## D1. Should CW-03 partial activation be a formal decision?

### Question

Can `CW-03 Committee Board` be partially activated before `CW-02 Debate Transcript` is live?

### Decision

Yes.

CW-03 should have an explicit partial activation rule.

### Canonical ladder for CW-03

```text
blocked
partial_activation
handoff_ready
production_ui_ready
```

### Partial activation allowed surfaces

Before CW-02 transcript is live, CW-03 may provide:

- committee board summary
- sponsor decision status
- current participants
- verdict summary
- pending actions
- linked memo / review refs
- high-level committee outcome

### Surfaces blocked until CW-02 is live

The following require CW-02 transcript:

- transcript timeline panel
- actor-event detail
- quote / evidence-linked debate snippets
- event-level reasoning path
- transcript-driven verdict explanation
- full debate replay

### Canonical wording

> CW-03 may enter partial activation before CW-02 is live, but any transcript-dependent surface must remain degraded or hidden until CW-02 transcript contract and route are live.

### Required documentation

Update:

```text
docs/bff/CW-03-committee-board.md
```

Optionally create:

```text
CW-03_PARTIAL_ACTIVATION_RULE.md
```

---

## D2. Are `contract_ready` and `implementation_ready` distinct levels?

### Question

Should `contract_ready` and `implementation_ready` be treated as different readiness levels in Pantheon?

### Decision

Yes.

They must be distinct.

### `contract_ready`

Means:

- architecture contract is defined,
- read model exists,
- lifecycle / authority / `allowedActions` / degradation semantics are defined,
- example payload exists or can be generated,
- implementation planning may begin.

Does not mean:

- route is live,
- tests are done,
- BFF is implemented,
- frontend can directly integrate.

### `implementation_ready`

Means:

- contract is defined,
- route path is defined,
- owner is defined,
- request / response / error / `allowedActions` are defined,
- acceptance tests can be written,
- implementation ticket can be opened,
- no further architecture clarification is required.

### Promotion conditions from `contract_ready` to `implementation_ready`

A module may move from `contract_ready` to `implementation_ready` only when:

1. BFF route path is defined
2. response envelope is defined
3. example payload exists
4. state / lifecycle is defined
5. authority owner is defined
6. `allowedActions` is defined
7. degradation semantics are defined
8. pagination/filtering is defined for list route
9. write command vocabulary is defined if mutation exists
10. affected files are listed
11. acceptance criteria are written

### Required documentation

Update:

```text
docs/conventions/MODULE_READINESS_LADDER.md
WORKBENCH_DELIVERY_BACKLOG.md
```

---

# E. What Must Move Out of Architecture Lane

The following should not remain blocked on architecture unless a new contradiction is found:

- modules with published contracts,
- modules with live routes,
- modules needing only BFF implementation,
- modules needing only frontend activation,
- modules needing only truth-hardening,
- modules needing only backlog status rebaseline.

Architecture should focus only on:

- global conventions,
- ownership decisions,
- module contract gaps,
- ratification issues.

---

# F. Required Deliverables from Architecture Team

This round should produce or update the following:

## Global conventions

```text
docs/conventions/MODULE_READINESS_LADDER.md
docs/conventions/BFF_RESPONSE_ENVELOPE.md
docs/conventions/DEGRADATION_DICTIONARY.md
```

## Ownership decisions

```text
docs/decisions/LINEAGE_READ_OWNERSHIP_AND_MIGRATION.md
docs/decisions/PERSONA_SERVICE_BOUNDARY.md
docs/decisions/COMMAND_AND_APPROVAL_OWNERSHIP_MATRIX.md
```

## Module contracts

```text
docs/bff/CW-02-debate-transcript.md
docs/bff/CW-04-redteam-memo.md
docs/bff/TW-02-parameter-controls.md
docs/bff/KW-05-strategy-spec.md
docs/bff/CW-03-committee-board.md
```

## Optional focused decision docs

```text
KW-05_STRATEGY_SPEC_VERSIONING_AND_COMPARE.md
CW-03_PARTIAL_ACTIVATION_RULE.md
```

---

# G. Final Architecture Response

The follow-up questions are valid, but they do not change the Pantheon system blueprint.

They close the last layer of canonical contract ambiguity.

The system direction remains:

- domain services own domain truth,
- BFF provides composed operator-facing read models,
- frontend consumes BFF only,
- runtime-manager and LEAN own execution truth,
- governance owns approval and deployment authority,
- module-level contracts do not imply new deployable services,
- frontend must not infer authority or lifecycle transitions,
- readiness states must be canonical and consistent across backlog, BFF, Lovable, and packet family docs.

After the deliverables listed above are produced, most of these items should move out of architecture lane and into:

- BFF implementation,
- truth-hardening,
- frontend / Lovable handoff,
- acceptance testing.

---

# Short Answer for Development Team

The blueprint direction is not changing.

We are only closing canonical contract gaps.

Use the decisions above as the authoritative answer for:

- readiness mapping,
- degradation semantics,
- staleness modeling,
- pagination naming,
- shared response envelope,
- lineage ownership,
- persona boundary,
- router/gateway/governance authority,
- CW-02 transcript,
- CW-04 memo,
- TW-02 controls,
- KW-05 strategy spec,
- CW-03 partial activation.

Do not create new services unless explicitly requested.

Do not let BFF or frontend become truth owners.

Move modules to implementation once their contract reaches `implementation_ready`.

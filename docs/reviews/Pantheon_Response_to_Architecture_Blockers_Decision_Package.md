# Pantheon Response to Architecture Blockers Decision Package

## Source document

This document is the formal response to:

`2026-04-21-architecture-blockers-decision-package.md`

## Purpose

This response is intended for the architecture and implementation teams. It clarifies which open items are true architecture blockers, which items should move to implementation or frontend activation, and which global conventions / ownership decisions must now be ratified.

The goal is not to redraw Pantheon's high-level system blueprint. The original Pantheon direction remains unchanged:

- Pantheon is a multi-persona quant research, governance, and execution platform.
- Research, knowledge, and consultation can be shared.
- Capital pools and live execution remain isolated.
- BFF is an operator-facing facade, not a source of truth.
- Frontend and Lovable must not infer authority, lifecycle state, or deployment eligibility.
- Domain services own canonical domain truth.
- Governance owns approval and promotion authority.
- Runtime-manager and LEAN own execution truth.
- Module-level contracts do not imply new deployable services.

This package should therefore be read as a **last-mile architecture blocker closure package**, not as a new blueprint.

---

# 1. Executive conclusion

The `2026-04-21 Architecture-Blocked Decision Package` is aligned with the prior Pantheon design direction, but it is more precise than the previous open-question lists.

It correctly narrows the remaining architecture blockers to:

1. **Four architecture-blocked modules**
   - `CW-02 Debate Transcript`
   - `CW-04 Red-team Memo`
   - `TW-02 Parameter Controls`
   - `KW-05 Strategy Spec`

2. **Six global conventions**
   - readiness ladder
   - degradation dictionary
   - staleness modeling
   - pagination naming
   - shared response envelope
   - `allowedActions` shape

3. **Three ownership / authority decisions**
   - lineage read ownership
   - persona canonical boundary
   - router / gateway / governance enforcement ownership

4. **One special gate rule**
   - `CW-03 Committee Board` partial activation rule

All other workbench modules should generally move out of architecture lane into one of:

- BFF implementation
- truth-hardening
- frontend activation
- runtime refresh
- delivery closeout
- documentation rebaseline

---

# 2. Relationship to previous architecture responses

## 2.1 What remains the same

This decision package is consistent with the previous architecture responses in the following ways:

- Do not redraw the high-level Pantheon blueprint.
- Do not let BFF become a truth owner.
- Do not let frontend / Lovable infer `allowedActions` or lifecycle authority.
- Do not interpret every module-level contract as a new deployable service.
- Resolve ownership boundaries explicitly.
- Ratify readiness status where backlog, packet family, BFF overview, and code truth disagree.

## 2.2 What this package improves

This package improves the prior discussion by reducing ambiguity. It more clearly separates:

- `architecture-blocked`
- `implementation-gap`
- `delivery-closeout`

It also corrects several prior vocabulary risks:

1. `allowedActions` should be object-shaped flags, not an array.
2. Pagination should remain `page_info.next_page_token`, not `next_cursor`.
3. `RW-05`, `KW-02`, `KW-03`, and `KW-04` should not remain architecture-blocked.
4. The true architecture-blocked modules are only `CW-02`, `CW-04`, `TW-02`, and `KW-05`.

These corrections should be adopted.

---

# 3. Final classification

## 3.1 True architecture-blocked modules

The following remain blocked on architecture ratification:

| Module | Status | Reason |
|---|---|---|
| `CW-02 Debate Transcript` | architecture-blocked | transcript event schema, ordering, actor labeling, evidence-link semantics not fully ratified |
| `CW-04 Red-team Memo` | architecture-blocked | memo lifecycle, session-to-memo mapping, governance review handoff authority not fully ratified |
| `TW-02 Parameter Controls` | architecture-blocked | patch semantics, reject behavior, validation contract, diff shape not fully ratified |
| `KW-05 Strategy Spec` | architecture-blocked | version identity, ancestry, immutability, lifecycle, compare semantics not fully ratified |

## 3.2 Not architecture-blocked

The following should not be sent back to architecture unless a new contradiction is discovered:

- `EW-04`
- `EW-05`
- `RW-01`
- `RW-02`
- `RW-03`
- `RW-04`
- `RW-05`
- `CW-01`
- `CW-03` route / authority contract itself
- `KW-01`
- `KW-02`
- `KW-03`
- `KW-04`
- `TW-01`
- `TW-03`
- `TW-04`

These should move into one of:

- implementation
- BFF wiring
- frontend activation
- runtime refresh
- truth-hardening
- documentation rebaseline
- delivery closeout

---

# 4. Global conventions — final decisions

## 4.1 Readiness ladder

### Decision

Pantheon adopts the following canonical readiness enum:

```text
blocked
contract_ready
screen_ready
handoff_ready
implementation_ready
production_ui_ready
```

### Mapping from existing vocabulary

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
| `implemented` | `implementation_ready` or `production_ui_ready` | Backend-only implementation is `implementation_ready`; frontend/BFF/acceptance complete is `production_ui_ready` |
| `ready` | deprecated | Too vague; must be replaced with a specific canonical enum |

### Rule

`ready` must not be used as a standalone canonical readiness value.

### Required document

```text
docs/conventions/MODULE_READINESS_LADDER.md
```

---

## 4.2 Degradation dictionary

### Decision

Pantheon adopts the following surface availability states:

```text
ok
partial
degraded
unavailable
suppressed
preview_unavailable
```

### Rule for `partial`

`partial` is valid only for non-authoritative read surfaces where incomplete auxiliary data is acceptable.

Allowed examples:

- lineage summary with unresolved optional refs
- transcript enrichment with unresolved actor display label or evidence links
- search result enrichment
- evidence panel with unavailable optional external refs
- insight aggregation with delayed sources

Not allowed for:

- `allowedActions`
- `ApprovalDecision`
- `DeploymentPlan`
- `RuntimeBinding`
- `killSwitch`
- `rollback`
- `paper / canary / live` authority
- persona lifecycle mutation
- capital pool binding authority

### Required document

```text
docs/conventions/DEGRADATION_DICTIONARY.md
```

---

## 4.3 Staleness modeling

### Decision

`stale` should not be a primary `meta.surfaces.*.state` value.

Freshness must be represented through:

```json
"meta": {
  "staleness": {
    "status": "fresh",
    "as_of": "2026-04-20T09:59:58Z",
    "max_age_seconds": 30
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

### Rule

Surface state describes whether a surface is available. Staleness describes freshness of the data behind that surface. Do not mix them.

### Required document

```text
docs/conventions/BFF_RESPONSE_ENVELOPE.md
docs/conventions/DEGRADATION_DICTIONARY.md
```

---

## 4.4 Pagination naming

### Decision

Pantheon keeps the existing canonical pagination field:

```json
"page_info": {
  "next_page_token": "..."
}
```

Do not change canonical output to `next_cursor`.

### Canonical list response

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

BFF adapters may accept internal aliases such as:

- `next_cursor`
- `cursor`
- `nextToken`

But BFF canonical responses must output:

```text
page_info.next_page_token
```

### Required document

```text
docs/conventions/BFF_RESPONSE_ENVELOPE.md
```

---

## 4.5 Shared response envelope

### Decision

The shared detail envelope is a minimum operator-facing wrapper. It does not replace domain-specific canonical identity.

Every detail response must expose:

```json
object_ref
```

Domain-specific fields remain in `data` or a module-specific block.

### Canonical detail shape

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
  "allowedActions": {
    "canApprove": true,
    "canReject": false
  },
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

### Required document

```text
docs/conventions/BFF_RESPONSE_ENVELOPE.md
```

---

## 4.6 `allowedActions` canonical shape

### Decision

`allowedActions` must be object-shaped flags.

Canonical example:

```json
"allowedActions": {
  "canApprove": true,
  "canReject": false,
  "canRequestReview": true,
  "canInitiateGovernanceReview": false
}
```

Do not use an array as canonical shape.

### Rule

Frontend must not derive action availability from actor role + object state. All CTA availability must come from backend-provided `allowedActions`.

### Required document

```text
docs/conventions/BFF_RESPONSE_ENVELOPE.md
docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md
```

---

# 5. Ownership / authority decisions

## 5.1 LIN-002 Lineage ownership

### Decision

```text
lineage-read-svc = UI-facing canonical read owner
telemetry lineage engine = internal substrate
domain services = normalized lineage write truth
BFF = consumer only
```

### Rule

BFF lineage / evolution surfaces must consume `lineage-read-svc` only. They must not directly consume telemetry lineage projection as a second UI truth path.

### Allowed internal telemetry lineage usage

Telemetry lineage engine may still be used for:

- incident reconstruction
- telemetry correlation
- background projection build
- lineage repair job

It must not be directly consumed by:

- Lovable
- frontend
- BFF UI surfaces
- operator UI

### Required document

```text
docs/decisions/LIN-002-lineage-ownership.md
```

---

## 5.2 Persona canonical boundary

### Decision

Persona service owns canonical persona identity and lifecycle. BFF owns only composed operator-facing views.

### Persona service owns

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

```text
latest deployment rollup
incident rollup
review rollup
display badges
operator convenience summary
action affordances from allowedActions
```

### Special rule

`PersonaCapitalBinding` is a capital/governance boundary object. It is not owned solely by persona service.

### Required document

```text
docs/decisions/control-plane-persona-boundary.md
```

---

## 5.3 Router / gateway / governance enforcement ownership

### Decision

| Concern | Owner |
|---|---|
| transport authn | gateway / BFF edge |
| ingress rate limit | gateway |
| domain rate limit | owning domain service |
| transport TTL | gateway |
| domain TTL | owning domain service |
| route selection | router |
| approval authority | governance / promotion |
| command execution | owning domain service |
| fallback intent classifier | router degraded mode only |

### Rule

Gateway must not become business authority. Router can decide routing, but cannot replace governance authority.

### Required document

```text
docs/decisions/control-plane-router-enforcement-ownership.md
```

---

# 6. Architecture-blocked modules

## 6.1 CW-02 Debate Transcript

### Status

Still architecture-blocked.

### Blocking issues

- append-only `TranscriptEvent` canonical schema
- ordering / stable cursor rule
- actor labeling contract
- inline evidence-link semantics
- transcript projection and replay boundary
- `partial transcript` degradation semantics

### Decision

`CW-02` may not move to full implementation until ratified.

Allowed before ratification:

- shell
- scaffolding
- UI placeholder
- non-authoritative mock display

Not allowed before ratification:

- locking transcript schema
- locking ordering rule
- locking actor labeling semantics
- locking evidence embedding behavior

### Minimum ratified schema

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

### Ordering rule

```text
TranscriptEvent is append-only.
sequence_no is monotonic within transcript_id.
BFF/frontend must not reorder events except by sequence_no.
```

### Partial transcript rule

`partial` may only mean enrichment is incomplete. It must not mean sequence gap, event loss, or untrusted ordering.

If the event stream integrity is compromised, surface state must be:

```text
degraded
```

### Required document

```text
docs/bff/CW-02-debate-transcript.md
```

---

## 6.2 CW-04 Red-team Memo

### Status

Still architecture-blocked.

### Blocking issues

- `ConsultMemo` lifecycle
- `session_to_memo_mapping`
- governance handoff contract
- `allowedActions.canInitiateGovernanceReview` authority rule
- memo publish and downstream review boundary

### Decision

`CW-04` may not move to full implementation until ratified.

Allowed before ratification:

- shell
- scaffolding
- non-authoritative mock display

### Minimum ratified lifecycle

```text
draft -> published
```

Published memos must not be edited in place. Changes require a new version or superseding memo.

### Mapping object

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

### `canInitiateGovernanceReview` gating rule

Canonical shape:

```json
"allowedActions": {
  "canInitiateGovernanceReview": true
}
```

It may be true only when all conditions hold:

1. memo lifecycle = `published`
2. memo has valid target, such as strategy / artifact / deployment plan
3. actor has governance / reviewer authority
4. no active governance review exists for the same target + memo
5. memo is not suppressed or withdrawn
6. evidence surface is not `unavailable`
7. governance service accepts the target type

### Required document

```text
docs/bff/CW-04-redteam-memo.md
```

---

## 6.3 TW-02 Parameter Controls

### Status

Still architecture-blocked.

### Blocking issues

- partial patch vs replace-style patch
- invalid patch response shape
- reject / partial-apply / noop policy
- canonical diff shape
- preview / replay / commit / discard boundary

### Decision

`TW-02` may not move to full implementation until ratified.

Allowed before ratification:

- shell
- scaffolding
- non-authoritative mock display

### Patch semantics

Use partial patch.

```text
Parameter controls are patched by field-level partial patch over an allowlisted control surface.
Omitted fields remain unchanged.
```

### Invalid patch response

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
  "allowedActions": {
    "canRetry": true,
    "canCommit": false
  }
}
```

### Diff shape

v1 must include:

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

Future extension may include:

```text
added_controls[]
removed_controls[]
derived_impacts[]
```

Frontend must not assume extension arrays always exist.

### Required document

```text
docs/bff/TW-02-parameter-controls.md
```

---

## 6.4 KW-05 Strategy Spec

### Status

Still architecture-blocked.

### Blocking issues

- canonical version identifier
- parent / ancestor / superseded relationship model
- lifecycle state
- compare semantics and diff granularity
- write paths that create new versions
- write paths that can mutate draft only

### Decision

`KW-05` remains architecture-blocked until strategy spec versioning and compare semantics are ratified.

### Minimum ratification content

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
supersedes_spec_version_id
superseded_by_spec_version_id
```

#### Lifecycle

```text
draft
candidate
approved
retired
```

#### Immutability

```text
candidate or higher is immutable.
Any change creates a new spec_version_id.
Only draft can be mutated in place.
```

#### Compare semantics

Compare output must be backend-generated. Frontend must not diff arbitrary JSON.

```json
{
  "left_spec_version_id": "...",
  "right_spec_version_id": "...",
  "changed_sections": [],
  "breaking_changes": [],
  "evidence_refs": []
}
```

### Required document

```text
docs/bff/KW-05-strategy-spec.md
```

Recommended additional decision doc:

```text
docs/decisions/KW-05-strategy-spec-versioning-and-compare.md
```

---

# 7. CW-03 Committee Board gate rule

## Decision

`CW-03` is not a new architecture-blocked module.

It may enter partial activation before `CW-02` is fully live.

However:

```text
CW-03 route-live != full module-ready
```

Full production handoff still requires `CW-02` transcript truth.

## Partial activation allowed surfaces

Before `CW-02` is live, `CW-03` may provide:

- committee board summary
- sponsor decision status
- current participants
- verdict summary
- pending actions
- high-level committee outcome

## Surfaces that require CW-02

The following must remain degraded or hidden until transcript truth is live:

- transcript timeline
- actor-event detail
- evidence-linked debate snippets
- transcript-driven verdict explanation
- full debate replay

## Required document

```text
docs/bff/CW-03-committee-board.md
```

Optionally:

```text
docs/decisions/CW-03-partial-activation-rule.md
```

---

# 8. Required deliverables from system design team

## 8.1 P0 global convention documents

```text
docs/conventions/GLOBAL_CANONICAL_CONVENTIONS.md
docs/conventions/BFF_RESPONSE_ENVELOPE.md
docs/conventions/DEGRADATION_DICTIONARY.md
docs/conventions/MODULE_READINESS_LADDER.md
```

## 8.2 P0 ownership decision documents

```text
docs/decisions/LIN-002-lineage-ownership.md
docs/decisions/control-plane-persona-boundary.md
docs/decisions/control-plane-router-enforcement-ownership.md
```

## 8.3 P0 module contract ratification documents

```text
docs/bff/CW-02-debate-transcript.md
docs/bff/CW-04-redteam-memo.md
docs/bff/TW-02-parameter-controls.md
docs/bff/KW-05-strategy-spec.md
```

## 8.4 Required status updates after ratification

After ratification, update:

- `WORKBENCH_DELIVERY_BACKLOG.md`
- readiness ratification record
- related packet family docs
- related frontend SA sections
- related BFF overview / examples

---

# 9. Final decision

The 2026-04-21 decision package should be accepted as the current architecture closure baseline.

It is consistent with the prior Pantheon blueprint, but more precise.

Final classification:

## Remain in architecture lane

- `CW-02`
- `CW-04`
- `TW-02`
- `KW-05`
- global conventions
- lineage ownership
- persona boundary
- router/gateway/governance ownership
- `CW-03` partial activation rule

## Move out of architecture lane

- `EW-04`
- `EW-05`
- `RW-01`
- `RW-02`
- `RW-03`
- `RW-04`
- `RW-05`
- `CW-01`
- `CW-03` route / authority implementation
- `KW-01`
- `KW-02`
- `KW-03`
- `KW-04`
- `TW-01`
- `TW-03`
- `TW-04`

These should move to implementation, runtime refresh, truth-hardening, frontend activation, or delivery closeout as appropriate.

---

# Short response for development team

The previous architecture direction is not changing.

This package narrows the remaining architecture blockers to four modules plus cross-cutting conventions and ownership decisions.

The only modules that should remain blocked on architecture are:

```text
CW-02
CW-04
TW-02
KW-05
```

The following global decisions should be adopted immediately:

- `allowedActions` is object-shaped flags.
- pagination remains `page_info.next_page_token`.
- `stale` moves to `meta.staleness`, not surface state.
- `partial` is allowed only for non-authoritative read surfaces.
- `lineage-read-svc` is the UI-facing lineage read owner.
- BFF and frontend must not infer authority.
- Module-level contract does not imply a new service.

Everything else should move out of architecture lane unless a new contradiction is discovered.

# PINT-011 — Persona daily product-truth contract

Date: 2026-07-17
Status: additive contract; runtime implementation pending `PINT-012`–`PINT-014`

## Contract truth

The v1.9 extension defines the product that daily strict operators may rely on.
An interaction is a durable resource with `queued`, `running`, `completed`,
`degraded`, or `failed` state. Its human request and typed source context are
immutable. Every selected Persona has an independent version/capability
snapshot and an independently correlated OpenClaw invocation. A successful
invocation may produce one typed opinion; a failed invocation produces an
error and degraded/missing-participant state, never a fabricated opinion.

Synthesis references the persisted independent opinion ids and cannot replace
their content. `no_consensus`, `more_research_required`, and partial provider
failure are first-class results. Recommended measures are structured provider
output with exact target/version, proposed value, evidence freshness,
uncertainty/risk, validation plan, rollback plan, and `execution_authority=none`.

Production conformance explicitly excludes deterministic or canned simulator
content. Keyword branches, magic topic strings, fixtures, and synthetic Persona
responses cannot satisfy `ProductionContentProvenance`; only a correlated
selected-Persona provider request and response can. The currently merged
`simulate_interaction_debate_and_synthesis` implementation therefore remains a
non-conforming baseline to be removed by `PINT-012`, not an alternate product
mode.

## Candidate and approval semantics

An operator may create a candidate only from an exact persisted
`RecommendedMeasure`, then modify, accept it for governed review, reject,
defer, or cancel it. Every decision binds the interaction, measure, proposal,
revision, digest, actor, rationale, time, and audit reference.

`accept_for_review` only creates a review-queue request. Its record is required
to say `formal_approval=false` and `execution_authority=none`. Formal approval
is separate canonical `ApprovalDecision` readback and requires exact tenant,
proposal revision/digest, authoritative validation receipt/digest, proposer,
distinct reviewer, decision time, expiry, and `self_approval=false`. Even a
formal approval receipt does not itself execute a target; a separately
authorized domain command would have to consume it.

The validation request contains immutable proposal references only. Browser-
supplied `validation_result` or arbitrary result JSON is not authoritative and
is rejected by the contract.

## Storage ownership matrix

| Record | Canonical durable owner | Projection/cache | Recovery rule |
| --- | --- | --- | --- |
| Immutable interaction request/context | Agora interaction Postgres | frontend/Workshop read projection | committed before `202`; RPO zero |
| Per-Persona provider invocation/error | Agora interaction Postgres | provider status/timeline projection | claim/retry by idempotent invocation id |
| Independent opinion | Agora interaction Postgres | Workshop opinion cards | only after correlated provider response |
| Synthesis | Agora interaction Postgres | Workshop synthesis card | references immutable opinion ids |
| Interaction outbox/event | Agora interaction Postgres | Workshop SSE/timeline | deterministic drain/replay; projection is not authority |
| Recommended measure | Agora interaction Postgres, nested in opinion | candidate composer | immutable provider provenance |
| Candidate revision/decision | Agora governance Postgres | proposal cards/review queue | ETag, idempotency, exact revision/digest |
| Validation receipt | canonical validation service | proposal validation panel | server-generated, expiring receipt |
| Formal approval | canonical ApprovalDecision store | approvals/reviewer UI | distinct reviewer and exact receipt/digest |
| Persona identity/version | Persona Registry snapshot | display metadata | snapshot is frozen per interaction |
| Provider response provenance | OpenClaw correlation plus Agora invocation record | diagnostics only | provider is not business-state authority |
| Browser/frontend state | none | cache only | reload must reconstruct from BFF readback |

Process-local maps, SSE buffers, Workshop cards, browser storage, and OpenClaw
conversation history are never the authoritative interaction or decision
store.

## Authority matrix

| Actor | May do | Must not do |
| --- | --- | --- |
| Authenticated operator | submit interaction; modify/accept-for-review/reject/defer/cancel candidate | self-approve; submit validation outcome; execute order/binding/promotion |
| Selected Persona/OpenClaw agent | return typed opinion and recommended measures | mutate proposal state, policy, memory, runtime, capital, or broker/order state |
| Synthesis worker | summarize cited independent opinions and expose disagreement | invent missing opinions or overwrite Persona output |
| Canonical validator | issue exact revision/digest validation receipt | approve or execute the proposal |
| Eligible distinct reviewer | issue formal ApprovalDecision against exact unexpired validation | approve own proposal or bypass validation/digest/tenant checks |
| Frontend | request actions and render authoritative readback | become authority through local state, capability hints, or arbitrary JSON |

Every interaction, invocation, opinion, measure, synthesis, candidate decision,
validation receipt, and formal approval carries the same closed boundary: no
order submission, broker call, capital change, runtime binding, lifecycle
promotion, policy mutation, or Persona-memory mutation.

## Historical evidence supersession

`PINT-010-R2` remains valid evidence for the bounded CRUD, authority-negative,
deployment, and restore operations it actually ran. It does not prove daily
write availability or real selected-Persona reasoning because its bounded
write proof used a permissive proof window and the final hosted frontend was
restored to read-only. This note changes only the daily-delivery interpretation;
it does not rewrite or invalidate the historical evidence bytes.

The v1.9 manifest labels all new routes `contract_only`. Hosted availability
may be claimed only after dependent runtime, persistence, auth, frontend,
deployment, and exact-SHA acceptance tasks merge and pass.

## Additive artifacts

- `services/control-plane/specs/agora/v10/persona_interaction_daily.schema.json`
- `services/control-plane/specs/agora/v10/capability_manifest_v1_9.json`
- `services/control-plane/specs/agora/bundle_index.v1_9.json`
- `services/control-plane/openapi/agora_v1_9.openapi.yaml`
- `scripts/test_agora_v1_9_bundle.py`

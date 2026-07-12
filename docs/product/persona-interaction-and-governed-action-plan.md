# Persona Interaction and Governed Action Plan

Status: frozen (PINT-001)
Last updated: 2026-07-12
Tier: L2 Planning & Execution (freezes L1-derived event contracts; does not redefine L1 semantics)
Scope: cross-repo persona-to-persona opinion consultation and governed action authority closure
Conflict rule: this document freezes wire-level event contracts that operationalize
`PERSONA_RUNTIME_MODEL.md` and the two `docs/decisions/control-plane-*.md`
decisions below. If any field here appears to disagree with one of those L1
documents, the L1 document wins and this plan must be corrected to match it.

## 1. Problem

Two related gaps exist between the conceptual persona-runtime model and any
frozen wire contract:

1. `PERSONA_RUNTIME_MODEL.md` section 13 states personas can hold three
   consultation roles -- requester, responder, committee participant -- all
   realized through `SessionPersona`, never a raw Persona Registry object.
   No event contract exists that freezes what a requester/responder exchange
   actually looks like on the wire, or how one repo hands that exchange to
   another (control-plane, `services/consultation`, persona-registry-owning
   services).
2. `PERSONA_RUNTIME_MODEL.md` section 6 defines a Consult Policy model
   (required reviewers, required committees, trigger rules, forbidden solo
   actions, escalation rules) that determines when a persona "may not act
   alone." No event contract closes the authority question end to end: which
   layer evaluates solo-eligibility, which layer grants approval, and which
   layer executes the action.

This plan traces the contracts that already exist, freezes the two additive
event contracts that close these gaps, and proves the authority boundary
against the canonical ownership decisions already on file.

## 2. Existing contracts traced

| Contract | Owner | Scope | Relationship to this freeze |
|---|---|---|---|
| `services/consultation/consult_request.schema.json` / `consult_memo.schema.json` | consultation service | Formal advisory review (strategy_review, redteam, data_leakage, execution_risk, capital_pool, incident, persona_policy) with committee memos and a `sponsor_decision_bridge.py` hand-off into governance/evolution proposals. | This is the **formal governance-review path**. It is heavier-weight (draft -> submitted -> assigned -> in_progress -> memo_pending -> published) and is bridged into capital/evolution decisions. The new `PersonaOpinionConsultationEvent` is a lighter-weight, session-bound interaction thread that may **escalate into** a `ConsultRequest` via `escalated_consult_request_id`, but does not replace or re-model it. |
| `services/consultation/sponsor_decision_bridge.py` / `sponsor_decision_bridge_contract.md` | consultation service | Converts a committee sponsor decision into an `ApprovalDecisionProposal` / `EvolutionDecisionProposal`; side-effect free, does not persist governance state itself. | Confirms the existing precedent that "record an evaluation/decision" and "execute the action" are already kept as separate steps in this codebase. `GovernedActionAuthorityRequest` follows the same discipline. |
| `services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json` | control-plane / Agora | Strategy-stage handoff (shadow/paper/canary/live) with `no_order_route_proof`. | Scoped to strategy-promotion handoff, not general persona action authority. Establishes the `no_*_proof` convention this freeze reuses (`no_capital_authority_proof`, `execution_authority_proof`). |
| `services/control-plane/specs/agora/v5/candidate_discussion.schema.json` | control-plane / Agora | Free-form discussion thread on a candidate pool/member; `author` may be "operator or persona ID" as a plain string. | Not session-bound and not role-typed (no requester/responder/committee distinction), so it cannot stand in for `PERSONA_RUNTIME_MODEL.md` section 13's consultation roles. `PersonaOpinionConsultationEvent` is the role-typed, session-bound counterpart for opinion exchanges that need to be correlated and audited as a thread. |
| `docs/decisions/control-plane-persona-boundary.md` | L1 | Persona service owns canonical `Persona`, `ConsultPolicyRef`, lifecycle, mandate, session metadata. BFF only composes read views. | `GovernedActionAuthorityRequest.consult_policy_id`, `persona_lifecycle_state`, and `solo_eligibility` are persona-owned evaluation fields, consistent with this boundary. |
| `docs/decisions/control-plane-router-enforcement-ownership.md` | L1 | Governance/promotion owns approval authority; domain services own command execution; router/gateway own routing and transport, not business approval. | `GovernedActionAuthorityRequest.decision` may only be set meaningfully by a governance/promotion surface, and the contract is explicit (`execution_authority_proof`) that it never executes the underlying command itself. |

No prior task, schema, or activity-log entry named "PINT" or "persona
interaction contract" was found before this task; this is a first freeze, not
a rename or migration of existing work.

## 3. Frozen contract 1 -- `PersonaOpinionConsultationEvent`

File: `services/control-plane/specs/agora/v7/persona_opinion_consultation_event.schema.json`

Operationalizes `PERSONA_RUNTIME_MODEL.md` section 13. Key properties:

- `requester` / `participants[].actor` are `sessionActorRef` objects. When
  `actor_type` is `persona_session`, `session_id` is required -- the schema
  enforces "always through `SessionPersona`, never a raw registry persona."
- `participants[].role` is restricted to `responder` or `committee_participant`,
  matching the three consultation roles in section 13 (the requester role is
  carried by the top-level `requester` field).
- `event_type: opinion_offered` requires a populated `opinion` object
  (`stance`, `confidence`, `rationale`); a `null` opinion on that event type is
  rejected.
- `event_type: opinion_escalated` requires `escalated_consult_request_id`,
  which is the only permitted bridge into `services/consultation`'s
  `ConsultRequest.request_id`. This event contract does not duplicate
  `ConsultRequest`/`ConsultMemo` fields.
- `no_capital_authority_proof` is a fixed-value marker (following the
  `no_order_route_proof` precedent in `governed_intent_handoff.schema.json`)
  making explicit that this event never grants deployment, rollback, broker,
  capital, or order authority.

## 4. Frozen contract 2 -- `GovernedActionAuthorityRequest`

File: `services/control-plane/specs/agora/v7/governed_action_authority_request.schema.json`

Operationalizes `PERSONA_RUNTIME_MODEL.md` section 6. Key properties:

- `solo_eligibility` (`evaluated`, `forbidden_solo_action`,
  `trigger_rules_matched`) is the **persona-owned Consult Policy evaluation**.
  It is explicitly documented as an evaluation, not an approval.
- `decision` (`pending` / `authorized` / `authorized_with_conditions` /
  `denied` / `escalated`) is the **governance-owned outcome**. A schema rule
  requires `decision_by` and `resolved_at` whenever `status` is `resolved`, so
  a resolved request can never be silently missing who decided it.
- A schema rule requires at least one `required_committees` or
  `required_reviewers` entry whenever `solo_eligibility.forbidden_solo_action`
  is `true` -- a forbidden-solo action can never be frozen without naming who
  must be consulted, closing the gap between "solo is forbidden" and "then
  who is required."
- `linked_opinion_consultation_ids` correlates to one or more
  `PersonaOpinionConsultationEvent.interaction_id` values, and
  `linked_consult_request_id` optionally bridges to a formal
  `services/consultation` review when the authority question escalates that
  far.
- `execution_authority_proof` is a fixed-value marker stating this contract
  "does not execute command" -- execution ownership stays with the domain
  service per `docs/decisions/control-plane-router-enforcement-ownership.md`.

## 5. Authority boundary proof

| Layer | Owns | Evidence in this freeze |
|---|---|---|
| Persona service (persona-owned truth) | `consult_policy_id`, `persona_lifecycle_state`, `solo_eligibility` evaluation | These fields are populated by the requesting persona's own policy/lifecycle state, never by `decision`. Matches `control-plane-persona-boundary.md`'s persona-owned truth list (`ConsultPolicyRef`, lifecycle). |
| Governance / promotion (approval authority) | `decision`, `decision_by` | Schema requires both whenever `status = resolved`; nothing in `solo_eligibility` can set `decision` directly -- they are separate objects with no shared enum. Matches `control-plane-router-enforcement-ownership.md`'s "approval authority" row. |
| Domain service (command execution) | executing the actual action once authorized | `execution_authority_proof` is a closed enum of exactly one value that says this contract does not execute; there is no field on this schema that could carry out a command. Matches the same decision's "command execution" row. |
| Consultation service (formal governance review) | `ConsultRequest` / `ConsultMemo` / `sponsor_decision_bridge` | Reached only via `escalated_consult_request_id` / `linked_consult_request_id`; the new contracts never redefine those fields. |

This closes the "who may act solo vs. who needs governance" question for any
action type a persona attempts: the persona layer can only ever assert
*whether it evaluated itself as solo-ineligible*, never approve itself.

## 6. Scope boundary of this freeze

This is a contract freeze, not a runtime implementation:

- No new BFF routes, no new OpenAPI file, and no `bff_route_families` are
  introduced. `capability_manifest_v1_6.json` marks both capabilities
  `implementation_status: "contract_frozen_no_backend_routes"`.
- `bundle_index.v1_6.json` extends `bundle_index.v1_5.json` and records
  sha256 digests for the two new schemas plus `capability_manifest_v1_6.json`
  so the frozen shape is tamper-evident (verify with
  `python3 scripts/agora_schema_bundle.py --verify` once that script is
  extended to cover versioned bundles, matching the same manual-hash
  precedent already used by `bundle_index.v1_1.json` through
  `bundle_index.v1_5.json`).
- Wiring these contracts into an actual emitter/consumer (control-plane
  routes, `services/consultation` bridge, persona-registry-owning service)
  is out of scope for PINT-001 and should be tracked as separate follow-up
  execution tasks once a consumer is ready to bind to the frozen shape.

## 7. Acceptance mapping

| Acceptance criterion | Evidence |
|---|---|
| Trace existing contracts | Section 2 |
| Freeze additive schemas | Section 3, 4; `services/control-plane/specs/agora/v7/*.schema.json`, `capability_manifest_v1_6.json`, `bundle_index.v1_6.json` |
| Prove authority boundary | Section 5 |
| Merge Pantheon PR | tracked via `scripts/git/task_finalize.sh` per `task/PINT-001` |

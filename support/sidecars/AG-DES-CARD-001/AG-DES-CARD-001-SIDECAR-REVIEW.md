# AG-DES-CARD-001 Sidecar: Review Packet and Evidence Summary

| Field | Value |
|---|---|
| Task ID | `AG-DES-CARD-001-SIDECAR-REVIEW` |
| Helper kind | `review_packet` |
| Parent task | `AG-DES-CARD-001` — workshop card projection contracts |
| Sidecar owner / reviewer | Claude / Claude2 |
| Prepared by | Claude |
| Date | 2026-06-21 |
| Mutates canonical truth | false |
| Status | Ready for reviewer handoff |

## Purpose

This support-only packet assembles the review evidence and design summary for the
parent task `AG-DES-CARD-001`. The parent task must produce a merged
`workshop_card.schema.json` (v4) before the CARD-dependent frontend tasks
(`AG-FE-SW-001`, `AG-FE-SW-002`, `AG-FE-SW-003`, `AG-FE-RS-001`,
`AG-FE-TR-001`, `AG-FE-TR-002`) can be dispatched.

This packet does not modify L1 canonical truth, OpenAPI bundles, BFF runtime
code, route registries, database schemas, or governance policy. All decisions
and artifacts remain the responsibility of the parent task owner and the
downstream execution owners.

---

## 1. What AG-DES-CARD-001 Must Deliver

Per the Round 2 design closure
(`docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/`) and
MASTER_SD_RESPONSE §E, `AG-DES-CARD-001` must produce a merged contract
covering:

- One JSON schema for the `WorkshopCard` discriminated union
  (`services/control-plane/specs/agora/v4/workshop_card.schema.json`)
- Twelve typed card payloads: `user_strategy_description`,
  `servant_reconstruction`, `completeness_update`, `missing_definition`,
  `next_question`, `research_plan_proposal`, `research_progress`,
  `research_result`, `consult_result`, `version_patch_proposal`,
  `version_compare`, `readiness_gate`
- A common card envelope shared by every card type (E1)
- The source prose already exists at
  `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md`

The `workshop_card.schema.json` schema must be merged into `pantheon@dev` before
any downstream CARD-dependent worker is dispatched.

---

## 2. Design Decisions — Status Check

The following decisions are finalized in MASTER_SD_RESPONSE §E and are not
open for re-interpretation:

### 2.1 Cards Are Projections, Not Truth Owners — CONFIRMED

Cards are derived from BFF projections. They do not own any truth that is not
already owned by the canonical source (workshop events, research runs, Registry
versions, etc.). This means the schema must reference BFF source types but must
not duplicate or shadow canonical fields.

### 2.2 Frontend Binds From BFF Projections Only — CONFIRMED

The frontend may render markdown inside a typed field, but it cannot infer card
type or meaning by parsing free-form assistant output. The BFF must always
produce a card with a typed `card_type` discriminant and a structured `payload`.

### 2.3 Prior Bundles Remain Immutable — CONFIRMED

`bundle_index.json`, `bundle_index.v1_1.json`, `bundle_index.v1_2.json`,
`agora_v1.openapi.yaml`, `agora_v1_1.openapi.yaml`, `agora_v1_2.openapi.yaml`
are frozen. `AG-DES-CARD-001` contributes only an additive file to
`services/control-plane/specs/agora/v4/`. It does not edit any v1, v1.1, v1.2,
or v3 artifact.

### 2.4 SSE Stream Carries Card-Update References, Not Payloads — CONFIRMED

The SSE stream (`workshop_stream_event.schema.json`, produced by
`AG-DES-SSE-001`) carries card-update references. It does not resend every
large card payload over the stream. The card detail is fetched via
`GET /bff/agora/workshops/{workshop_id}/cards`.

---

## 3. Required Artifacts

The parent task must produce the following file. It does not exist yet in
`pantheon@dev`.

| Artifact | Location | Status |
|---|---|---|
| `WorkshopCard` JSON schema | `services/control-plane/specs/agora/v4/workshop_card.schema.json` | NOT CREATED |

The following v4 files already exist (produced by `AG-DES-VERS-001`) and must
not be modified by `AG-DES-CARD-001`:

| File | Status |
|---|---|
| `v4/version_patch_proposal.schema.json` | Merged ✓ |
| `v4/version_compare.schema.json` | Merged ✓ |
| `v4/strategy_readiness.schema.json` | Merged ✓ |

`bundle_index.v1_3.json` and `capability_manifest_v1_3.json` will be produced
by `AG-XR-OPENAPI-004` after all v4 schemas are merged. `AG-DES-CARD-001` must
not pre-create or edit these.

---

## 4. Common Card Envelope (E1)

Every `WorkshopCard` must include the following fields
(from `05_workshop_card_contracts.md` §E1):

```text
card_id                     string — unique card identifier
card_type                   string — discriminant (enum of 12 types)
workshop_id                 string
sequence_no                 integer — per-workshop monotonic
source_event_ids            string[] — causal workshop event IDs
workshop_version_id         string | null — current active version at card time
strategy_spec_registry_id   string | null — locked version ref, if applicable
status                      string — enum: informational | action_required |
                                     running | completed | failed | stale
title                       string
summary                     string
payload                     object — typed by card_type (discriminated union)
evidence_refs               array
allowed_actions             object — keyed by action name
created_at                  datetime
updated_at                  datetime
```

Card `status` enum:

```text
informational
action_required
running
completed
failed
stale
```

---

## 5. Card Type Payload Summaries

### 5.1 `user_strategy_description`

Source: `OwnerWorkshopEventResponse`

```text
owner_visible_content       string
redacted_summary            string
attachment_refs             string[]
message_event_id            string
created_at                  datetime
```

Rules: owner-visible only; no private-content object URI; no localStorage persistence.

### 5.2 `servant_reconstruction`

Source: `agora-strategy-dialogue` skill result

```text
strategy_title              string
causal_chain[]              array of {step_id, premise, mechanism,
                              expected_observation, confidence, evidence_refs}
explicit_definitions        string[]
servant_inferences[]        array of {statement, confidence, needs_confirmation}
uncertainties               string[]
contradictions              string[]
proposed_next_actions       string[]
patch_proposal_ref          string | null
```

Distinguishes trader-stated facts from servant inference.

### 5.3 `completeness_update`

Source: `StrategyCompleteness` + `StrategyReadinessAssessment`

```text
overall_grade               string
dimension_updates[]         array of {dimension, prior_grade, current_grade,
                              gaps[], required_actions[]}
blockers                    string[]
research_ready              boolean
readiness_gates[]           array — gate summary refs
change_since_previous       string
```

### 5.4 `missing_definition`

```text
gap_id                      string
category                    string
severity                    string
missing_definition          string
why_it_matters              string
downstream_blocked_capabilities  string[]
suggested_temporary_assumption   string | null
answer_options              array
can_defer                   boolean
deferral_consequence        string | null
```

One material question per card; not a form containing every gap.

### 5.5 `next_question`

```text
question_id                 string
question                    string
why_now                     string
score_total                 number
score_components            object:
  information_gain          number
  downstream_blocking_weight number
  risk_impact               number
  research_cost_reduction   number
  user_relevance            number
  penalties                 number
answer_options              array
freeform_allowed            boolean
defer_allowed               boolean
defer_consequence           string | null
golden_case_ref             string | null
```

### 5.6 `research_plan_proposal`

Source: `ResearchPlan` + `ResearchPlanExecution`

```text
plan_id                     string
objectives                  string[]
data_requirements           string[]
stages[]                    array of {stage_id, stage_type, purpose,
                              preferred_backend, dependencies[]}
evaluation_criteria         string
budget                      object
assumptions                 string[]
warnings                    string[]
approval_requirement        string
```

Actions: `approve | edit | reject/cancel | request_explanation`

### 5.7 `research_progress`

Source: `ResearchRunProjection` + workshop SSE

```text
run_id                      string
plan_id                     string
stage_id                    string
stage_type                  string
execution_status            string
progress                    number (0–1)
backend                     string
latest_progress_message     string | null
warnings                    string[]
blocking_reasons            string[]
started_at                  datetime
updated_at                  datetime
```

Actions: `cancel | open_detail`

### 5.8 `research_result`

Source: `ResearchRunProjection`

```text
run_id                      string
outcome                     string
metrics                     object (grouped by category)
findings                    array
warnings                    string[]
blocking_reasons            string[]
artifact_refs               string[]
evidence_refs               array
gate_impacts                array
recommended_patch_proposal_refs  string[]
backend                     string
mode                        string — real | fixture | stub
data_cutoff                 datetime | null
```

Card visibly labels real/fixture/stub.

### 5.9 `consult_result`

```text
consultation_id             string
consultation_type           string
participant_persona_refs    string[]
status                      string
consensus_summary           string
disagreements               array
risk_notes                  string[]
conditions                  array
evidence_refs               array
freshness                   string
```

Private servant synthesizes; central personas do not receive unrelated raw user content.

### 5.10 `version_patch_proposal`

Binds `VersionPatchProposal` (schema already in `v4/version_patch_proposal.schema.json`):

```text
proposal_id                 string
base_version                string
change_summary              array
rationale                   string
predicted_effects           array
validation                  object (state)
warnings                    array
conflicts                   array
```

Actions: `validate | accept | reject | open_diff`

### 5.11 `version_compare`

Binds `VersionCompare` (schema already in `v4/version_compare.schema.json`):

```text
base_version                string
candidate_versions          string[]
field_diffs                 array
metric_diffs                array (with evidence_class)
risk_diffs                  array
readiness_diffs             array
recommendation              string
limitations                 string[]
```

### 5.12 `readiness_gate`

Binds `StrategyReadinessAssessment` (schema already in `v4/strategy_readiness.schema.json`):

```text
gates                       array (3 gates: preliminary_research,
                              full_validation, trading_room)
requirement_states          array
hard_blockers               array
temporary_assumptions       array
staleness                   string | null
highest_ready_gate          string | null
```

---

## 6. BFF Projection Endpoint

Workshop detail response may embed `cards[]` inline. For pagination/streaming:

```text
GET /bff/agora/workshops/{workshop_id}/cards
```

SSE stream (`workshop_stream_event`) carries card-update references; it does not
resend every large card payload. The BFF produces cards from BFF projections
only — it never synthesizes card content from arbitrary LLM markdown.

### Frontend Source-of-Truth Map

| Card | BFF source |
|---|---|
| `user_strategy_description` | workshop events / owner projection |
| `servant_reconstruction` | workshop card projection |
| `completeness_update`, `missing_definition`, `next_question` | completeness / readiness projection |
| `research_plan_proposal` | research plan detail |
| `research_progress`, `research_result` | research run projection |
| `consult_result` | consultation projection |
| `version_patch_proposal`, `version_compare` | patch and comparison routes |
| `readiness_gate` | workshop readiness route |

---

## 7. Schema Design Notes

The `workshop_card.schema.json` parent task owner should implement as a
discriminated union using `card_type` as the discriminant key, with `payload`
validated by the corresponding sub-schema per card type. Suggested structure:

```json
{
  "oneOf": [
    { "$ref": "#/definitions/card_user_strategy_description" },
    { "$ref": "#/definitions/card_servant_reconstruction" },
    ...
  ]
}
```

Each definition repeats the common envelope fields with `card_type` as a
fixed enum and `payload` as the typed object for that card type. This keeps the
schema self-contained and avoids cross-file imports that complicate frontend
code generation.

Cross-referencing existing v4 schemas is allowed but not mandatory. The card
schema for `version_patch_proposal`, `version_compare`, and `readiness_gate`
payloads may `$ref` the corresponding v4 schema files or inline the required
subset — the parent task owner must choose and document the decision in the
artifact commit message.

---

## 8. Downstream Unblock Conditions

| Task | Blocked until |
|---|---|
| `AG-FE-SW-001` | `workshop_card.schema.json` merged |
| `AG-FE-SW-002` | `workshop_card.schema.json` + `workshop_stream_event.schema.json` merged |
| `AG-FE-SW-003` | `version_patch_proposal/compare/readiness` + `workshop_card.schema.json` merged |
| `AG-FE-RS-001` | VERS + RS + `workshop_card.schema.json` merged |
| `AG-FE-TR-001` | TR aggregates + `workshop_card.schema.json` + BFF client generated |
| `AG-FE-TR-002` | TR + candidate-decision contract available |

`AG-FE-SW-001` has the fewest additional dependencies; it is the earliest task
that can be unblocked by `AG-DES-CARD-001` alone.

---

## 9. Open Questions for Reviewer

The following points require reviewer confirmation before the parent task owner
begins the implementation:

1. **`allowed_actions` shape** — E1 lists `allowed_actions{}` as a dictionary but
   does not define the value type per action. The schema needs a concrete type
   (e.g. `{ "type": "object", "additionalProperties": { "$ref": "#/definitions/action_def" }`).
   Is the value schema `{ label, description, enabled }` or just a boolean?

2. **Card staleness trigger** — E1 includes a `stale` status but the design doc
   does not specify per-card staleness rules (e.g. `completeness_update` goes
   stale after a new message; `research_result` goes stale if a new run supersedes
   it). Should the schema encode staleness triggers, or should staleness be a
   BFF concern only?

3. **Inline vs. reference payloads for VERS schemas** — For `version_patch_proposal`,
   `version_compare`, and `readiness_gate` card payloads, should `workshop_card.schema.json`
   inline the payload fields or `$ref` the corresponding v4 schemas
   (`version_patch_proposal.schema.json`, `version_compare.schema.json`,
   `strategy_readiness.schema.json`)? The cross-ref approach reduces duplication
   but requires the frontend code-gen toolchain to handle multi-file schemas.

4. **`workshop_version_id` nullability** — Early cards (`user_strategy_description`,
   `servant_reconstruction`) are created before a StrategySpec version exists.
   Should `workshop_version_id` and `strategy_spec_registry_id` be `string | null`
   for all card types, or required for specific card types only (e.g. `readiness_gate`)?

5. **Card sequence reset on replay** — `sequence_no` is per-workshop monotonic.
   When the SSE client uses `Last-Event-ID` replay and fetches cards, should
   the card `sequence_no` match the original creation order or the SSE stream
   `sequence_no`? Are these the same counter?

---

## 10. Boundary and Handoff Notes

**This sidecar does not:**
- Create or modify `services/control-plane/specs/agora/v4/workshop_card.schema.json`.
- Create or modify any OpenAPI file.
- Create or modify `bundle_index.v1_3.json` or `capability_manifest_v1_3.json`.
- Touch any frozen v1, v1.1, v1.2, or v3 artifact.
- Modify `ai-status.json` fields beyond task lifecycle commands.

**The reviewer (Claude2) should:**
- Confirm the design decisions in §2 are consistent with the Agora v1.3 design
  closure (MASTER_SD_RESPONSE §E and `05_workshop_card_contracts.md`).
- Confirm the card type list in §5 matches the final MASTER_SD_RESPONSE §E
  catalog (12 card types; `user_strategy_description` through `readiness_gate`).
- Confirm the downstream unblock conditions in §8 are accurate per the Round 2
  dispatch unblock matrix (`07_dispatch_unblock_matrix.md`).
- Flag whether any of the open questions in §9 are blockers for dispatch, or
  whether they can be resolved by the parent task owner during implementation.
- If the packet is approved, indicate approval so the parent task `AG-DES-CARD-001`
  owner can proceed to producing the schema artifact.

---

## 11. Evidence References

| Evidence | Location |
|---|---|
| Round 2 design closure index | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/INDEX.md` |
| Workshop card contracts prose (source of truth for AG-DES-CARD-001) | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md` |
| MASTER_SD_RESPONSE §E | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/MASTER_SD_RESPONSE.md` |
| Round 2 dispatch unblock matrix | `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/07_dispatch_unblock_matrix.md` |
| v4 version_patch_proposal schema (merged) | `services/control-plane/specs/agora/v4/version_patch_proposal.schema.json` |
| v4 version_compare schema (merged) | `services/control-plane/specs/agora/v4/version_compare.schema.json` |
| v4 strategy_readiness schema (merged) | `services/control-plane/specs/agora/v4/strategy_readiness.schema.json` |
| Agora bundle index v1.2 (chain root for v1.3) | `services/control-plane/specs/agora/bundle_index.v1_2.json` |

---

## 12. Reviewer Findings (Claude2 — 2026-06-21)

Reviewer: Claude2
Review date: 2026-06-21
Verdict: **APPROVED**

### 12.1 Design Decision Cross-Check

All four design decisions in §2 are confirmed against source documents.

| Decision | Source | Verdict |
|---|---|---|
| §2.1 Cards are projections, not truth owners | `05_workshop_card_contracts.md`: "Cards are projections, not separate truth owners." | ✓ Confirmed |
| §2.2 Frontend binds from BFF projections only | MASTER_SD_RESPONSE §E: "The frontend may render markdown inside a typed field, but cannot infer card type/meaning by parsing free-form assistant output." | ✓ Confirmed |
| §2.3 Prior bundles remain immutable | MASTER_SD_RESPONSE §2: `bundle_index.json`, `bundle_index.v1_1.json`, `bundle_index.v1_2.json`, all three `agora_v1*.openapi.yaml` files explicitly listed as "不修改" | ✓ Confirmed |
| §2.4 SSE stream carries references, not payloads | `05_workshop_card_contracts.md` E14: "The SSE stream carries card-update references; it need not resend every large card payload." | ✓ Confirmed |

### 12.2 Card Type List Cross-Check

MASTER_SD_RESPONSE §E lists exactly 12 required card types in the same order as §5. No additions, deletions, or renames detected.

```text
user_strategy_description       ✓ §5.1
servant_reconstruction          ✓ §5.2
completeness_update             ✓ §5.3
missing_definition              ✓ §5.4
next_question                   ✓ §5.5
research_plan_proposal          ✓ §5.6
research_progress               ✓ §5.7
research_result                 ✓ §5.8
consult_result                  ✓ §5.9
version_patch_proposal          ✓ §5.10
version_compare                 ✓ §5.11
readiness_gate                  ✓ §5.12
```

All 12 payload field sets in §5 match `05_workshop_card_contracts.md` E2–E13 field-for-field. The envelope in §4 matches E1 exactly.

### 12.3 Downstream Unblock Condition Cross-Check

All six conditions in §8 are confirmed against `07_dispatch_unblock_matrix.md`.

| Task | §8 condition | Matrix condition | Verdict |
|---|---|---|---|
| `AG-FE-SW-001` | `workshop_card.schema.json` merged | "CARD contract available" | ✓ |
| `AG-FE-SW-002` | `workshop_card.schema.json` + `workshop_stream_event.schema.json` merged | "CARD + SSE contract available" | ✓ |
| `AG-FE-SW-003` | VERS + `workshop_card.schema.json` merged | "VERS + CARD contracts mirrored to frontend" | ✓ |
| `AG-FE-RS-001` | VERS + RS + `workshop_card.schema.json` merged | "VERS + RS + CARD generated types mirrored" | ✓ |
| `AG-FE-TR-001` | TR aggregates + `workshop_card.schema.json` + BFF client generated | "TR + CARD types and BFF client generated" | ✓ |
| `AG-FE-TR-002` | TR + candidate-decision contract available | "TR + candidate-decision integration contract available" | ✓ |

### 12.4 Open Questions — Dispatch Blocker Assessment

None of the five open questions in §9 are dispatch blockers. The parent task owner may resolve them during implementation.

| # | Question | Dispatch blocker? | Recommended resolution |
|---|---|---|---|
| Q1 | `allowed_actions` value schema | No | Default to `{ "enabled": boolean, "label": string }`. The schema needs `additionalProperties` with this sub-schema. The parent task owner must document the choice in the commit message. |
| Q2 | Card staleness trigger | No | `stale` is already in the E1 `status` enum. Per-card staleness trigger rules are a BFF runtime concern, not a schema constraint. No schema encoding needed. |
| Q3 | Inline vs. `$ref` for VERS payloads | No | Recommend `$ref` to avoid duplication. Frontend code-gen runs during `AG-XR-OPENAPI-004` which handles multi-file schemas. Owner must document the choice in the commit message. |
| Q4 | `workshop_version_id` nullability | No | Safe default: `string | null` for all card types. Can be tightened to required-only for specific card types (e.g. `readiness_gate`) in a follow-on revision. |
| Q5 | `sequence_no` alignment with SSE replay | No | Card `sequence_no` is a per-workshop creation-order counter; SSE stream may use a separate monotonic counter. These need not be the same. BFF must document which counter drives each. No schema change needed. |

### 12.5 Summary

The review packet is accurate, internally consistent, and grounded in the Round 2 design closure. The parent task owner (`AG-DES-CARD-001`) may proceed to produce `workshop_card.schema.json` at `services/control-plane/specs/agora/v4/workshop_card.schema.json`. All open questions can be resolved locally by the parent task owner with no further reviewer gate required before dispatch.

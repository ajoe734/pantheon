# E — Strategy Workshop Conversation Card Field Contracts

## E1. Common card envelope

Every card uses:

```text
card_id
card_type
workshop_id
sequence_no
source_event_ids[]
workshop_version_id
strategy_spec_registry_id
status
title
summary
payload
evidence_refs[]
allowed_actions{}
created_at
updated_at
```

Card status:

```text
informational
action_required
running
completed
failed
stale
```

Cards are projections, not separate truth owners.

## E2. `user_strategy_description`

Source:

```text
OwnerWorkshopEventResponse
```

Payload:

```text
owner_visible_content
redacted_summary
attachment_refs[]
message_event_id
created_at
```

Rules:

- owner-visible only;
- no private-content reference or object URI;
- no localStorage persistence.

## E3. `servant_reconstruction`

Source:

```text
agora-strategy-dialogue skill result
```

Payload:

```text
strategy_title
causal_chain[]:
  step_id
  premise
  mechanism
  expected_observation
  confidence
  evidence_refs[]
explicit_definitions[]
servant_inferences[]:
  statement
  confidence
  needs_confirmation
uncertainties[]
contradictions[]
proposed_next_actions[]
patch_proposal_ref
```

The card distinguishes trader-stated facts from servant inference.

## E4. `completeness_update`

Source:

```text
StrategyCompleteness + StrategyReadinessAssessment
```

Payload:

```text
overall_grade
dimension_updates[]:
  dimension
  prior_grade
  current_grade
  gaps[]
  required_actions[]
blockers[]
research_ready
readiness_gates[]
change_since_previous
```

## E5. `missing_definition`

Payload:

```text
gap_id
category
severity
missing_definition
why_it_matters
downstream_blocked_capabilities[]
suggested_temporary_assumption
answer_options[]
can_defer
deferral_consequence
```

The card asks one material question, not a form containing every gap.

## E6. `next_question`

Payload:

```text
question_id
question
why_now
score_total
score_components:
  information_gain
  downstream_blocking_weight
  risk_impact
  research_cost_reduction
  user_relevance
  penalties
answer_options[]
freeform_allowed
defer_allowed
defer_consequence
golden_case_ref
```

## E7. `research_plan_proposal`

Source:

```text
ResearchPlan + ResearchPlanExecution
```

Payload:

```text
plan_id
objectives[]
data_requirements[]
stages[]:
  stage_id
  stage_type
  purpose
  preferred_backend
  dependencies[]
evaluation_criteria
budget
assumptions[]
warnings[]
approval_requirement
```

Actions:

```text
approve
edit
reject/cancel
request_explanation
```

## E8. `research_progress`

Source:

```text
ResearchRunProjection + workshop SSE
```

Payload:

```text
run_id
plan_id
stage_id
stage_type
execution_status
progress
backend
latest_progress_message
warnings[]
blocking_reasons[]
started_at
updated_at
```

Actions:

```text
cancel
open_detail
```

## E9. `research_result`

Source:

```text
ResearchRunProjection
```

Payload:

```text
run_id
outcome
metrics grouped by category
findings[]
warnings[]
blocking_reasons[]
artifact_refs[]
evidence_refs[]
gate_impacts[]
recommended_patch_proposal_refs[]
backend mode
data cutoff
```

The card visibly labels real/fixture/stub.

## E10. `consult_result`

Payload:

```text
consultation_id
consultation_type
participant_persona_refs[]
status
consensus_summary
disagreements[]
risk_notes[]
conditions[]
evidence_refs[]
freshness
```

The private servant synthesizes; central personas do not receive unrelated raw user content.

## E11. `version_patch_proposal`

Payload binds `VersionPatchProposal`:

```text
proposal_id
base_version
change_summary[]
rationale
predicted_effects[]
validation state
warnings/conflicts
```

Actions:

```text
validate
accept
reject
open_diff
```

## E12. `version_compare`

Payload binds `VersionCompare`:

```text
base/candidate versions
field diffs
metric diffs with evidence class
risk/readiness diffs
recommendation and limitations
```

## E13. `readiness_gate`

Payload binds `StrategyReadinessAssessment`:

```text
three gates
requirement states
hard blockers
temporary assumptions
staleness
highest ready gate
```

## E14. Projection endpoint

The workshop detail response may include:

```text
cards[]
```

For pagination/streaming:

```text
GET /bff/agora/workshops/{workshop_id}/cards
```

The SSE stream carries card-update references; it need not resend every large card payload.

## E15. Frontend source-of-truth map

| Card | BFF source |
|---|---|
| User description | workshop events owner projection |
| Servant reconstruction | workshop card projection |
| Completeness/missing/next question | completeness/readiness projection |
| Research plan | research plan detail |
| Research progress/result | research run projection |
| Consult result | consultation projection |
| Patch/compare | patch and comparison routes |
| Readiness | workshop readiness route |

Pages do not parse arbitrary LLM markdown to invent these cards.

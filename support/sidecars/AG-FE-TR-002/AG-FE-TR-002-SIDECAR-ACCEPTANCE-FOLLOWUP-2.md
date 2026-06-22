# AG-FE-TR-002 Sidecar Acceptance Follow-up 2

| Field | Value |
|---|---|
| Task ID | `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-FE-TR-002` - Candidate review and entry/position/exit queues |
| Parent owner / reviewer | Claude / Codex |
| Sidecar owner / reviewer | Codex / Claude |
| Prepared by | Codex |
| Date | 2026-06-22 |
| Checked base | `origin/dev` at `fbca12b367054e416da9e0b2d221ca0924d7598b` |
| Mutates canonical truth | false |
| Status | Ready for support-only review |

## Purpose

This follow-up is a support-only refresh for `AG-FE-TR-002`. It does not
replace the approved baseline packet and does not edit L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance code, or execute-plans
frontend implementation.

The already-approved baseline remains the full acceptance packet:

- `support/sidecars/AG-FE-TR-002/AG-FE-TR-002-SIDECAR-ACCEPTANCE.md`
- archived task snapshot:
  `ai-task-archive/tasks/AG-FE-TR-002-SIDECAR-ACCEPTANCE.json`
- closeout PR: `#2269`
- baseline merge commit:
  `fffeb9ebb59f8442471d2516af447ca16040659a`

This follow-up records the current task graph after that baseline was merged,
checks whether the relevant Pantheon-side contract/support surface changed, and
keeps the parent review gates explicit while `AG-FE-TR-002` is still in
progress.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current Task Graph Snapshot

Source: `AI_NAME=Codex ./scripts/ai-status.sh show <task-id>` on 2026-06-22.

| Task | Current status | Parent impact |
|---|---:|---|
| `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | active `in_progress` | This file is the intended support-only deliverable. |
| `AG-FE-TR-002-SIDECAR-ACCEPTANCE` | archived `done` | Baseline packet was approved and merged in PR `#2269` at `fffeb9e`; keep it as the main checklist. |
| `AG-FE-TR-002` | active `in_progress` | Parent implementation is not yet review proof; this sidecar must not be treated as UI/runtime completion. |
| `AG-FE-TR-001` | archived `done` | Trading Room route/client foundation is available; final review approved contract-backed `dashboard_recipe_id` loading and expanded decision-event fields. |
| `AG-BE-CP-001` | archived `done` | Candidate pool BFF implementation is available, including A2 score components and rejected-as-negative-example retention. |
| `AG-XR-OPENAPI-004` | archived `done` | v1.3 Trading Room / governed intent OpenAPI and v4 schemas are available. |
| `AG-XR-CP-001` | archived `done` | v1.4 candidate-pool route contract and v5 schemas are available; this is the candidate review contract source. |
| `AG-BE-TR-001` | archived `done` | Trading Room aggregate and event queues are available; decision events enforce `no_order_route_proof`. |
| `AG-BE-TR-002` | archived `done` | Governed TradingIntent / handoff routes are request-only and idempotency/header-gated. |
| `AG-E2E-TR-001` | active `todo` | Downstream proof remains gated on parent `AG-FE-TR-002`; do not count this sidecar as E2E proof. |

## Delta Since Baseline

The baseline packet was merged at `fffeb9ebb59f8442471d2516af447ca16040659a`.
The checked `origin/dev` is `fbca12b367054e416da9e0b2d221ca0924d7598b`.

The relevant Pantheon-side acceptance surface has no file delta between those
points:

```bash
git diff --name-status fffeb9ebb59f8442471d2516af447ca16040659a..origin/dev -- \
  support/sidecars/AG-FE-TR-002 \
  services/control-plane/openapi/agora_v1_3.openapi.yaml \
  services/control-plane/openapi/agora_v1_4.openapi.yaml \
  services/control-plane/specs/agora/v4 \
  services/control-plane/specs/agora/v5 \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure \
  docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2 \
  .orchestrator/task-briefs/ag_fe_tr_002.md
```

Result: no output.

Conclusion: the approved baseline checklist is still current. The parent owner
can use it directly; the only follow-up adjustment is to make explicit that
`AG-FE-TR-002` remains `in_progress` and still needs real parent implementation,
tests, review, PR, and closeout before downstream E2E can start.

## Parent Acceptance Gates To Preserve

Use the approved baseline packet for the full checklist. During parent review,
do not relax these gates:

| Gate | Required review evidence |
|---|---|
| Candidate routes are canonical | Calls use v1.4 `/bff/agora/candidate-pools*` routes and existing BFF client patterns; no invented endpoint or local fixture success path. |
| Score is decomposed | `CandidateReviewDrawer` shows `raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`, `recipe_id`, `recipe_version`, `band`, `data_cutoff`, and `scored_at`; it must not collapse A2 into one final number. |
| Score components are complete | Each component exposes `component_id`, label, category, raw/normalized values when available, transform, direction, weight, contribution, missing policy, evidence refs, and explanation when present. |
| Missing/suppressed state is visible | `blockers`, missing component values, capped/suppressed bands, `park`, and `needs_research` states cannot look like a precise high-confidence score. |
| Review verbs match schema | Review request body values are only `approve_for_monitoring`, `send_to_shadow`, `needs_more_research`, `park`, or `reject`. |
| Reject/park is retained | UI state and refresh behavior preserve rejected or parked candidates as recorded decisions / negative examples, not hard deletes. |
| Queue taxonomy is schema-backed | Entry, add, reduce, exit, and review cards render from `TradingDecisionEvent.event_kind`, not a local event taxonomy. |
| Decision support is complete | Cards/details show confidence/calibration, probability target/horizon/interval, gross/cost/net/downside EV with unit/horizon, rationale, risk notes, evidence refs, invalidation, suggested action, suggested size, data cutoff, and no-order proof. |
| Actions remain request-only | Trader decisions call the accepted decision/request client path only; no broker, order, capital-binding, RuntimeBinding, or promotion approval route appears in the UI. |
| Governed handoff copy is safe | Shadow/paper/canary/live CTAs remain request/review wording, not execution wording. |
| Required write headers are present | Decision/review/handoff calls supply required `If-Match`, `Idempotency-Key`, and `X-Request-Id` headers where the OpenAPI requires them. |
| Live strict fallback is preserved | BFF unavailable/error states surface typed errors; no synthetic successful candidate review, trading decision, intent, or handoff is generated from fixtures. |
| Parent tests use schema names | Focused tests exercise v1.4 candidate score/review fields and v1.3/v4 `TradingDecisionEvent` fields with snake_case contract names and no-order proof literals. |

## Route and Schema Guardrails

These checks were reverified from current files:

| Surface | Guardrail |
|---|---|
| Candidate pool routes | v1.4 defines `/bff/agora/candidate-pools`, `/score`, `/members`, `/review`, `/discussions`, `/monitoring`, and `/monitor` route families. |
| Candidate score required fields | `candidate_id`, `pool_id`, `recipe_id`, `recipe_version`, `raw_score`, `penalty_score`, `evidence_confidence`, `effective_score`, `band`, `components`, `blockers`, `data_cutoff`, and `scored_at`. |
| Score component required fields | `component_id`, `label`, `category`, `direction`, `weight`, `contribution`, `transform`, `missing_policy`, and `evidence_refs`. |
| Candidate review decisions | `approve_for_monitoring`, `send_to_shadow`, `needs_more_research`, `park`, `reject`. |
| Trading decision event kinds | `entry`, `add`, `reduce`, `exit`, `review`. |
| Suggested actions | `enter`, `add`, `reduce`, `exit`, `review`, `no_action`. |
| Decision-event no-order proof | `agora_decision_support_only`. |
| TradingIntent no-order proof | `agora_intent_record_only`. |
| Governed handoff requested stages | `shadow`, `paper`, `canary`, `live`. |
| Governed handoff no-order proof | `agora_request_only_no_order_route`. |

## Evidence Checked

Commands run from this task branch:

| Command | Result |
|---|---|
| `git status -sb` | On `task/AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2`; before packet edits only the generated task brief was untracked. |
| `git branch --show-current` | Confirmed the expected task branch. |
| `git remote -v` | `origin` is `https://github.com/ajoe734/pantheon.git`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | Active `in_progress`, owner `Codex`, reviewer `Claude`, artifact path is this file. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-002` | Parent remains active `in_progress`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-002-SIDECAR-ACCEPTANCE` | Baseline sidecar archived `done`; PR `#2269` merged at `fffeb9e`. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-FE-TR-001` | Archived `done`; final review approved contract-backed recipe loading and expanded decision-event fields. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-CP-001` | Archived `done`; candidate pool BFF and A2 score component implementation available. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-OPENAPI-004` | Archived `done`; v1.3 OpenAPI/schema bundle available. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-XR-CP-001` | Archived `done`; v1.4 candidate pool contract available. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-001` | Archived `done`; Trading Room aggregate/event queues available. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-BE-TR-002` | Archived `done`; governed intent/handoff request-only path available. |
| `AI_NAME=Codex ./scripts/ai-status.sh show AG-E2E-TR-001` | Active `todo`; downstream E2E depends on parent completion. |
| `git diff --name-status fffeb9e..origin/dev -- <acceptance pathset>` | No relevant Pantheon-side support/OpenAPI/schema/design delta since the approved baseline. |
| `jq ... candidate_score_result.schema.json` | Required score fields and component required fields match the baseline packet. |
| `jq ... candidate_member_review.schema.json` | Review enum values match the baseline packet. |
| `jq ... trading_decision_event.schema.json` | Required decision fields, event kinds, suggested actions, and no-order proof literal match the baseline packet. |
| `jq ... governed_intent_handoff.schema.json` | Requested stages and no-order proof literal match the baseline packet. |
| `jq ... trading_intent.schema.json` | TradingIntent no-order proof literal matches the baseline packet. |

## Reviewer Guidance

Claude should review this as a support-only follow-up:

1. Approve if the packet accurately says the first sidecar remains current, no
   relevant contract/support delta landed after `fffeb9e`, and parent
   `AG-FE-TR-002` is still responsible for implementation proof.
2. Reject if this packet appears to approve the parent implementation, redefine
   canonical schema/route truth, or weaken request-only/no-order guardrails.
3. Keep downstream `AG-E2E-TR-001` gated until the parent task itself is
   reviewed, merged, and closed.

## Sidecar Acceptance Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This task creates only `support/sidecars/AG-FE-TR-002/AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2.md`. |
| No canonical truth edited | PASS | No L1 policy doc, OpenAPI, JSON schema, BFF runtime, registry/governance, or execute-plans frontend file is modified by this packet. |
| Baseline preserved | PASS | The approved `AG-FE-TR-002-SIDECAR-ACCEPTANCE.md` remains the full checklist and is not edited. |
| Dependency map refreshed | PASS | Current active/archive statuses are listed in the task graph snapshot. |
| Delta since baseline checked | PASS | Relevant Pantheon-side support/schema/OpenAPI/design pathset has no delta from `fffeb9e` to `origin/dev`. |
| Handoff target identified | PASS | Assigned reviewer is `Claude`; parent owner decides whether to absorb this packet into mainline work. |

## Non-goals

This sidecar does not:

- implement `CandidateReviewDrawer` or `TradeDecisionCard`;
- inspect or approve current execute-plans parent diffs;
- add or alter OpenAPI, JSON schema, or capability manifests;
- change BFF runtime, Registry, governance, broker, capital-binding, or
  RuntimeBinding code;
- mark parent `AG-FE-TR-002` as review-ready or complete;
- start downstream `AG-E2E-TR-001`.

*Prepared by Codex for the `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2`
support slice.*

# AG-FE-TR-002 Sidecar Acceptance Follow-up 3

| Field | Value |
|---|---|
| Task ID | `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` |
| Helper kind | `acceptance_packet` |
| Parent task | `AG-FE-TR-002` - Candidate review and entry/position/exit queues |
| Parent owner / reviewer | Claude / Codex |
| Sidecar owner / reviewer | Claude2 / Claude |
| Prepared by | Claude2 |
| Date | 2026-06-22 |
| Checked base | `origin/dev` at `c2a6a6d642ec9e1192066105cb06a1c8329c8848` |
| Mutates canonical truth | false |
| Status | Ready for support-only review |

## Purpose

This follow-up is a support-only third refresh for `AG-FE-TR-002`. It does not
replace the approved baseline packet and does not edit L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance code, or execute-plans
frontend implementation.

The approved baseline and prior follow-up remain the full acceptance packet
chain:

| Packet | Task | Merged at | PR |
|---|---|---|---|
| Baseline | `AG-FE-TR-002-SIDECAR-ACCEPTANCE` | `fffeb9ebb59f8442471d2516af447ca16040659a` | #2269 |
| Follow-up 2 | `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | `c2a6a6d642ec9e1192066105cb06a1c8329c8848` | #2272 |
| This packet | `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` | (this PR) | pending |

This follow-up records the current task graph and confirms whether the
Pantheon-side contract/support surface has changed since FOLLOWUP-2 was merged.

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

## Current Task Graph Snapshot

Source: `AI_NAME=Claude2 ./scripts/ai-status.sh show <task-id>` on 2026-06-22.

| Task | Current status | Parent impact |
|---|---:|---|
| `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` | active `in_progress` | This file is the intended support-only deliverable. |
| `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | archived `done` | Previous refresh merged in PR #2272 at `c2a6a6d`; baseline remains the main checklist. |
| `AG-FE-TR-002-SIDECAR-ACCEPTANCE` | archived `done` | Baseline packet approved and merged in PR #2269 at `fffeb9e`; remains the primary parent acceptance checklist. |
| `AG-FE-TR-002` | active `in_progress` | Parent implementation is not yet review-ready; this sidecar must not be treated as UI/runtime completion. |
| `AG-FE-TR-001` | archived `done` | Trading Room route/client foundation is available; final review approved contract-backed `dashboard_recipe_id` loading and expanded decision-event fields. |
| `AG-BE-CP-001` | archived `done` | Candidate pool BFF implementation is available, including A2 score components and rejected-as-negative-example retention. |
| `AG-XR-OPENAPI-004` | archived `done` | v1.3 Trading Room / governed intent OpenAPI and v4 schemas are available. |
| `AG-XR-CP-001` | archived `done` | v1.4 candidate-pool route contract and v5 schemas are available; this is the candidate review contract source. |
| `AG-BE-TR-001` | archived `done` | Trading Room aggregate and event queues are available; decision events enforce `no_order_route_proof`. |
| `AG-BE-TR-002` | archived `done` | Governed TradingIntent / handoff routes are request-only and idempotency/header-gated. |
| `AG-E2E-TR-001` | active `todo` | Downstream proof remains gated on parent `AG-FE-TR-002`; do not count this sidecar as E2E proof. |

## Delta Since FOLLOWUP-2

FOLLOWUP-2 was merged at `c2a6a6d642ec9e1192066105cb06a1c8329c8848`.
The checked `origin/dev` is also at `c2a6a6d642ec9e1192066105cb06a1c8329c8848`.

```bash
git diff --name-status c2a6a6d642ec9e1192066105cb06a1c8329c8848..origin/dev -- \
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

Conclusion: the approved baseline checklist is still current. The `origin/dev`
HEAD is the FOLLOWUP-2 merge commit itself; no new commits have landed since.
The parent owner can continue using the baseline packet directly. The only
follow-up adjustment is to confirm again that `AG-FE-TR-002` remains
`in_progress` and still needs real parent implementation, tests, review, PR,
and closeout before downstream `AG-E2E-TR-001` can start.

## Schema Spot Checks (Reverified)

All schema values match the baseline and FOLLOWUP-2 packets. No changes.

| Command | Result |
|---|---|
| `jq '.required' services/control-plane/specs/agora/v5/candidate_score_result.schema.json` | `["candidate_id","pool_id","recipe_id","recipe_version","raw_score","penalty_score","evidence_confidence","effective_score","band","components","blockers","data_cutoff","scored_at"]` |
| `jq '.properties.decision.enum' services/control-plane/specs/agora/v5/candidate_member_review.schema.json` | `["approve_for_monitoring","send_to_shadow","needs_more_research","park","reject"]` |
| `jq '.properties.no_order_route_proof.enum' services/control-plane/specs/agora/v4/trading_decision_event.schema.json` | `["agora_decision_support_only"]` |
| `jq '.properties.no_order_route_proof.enum' services/control-plane/specs/agora/v4/governed_intent_handoff.schema.json` | `["agora_request_only_no_order_route"]` |
| `jq '.properties.no_order_route_proof.enum' services/control-plane/specs/agora/trading_intent.schema.json` | `["agora_intent_record_only"]` |

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

## Evidence Checked

Commands run from this task branch:

| Command | Result |
|---|---|
| `git branch --show-current` | `task/AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3`; confirmed expected branch. |
| `git status --short` | Only untracked task brief before packet edits. |
| `git remote -v` | `origin` is `https://github.com/ajoe734/pantheon.git`. |
| `git rev-parse origin/dev` | `c2a6a6d642ec9e1192066105cb06a1c8329c8848` (FOLLOWUP-2 merge commit). |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3` | Active `in_progress`, owner `Claude2`, reviewer `Claude`, artifact path is this file. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-TR-002` | Parent remains active `in_progress`; owner `Claude`, reviewer `Codex`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-2` | Archived `done`; PR #2272 merged at `c2a6a6d`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-FE-TR-002-SIDECAR-ACCEPTANCE` | Archived `done`; PR #2269 merged at `fffeb9e`. |
| `AI_NAME=Claude2 ./scripts/ai-status.sh show AG-E2E-TR-001` | Active `todo`; downstream E2E depends on parent completion. |
| `git diff --name-status c2a6a6d..origin/dev -- <acceptance pathset>` | No output; no relevant delta since FOLLOWUP-2. |
| `jq ... candidate_score_result.schema.json` | Required fields match FOLLOWUP-2 packet. |
| `jq ... candidate_member_review.schema.json` | Enum values match FOLLOWUP-2 packet. |
| `jq ... trading_decision_event.schema.json` | No-order proof literal matches FOLLOWUP-2 packet. |
| `jq ... governed_intent_handoff.schema.json` | Handoff no-order proof literal matches FOLLOWUP-2 packet. |
| `jq ... trading_intent.schema.json` | TradingIntent no-order proof literal matches FOLLOWUP-2 packet. |

## Reviewer Guidance

Claude should review this as a support-only follow-up:

1. Approve if the packet accurately confirms that FOLLOWUP-2 remains current,
   no relevant contract/support delta has landed since `c2a6a6d`, and parent
   `AG-FE-TR-002` is still responsible for implementation proof.
2. Reject if this packet appears to approve the parent implementation, redefine
   canonical schema/route truth, or weaken request-only/no-order guardrails.
3. Keep downstream `AG-E2E-TR-001` gated until the parent task itself is
   reviewed, merged, and closed.

## Sidecar Acceptance Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | This task creates only `support/sidecars/AG-FE-TR-002/AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3.md`. |
| No canonical truth edited | PASS | No L1 policy doc, OpenAPI, JSON schema, BFF runtime, registry/governance, or execute-plans frontend file is modified by this packet. |
| Baseline and FOLLOWUP-2 preserved | PASS | Both prior packets are not edited; baseline `AG-FE-TR-002-SIDECAR-ACCEPTANCE.md` remains the full checklist. |
| Dependency map refreshed | PASS | Current active/archive statuses are listed in the task graph snapshot. |
| Delta since FOLLOWUP-2 checked | PASS | Relevant Pantheon-side support/schema/OpenAPI/design pathset has no delta from `c2a6a6d` to `origin/dev` (same commit). |
| Schema spot checks reverified | PASS | All five schema spot checks match FOLLOWUP-2 documented values; no schema changes detected. |
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

*Prepared by Claude2 for the `AG-FE-TR-002-SIDECAR-ACCEPTANCE-FOLLOWUP-3`
support slice.*

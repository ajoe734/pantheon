# Dispatchable Briefs — Seed Pipeline Close-out (EPIC DATASTRAT-SEEDFLOW)

Generated: 2026-06-12
Companion to: `GAP_ADDENDUM_2026-06-12.md` (gaps #1, #2, #5)
Goal: make the already-delivered seed backbone *usable* — let a `StrategySpecSeed`
move into replication, and give humans a review surface. These two briefs are
ready to paste into the orchestrator.

Baseline (verified on `dev` @ `0d9fe586`):
- `services/source_ingestion/strategy_seed_store.py` — `StrategySpecSeedStore`
  (JSONL), status enum `draft / promoted_to_strategy_spec / rejected`.
- `services/source_ingestion/seed_materializer.py` — `SeedMaterializationService`.
- `services/research/strategy_spec/conversion.py` — builds review artifact only;
  explicitly does **not** launch experiments.
- Research orchestrator owns `ExperimentTask` / replication queue (separate svc).

---

## DATASTRAT-SEEDFLOW-001 — Seed -> Replication bridge

- Owner: Codex · Reviewer: Claude · Phase: EPIC DATASTRAT-SEEDFLOW / Replication bridge
- Depends on: none (backbone already merged)

### Summary
Wire an accepted `StrategySpecSeed` through to a research `ExperimentTask` /
replication-queue submission, **without** letting the seed path create execution
routes or registry-approved artifacts. The bridge is one-directional:
`StrategySpecSeed (promoted) -> StrategySpec candidate -> replication submission`,
returning a `replication_ref` recorded on the seed lineage.

### Scope / deliverables
1. `services/source_ingestion/replication_bridge.py` (or
   `services/research/strategy_spec/replication_submit.py` if the team prefers
   the research side to own submission) — a `submit_seed_to_replication(seed_id)`
   that: loads the seed, asserts `status == promoted_to_strategy_spec`, calls the
   existing `conversion.py` to scaffold the `StrategySpec` candidate, and submits
   a replication task to the research orchestrator's existing queue API.
2. Persist `replication_ref` + `experiment_task_id` back onto the seed lineage
   (extend store, do not break existing schema; lineage is currently an empty
   placeholder — fill it).
3. BFF endpoint: `POST /bff/management/strategy-seeds/{seed_id}/submit-replication`
   (202, operator role, idempotent via Idempotency-Key) returning the
   replication ref. Read-role users cannot submit.

### Acceptance criteria
- A seed in `promoted_to_strategy_spec` can be submitted and produces a real
  replication/ExperimentTask ref from the research orchestrator (not a stub).
- A seed in `draft` or `rejected` is refused with a typed error.
- Invariant preserved: submission creates **no** DeploymentPlan / RuntimeBinding /
  approved artifact / execution route. Add a test asserting
  `registry_write_performed == false` and `execution_route == none` on all
  artifacts produced by the bridge.
- Idempotent: re-submitting the same seed returns the same `replication_ref`.
- Tests: `services/source_ingestion/tests/test_replication_bridge.py` covering
  happy path, wrong-status refusal, idempotency, and the no-execution-route guard.

### Out of scope
- Promotion gating policy (who may promote a seed) — that is SEEDFLOW-002.
- Any change to runtime-manager / LEAN.

---

## DATASTRAT-SEEDFLOW-002 — Strategy Seed Review Inbox (read model + actions)

- Owner: Claude · Reviewer: Codex · Phase: EPIC DATASTRAT-SEEDFLOW / Seed review inbox
- Depends on: DATASTRAT-SEEDFLOW-001 (submit action target)

### Summary
Give operators a governed review surface over `StrategySpecSeed`s. Today the
store only has 3 statuses and no review actions; this adds the accept / reject /
merge / request-evidence / convert-to-spec-seed / submit-replication actions and
a BFF read model (the "Strategy Seed Inbox").

### Scope / deliverables
1. Extend `StrategySpecSeedStore` with review actions and a `SeedReviewDecision`
   record (reviewer_id, decision, reason, target refs, created_at). Add statuses
   needed for review (`needs_more_evidence`, `accepted`, `merged`,
   `archived_as_insight`) without breaking existing `draft/promoted/rejected`.
2. BFF read model: `GET /bff/management/strategy-seeds` (filter by status,
   source_kind, strategy_family, min_confidence) + `GET .../strategy-seeds/{id}`
   returning a seed card (source, seed_kind, hypothesis, market/asset,
   required_data, evidence_count, similar_existing_strategies, recommended_action,
   review_status, allowedActions).
3. BFF command endpoints (202, operator role, idempotent):
   `POST .../strategy-seeds/{id}/review` (accept/reject/request-evidence/
   convert-to-spec-seed/archive), `POST .../{id}/merge`. `submit-replication`
   reuses SEEDFLOW-001.
4. Wire the persona discovery advisory action (gap #5): when a persona match
   recommends `promote_seed_candidate`, surface it in the inbox as a suggested
   action — but the human/operator still makes the decision.

### Acceptance criteria
- Inbox lists seeds with correct filters and a populated seed card.
- Each review action transitions status per a documented state machine and writes
  a `SeedReviewDecision` audit record.
- Accept -> convert-to-spec-seed makes the seed eligible for SEEDFLOW-001 submit.
- Reject / archive are terminal and refuse further transitions.
- Read-role users can read the inbox but cannot execute actions.
- A persona `promote_seed_candidate` recommendation appears as a *suggestion*,
  never auto-promotes.
- Tests covering: listing/filtering, each transition, terminal-state refusal,
  audit-record creation, role enforcement.

### Out of scope
- Frontend / Lovable inbox UI (separate FE packet once the read model is live).
- Negative-memory matching on the seed card (lands with the interaction EPIC, #4).

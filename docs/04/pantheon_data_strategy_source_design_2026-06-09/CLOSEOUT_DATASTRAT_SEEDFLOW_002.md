# Closeout: DATASTRAT-SEEDFLOW-002

Owner: Claude2
Reviewer: Codex
Date: 2026-06-12
Status: owner finalization complete

## Delivered Scope

`DATASTRAT-SEEDFLOW-002` adds a governed review surface to `StrategySpecSeed`
objects. The delivery comprises three layers:

### Store layer (`services/source_ingestion/strategy_seed_store.py`)

- `SeedReviewDecisionAction` enum: accept / reject / request_evidence /
  convert_to_spec_seed / archive / merge.
- `SeedReviewDecision` audit record dataclass with idempotency fields
  (`idempotency_key`, `request_hash`).
- `StrategySpecSeedReviewError` typed error with `code` attribute.
- `StrategySpecSeedStore.record_review_decision()` — applies a governed
  transition, appends an audit record to seed lineage, enforces the state
  machine, and preserves the no-execution-route invariant.
- `StrategySpecSeedStore.merge_seed()` — handles the merge action with
  `merged_into_seed_id` lineage tracking.
- Terminal statuses (`rejected`, `archived_as_insight`, `merged`) refuse
  further review transitions.

### BFF read model (`GET /bff/management/strategy-seeds[/{id}]`)

- Seed card: source, seed_kind, hypothesis, market/asset, required_data,
  evidence_count, similar_existing_strategies, recommended_action,
  review_status, allowedActions.
- Filter parameters: status, source_kind, strategy_family, min_confidence.
- Persona `promote_seed_candidate` recommendation surfaces as a *suggestion*
  (`mode: suggestion`, `auto_promote: false`) — human/operator retains the
  decision.

### BFF command endpoints (202, operator role, idempotent)

```text
POST /bff/management/strategy-seeds/{seed_id}/review
POST /bff/management/strategy-seeds/{seed_id}/merge
```

- Review actions: accept, reject, request-evidence, convert-to-spec-seed, archive.
- `convert-to-spec-seed` transitions to `promoted_to_strategy_spec` and
  surfaces `submit-replication` in `allowedActions` (SEEDFLOW-001 target).
- Read-role users may read the inbox but cannot execute commands (403).

## Review Record

Codex approved the implementation after PRs #1369 and #1372 merged to `dev`.

- PR #1369 (`2a5b1cbe`): add seed review inbox — store review actions, BFF
  read model and command endpoints, persona suggestion wiring.
- PR #1372 (`62f49c93`): fix seed review idempotency — durable store-level
  replay when BFF idempotency cache is cleared between requests.

## Final Verification

Owner closeout re-ran the full test suite for the delivered scope:

```bash
pytest services/control-plane/bff/test_datastrat_seed_review_bff.py \
       services/source_ingestion/tests/test_strategy_seed_store.py -q
```

Result: 27 passed in 6.93s.

```bash
python3 -m py_compile \
  services/source_ingestion/strategy_seed_store.py \
  services/control-plane/bff/test_datastrat_seed_review_bff.py
```

Result: passed.

## Non-Scope

- No DeploymentPlan, RuntimeBinding, execution route, or approved registry
  artifact is created by any review action. The invariant
  `registry_write_performed == false` and `execution_route == none` is
  enforced in every review transition.
- No frontend / Lovable inbox UI (separate FE packet once the read model is live).
- Negative-memory matching on the seed card deferred to the interaction EPIC.

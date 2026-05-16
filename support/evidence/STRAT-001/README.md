# STRAT-001 Evidence

Task: StrategySpec schema / model
Owner: Codex
Reviewer: Claude

## Scope

- Added a typed StrategySpec domain model in `services/research/strategy_spec/models.py`.
- Strengthened the canonical StrategySpec JSON schema at `services/control-plane/specs/strategy_spec.schema.json` with non-empty core strings, lifecycle state, `source_record` dependency refs, and canary execution hints.
- Re-exported the model helpers from `services/research/strategy_spec/__init__.py`.
- Routed the RS-002 normalizer's StrategySpec validation through the shared schema helper without changing its output shape.
- Documented the model/schema relationship in `services/research/strategy_spec/README.md`.

## Verification

```bash
python3 -m py_compile services/research/strategy_spec/models.py services/research/strategy_spec/normalizer.py services/research/strategy_spec/__init__.py
python3 -m pytest services/research/strategy_spec -q
git diff --check -- services/research/strategy_spec/models.py services/research/strategy_spec/test_models.py services/research/strategy_spec/normalizer.py services/research/strategy_spec/__init__.py services/research/strategy_spec/README.md services/control-plane/specs/strategy_spec.schema.json
```

Result:

- `py_compile`: passed
- `pytest services/research/strategy_spec -q`: 10 passed
- `git diff --check`: passed

## Worktree Boundary

The repository already had unrelated dirty files from other active tasks and generated orchestrator state before this implementation. STRAT-001-owned changes are limited to the files listed in Scope plus this evidence note.

## Redispatch Finalization

At 2026-05-16T08:43:01Z, STRAT-001 was redispatched for owner finalization after L0 state returned to `review_approved` even though prior task-scoped commits existed:

- `75723d4c` - STRAT-001 implementation commit for the schema-backed model, strengthened schema, shared validation, tests, and evidence.
- `3c77cb97` - prior STRAT-001 archive closeout commit.

Current revalidation:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile services/research/strategy_spec/models.py services/research/strategy_spec/normalizer.py services/research/strategy_spec/__init__.py
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest services/research/strategy_spec -q
git diff --check -- services/research/strategy_spec/models.py services/research/strategy_spec/test_models.py services/research/strategy_spec/normalizer.py services/research/strategy_spec/__init__.py services/research/strategy_spec/README.md services/control-plane/specs/strategy_spec.schema.json support/evidence/STRAT-001/README.md support/evidence/STRAT-001/review-claude.md
```

Result:

- `py_compile`: passed
- `pytest services/research/strategy_spec -q`: 21 passed
- `git diff --check`: passed
- No new uncommitted STRAT-001-owned code/schema diff existed before this evidence-only finalization note.

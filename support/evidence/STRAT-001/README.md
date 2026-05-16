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

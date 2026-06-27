# LOOP-AUTO-000 Evidence

Task: `LOOP-AUTO-000`
Owner: `Codex`
Reviewer: `Claude`

## Delivered Artifact

- `docs/deployment/loop-catalog.schema.json`
- `docs/deployment/loop-catalog.registry.json`
- `tests/test_loop_catalog_registry.py`

The registry publishes the Wave 0 static catalog substrate from SA-21. It gives
each L1 loop a stable `loop_id`, current maturity, target maturity, owner,
desired-state sources, actual-state sources, controller contract placeholder,
truth-level evidence profile, and follow-up execution task path.

## Boundary

This task does not raise any loop to `reconciled` or `proven-live`. The schema
allows those claims only when the entry has implemented controller queries,
restart behavior, liveness metric, and the matching evidence profile status.

## Validation

Run on 2026-06-27:

```bash
python3 -m json.tool docs/deployment/loop-catalog.schema.json >/tmp/loop-catalog.schema.pretty.json
python3 -m json.tool docs/deployment/loop-catalog.registry.json >/tmp/loop-catalog.registry.pretty.json
pytest -q tests/test_loop_catalog_registry.py
```

Result:

```text
7 passed in 1.31s
```

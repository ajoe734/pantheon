# P0-LEAN-CTX-001 Sidecar Acceptance

## Scope

Implemented Pantheon runtime context access and event attachment in the official `pantheon/lean` bridge:

- `lean/Algorithm.Python/pantheon_algo/base.py`
- `lean/Algorithm.Python/pantheon_algo/test_base.py`

## Delivered Behavior

- `PantheonAlgoBase.Initialize()` loads `PantheonRuntimeContext` from `PANTHEON_LAUNCH_MANIFEST` when present, otherwise from whitelisted `PANTHEON_*` env vars.
- `get_pantheon_context()` exposes the loaded runtime context to LEAN algorithms and downstream telemetry code.
- `emit_pantheon_event()` builds event payloads with automatic context fields:
  - `runtime_binding_id`
  - `runtime_id`
  - `deployment_plan_id`
  - `deployment_stage`
  - artifact id/version/checksum and strategy id
  - capital pool and persona capital binding ids
  - engine bridge repo/path/commit and adapter version
  - trace/correlation ids and context source
- Missing context remains degraded/visible for dev and paper, via `RuntimeContextMissing`.
- Missing context fails closed for deployment-managed stages/roles: staging, canary, live, prod, production.

## Verification

```bash
PYTHONPATH=lean/Algorithm.Python:. python3 lean/Algorithm.Python/pantheon_algo/test_base.py -v
pytest -q services/execution/lean_runtime/test_runtime_context.py
```

Results:

- `PantheonAlgoBase` bridge tests: 3 passed.
- Runtime context model tests: 11 passed.

Note: an initial `python3 -m unittest lean/Algorithm.Python/pantheon_algo/test_base.py -v` invocation failed because `unittest` interpreted the dotted directory name `Algorithm.Python` as a module path. The direct file invocation above is the valid focused command for this bridge test.

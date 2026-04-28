# SVC-RUNTIME-CONTROL — Claude Implementation Handoff

- Date: 2026-04-28
- Owner: Claude
- Reviewer: Gemini
- Source packet: docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/execution-materialization.md
- Branch: feat/claude-execution-control

## Acceptance Coverage

1. **runtime-control is exposed as a deployable service with a stable port and health surface.** The `runtime-manager` Flask app at `services/runtime-manager/main.py` already exposes `/__health__` on container port `8081` (host `18081`) per the SVC-BASELINE port map. This change folds the legacy operator-facing `/api/internal/v1/...` command paths onto the same Flask app so the deployable container is the single command plane. See `services/runtime-manager/internal_api_routes.py`.
2. **Evolution approval/action no longer terminate in local BFF placeholders.** The BFF's `command_executor` already routes evolution approve/reject/execute commands to `${PANTHEON_GOVERNANCE_API_URL}` (or `PANTHEON_EVOLUTION_API_URL`). Compose now wires `operator-bff` to the real evolution service at `http://evolution:8093` and adds the corresponding `depends_on` edge so commands cannot be issued before the evolution service is healthy.

## What Changed

### `services/runtime-manager/internal_api_routes.py` (new)
- Defines `_InProcessRuntimeManagerAdapter` exposing the small `RuntimeManagerClient` surface (`get` / `transition` / `retire`) used by the legacy operator routes, backed by the runtime-manager's in-process `RuntimeManagerService`. The adapter resolves the live service through a factory so test harnesses that reset `_svc` keep working.
- Defines `_SharedKillSwitchProxy` that forwards attribute access to the in-process `KillSwitchController` and persists the durable kill-switch snapshot after every `dispatch` / `advance_safe_mode` call. This keeps the legacy operator path coherent with the canonical `/api/kill-switch/dispatch` route both in memory and on disk.
- `register_internal_api_routes(app, get_service)` patches the legacy module's enum and error globals to the canonical `kill_switch_controller` / `runtime_manager_client` symbols, copies its URL rules onto the runtime-manager Flask app under `legacy_*` endpoints, and re-reads `PANTHEON_COMMAND_STATE_FILE` so operators (and tests) can override the command-state file path. `legacy._RuntimeManagerClient` is intentionally **not** overridden so smoke tests that null-out `_runtime_manager_client` continue to materialise the canonical HTTP client.

### `services/runtime-manager/main.py`
- Calls `register_internal_api_routes(app, _get_service)` after the canonical routes are defined so the runtime-manager process serves both surfaces from one Flask app.

### `services/runtime-manager/test_internal_api_routes.py` (new)
- Covers the deployable surface end-to-end: legacy pause/rollback drive the same RuntimeBindingStore the canonical routes read, legacy kill-switch dispatch survives a process restart (the new persist-after-dispatch wrapper), and legacy command lookups land in the configured command-state file.

### `docker-compose.yml`
- `runtime-manager`: pin `PANTHEON_COMMAND_STATE_FILE=/data/runtime/internal_api_commands.json` and `PANTHEON_RUNTIME_CONSULTATION_DATA_DIR=/data/runtime/consultation` so the legacy command surface and consultation sponsor handoff land on the runtime-data volume rather than `/tmp`.
- `operator-bff`: add `PANTHEON_RUNTIME_MANAGER_URL=http://runtime-manager:8081`, `PANTHEON_RUNTIME_MANAGER_TOKEN=runtime-control-internal`, and `PANTHEON_GOVERNANCE_API_URL=http://evolution:8093` so the BFF's in-process `RuntimeManagerClient` reaches the canonical service over HTTP and evolution approve/reject/execute commands hit the real evolution service. Adds `depends_on: evolution: service_healthy`.

## Tests

```
PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3.12 -m pytest \
  services/runtime-manager/ \
  services/control_plane/ \
  services/control-plane/bff/test_command_executor.py \
  services/control-plane/bff/test_cw01_consult_request_contract.py \
  services/control-plane/bff/test_cw03_committee_board_contract.py
# 97 passed in 4.67s

PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3.12 -m pytest \
  services/governance/ services/evolution/
# 75 passed in 3.16s

PYTHONPATH=/home/edna/.local/lib/python3.12/site-packages python3.12 \
  services/runtime-manager/smoke_test.py
# 138 passed, 0 failed out of 138 checks

docker compose config --quiet
# exit 0
```

## Reviewer Focus

- Confirm that mounting the legacy `services.control_plane.internal_api` Flask app onto the runtime-manager process is acceptable as the SVC-RUNTIME-CONTROL convergence step, given the open planning question on whether evolution approval/action should also live in `runtime-control` vs `governance-api`. This change leaves evolution-decision approve/reject/execute on the governance-owned `evolution` service via `PANTHEON_GOVERNANCE_API_URL`, and only adopts runtime/binding/kill-switch/sponsor/command-status routes onto runtime-manager.
- Validate that the kill-switch persistence wrapper (`_SharedKillSwitchProxy._PERSIST_AFTER`) is the right durability stance for legacy-path dispatches. The runtime-manager's foundation idempotency layer in `service.execute_kill_switch` is intentionally **not** replicated here — that convergence belongs to a follow-up.
- Sanity-check the new compose env for `operator-bff`. With `PANTHEON_GOVERNANCE_API_URL=http://evolution:8093` and `depends_on: evolution`, evolution approve/reject/execute commands now reach the real service; previously they would have failed with `COMMAND_BACKEND_UNCONFIGURED` because the env was unset.

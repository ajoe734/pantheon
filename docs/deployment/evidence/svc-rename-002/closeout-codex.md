# SVC-RENAME-002 Closeout — Codex

**Owner:** Codex
**Reviewer:** Claude2
**Date:** 2026-05-12
**Task:** Execute `services/control_plane` -> `services/control-plane/internal` migration (Pair A)

## Finalization Summary

Pair A remains in the approved state:

- Legacy implementation files live under `services/control-plane/internal/`.
- `services.control_plane.internal.*` provides the executable import target.
- Flat legacy wrappers under `services/control_plane/internal_api*.py` remain compatibility shims only.
- Runtime-manager and smoke import sites use the new executable shim package.
- Pair E feedback split remains deferred to SVC-RENAME-003.

## Closeout Verification

Commands run during owner closeout:

```bash
/tmp/pantheon-svc-rename-002-venv/bin/python -m pytest -q services/control-plane/internal/test_internal_api_incident.py services/runtime-manager/test_internal_api_routes.py services/runtime-manager/test_runtime_hardening.py
/tmp/pantheon-svc-rename-002-venv/bin/python tests/run_internal_api_smoke.py
python3 -m compileall -q services/control-plane services/control_plane tests/run_internal_api_smoke.py
docker compose -f docker-compose.yml config --quiet
docker compose -f docker-compose.control.yml config --quiet
rg -n "services\\.control_plane\\.internal_api|services\\.control_plane\\.internal_api_min|from services\\.control_plane import internal_api|from services\\.control_plane import internal_api_min" services tests scripts
```

Results:

- `pytest`: 40 passed, 4 existing datetime deprecation warnings.
- Internal API smoke: `SMOKE OK`.
- `compileall`: clean.
- Both docker compose config checks: clean.
- Old-style flat internal API import search: no matches.

## Worktree Separation

The closeout commit stages only SVC-RENAME-002 code and task evidence. Existing dirty work outside this task, including BFF persona changes, QLIB sidecar edits, generated orchestrator task briefs, and unrelated state/archive files, is intentionally left unstaged.

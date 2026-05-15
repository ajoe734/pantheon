# SVC-RENAME-001 Codex2 Review

Reviewer: Codex2
Date: 2026-05-10
Disposition: changes requested

## Scope Reviewed

- `docs/architecture/services-namespace-migration-map-2026-05-10.md`
- Current `services/` directory layout
- Import and path references for the proposed rename/move surfaces
- Docker Compose feedback/source-ingest references

## Blocking Findings

1. Pair E omits an active downstream consumer of `services/control-plane/feedback`.

   `services/telemetry/feedback_adapter.py` inserts `services/control-plane` into `sys.path` and imports `feedback.store`. Moving `services/control-plane/feedback` to `services/trader-feedback` would break that import unless the migration plan defines a Python-importable package or compatibility shim and rewrites the telemetry adapter. The current Pair E import/rewrite table only covers Docker Compose and the feedback service's own files, so it does not satisfy the downstream-consumer/risk-table part of the task acceptance.

2. Pair J incorrectly describes `services/research/trl` as existing.

   The current tree has no `services/research/trl` directory. `services/research/dspy` and `services/research/imitation` contain only `Dockerfile` and `requirements.txt`, while `services/learning/trl` contains the executable TRL implementation. Pair J should be corrected to say `learning/trl` would move into a new `services/research/trl` target, not merge with or replace an existing research TRL implementation.

3. Pair A needs an executable Python import/shim layout.

   The proposed new import path `services.control_plane.internal.*` cannot be backed merely by moving files under `services/control-plane/internal`, because `control-plane` is not importable as a normal Python package. The map already notes the hyphen risk, but the migration table should spell out the actual transition layout: either real shim modules under `services/control_plane/internal/`, an explicit `__path__` extension/importlib loader, or a different import target. Without that, the import rewrite rules are not actionable.

## Verification Commands

- `find services -maxdepth 2 -type d`
- `find services/research -maxdepth 2 -type d`
- `find services/research/dspy services/research/imitation -maxdepth 2 -type f`
- `rg -n "trl|services\\.research\\.trl|services/learning/trl|services\\.learning\\.trl" services/research services/learning scripts docs -g '!**/__pycache__/**'`
- `sed -n '1,140p' services/telemetry/feedback_adapter.py`
- `rg -n "dockerfile: services/(control-plane/feedback|feedback|source_ingestion)|source-ingest:|feedback:" docker-compose*.yml`
- `git diff --cached --name-status`

`git diff --cached --name-status` showed only the migration-map doc plus generated state files staged for this task; no application code changes were part of the reviewed deliverable.

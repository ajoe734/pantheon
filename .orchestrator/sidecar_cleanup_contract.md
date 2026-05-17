# Sidecar Cleanup Contract

Task: `OPS-SIDECAR-CLEANUP-001`
Owner: `Codex`
Reviewer: `Claude`

## Scope

`.orchestrator/sidecar_cleanup.py` owns retention planning and cleanup for `support/sidecars/` packets. It is intentionally independent of supervisor runtime state so cron, chair-review, or a manual operator can invoke it safely.

## Retention Policy

- Default archive threshold: `14` days after the parent task reaches terminal `done`.
- Default delete threshold: `60` days after the parent task reaches terminal `done`.
- Parent terminal truth comes from `ai-task-archive/tasks/<task-id>.json`; `ai-status.json` is only a fallback for rare terminal tasks that have not yet been archived.
- Unknown, active, or timestamp-invalid parents are kept.
- `support/sidecars/archived/` is skipped for archive moves and scanned only for delete-eligible packets.

## Python API

- `scan(...) -> CleanupPlan`: scans immediate sidecar packet directories and returns a full plan.
- `classify(sidecar_path, ...) -> CleanupItem`: classifies one packet as `keep`, `archive`, or `delete`.
- `execute(plan, dry_run=True)`: returns the original `CleanupPlan` without filesystem changes.
- `execute(plan, dry_run=False) -> ExecutionResult`: creates `support/sidecars/archived/` as needed, moves archive-eligible packets there, and deletes delete-eligible packets.

## CLI

Default mode is dry-run:

```bash
python3 .orchestrator/sidecar_cleanup.py
```

Apply the plan:

```bash
python3 .orchestrator/sidecar_cleanup.py --execute
```

Useful options:

- `--archive-after-days N`
- `--delete-after-days M`
- `--sidecars-root PATH`
- `--archive-tasks-dir PATH`
- `--status-path PATH`
- `--now ISO_TIMESTAMP` for deterministic verification
- `--no-archived-scan` to skip deletion checks under `support/sidecars/archived/`

## Safety Guarantees

- Dry-run is the default and performs no filesystem writes.
- Archive destinations are made unique if a same-named archived packet already exists.
- The module never edits `ai-status.json`, `current-work.md`, or `ai-activity-log.jsonl`.
- Deletion is limited to packet paths discovered under the configured sidecar root and classified from terminal parent task age.

## Verification

Focused test:

```bash
python3 -m pytest .orchestrator/test_sidecar_cleanup.py
```

The test covers fresh, stale-but-archivable, and beyond-deletion packet ages; dry-run no-op behavior; real archive/delete execution; and CLI dry-run exit code `0`.

Closeout verification on 2026-05-17:

```bash
python3 -m pytest .orchestrator/test_sidecar_cleanup.py
```

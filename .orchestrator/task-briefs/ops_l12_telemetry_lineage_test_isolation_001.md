# Task Brief: OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Isolate telemetry lineage full-stack test from ambient runtime-manager configuration
- Status: review_approved
- Owner: Claude
- Reviewer: Codex2
- Next: Codex2 independent review approved: clean and hostile ambient runs each pass 7/7; baseline 193 tests/3 errors/1 skip versus merged 197 tests/2 pre-existing loader errors/1 skip, with 194 pass-equivalent and 3 deprecation plus 1 ResourceWarning observed; production RuntimeManagerClient remains unchanged and fail-closed guard passes; env/tempdir cleanup passes; test SHA matches manifest; schema and checksums pass; PRs #4213, #4214, #4216 are merged to dev with all required checks green.

## Summary
修正 telemetry lineage full-stack 測試對 ambient PANTHEON_RUNTIME_MANAGER_URL 的隱性依賴；測試必須自行建立明確、隔離、fail-closed 相容的 runtime-manager fixture，讓乾淨環境可重現且不得降低 production fail-closed 行為。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner Closeout Verification (2026-07-26)

Re-verified by the owner on the approved deliverable before `done`. No
product or evidence file was modified during closeout; only this brief was
brought in sync with the approved canonical row.

- Canonical row read through the governed command root:
  `AI_NAME=Claude "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" show OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001`
  → `status=review_approved`, `owner=Claude`, `reviewer=Codex2`,
  `review_file=docs/deployment/evidence/twelve-loop-gap/OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001/evidence.json`
  (reviewer-bound; carried forward unchanged at `done`).
- Delivery is merged: task branch tip `70d657917` is an ancestor of
  `origin/dev` (`git merge-base --is-ancestor HEAD origin/dev`); PRs #4213,
  #4214, #4216 all merged into `dev`.
- Focused test, clean ambient env:
  `env -u PANTHEON_RUNTIME_MANAGER_URL /home/lupin/pantheon/.venv/bin/python3 -m unittest services.telemetry.test_lineage_write_path`
  → Ran 7 tests, OK.
- Focused test, hostile ambient env:
  `PANTHEON_RUNTIME_MANAGER_URL=http://198.51.100.7:8099 PANTHEON_RUNTIME_MANAGER_TOKEN_FILE=/nonexistent/token INCIDENTS_DATA_DIR=/nonexistent/ambient/incidents /home/lupin/pantheon/.venv/bin/python3 -m unittest services.telemetry.test_lineage_write_path`
  → Ran 7 tests, OK.
- Evidence integrity: `sha256sum` of `evidence.json`
  (`56a6d610f35e345525a40062eb62a39108a4c8a1d2c8d2c14e3b5cdd39f5874f`) and
  `README.md` (`d8729321914da6b2bdeab1bcbd1c734335f1d73b15bf9c70b1f693591d7468d5`)
  match `evidence.sha256`; the manifest's recorded source SHA for
  `services/telemetry/test_lineage_write_path.py`
  (`12e1159b8ad9b7ebc05b50ce466ba98cdf7ac84d2b2d1a53b6e6e14edb294b4c`)
  matches the worktree file.
- Evidence schema: `evidence.json` validates against
  `schemas/product-evidence.schema.json` (Draft 2020-12).

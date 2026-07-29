# Task Brief: OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Isolate telemetry lineage full-stack test from ambient runtime-manager configuration
- Status: review
- Owner: Claude
- Reviewer: Codex2
- Next: Delivery complete and merged to dev; handed off to Codex2 for independent review. PR #4213 squash-merged as 0410a89f0 (implementation + evidence) and PR #4214 merged as f687d7aeb (evidence re-cut with merge facts), all Branch CI Gate and Orchestrator Sync checks green. Review evidence manifest: docs/deployment/evidence/twelve-loop-gap/OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001/evidence.json (validates clean against schemas/product-evidence.schema.json). Root cause: the full-stack case built RuntimeManagerClient() with no allow_local opt-in and imported services.incidents.main whose module-level CanonicalReferenceValidator() builds the same default client, so both resolved transport from ambient PANTHEON_RUNTIME_MANAGER_URL and failed closed in any clean workspace. Fix is test-owned only: RuntimeManagerClient(allow_local=True), per-test tempdir for binding store, bearer token and INCIDENTS_DATA_DIR, ambient URL/TOKEN_FILE/TIMEOUT_SECONDS cleared, and an inert unroutable URL bound solely for the services.incidents.main import whose validator is replaced before any request. No production file changed; fail-closed default is now guarded by test_default_runtime_manager_client_stays_fail_closed_without_url and no-leakage is guarded by TestFullStackFixtureIsolation. Verified on merged dev tip: services.telemetry.test_lineage_write_path 7 tests OK in clean and hostile ambient env; services/telemetry discover 193 tests/3 errors/1 skip baseline to 197 tests/2 errors/1 skip, the 2 residual being pre-existing test_capture and test_feedback_adapter loader errors, all four re-run independently by the owner on dev tip f687d7aeb. The manifest previously named the pre-reassignment reviewer Antigravity; it is now rebound to Codex2, with the Human/Ops reassignment recorded in record_log. Ready for approve with REVIEW_FILE=docs/deployment/evidence/twelve-loop-gap/OPS-L12-TELEMETRY-LINEAGE-TEST-ISOLATION-001/evidence.json.

## Summary
修正 telemetry lineage full-stack 測試對 ambient PANTHEON_RUNTIME_MANAGER_URL 的隱性依賴；測試必須自行建立明確、隔離、fail-closed 相容的 runtime-manager fixture，讓乾淨環境可重現且不得降低 production fail-closed 行為。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

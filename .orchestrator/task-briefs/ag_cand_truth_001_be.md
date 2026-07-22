# Task Brief: AG-CAND-TRUTH-001-BE

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Complete Agora candidate provenance projection
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Review fixes and dev composition are complete through the current branch HEAD; push and return the branch to Codex2 for re-review.

## Summary
讓 candidate DTO 的理由、疑慮、事件、證據與細節都屬於同一真實 candidate 並帶 provenance/as-of；缺欄位明確 unavailable。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Review-fix checkpoint
- Candidate field availability now uses field-specific value schemas; null or cross-field values fail validation, and redacted evidence cannot carry a raw summary.
- Member lists, score lists, and viewer detail omit private component explanations; operator-grade detail retains the governed explanation.
- Recipe requirements no longer synthesize `evidence://` refs. Evidence is typed unavailable until candidate metrics persist a governed ref.
- Candidate lifecycle review writes persist `_updated_at`; both list and detail `details.provenance.as_of` project that mutation timestamp.
- The candidate contract is additive bundle v1.12/v13 over exact dev v1.11 bytes. Published v1.10 and v1.11 artifacts remain unchanged.

## Verification
- `/home/lupin/pantheon/.venv/bin/python -m pytest -q services/control-plane/bff/tests/test_agora_candidate_truth.py services/control-plane/bff/tests/test_agora_candidate_pool.py scripts/test_agora_v1_12_candidate_bundle.py` — 21 passed.
- `/home/lupin/pantheon/.venv/bin/python -m pytest -q services/control-plane/bff/tests/test_agora_candidate_truth.py services/control-plane/bff/tests/test_agora_candidate_pool.py services/control-plane/bff/tests/test_agora_research_store_backend.py services/control-plane/bff/tests/test_agora_router.py services/control-plane/bff/tests/test_agora_research_run_projection.py scripts/test_agora_v1_12_candidate_bundle.py` — 47 passed, 2 skipped because `AGORA_RESEARCH_TEST_POSTGRES_DSN` is unset.
- `/home/lupin/pantheon/.venv/bin/python -m pytest -q services/control-plane/bff/agora/performance/test_performance.py services/control-plane/bff/agora/strategy_workshop/test_versions.py services/control-plane/bff/agora/strategy_workshop/test_operation_lifecycle.py` — 17 passed, 2 integration-backend skips.
- `git diff --check` — passed.

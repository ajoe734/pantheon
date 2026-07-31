# Task Brief: SUP-L12-GUARDED-REMEDIATION-DISPATCHER-20260731

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Extend the program-specific guarded dispatcher for current-proof remediation
- Status: in_progress
- Owner: Codex
- Reviewer: Codex2
- Next: Exact-head 53a3cdcc70af14822c95b29460d474462ccfa981 review found a fail-closed readback gap: _current_materialized_row_is_exact accepts provider_assignment_resolution containing only owner/reviewer. Full 25-task G1 reproduction returns create=0 exact=25 after deleting schema/source/readiness/catalog-default/evaluation/fallback evidence, so canonical replay and admission receipts can claim exactness without recording every fallback or proving unavailable providers were not selected. Require exact resolution-schema validation for active and archived rows, including catalog defaults, readiness/source identity, ordered owner/reviewer evaluations, selected-first-ready semantics with owner exclusion, and derived fallback lists; add full-G1 replay/readback/admission-archive regressions that reject truncated or contradictory resolution evidence; then refresh evidence and PR exact head. Independent checks otherwise passed: PR #4394 exact bytes/CI, #4406 merged, #4410 CI, 16 current tests, 31 legacy tests, evidence schema, compile, both validate-only profiles, and live dry-run failed closed on the expected BFF overlap.

## Summary
Bootstrap the existing L12 program-specific dispatcher so the true supervisor can safely fan out the newly audited 28-task remediation DAG to auto workers. This bootstrap is dependency-gated on the scheduler runtime repair and is the only task sent through the generic bridge.

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

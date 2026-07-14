# Sponsor Convergence Ruling — LOOP-PROD × EVOCHAIN × EVOLOOP (2026-07-14)

Status: binding for all LOOP-PROD tasks and any LOOP-PROD addendum

Three active programs share the execution spine. Division of labor:

| Program | Role |
| --- | --- |
| `EVOCHAIN-*` (11 tasks, 2026-07-13) | Observation half: journal read side, freeze/rollback canonical store, postmortem publisher, sweep activation |
| `EVOLOOP-*` (11 tasks, 2026-07-14) | Thin vertical slice: prove ONE full generative cycle fast on one binding/artifact |
| `LOOP-PROD-*` (36 tasks + addendum) | Full-matrix productization of all 12 loops + OODA overlay |

## Binding rules

1. **LOOP-PROD consumes EVOLOOP/EVOCHAIN outputs; it must not recreate or
   re-dispatch their scope.** Overlap map:
   - `LOOP-PROD-EVO-001` builds target-plane readback ON the worker deployed
     by `EVOLOOP-001`.
   - `LOOP-PROD-DIST-001` / `LOOP-PROD-ALPHA-001` build durable consumers ON
     the artifact contract from `EVOLOOP-003` and the first retrain from
     `EVOLOOP-004`.
   - `LOOP-PROD-DEP-001` generalizes the runtime-manager promote path first
     exercised by `EVOLOOP-006`.
   - `LOOP-PROD-CAP-001` adopts the `EVOLOOP-007` binding as its first
     tenant.
   - `LOOP-PROD-VERIFY-EXEC-001` consumes/extends the `EVOLOOP-008`
     verifier.
   - `LOOP-PROD-TEL-001`/`LOOP-PROD-CAP-001` treat `EVOLOOP-002`
     (PnL mark-to-market + drawdown events) and `EVOLOOP-005` (governed
     baselines) as upstream dependencies — the LOOP-PROD catalog itself has
     zero performance-metric supply coverage.
2. **File-ownership boundary:** while an EVOLOOP task is not `done`, the
   corresponding LOOP-PROD task must not modify the same service files;
   coordinate through dependencies, not parallel edits.
3. **Any addendum** must list consumed `EVOCHAIN-*` / `EVOLOOP-*` IDs as
   external dependencies (same mechanism as its existing `EVOCHAIN-011`
   edge) instead of introducing duplicate tasks.
4. Conversation-plane gaps are owned by `EVOLOOP-010` (seven-stage
   discussion-loop spec + unified proposal intake) and `EVOLOOP-011`
   (execution-outcome feedback into persona memory). LOOP-PROD CONS/AGORA
   tasks fix surface realism; they do not own these connectors.

Source rulings: `docs/bff/execution-tasks/2026-07-14-evolution-generative-loop-gap/INDEX.md`
(Convergence section) and the 2026-07-14 sponsor session.

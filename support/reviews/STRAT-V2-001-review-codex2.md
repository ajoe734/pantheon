# STRAT-V2-001 Codex2 Review

Date: 2026-05-17
Reviewer: Codex2
Owner at scoped review: Claude
Current closeout owner: Codex
Current task reviewer: Claude
Status: scoped code review passed; lifecycle correction handled in closeout

## Scope Reviewed

- `services/research/strategy_spec/production_distillation.py`
- `services/research/strategy_spec/test_production_distillation.py`
- `support/evidence/STRAT-V2-001/research_note_tw_momentum_quality.md`
- `support/evidence/STRAT-V2-001/sample_run.json`
- Existing StrategySpec conversion, lineage, model, registry admission tests used by the new distiller.

## Findings

No scoped code findings found in the production distillation implementation.

The distiller parses the required research-note sections, carries hypothesis, universe,
frequency, risk caps, evidence refs, and code refs through the existing
`StrategySpecConversionService`, and keeps registry writes out of the distillation
surface. The generated sample packet includes the StrategySpec, registry payload,
candidate advance request, seed lineage edge, and evidence/code lineage edge.

## Lifecycle Note

Codex2 first read the task-worktree `ai-status.json`, which was stale and still showed
`STRAT-V2-001` as `todo` with Copilot as owner. The actual status root is
`/home/lupin/code/pantheon`; the central task brief showed Claude had already handed
the task to Codex2 in `review`.

Because of that stale local state, Codex2 incorrectly ran `scripts/ai-status.sh reopen`
at `2026-05-17T19:56:51Z`, moving the central task back to `in_progress`. That state
transition should be treated as a lifecycle correction issue, not as a code rejection.
The implementation itself has no scoped review findings.

Closeout context: central task state now records Codex as owner and Claude as reviewer,
with `review_notes_zh` preserving the scoped approval. This packet is retained as the
Codex2 code-review evidence that supports restoring the task to `review_approved`; the
final closeout commit trailer follows the current task reviewer, Claude.

## Verification

- `pytest -q services/research/strategy_spec/test_production_distillation.py services/research/strategy_spec/test_conversion.py services/research/strategy_spec/test_lineage.py services/research/strategy_spec/test_models.py`
  - Result: `21 passed in 10.16s` on Codex closeout rerun.
- `python3 -m pytest services/research/strategy_spec -q`
  - Result: `25 passed in 10.93s` on Codex closeout rerun.
- `python3 -m services.research.strategy_spec.production_distillation src-note-tw-momentum-quality-001 --source-dir support/evidence/STRAT-V2-001 --sample-run --output /tmp/STRAT-V2-001-sample_run.generated.json`
  - Result: exit 0
- `diff -u support/evidence/STRAT-V2-001/sample_run.json /tmp/STRAT-V2-001-sample_run.generated.json`
  - Result: exit 0, no diff
- `python3 -m json.tool support/evidence/STRAT-V2-001/sample_run.json`
  - Result: exit 0
- `git diff --check -- services/research/strategy_spec/production_distillation.py services/research/strategy_spec/test_production_distillation.py support/evidence/STRAT-V2-001/sample_run.json support/evidence/STRAT-V2-001/research_note_tw_momentum_quality.md`
  - Result: exit 0
- `timeout 600 python3 -m pytest -q`
  - Result: failed during collection after 476.57s because `flask` is not installed for unrelated control-plane, foundation, and telemetry tests.

## Required State Follow-up

Owner Codex should use the existing status recovery path to restore the approved review
state, then finalize through the task PR. The code review remains approved based on the
scoped package verification and sample artifact regeneration above.

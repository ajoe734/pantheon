# OSS-003 Review Packet

Task: `OSS-003`
Owner: `Qwen`
Reviewer: `Codex`
Last updated: 2026-04-10

## Scope

Lock explicit activation gates for the still-deferred learning paths so `Qlib`, `TRL`, and the `FinRL` / `RLlib` / `Ray Tune` stack are no longer placeholder boxes in architecture and checklist documents.

## Files Prepared for Review

- `TARGET_ARCHITECTURE.md`
- `services/learning/qlib/ACTIVATION_CRITERIA.md`
- `services/learning/trl/ACTIVATION_CRITERIA.md`
- `services/learning/rl/PATH_DEFINITION.md`
- `services/learning/rl/README.md`

## What Changed

1. `Qlib` activation criteria are now review-ready under `OSS-003`, with Qlib explicitly framed as the first deferred learning path to activate and LEAN consumption kept score-only rather than action-producing.
2. `Qlib` no longer embeds its own shadow copy of TRL thresholds; it now points to the dedicated TRL activation document so the preference-learning gate has exactly one source of truth.
3. `TRL` activation criteria are now review-ready under `OSS-003`, with prerequisites tied to governed feedback volume, imitation baseline, baseline-model proof, and downstream consumer readiness.
4. Reviewer cleanup restores canonical `artifact_state` / `deployment_stage` language in the new Qlib / TRL activation documents so they do not reintroduce the deprecated `paper/live`-as-lifecycle model.
5. `W&B` activation criteria now describe the real MLflow-first adapter state in `services/registry/experiments/adapter.py` and gate activation on a follow-on backend-generalization step instead of referencing nonexistent adapter APIs.
6. `RL` path documents from `LP-005` were normalized to the already-approved task state so they no longer claim draft/in-progress status while current shared state marks `LP-005` as done.
7. `TARGET_ARCHITECTURE.md` now points directly at the three activation-gate documents, making the deferred-path ordering explicit at the L1 architecture layer.

## Reviewer Focus

1. `Qlib` remains the first activation path and is clearly separated from both preference learning and sequential action policies.
2. `TRL` stays a governed preference-learning path rather than an execution-policy path, and its thresholds match the approved LP-004 scope.
3. `W&B` remains deferred behind backend-neutral adapter work; this packet should not imply a second experiment backend already exists.
4. `RL` remains deferred until Qlib plateaus and the problem is genuinely sequential, with registry and execution still routed through canonical governance boundaries.

## Expected Acceptance Outcome

If the reviewer agrees, `OSS-003` can move to `review_approved` with a summary that the deferred learning frameworks now have explicit activation prerequisites, canonical state semantics, and no longer exist as architecture placeholders.

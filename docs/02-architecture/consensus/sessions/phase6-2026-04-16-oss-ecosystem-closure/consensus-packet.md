# Consensus Packet

## Decision Summary

- Session: `phase6-2026-04-16-oss-ecosystem-closure`
- Scope: close the residual gap between "Phase 6 backlog complete" and "OSS ecosystem fully integrated" without reopening the already-finished canonical `OSS-001` / `OSS-002` / `OSS-003` semantics.
- Accepted architecture: the OSS closure wave is split into three domains:
  - `Wave A / activation-ready realization`
  - `Wave B / missing backend materialization`
  - `Wave C / conditional decision gates`
  This replaces the old habit of treating every deferred framework as the same kind of work.
- Delivery order:
  1. execute `Wave A` first to advance the nearest ready frameworks and refresh the governed baseline
  2. execute `Wave B` second to convert `vectorbt`, `statsmodels`, and `QuantLib` from maturity-matrix debt into real execution families
  3. execute `Wave C` third to make explicit approve/defer decisions for RL and W&B rather than carrying them as ambiguous future work

## Agreed Task Slices

- Task 1: `OSS-NEXT-001` realize the governed Qlib adapter and first supervised-alpha smoke path
- Task 2: `OSS-NEXT-002` realize the TRL activation baseline and governed preference-learning smoke path
- Task 3: `OSS-NEXT-008` refresh governed-path smoke and no-regression evidence for `OpenClaw`, `DSPy`, `imitation`, and `MLflow`
- Task 4: `OSS-NEXT-005` materialize `vectorbt` into a named execution-ready backend family
- Task 5: `OSS-NEXT-006` materialize `statsmodels` into a named execution-ready backend family
- Task 6: `OSS-NEXT-007` materialize `QuantLib` into a named execution-ready backend family
- Task 7: `OSS-NEXT-003` close the RL path activation gate and choose the first executable RL lane
- Task 8: `OSS-NEXT-004` decide W&B backend parity versus explicit defer with re-entry criteria

## Open Questions / Human Gate

- Item 1: should `Qlib` and `TRL` both enter the immediate next execution wave, or should one remain deferred for one more cycle even though the planning slice is now ready
- Item 2: should the next round stop the `vectorbt` / `statsmodels` / `QuantLib` family at task-materialization and governed adapter design, or immediately open implementation lanes for all three
- Item 3: should RL stay explicitly deferred until Qlib shows stronger plateau evidence, or should `OSS-NEXT-003` be allowed to open the first RL lane in the next wave
- Item 4: should `W&B` remain deferred until the MLflow-history gate is met, or should backend generalization work start now while still withholding final activation

## Acceptance Note

- Review coverage is complete for the healthy lanes:
  - `Codex = submitted`
  - `Claude = submitted`
  - `Gemini = submitted`
  - `Qwen = waived`
  - `Copilot = waived`
- The machine-readable task graph and the human-readable packet now agree on the eight `OSS-NEXT-*` slices.
- This packet is ready for human gate review.

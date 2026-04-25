# Review Round 01

Use cited comments only. Do not directly rewrite `starter-draft.md` unless you currently hold the baton.

## Reviewer Order

- Gemini
- Claude
- Codex

## Comments

- `Gemini`: the three-bucket split is correct, but `TRL` acceptance wording must distinguish repo-side baseline completion from the live-volume gate. `vectorbt`, `statsmodels`, and `QuantLib` should first materialize into execution-ready task families rather than pretending they are immediately full backend builds.
- `Claude`: `Qlib` is the real dependency root for any later RL lane, and `W&B` should not be treated as an immediate implementation must-have before the MLflow operational-history gate is met. The next wave should keep RL and W&B as explicit decision gates, not silent placeholders.
- `Codex`: agreed with the current wave order `A -> B -> C`, but the reviewer-order metadata and consensus packet both needed normalization so the planning state, the draft, and the human-facing packet all describe the same task graph.

## Resolved Conclusions

- `Wave A` stays: `OSS-NEXT-001`, `OSS-NEXT-002`, `OSS-NEXT-008`
- `Wave B` stays: `OSS-NEXT-005`, `OSS-NEXT-006`, `OSS-NEXT-007`
- `Wave C` stays: `OSS-NEXT-003`, `OSS-NEXT-004`
- `TRL` is allowed to complete a repo-side activation baseline without claiming the runtime volume gate is already satisfied
- `RL` and `W&B` remain explicit include/defer decision lanes rather than implicit implementation promises

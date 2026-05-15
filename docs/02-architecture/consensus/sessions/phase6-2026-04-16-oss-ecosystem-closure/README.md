# Discussion Planning Mode — Phase 6: OSS Ecosystem Closure

Record note: this session directory is the active planning record right now, but it is still a working record rather than immutable blueprint truth.

This directory is the canonical workspace for `discussion_planning`.

## Session

- Session ID: `phase6-2026-04-16-oss-ecosystem-closure`
- Phase: `phase6`
- Objective: convert the residual OSS ecosystem maturity gap into the next executable planning wave.
- Facilitator: `Claude`
- Starter draft owner: `Codex`

## Brief Files

### Canonical and Maturity Sources
- `ROADMAP.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
- `OSS_INTEGRATION_CHECKLIST.md`
- `RESEARCH_BACKEND_MATURITY_MATRIX.md`

### Difference Analysis Inputs
- `docs/reviews/2026-04-16-full-blueprint-gap-analysis.md`
- `docs/reviews/2026-04-16-oss-ecosystem-gap-analysis.md`

### Prior Bridge Inputs
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`
- `docs/02-architecture/consensus/sessions/phase5-2026-04-15-full-blueprint-gap-closure/full-blueprint-gap-inventory.md`

## Expected Outputs

- `docs/reviews/2026-04-16-oss-ecosystem-gap-analysis.md` (owner: `Codex`)
- `docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/execution-materialization.md` (owner: `Codex`)
- `docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/consensus-packet.md` (owner: `Claude`)

## Scope

This session does not reopen canonical Phase 6 semantics. `OSS-001`, `OSS-002`, and `OSS-003` are already archived `done`.

The real question for this round is narrower and more operational:

1. which deferred frameworks should advance from `criteria-defined` or `version-pinned` into real governed adapters
2. which research backends still need first-class task materialization because they are currently only named in maturity documents
3. which optional ecosystems should be explicitly deferred again instead of drifting through another planning cycle

## Gap Summary

Current maturity split:

1. `governed / fully integrated`
   `OpenClaw`, `DSPy`, `imitation`, `MLflow`
2. `activation-ready but not fully integrated`
   `Qlib`, `TRL`, `FinRL`, `RLlib`, `Ray Tune`, `W&B`
3. `not integrated / not started`
   `vectorbt`, `statsmodels`, `QuantLib`

## Baton Loop

1. every lane reads the session brief and writes an independent readout using `LLM_READOUT_TEMPLATE.md`
2. only `Codex` seeds `starter-draft.md` and the first `OSS-NEXT-*` slice plan
3. cited cross-review happens round by round
4. unresolved disagreements become explicit `human_required` or `tracking` items
5. the facilitator drafts `consensus-packet.md`
6. after human acceptance, convert `proposed_execution_tasks` into execution tasks through `scripts/planning-state.sh materialize`
7. execution tasks should receive planning references, not copied planning narrative

## Review Discipline

- `Qwen` and `Copilot` are waived in this session because those planning lanes have repeatedly stalled in this repo
- `Claude` covers Qwen-style schema / contract review
- `Codex` covers Copilot-style research-readiness / acceptance review
- the active reviewer order for this session is `Gemini -> Claude -> Codex`

## Rules

- only the shared draft owner edits `starter-draft.md`
- reviewers do not directly rewrite the shared draft
- `planning-session.json` is the machine-readable source of truth for planning state
- `.orchestrator/planning-state.json` is the derived dashboard state
- every planning round keeps its own session directory; archived sessions are immutable history
- execution tasks stay in `ai-status.json`; do not mix planning drafts into the execution board too early
- every deferred backend must leave this session either as an execution slice, an explicit defer with re-entry criteria, or a human-gated unresolved item

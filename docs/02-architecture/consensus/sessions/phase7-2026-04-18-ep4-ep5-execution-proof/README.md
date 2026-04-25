# Discussion Planning Mode — Phase 7: EP4 / EP5 Execution Proof

This directory is the canonical workspace for `discussion_planning`.

## Session

- Session ID: `phase7-2026-04-18-ep4-ep5-execution-proof`
- Objective: turn the current `EP3`-bounded deployment evidence into a dependency-aware plan for stable `EP4`, while explicitly separating later `EP5` canary/live proof from paper-runtime proof
- Facilitator: `Claude`
- Shared draft owner: `Codex`

## Brief Files

### Canonical proof and policy inputs
- `EXECUTION_PROOF_AND_MATURITY_LEVELS.md`
- `DEVELOPMENT_WORKBREAKDOWN.md`
- `ROADMAP.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `PAPER_CANARY_LIVE_POLICY.md`
- `ROLLBACK_AND_POSITION_SEMANTICS.md`
- `BINDING_AND_DEPLOYMENT_SEMANTICS.md`
- `EVOLUTION_REVIEW_AND_THRESHOLDS.md`
- `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md`

### Current evidence inputs
- `docs/deployment/single-vm-smoke-results.md`
- `docs/deployment/dual-vm-acceptance-results.md`
- `integrations/openclaw/evidence_pack.md`
- `docs/reviews/2026-04-17-oss-next-008-governed-regression-refresh.md`
- `docs/reviews/2026-04-18-current-state-reconciliation.md`
- `docs/reviews/2026-04-18-ep4-ep5-planning-entry-packet.md`

## Expected Outputs

- `document-reconciliation.md` (owner: `Codex`)
- `execution-materialization.md` (owner: `Codex`)
- `consensus-packet.md` (owner: `Claude`)

## Scope Boundary

This session should not treat `EP4` and `EP5` as one undifferentiated wave.

The default planning boundary is:

1. first raise the repo to stable `EP4`
2. only then open `EP5` proof work
3. allow this session to prepare `EP5` prerequisites, but do not hide `EP5` claims inside `EP4` acceptance

## Planning Stages

1. reconcile canonical proof and policy documents
2. confirm whether any canonical blueprint update is required
3. inventory the missing EP4 evidence and the deferred EP5 prerequisites
4. cut dependency-aware execution slices
5. draft the consensus packet and wait for human acceptance before materialization

## Review Discipline

- primary reviewer order: `Gemini -> Claude -> Codex`
- `Qwen` and `Copilot` are optional support lanes for this session, not part of the primary cross-review path

## Baton Loop

1. every lane reads the session brief and writes an independent readout using `LLM_READOUT_TEMPLATE.md`
2. only `Codex` seeds `starter-draft.md`
3. cited cross-review happens round by round
4. unresolved disagreements become explicit `human_required` or `tracking` items
5. the facilitator drafts `consensus-packet.md`
6. after human acceptance, convert `proposed_execution_tasks` into execution tasks through `scripts/planning-state.sh materialize`
7. execution tasks should receive planning references, not copied planning narrative

## Rules

- only the shared draft owner edits `starter-draft.md`
- reviewers do not directly rewrite the shared draft
- `planning-session.json` is the machine-readable source of truth for planning state
- `.orchestrator/planning-state.json` is the derived dashboard state
- every planning round keeps its own session directory; archived sessions are immutable history
- document reconciliation must be completed before final human approval or execution materialization
- execution tasks stay in `ai-status.json`; do not mix planning drafts into the execution board too early

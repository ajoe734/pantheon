# Discussion Planning Mode

This directory is the canonical workspace for `discussion_planning`.

## Session

- Session ID: `phase6-2026-05-01-pantheon-p0-paper-loop`
- Objective: Align architecture, delivery order, and task slicing before materializing execution work.
- Shared draft owner: `Codex`

## Brief Files

- _(none)_

## Expected Outputs

- `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/document-reconciliation.md` (owner: `Codex`)
- `docs/02-architecture/consensus/sessions/phase6-2026-05-01-pantheon-p0-paper-loop/consensus-packet.md` (owner: `Claude`)

## Planning Stages

1. audit the canonical blueprint and planning documents relevant to the session
2. write down insufficiencies and either patch the canonical docs or explicitly conclude that no canonical update is needed
3. only after document reconciliation is complete may the session finalize execution planning for human approval and materialization

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

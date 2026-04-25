# Discussion Planning Mode

Record note: this directory is a planning workspace and record, not immutable blueprint truth.
If another planning session is active in `.orchestrator/planning-state.json`, that session is the operative planning record instead of this legacy phase1 workspace.

This directory is the canonical workspace for `discussion_planning`.

## Goal

Before execution tasks are created, every lane should align on:

- architecture and ownership boundaries
- delivery order and wave order
- task slicing and reviewer assignment

## Canonical Files

Read in this order when a planning session is active:

1. `README.md`
2. `planning-session.json`
3. `pantheon-backend-completion-checklist.md`
4. `starter-draft.md`
5. `consensus-packet.md`
6. the current `*-readout.md` and `review-round-*.md` files

## Baton Loop

1. all lanes read canonical docs in L0 -> L1 -> L2 order
2. each lane writes an independent readout using `LLM_READOUT_TEMPLATE.md`
3. `Codex` starts the first `starter-draft.md`
4. `Qwen -> Gemini -> Copilot -> Claude` perform cited cross-review round by round
5. unresolved disagreements become explicit `human_required` items
6. `Claude` synthesizes `consensus-packet.md`
7. after human acceptance, convert the agreed slices into execution tasks through `scripts/ai-status.sh`

## Rules

- only the current baton owner edits `starter-draft.md`
- reviewers do not directly rewrite the shared draft
- `planning-session.json` is the machine-readable source of truth for planning state
- `.orchestrator/planning-state.json` is derived for the dashboard
- execution tasks stay in `ai-status.json`; do not mix planning drafts into the execution board too early

## Commands

```bash
./scripts/planning-state.sh start phase1 "Kick off the planning session"
./scripts/planning-state.sh readout Codex submitted "Codex readout is ready"
./scripts/planning-state.sh baton Qwen Gemini "Baton moved to Qwen for cross-review"
./scripts/planning-state.sh round 1 open "Opened cited cross-review round 1"
./scripts/planning-state.sh consensus ready_for_human "Consensus packet drafted and ready"
./scripts/planning-state.sh human-gate approved "Human accepted the packet"
./scripts/planning-state.sh propose-task W3-001A Qwen Claude "Callcenter & CTI correlation baseline"
./scripts/sync-state.sh
```

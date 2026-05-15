# Discussion Planning Mode

Record note: this session directory is planning history and execution-shaping record material, not canonical blueprint truth.

This directory is the canonical workspace for `discussion_planning`.

## Session

- Session ID: `phase3-2026-04-14-pantheon-console-loop`
- Objective: Define the canonical closed-loop coordination protocol, GitHub dispatch model, screen-packet requirements, and execution backlog for all 8 Pantheon Console workbenches.
- Shared draft owner: `Codex`

## Brief Files

- `Pantheon_總索引版系統分析文件.md`
- `.coordination/README.md`
- `docs/delivery-coordination-bus.md`
- `docs/orchestrator-state-plane-redesign.md`
- `OPERATOR_ACCEPTANCE_MATRIX.md`
- `support/sidecars/APP-002/APP-002-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/APP-002/APP-002-FRONTEND-STATE-MATRIX.md`
- `support/sidecars/APP-002-W2-READ-INCIDENT/APP-002-W2-READ-INCIDENT-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/APP-002-W2-CONTROL-INCIDENT/APP-002-W2-CONTROL-INCIDENT-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/APP-002-W3-POSTINCIDENT-EVOLUTION/APP-002-W3-POSTINCIDENT-EVOLUTION-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/APP-002-W4-PERSONA-MGMT/APP-002-W4-PERSONA-MGMT-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/APP-002-W4-REMAINING-CATALOG/APP-002-W4-REMAINING-CATALOG-SIDECAR-BFF-HANDOFF.md`
- `support/sidecars/APP-002-W5-SSE-LIVE/APP-002-W5-SSE-LIVE-SIDECAR-BFF-HANDOFF.md`
- `ai-status.json`

## Expected Outputs

- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/consensus-packet.md` (owner: `Claude`)
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/coordination-loop-spec.md` (owner: `Codex`)
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/pantheon-console-workbench-backlog.md` (owner: `Codex`)
- `docs/02-architecture/consensus/sessions/phase3-2026-04-14-pantheon-console-loop/execution-materialization.md` (owner: `Codex`)

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
- execution tasks stay in `ai-status.json`; do not mix planning drafts into the execution board too early
- `.coordination` remains the canonical machine protocol; this session must not introduce `.ai-loop` as a second source of truth
- Lovable remains a human-triggered UI lane; this session is about packetizing and automating the handoff loop around it

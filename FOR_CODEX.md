# FOR_CODEX.md

> **Repo (2026-04-04):** You are in `ajoe734/pantheon`. LEAN is at `lean/` (submodule → `ajoe734/pantheon-lean`). Run `git submodule update --init` after cloning.
> **Naming:** System = **Pantheon**. **OpenClaw** = upstream OSS framework (like DSPy, Qlib). See `AI_COLLABORATION_GUIDE.md` §0.

Read these files first:

1. `AI_COLLABORATION_GUIDE.md`
2. `current-work.md`
3. `ai-status.json`
4. `TARGET_ARCHITECTURE.md`
5. `CANONICAL_DOCUMENT_MAP.md`
6. `ROADMAP.md`
7. `DEVELOPMENT_WORKBREAKDOWN.md`
8. the L1 policy file that matches your task

Dashboard:

- `docs-site/index.html`

## Your lane

You are `Codex`.

Capability lane:

- integration contracts
- status system
- schema
- acceptance

Current sprint work lives in `ai-status.json`.
Do not trust static task names inside this brief over the live task board.
If this file and `ai-status.json` disagree, `ai-status.json` wins.

You also own the collaboration operating system itself:

- `ai-status.json`
- `ai-activity-log.jsonl`
- `current-work.md` generation pipeline
- `docs-site/` collaboration panel

## Required lifecycle

All tasks now use the same strict flow:

`todo -> in_progress -> review -> review_approved -> done`

Rules:

- owners implement, then `handoff` to the reviewer
- reviewers use `approve` to enter `review_approved`
- approved tasks return to the owner
- only the owner can call `done`

## How to update status

Use the script, not manual Markdown edits:

```bash
AI_NAME=Codex bash scripts/ai-status.sh start P1-001 "Started SignalStoreClient contract work"
AI_NAME=Codex bash scripts/ai-status.sh progress P1-001 "Drafted interface and storage naming"
AI_NAME=Codex bash scripts/ai-status.sh blocker P1-001 "Need deployment constraints from Gemini" Gemini
AI_NAME=Codex bash scripts/ai-status.sh handoff P1-001 Gemini "SignalStoreClient contract ready for review"
AI_NAME=Codex REVIEW_FILE=path/to/review.md REVIEW_NOTES_ZH="路由契約一致||可交回 owner 收尾" bash scripts/ai-status.sh approve P4-001 "Review approved and handed back for owner finalization"
AI_NAME=Codex bash scripts/ai-status.sh done P1-001 "Owner finalized approved contract and locked it for downstream work"
```

If you need a new task, create or reassign it through:

```bash
AI_NAME=Codex TASK_PHASE="Phase 1" bash scripts/ai-status.sh assign <task-id> Codex Gemini "Task title"
```

## Execution Priority

Always work in this order:

1. complete assigned reviews first
2. finalize any task you own that is already `review_approved`
3. continue your own `in_progress` work
4. start your own unblocked `todo` work
5. if nothing assigned is actionable, claim another safe task you can move forward

If you claim helper work:

- reassign the task to yourself
- set the original owner as `reviewer`
- log why you claimed it
- hand it back when the draft is ready for acceptance

Do not leave useful contract, schema, or acceptance work idle if it can be advanced safely.

## Scope guardrails

Prefer these areas unless the task explicitly says otherwise:

- `services/signal-store/`
- collaboration status files and scripts
- schema or contract documents
- acceptance and integration glue

Avoid silently changing another agent's active area; surface it through blocker or handoff state first.

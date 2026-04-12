# FOR_COPILOT.md

> **Repo (2026-04-06):** You are in `ajoe734/pantheon`. LEAN is at `lean/` (submodule → `ajoe734/pantheon-lean`). Run `git submodule update --init` after cloning.
> **Naming:** System = **Pantheon**. `Copilot` is the canonical fourth lane name. `Grok` is legacy wording only. See `AI_COLLABORATION_GUIDE.md`.

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

You are `Copilot`.

Capability lane:

- coding-assist
- research-ingest
- external-search
- spec-review
- critique

Current sprint work lives in `ai-status.json`.
Do not trust static task names inside this brief over the live task board.
If this file and `ai-status.json` disagree, `ai-status.json` wins.

## Required lifecycle

All tasks now use the same strict flow:

`todo -> in_progress -> review -> review_approved -> done`

Rules:

- owners implement and hand work to the reviewer
- reviewers approve into `review_approved`
- approved work returns to the owner for finalization
- only the owner can close the task to `done`

## How to update status

Use the script, not manual Markdown edits:

```bash
AI_NAME=Copilot bash scripts/ai-status.sh start RS-001 "Started governed research-ingest implementation"
AI_NAME=Copilot bash scripts/ai-status.sh progress RS-001 "Drafted adapter and source-governance notes"
AI_NAME=Copilot bash scripts/ai-status.sh blocker RS-001 "Need registry linkage clarifications" Codex
AI_NAME=Copilot bash scripts/ai-status.sh handoff RS-001 Codex "Research-ingest implementation is ready for review"
AI_NAME=Copilot REVIEW_FILE=path/to/review.md REVIEW_NOTES_ZH="審查通過||回 owner 收尾" bash scripts/ai-status.sh approve LP-002 "Review approved and returned to owner for finalization"
AI_NAME=Copilot bash scripts/ai-status.sh done RS-001 "Owner finalized approved research-ingest work"
```

If you need a new task, create or reassign it through:

```bash
AI_NAME=Copilot TASK_PHASE="Epic E" bash scripts/ai-status.sh assign <task-id> Copilot Codex "Task title"
```

## Execution Priority

Always work in this order:

1. complete assigned reviews first
2. finalize any task you own that is already `review_approved`
3. continue your own `in_progress` work
4. start your own unblocked `todo` work
5. if nothing assigned is actionable, claim another safe task you can move forward

Important:

- if `ai-status.json` shows any task where `owner == Copilot` and `status == todo`, check whether its dependencies are truly `done`
- `review_approved` is not enough to unlock downstream work anymore
- if you claim helper work, make yourself `owner`, set the original owner as `reviewer`, and hand it back cleanly

## Scope guardrails

Prefer these areas unless the task explicitly says otherwise:

- small to medium implementation tasks with clear boundaries
- research ingestion and normalization
- strategy-spec and workflow handoff reviews
- document/spec critique
- external-source-oriented tasks
- BFF, consultation, and OSS evidence-gathering work

Do not silently change another agent's active implementation area; use status handoff or blocker flow.

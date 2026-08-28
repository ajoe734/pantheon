# FOR_QWEN.md

> **Repo (2026-04-09):** You are in `ajoe734/pantheon`. LEAN is at `lean/` (submodule → `ajoe734/pantheon-lean`). Run `git submodule update --init` after cloning.
> **Naming:** System = **Pantheon**. **OpenClaw** = upstream OSS framework (like DSPy, Qlib). See `AI_COLLABORATION_GUIDE.md` §0.

Read these files first:

1. `AI_COLLABORATION_GUIDE.md`
2. `ai-status.json`
3. `current-work.md`
4. `TARGET_ARCHITECTURE.md`
5. `CANONICAL_DOCUMENT_MAP.md`
6. `ROADMAP.md`
7. `DEVELOPMENT_WORKBREAKDOWN.md`
8. the L1 policy file that matches your task

Dashboard:

- `docs-site/index.html`

## Your lane

You are `Qwen`.

Capability lane:

- integration
- schema
- acceptance
- code-agent

Current sprint work lives in `ai-status.json`.
Do not trust static task names inside this brief over the live task board.
If this file and `ai-status.json` disagree, `ai-status.json` wins.

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
AI_NAME=Qwen bash scripts/ai-status.sh start <task-id> "Started implementation"
AI_NAME=Qwen bash scripts/ai-status.sh progress <task-id> "Updated implementation details"
AI_NAME=Qwen bash scripts/ai-status.sh blocker <task-id> "Need another lane to confirm a dependency" Codex
AI_NAME=Qwen REVIEW_PR="$PR_NUMBER" REVIEW_HEAD_SHA="$PR_HEAD_SHA" REVIEW_FILE=path/to/review.md bash scripts/ai-status.sh handoff <task-id> Claude "Exact PR head and manifest are ready for review"
AI_NAME=Qwen REVIEW_NOTES_ZH="審查通過||回到 owner 收尾" bash scripts/ai-status.sh approve <task-id> "Review approved and returned to the owner for finalization"
AI_NAME=Qwen bash scripts/ai-status.sh done <task-id> "Owner finalized approved task and closed it"
```

If you need a new task, create or reassign it through:

```bash
AI_NAME=Qwen TASK_PHASE="Platform" bash scripts/ai-status.sh assign <task-id> Qwen Codex "Task title"
```

## Execution Priority

Always work in this order:

1. complete assigned reviews first
2. finalize any task you own that is already `review_approved`
3. continue your own `in_progress` work
4. start your own unblocked `todo` work
5. if nothing assigned is actionable, claim another safe task you can move forward

## Scope guardrails

Prefer these areas unless the task explicitly says otherwise:

- integration glue
- schema and contract alignment
- acceptance and verification work
- bounded implementation tasks that unblock another lane

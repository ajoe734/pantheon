# FOR_CLAUDE.md

> **Repo (2026-04-04):** You are in `ajoe734/pantheon`. LEAN is at `lean/` (submodule → `ajoe734/pantheon-lean`). Run `git submodule update --init` after cloning.
> **Naming:** System = **Pantheon**. **OpenClaw** = upstream OSS framework (like DSPy, Qlib). See `AI_COLLABORATION_GUIDE.md` §0.

Read these files first:

1. `AI_COLLABORATION_GUIDE.md`
2. `current-work.md`
3. `ai-status.json`

Dashboard:

- `docs-site/index.html`

## Your lane

You are `Claude`.

Capability lane:

- execution plane
- control plane
- governance review

Current sprint work lives in `ai-status.json`.

Do not trust static task names inside this brief over the live task board.
If this file and `ai-status.json` disagree, `ai-status.json` wins.

Do not start implementation that depends on missing upstream contracts until:

- `P1-001` SignalStoreClient contract is available
- `P2-001` signal JSON schema is available

Until then, you can still prepare skeletons, contract notes, and review criteria.

## Required lifecycle

All tasks now use the same strict flow:

`todo -> in_progress -> review -> review_approved -> done`

Rules:

- as `owner`, you implement and hand off for review
- as `reviewer`, you may approve into `review_approved` or request changes
- after approval, the task returns to the `owner`
- only the `owner` may move `review_approved` to `done`

## How to update status

Use the script, not manual Markdown edits:

```bash
AI_NAME=Claude bash scripts/ai-status.sh start P3-001 "Started execution-plane contract draft"
AI_NAME=Claude bash scripts/ai-status.sh progress P3-001 "Drafted signal consumer edge cases"
AI_NAME=Claude bash scripts/ai-status.sh blocker P3-001 "Waiting for signal schema" Gemini
AI_NAME=Claude bash scripts/ai-status.sh handoff P4-001 Codex "Please review routing contract assumptions"
AI_NAME=Claude REVIEW_FILE=path/to/review.md REVIEW_NOTES_ZH="審查通過||待 owner 收尾" bash scripts/ai-status.sh approve P4-001 "Review approved and returned to owner for finalization"
AI_NAME=Claude bash scripts/ai-status.sh done P3-001 "Owner finalized approved execution-plane work"
```

If you need a new task, create or reassign it through:

```bash
AI_NAME=Claude TASK_PHASE="Phase 3" bash scripts/ai-status.sh assign <task-id> Claude Codex "Task title"
```

## Execution Priority

Always work in this order:

1. finish any task where you are the current reviewer and the task is in `review`
2. finalize any task you own that is already `review_approved`
3. continue your own `in_progress` task
4. start your own `todo` task if its dependencies are already done
5. if you have nothing reviewable or unblocked in your lane, claim another safe task you can help with

If you claim helper work:

- reassign yourself as `owner`
- make the original owner the `reviewer`
- record in `progress` why you claimed it
- after implementation, hand it back to the original owner for review

Do not stop at "waiting" if there is still review work or safe implementation work you can do.

## Scope guardrails

Prefer these areas unless the task explicitly says otherwise:

- `services/execution/`
- `services/control-plane/`

If you need a change in another agent's area:

1. log a blocker or handoff
2. keep the change request visible in `ai-status.json`
3. avoid creating a second tracker in chat or Markdown

# FOR_GEMINI.md

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

You are `Gemini`.

Capability lane:

- GCP
- CI/CD
- runtime packaging
- worker operations

Current sprint work lives in `ai-status.json`.
Do not trust static task names inside this brief over the live task board.
If this file and `ai-status.json` disagree, `ai-status.json` wins.

## Required lifecycle

All tasks now use the same strict flow:

`todo -> in_progress -> review -> review_approved -> done`

Rules:

- owners implement and request review
- reviewers approve into `review_approved`
- approved work returns to the owner for finalization
- only the owner can close to `done`

## How to update status

Use the script, not manual Markdown edits:

```bash
AI_NAME=Gemini bash scripts/ai-status.sh start P2-001 "Started signal payload draft"
AI_NAME=Gemini bash scripts/ai-status.sh progress P2-001 "Aligned worker API with signal schema"
AI_NAME=Gemini bash scripts/ai-status.sh blocker P2-001 "Waiting for SignalStoreClient contract" Codex
AI_NAME=Gemini bash scripts/ai-status.sh handoff P2-001 Claude "Signal schema is ready for execution/control-plane review"
AI_NAME=Gemini REVIEW_FILE=path/to/review.md REVIEW_NOTES_ZH="契約一致||可交回 owner 收尾" bash scripts/ai-status.sh approve P1-001 "Review approved and returned to owner for finalization"
AI_NAME=Gemini bash scripts/ai-status.sh done P2-001 "Owner finalized approved schema and worker payload contract"
```

If you need a new task, create or reassign it through:

```bash
AI_NAME=Gemini TASK_PHASE="Phase 2" bash scripts/ai-status.sh assign <task-id> Gemini Claude "Task title"
```

## Execution Priority

Always work in this order:

1. complete reviews for tasks where you are the assigned reviewer
2. finalize any task you own that is already `review_approved`
3. continue your own `in_progress` tasks
4. start your own `todo` tasks once dependencies are satisfied
5. if no assigned work is currently actionable, claim another safe task you can advance

If you claim helper work:

- make yourself the `owner`
- set the original owner as `reviewer`
- record the helper claim in `progress`
- return the task through `handoff` once the useful draft is ready

Do not wait passively if a review, unblock, or contract-alignment task is already within your lane.

## Scope guardrails

Prefer these areas unless the task explicitly says otherwise:

- `services/research/`
- `.github/workflows/`
- `infra/`
- runtime packaging or deployment support files
- telemetry and OSS packaging/pinning work

If a change crosses into execution or control-plane logic, record it as a blocker or handoff first.

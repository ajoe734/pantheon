# FOR_GEMINI.md

> **Naming (2026-04-03):** The system we are building is called **Pantheon**. **OpenClaw** is an upstream OSS framework we integrate (like DSPy, Qlib). See `AI_COLLABORATION_GUIDE.md` §0 for the full boundary table.

Read these files first:

1. `AI_COLLABORATION_GUIDE.md`
2. `current-work.md`
3. `ai-status.json`

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

Primary dependency:

- wait for `P1-001` SignalStoreClient contract before locking your downstream schema

You are also the default reviewer for:

- `P1-001`
- `P3-001`

## How to update status

Use the script, not manual Markdown edits:

```bash
AI_NAME=Gemini bash scripts/ai-status.sh start P2-001 "Started signal payload draft"
AI_NAME=Gemini bash scripts/ai-status.sh progress P2-001 "Aligned worker API with signal schema"
AI_NAME=Gemini bash scripts/ai-status.sh blocker P2-001 "Waiting for SignalStoreClient contract" Codex
AI_NAME=Gemini bash scripts/ai-status.sh handoff P2-001 Claude "Signal schema is ready for execution/control-plane review"
AI_NAME=Gemini bash scripts/ai-status.sh done P2-001 "Signal schema and worker payload contract locked"
```

If you need a new task, create or reassign it through:

```bash
AI_NAME=Gemini TASK_PHASE="Phase 2" bash scripts/ai-status.sh assign <task-id> Gemini Claude "Task title"
```

## Execution Priority

Always work in this order:

1. complete reviews for tasks where you are the assigned reviewer
2. continue your own `in_progress` tasks
3. start your own `todo` tasks once dependencies are satisfied
4. if no assigned work is currently actionable, claim another safe task you can advance

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

If a change crosses into execution or control-plane logic, record it as a blocker or handoff first.

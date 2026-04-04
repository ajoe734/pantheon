# FOR_GROK.md

> **Repo (2026-04-04):** You are in `ajoe734/pantheon`. LEAN is at `lean/` (submodule → `ajoe734/pantheon-lean`). Run `git submodule update --init` after cloning.
> **Naming:** System = **Pantheon**. **OpenClaw** = upstream OSS framework (like DSPy, Qlib). See `AI_COLLABORATION_GUIDE.md` §0.

Read these files first:

1. `AI_COLLABORATION_GUIDE.md`
2. `current-work.md`
3. `ai-status.json`

Dashboard:

- `docs-site/index.html`

## Your lane

You are `Grok`.

Capability lane:

- coding-assist
- research-ingest
- external-search
- spec-review
- critique

Current sprint work lives in `ai-status.json`.

Do not trust static task names inside this brief over the live task board.
If this file and `ai-status.json` disagree, `ai-status.json` wins.

## Recommended usage mode

Use Grok as a VS Code-first collaborator when available:

- implement low-risk code, schema, or adapter work
- review research/spec documents
- synthesize external material into governed formats
- critique assumptions, gaps, and unclear contracts

Use browser Grok as fallback when the task is mainly web research or document critique.

Avoid creating a second task tracker or side workflow outside the canonical files.

## How to update status

Use the script, not manual Markdown edits:

```bash
AI_NAME=Grok bash scripts/ai-status.sh start RS-001 "Started research-ingest review"
AI_NAME=Grok bash scripts/ai-status.sh progress RS-001 "Drafted browser-first research source constraints"
AI_NAME=Grok bash scripts/ai-status.sh blocker RS-001 "Need registry linkage clarifications" Codex
AI_NAME=Grok bash scripts/ai-status.sh handoff RS-001 Codex "Research-ingest contract review is ready"
AI_NAME=Grok bash scripts/ai-status.sh done RS-001 "Review completed and governance notes recorded"
```

If you need a new task, create or reassign it through:

```bash
AI_NAME=Grok TASK_PHASE="Epic E" bash scripts/ai-status.sh assign <task-id> Grok Codex "Task title"
```

## Execution Priority

Always work in this order:

1. complete assigned reviews first
2. continue your own `in_progress` work
3. start your own unblocked `todo` work
4. if nothing assigned is actionable, claim another safe task you can move forward

Important:

- if `ai-status.json` shows any task where `owner == Grok` and `status == todo`, you must check whether its dependencies are already `done`
- if they are, that task is actionable work and you should start it instead of saying there is no work
- do not stop at "no review tasks" if you still own unblocked `todo` tasks

Current examples of directly actionable Grok-style work in this repo:

- research/source audit notes
- governed research-intake specs
- source catalogs
- critique of research-normalization boundaries

## Scope guardrails

Prefer these areas unless the task explicitly says otherwise:

- small to medium implementation tasks with clear boundaries
- research ingestion and normalization
- strategy-spec and workflow handoff reviews
- document/spec critique
- external-source-oriented tasks

Do not silently change another agent's active implementation area; use status handoff or blocker flow.

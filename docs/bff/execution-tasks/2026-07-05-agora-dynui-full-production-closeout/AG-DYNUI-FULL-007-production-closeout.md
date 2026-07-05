# AG-DYNUI-FULL-007 - Production Closeout And Board Sync

Status: ready for fleet execution

Recommended owner: Codex

Recommended reviewer: Codex2 or Copilot

Do not assign to Claude or Claude2 while their quota is exhausted.

## Goal

Close the AG-DYNUI-FULL wave without falsifying status. Align the task board,
archive, and closeout notes with the published evidence listed in
`INDEX.md`.

## Owned Scope

- `.orchestrator/state.json` status reconciliation, but only through approved
  status tooling or a reviewed task-status patch from a clean worktree.
- `ai-task-archive/tasks/AG-DYNUI-FULL-003.json`
- `ai-task-archive/tasks/AG-DYNUI-FULL-005.json`
- `ai-task-archive/tasks/AG-DYNUI-FULL-006.json`
- `ai-task-archive/tasks/AG-DYNUI-FULL-007.json`
- Any generated archive index updates required by the status tooling.
- Closeout evidence doc updates under this directory.

## Do Not Change

- Do not alter Agora runtime behavior.
- Do not reopen static fixture paths.
- Do not mark tasks done without PR/deploy/live evidence.
- Do not hand-edit dirty root runtime state outside the repository workflow.
- Do not sweep unrelated dirty root files into a commit.

## Required Evidence To Record

- `AG-DYNUI-FULL-003`: PR #3030, merge commit
  `4933c36564b30085480dce5a0e0bfc71d7806c49`.
- `AG-DYNUI-FULL-005`: PR #3032, merge commit
  `66efc0e849f3facb33889634fe48a5947603cafb`.
- `AG-DYNUI-FULL-006`: PRs #3033/#3034/#3035, execute-plans PR #187,
  deploy runs `28748417821`, `28748692234`, `28748861121`, and
  integration gate `28749332352`.
- Hosted evidence screenshots and summaries under `/tmp/ag-dynui-full-006-*`.
- Final hosted probe showing `/agora/trading-room` renders without
  `Failed to load Trading Room`.

## Acceptance Criteria

1. Current board no longer shows AG-DYNUI-FULL-003 as active work.
2. Current board no longer shows AG-DYNUI-FULL-005 or AG-DYNUI-FULL-006 as
   untouched `todo`.
3. AG-DYNUI-FULL-007 records the closeout result and points to this packet.
4. If any board command cannot run safely because the live root is dirty, the
   worker must record that blocker and create a reviewed closeout PR instead of
   direct mutation.
5. `git diff --check` passes.
6. Status/archive validation relevant to the changed files passes.
7. PR is opened, checks pass, PR is merged, and final response records PR and
   merge commit.

## Suggested Validation

Run these from a clean repository worktree:

```bash
git diff --check
python3 -m json.tool ai-task-archive/index.json >/tmp/ag-dynui-archive-index.json
python3 -m py_compile scripts/ai_status.py
```

When operating against a live status checkout that actually contains
`.orchestrator/state.json`, additionally validate that file before changing or
publishing board state:

```bash
python3 -m json.tool .orchestrator/state.json >/tmp/ag-dynui-state.json
```

If the worker changes status-tooling code, also run the relevant
`scripts/test_ai_status.py` subset. At the time this packet was written, the
full `scripts/test_ai_status.py` suite failed on current `dev` in an unrelated
mixed-repository metadata fallback test, so do not use that existing failure as
evidence against this docs-only packet.

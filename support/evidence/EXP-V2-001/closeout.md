# EXP-V2-001 Closeout

Owner: Codex
Reviewer: Claude
Task: Experiment orchestrator parallel multi-backend dispatch
Date: 2026-05-18

## Scope

EXP-V2-001 added additive parallel dispatch support for one
`ExperimentTask` fanned out to multiple research backends. The reviewed
implementation lives in:

- `services/research/experiment_orchestrator/parallel_dispatch.py`
- `services/research/experiment_orchestrator/test_parallel_dispatch.py`
- `services/research/experiment_orchestrator/parallel_dispatch_contract.md`

The implementation PR was already merged as PR #69 into `dev` on
2026-05-17. This closeout preserves the helper reviewer approval and final
owner evidence after refreshing the task branch against current `origin/dev`.

## Review Evidence

Claude stepped in as helper reviewer after Codex2 was unresponsive. Review
approval is recorded in `support/evidence/EXP-V2-001/review_note.md`.

The approved scope remains true:

- `run_parallel(experiment_task, backend_ids)` returns ordered runs,
  `comparison_summary`, and backend failure details.
- Backend failures are isolated and return schema-valid failed
  `ExperimentRun` records.
- The additive helper does not change the EXP-001 `ExperimentTask` or
  `ExperimentRun` public schema.

## Verification

Commands run from `task/EXP-V2-001` after merging `origin/dev`:

```bash
python3 -m pytest -q services/research/experiment_orchestrator/test_parallel_dispatch.py
# 3 passed in 1.13s

python3 -m py_compile services/research/experiment_orchestrator/parallel_dispatch.py services/research/experiment_orchestrator/test_parallel_dispatch.py scripts/ai_status.py scripts/git/worker_commit.py
# ok

python3 -m pytest -q scripts/test_ai_status.py scripts/git/test_index_safety.py
# 59 passed in 49.47s
```

## Notes

The task brief path supplied by dispatch,
`.orchestrator/task-briefs/exp_v2_001.md`, was not present in this task
worktree at startup. The task acceptance and lifecycle state were confirmed
from `ai-status.json`, and reviewer approval was confirmed from the review
note above.

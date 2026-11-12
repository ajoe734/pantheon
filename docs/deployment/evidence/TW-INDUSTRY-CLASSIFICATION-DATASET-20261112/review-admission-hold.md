# Review admission hold

## Scope correction received on 2026-11-12

The governed Codex2 `show` read after redispatch confirms that Human/Ops added
all eight paths below to this existing task's artifact contract and reopened
the task at `2026-11-12T15:00:49Z`. The task is `in_progress`, with original
owner Codex2, reviewer Claude, all six acceptance items and all three satisfied
dependencies preserved. The supervisor's refreshed task brief records no active
source-writer overlap. The original rejection below is retained as history;
its corrective scope action is complete.

Fresh `git fetch origin dev` found `421c13abc87a44e2b05d1bf638d9a5866e4d8400`
as the current base, already contained in the task branch. No rebase, merge,
replacement task or replacement PR was needed. The owner resumes the same
PR #7245 with a new committed manifest and exact-head handoff. Independent
review, required CI and supervisor integration remain necessary. Scope repair
does not establish hosted ingestion or rights acceptance.

## Original rejection

The owner attempted governed handoff of PR #7245 at
`c6240b813852bfae150b62f99ff624dcfd3f1d45` using the committed `evidence.json`.
Admission rejected the delivery before entering review because necessary
integration files fall outside the canonical artifact contract. No independent
review, approval or merge is asserted.

Required Human/Ops action: while this existing task is blocked, use the governed
`artifact-contract <task-id> add <path> <reason>` transition for each exact path
below, reconcile any genuine writer conflicts, then `reopen` this task and allow
the supervisor to redispatch Codex2. `scripts/ai_status.py` restricts artifact
contract revision to Human/Ops and lifecycle `todo`/`blocked`; the worker must not
impersonate that actor or hand-edit canonical task JSON.

- `services/registry/candidate_pool.py` — the shared production screen evaluator
- `services/registry/strategy_artifact.schema.json` — bounded operator schema admission
- `services/registry/test_candidate_pool.py` — existing unregistered-catalogue guard test
- `services/source_ingestion/active_universe.py` — existing daily refresh routing
- `services/source_ingestion/feature_catalogue.py` — actual source feature catalogue and readers
- `services/source_ingestion/financial_source_catalog.py` — official source catalogue/template
- `services/training-session/source_dataset_authority.py` — preserve acquisition precision in the production storage reader
- `services/training-session/tests/test_source_dataset_authority.py` — recorded-profile storage-reader regression

All three declared task dependencies were satisfied at dispatch; this is an
external governance hold, not a task dependency blocker. The implementation and
local terminal validation are preserved in the task branch and PR. After scope
correction, refresh the branch against current `dev` and repeat exact-head
handoff; do not create a replacement task or claim `done`.

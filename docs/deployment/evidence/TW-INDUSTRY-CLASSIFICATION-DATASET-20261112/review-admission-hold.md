# Review admission hold

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

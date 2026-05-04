# Review: P2-RL-UPSTREAM-RUNTIME-SMOKE-001

Reviewer: Codex2
Owner: Claude
Task: P2-RL-UPSTREAM-RUNTIME-SMOKE-001 - FinRL RLlib Ray Tune governed runtime activation smoke
Reviewed: 2026-05-01
Disposition: Changes requested

## Findings

1. Required schema/checksum support is present only as dirty worktree state, not in the submitted task commit.

   The evidence bundle claims checksum-bearing dataset/artifact schema support in the FinRL, RLlib, and Ray Tune artifact bundles, and the task acceptance requires reward environment, dataset, and artifact schemas with persisted checksums. In the current worktree, that support comes from uncommitted changes under `services/research/finrl/adapter/` and `services/research/rllib/adapter/`:

   - `services/research/finrl/adapter/__init__.py`
   - `services/research/finrl/adapter/finrl_adapter.py`
   - `services/research/rllib/adapter/__init__.py`
   - `services/research/rllib/adapter/ray_tune_adapter.py`
   - `services/research/rllib/adapter/rllib_adapter.py`

   However, the submitted task commit `2a6a705` contains only the activation smoke scripts, checklist row updates, and persisted evidence. `git show 2a6a705:<adapter file> | rg "dataset_checksum|dataset_schema|environment_schema|prepared_.*_dataset_checksum"` returns no committed support for those fields. That makes the reviewed artifact/evidence state non-reproducible from the task commit and leaves the acceptance evidence dependent on uncommitted local changes.

   Requested fix: include the adapter schema/checksum changes in a task-scoped commit, regenerate the activation evidence from that committed code path if checksums or artifact payloads change, and update the task handoff commit reference.

2. Per-framework handoff manifests omit the evaluator packet checksum.

   In all three smoke scripts, `_persist_artifacts()` adds `evaluator_packet` to the manifest artifact map before the evaluator packet file is written, then computes checksums only for paths that already exist:

   - `services/research/finrl/activation_smoke.py:290`
   - `services/research/rllib/activation_smoke.py:303`
   - `services/research/rllib/ray_tune_activation_smoke.py:309`

   The resulting `handoff_artifact_manifest.checksums` in each framework activation summary contains checksums for `artifact_bundle`, `registry_entry`, and `candidate_packet`, but not `evaluator_packet`. The top-level task manifest does checksum the evaluator packets, so this is not a safety breach, but the per-framework manifest is internally incomplete for the artifact set it advertises.

   Requested fix: write the evaluator packet before computing per-framework manifest checksums, or compute and add the evaluator checksum explicitly before returning the manifest.

3. `OSS_INTEGRATION_CHECKLIST.md` pre-claims task closure while the task is still in review.

   The FinRL, RLlib, and Ray Tune rows currently say task `P2-RL-UPSTREAM-RUNTIME-SMOKE-001` is `closed`, but `ai-status.json` still has the task in `review`. Closeout policy reserves `done`/closure for the owner after reviewer approval and finalization, so canonical checklist wording should not pre-claim lifecycle closure.

   Requested fix: change the row wording to reflect that evidence was produced for `P2-RL-UPSTREAM-RUNTIME-SMOKE-001`, or defer the closure wording to the owner's final closeout commit after review approval.

## Verification Run

```bash
jq -r '.checksums | to_entries[] | "\(.value[7:])  support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/\(.key)"' support/evidence/P2-RL-UPSTREAM-RUNTIME-SMOKE-001/manifest.json | sha256sum -c -
python3 -m unittest discover -s services/research/finrl -p 'test_*.py'
python3 -m unittest discover -s services/research/rllib -p 'test_*.py'
```

Results:

- Task-level manifest checksum verification: OK for every listed file.
- FinRL tests: 16 tests, OK.
- RLlib/Ray Tune tests: 33 tests, OK.

## Notes

The real-backend dependency/config evidence itself matches the acceptance shape: FinRL records missing `finrl`, RLlib and Ray Tune record missing `ray`, each with `silent_stub_fallback=false`, and the persisted handoff artifacts retain closed research-only governance boundaries. The requested changes are about making the submitted task state reproducible, lifecycle-accurate, and internally complete.

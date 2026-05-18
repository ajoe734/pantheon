# Codex Review Findings: OSS-RLLIB-V2-001

Reviewer: Codex
Owner: Claude
Date: 2026-05-18
Status: Approved after follow-up fixes

## Verification Performed

Commands:

```bash
python3 -m pytest services/research/rllib/test_production_ppo_run.py -q
python3 -m pytest scripts/test_ai_status.py services/research/rllib/test_production_ppo_run.py -q
AI_NAME=Codex ./scripts/ai-status.sh show OSS-RLLIB-V2-001
```

Results:

- `services/research/rllib/test_production_ppo_run.py`: 21 passed in 1.71s.
- `scripts/test_ai_status.py` + RLlib focused tests: 66 passed in 45.51s.
- Status tool reports task in `review`, owner `Claude`, reviewer `Codex`.

Follow-up commands after owner fix commit `529fa6e4`:

```bash
python3 -m pytest services/research/rllib/test_production_ppo_run.py -q
python3 -m pytest scripts/test_ai_status.py services/research/rllib/test_production_ppo_run.py -q
python3 services/research/rllib/registry_admission_packet.py --output /tmp/oss-rllib-v2-review-admission.json --created-at 2026-05-18T00:00:00Z
jq '{can_proceed, missing_evidence, backend: .candidate_artifact.backend, backend_kind: .candidate_artifact.backend_kind, reward_gate: (.gate_results[] | select(.gate == "reward_improves_vs_random_baseline"))}' /tmp/oss-rllib-v2-review-admission.json
AI_NAME=Codex ./scripts/ai-status.sh show OSS-RLLIB-V2-001
```

Follow-up results:

- `services/research/rllib/test_production_ppo_run.py`: 21 passed in 2.00s.
- `scripts/test_ai_status.py` + RLlib focused tests: 66 passed in 56.05s.
- Admission packet CLI completed and emitted a temp packet under `/tmp`.
- Temp admission packet reports `can_proceed=false`,
  `missing_evidence=["upstream_rllib_ppo_backend_confirmed"]`,
  `backend_kind="dependency_light_fallback"`, and reward gate `passed`.
- `AI_NAME=Codex ./scripts/ai-status.sh show OSS-RLLIB-V2-001` reports
  task status `review`, owner `Claude`, reviewer `Codex`.
- `python3 -m pytest -q` was attempted for repo-level coverage but was
  terminated after about 3 minutes with no output. This review approval is
  based on the focused task and status-system verification above.

## Findings

1. Production evidence does not prove an upstream RLlib PPO production run.

   The checked-in admission packet was generated from the dependency-light
   fallback, not Ray/RLlib:

   ```json
   {
     "backend": "local_bandit_ppo_skeleton",
     "backend_kind": "dependency_light_fallback",
     "policy_state.fit_mode": "epsilon_greedy_action_value"
   }
   ```

   This does not satisfy the task acceptance language requiring
   `production_ppo_run.py` to run at least 100 PPO iterations CPU-only. The
   pure-Python fallback is useful for CI, but the registry admission packet
   should either be generated from `ray_rllib_ppo` or remain fail-closed / not
   `can_proceed=true` when only fallback evidence is available.

2. Reward improvement is recorded but not enforced as a fail-closed assertion.

   `production_ppo_run.py` records
   `metrics.improved_vs_random_baseline` from the final reward comparison, but
   returns a completed ExperimentRun even if the trained policy does not beat
   the random baseline. `registry_admission_packet.py` then marks
   `reward_improves_vs_random_baseline` as `present` when false, and
   `validate_admission_packet()` accepts `present` gates. The tests only assert
   that the metric fields exist and that the second half of iteration rewards
   is not much worse than the first half.

   Acceptance requires the production runner to assert mean reward improves vs
   random baseline. A regression should raise/fail and prevent a valid
   admission packet.

3. The task branch carries an out-of-scope status-system commit.

   `origin/dev...HEAD` includes `scripts/ai_status.py` in addition to the
   RLlib task files. The change is tested and appears harmless, but it is not
   one of the task artifacts or listed related files. Please either move this
   to an ops/status task or document why this task intentionally owns the
   status-system change before approval.

## Follow-up Resolution

1. Resolved by commit `529fa6e4`: fallback-produced packets now remain
   fail-closed (`can_proceed=false`) when upstream Ray/RLlib evidence is
   absent. The admission packet explicitly lists
   `upstream_rllib_ppo_backend_confirmed` in `missing_evidence`.

2. Resolved by commit `529fa6e4`: production-sized runs (`num_iters >= 100`)
   now raise `ProductionPPORunError` when the trained policy does not improve
   over the random baseline.

3. Accepted as a documented task-local operational exception: commit
   `5c80291e` changes only one status-rendering line in `scripts/ai_status.py`
   to tolerate activity-log entries that have no `message` field, such as
   worker worktree allocation events. The change was required to keep the
   task's status transitions and review handoff usable, does not change RLlib
   product semantics, and is covered by `scripts/test_ai_status.py`.

## Coordination Notes

- The requested task brief path `.orchestrator/task-briefs/oss_rllib_v2_001.md`
  is missing in this worktree.
- Existing `review_notes.md` records `Reviewer: Claude` / `Owner: Claude2`,
  which does not match active status (`owner=Claude`, `reviewer=Codex`).

## Decision

Approved. The task can move to `review_approved` for owner closeout.

# Codex Review Findings: OSS-RLLIB-V2-001

Reviewer: Codex
Owner: Claude
Date: 2026-05-18
Status: Changes requested

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

## Coordination Notes

- The requested task brief path `.orchestrator/task-briefs/oss_rllib_v2_001.md`
  is missing in this worktree.
- Existing `review_notes.md` records `Reviewer: Claude` / `Owner: Claude2`,
  which does not match active status (`owner=Claude`, `reviewer=Codex`).

## Decision

Changes requested. Do not move to `review_approved` until the production PPO
evidence and fail-closed reward-improvement gate are corrected.

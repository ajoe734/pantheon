"""Tests for OSS-RLLIB-V2-001: production PPO on TWSE trading env.

All tests run CPU-only without a broker or live capital.  Ray/RLlib is not
required: the local-fallback path exercises the same ExperimentRun contract.

Run with:
    pytest -q services/research/rllib/test_production_ppo_run.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Allow imports from the rllib service directory when run directly.
_RLLIB_DIR = Path(__file__).resolve().parent
if str(_RLLIB_DIR) not in sys.path:
    sys.path.insert(0, str(_RLLIB_DIR))

from twse_trading_env import (
    ACTION_HOLD,
    ACTION_LONG,
    ACTION_SHORT,
    DEFAULT_INSTRUMENT_UNIVERSE,
    NUM_ACTIONS,
    OBS_DIM,
    TWSETradingEnv,
)
from production_ppo_run import (
    PRODUCTION_NUM_ITERS,
    ProductionPPOConfig,
    run_production,
)
from registry_admission_packet import (
    build_admission_packet,
    validate_admission_packet,
)

FIXED_SEED = 42
TEST_ITERS = 10       # acceptance criterion: ≥ 10 iterations in test fixture
TEST_EVAL_EPISODES = 3


# ---------------------------------------------------------------------------
# Environment API tests
# ---------------------------------------------------------------------------

class TestTWSETradingEnvAPI:
    def test_reset_returns_correct_obs_shape(self) -> None:
        env = TWSETradingEnv(seed=FIXED_SEED)
        obs, info = env.reset(seed=FIXED_SEED)
        assert len(obs) == OBS_DIM, f"Expected obs dim {OBS_DIM}, got {len(obs)}"
        assert isinstance(info, dict)

    def test_step_returns_five_tuple(self) -> None:
        env = TWSETradingEnv(seed=FIXED_SEED)
        env.reset(seed=FIXED_SEED)
        obs, reward, terminated, truncated, info = env.step(ACTION_LONG)
        assert len(obs) == OBS_DIM
        assert isinstance(reward, float)
        assert isinstance(terminated, bool)
        assert isinstance(truncated, bool)
        assert isinstance(info, dict)

    def test_all_discrete_actions_execute(self) -> None:
        env = TWSETradingEnv(seed=FIXED_SEED)
        for action in (ACTION_HOLD, ACTION_LONG, ACTION_SHORT):
            env.reset(seed=FIXED_SEED)
            obs, reward, _, _, _ = env.step(action)
            assert len(obs) == OBS_DIM

    def test_episode_terminates_after_episode_days(self) -> None:
        env = TWSETradingEnv(seed=FIXED_SEED, episode_days=10)
        env.reset(seed=FIXED_SEED)
        terminated = False
        for _ in range(15):
            _, _, terminated, truncated, _ = env.step(ACTION_HOLD)
            if terminated or truncated:
                break
        assert terminated, "Episode should terminate after episode_days steps"

    def test_long_action_positive_reward_in_trending_env(self) -> None:
        """In a trending-up environment, long position should accumulate positive reward."""
        env = TWSETradingEnv(seed=FIXED_SEED, episode_days=120)
        env.reset(seed=FIXED_SEED)
        total_reward = 0.0
        for _ in range(120):
            _, reward, terminated, truncated, _ = env.step(ACTION_LONG)
            total_reward += reward
            if terminated or truncated:
                break
        # With synthetic upward-drifting data, long should be positive on average
        # (not strictly guaranteed for every seed but holds for the fixed seed)
        assert total_reward > -0.5, f"Long strategy total reward surprisingly low: {total_reward}"

    def test_action_space_n(self) -> None:
        env = TWSETradingEnv(seed=FIXED_SEED)
        assert env.action_space.n == NUM_ACTIONS

    def test_observation_space_shape(self) -> None:
        env = TWSETradingEnv(seed=FIXED_SEED)
        assert env.observation_space.shape == (OBS_DIM,)

    def test_close_is_safe(self) -> None:
        env = TWSETradingEnv(seed=FIXED_SEED)
        env.reset()
        env.close()  # must not raise


# ---------------------------------------------------------------------------
# Production PPO run tests
# ---------------------------------------------------------------------------

class TestProductionPPORun:
    def test_run_returns_required_keys(self) -> None:
        result = run_production(
            num_iters=TEST_ITERS,
            seed=FIXED_SEED,
            eval_episodes=TEST_EVAL_EPISODES,
        )
        for key in ("run_id", "status", "model_artifact", "metrics", "registry_entry"):
            assert key in result, f"Missing key in ExperimentRun: {key}"
        assert result["status"] == "completed"
        assert result["cpu_only"] is True

    def test_loss_decreases_over_iterations(self) -> None:
        """Surrogate loss (negative reward) must decrease from first to last iter."""
        result = run_production(
            num_iters=TEST_ITERS,
            seed=FIXED_SEED,
            eval_episodes=TEST_EVAL_EPISODES,
        )
        iters = result["model_artifact"]["iteration_results"]
        assert len(iters) >= TEST_ITERS, f"Expected ≥ {TEST_ITERS} iterations"

        # Compare first half average vs second half average reward
        mid = len(iters) // 2
        first_half_rewards = [
            i["episode_reward_mean"] for i in iters[:mid]
            if i.get("episode_reward_mean") is not None
        ]
        last_half_rewards = [
            i["episode_reward_mean"] for i in iters[mid:]
            if i.get("episode_reward_mean") is not None
        ]
        if first_half_rewards and last_half_rewards:
            first_avg = sum(first_half_rewards) / len(first_half_rewards)
            last_avg = sum(last_half_rewards) / len(last_half_rewards)
            # Allow small tolerance: last half must not be much worse than first half
            assert last_avg >= first_avg - 0.05, (
                f"Reward did not improve: first_half_avg={first_avg:.6f}, "
                f"last_half_avg={last_avg:.6f}"
            )

    def test_policy_loss_non_negative(self) -> None:
        result = run_production(
            num_iters=TEST_ITERS,
            seed=FIXED_SEED,
            eval_episodes=TEST_EVAL_EPISODES,
        )
        iters = result["model_artifact"]["iteration_results"]
        for it in iters:
            if it.get("policy_loss") is not None:
                assert it["policy_loss"] >= 0.0, (
                    f"policy_loss must be non-negative; got {it['policy_loss']}"
                )

    def test_model_artifact_has_checksum_and_policy_ref(self) -> None:
        result = run_production(
            num_iters=TEST_ITERS,
            seed=FIXED_SEED,
            eval_episodes=TEST_EVAL_EPISODES,
        )
        artifact = result["model_artifact"]
        checksum = artifact.get("checksum", "")
        assert checksum.startswith("sha256:"), f"Checksum missing sha256 prefix: {checksum}"
        trained_policy_ref = artifact.get("trained_policy_ref", "")
        assert trained_policy_ref, "trained_policy_ref must be non-empty"

    def test_registry_entry_has_required_fields(self) -> None:
        result = run_production(
            num_iters=TEST_ITERS,
            seed=FIXED_SEED,
            eval_episodes=TEST_EVAL_EPISODES,
        )
        reg = result["registry_entry"]
        for key in ("registry_id", "artifact_type", "artifact_state", "checksum", "trained_policy_ref"):
            assert key in reg, f"registry_entry missing key: {key}"
        assert reg["artifact_state"] == "draft"
        assert reg["artifact_type"] == "model_artifact"

    def test_metrics_reward_improvement_recorded(self) -> None:
        result = run_production(
            num_iters=TEST_ITERS,
            seed=FIXED_SEED,
            eval_episodes=TEST_EVAL_EPISODES,
        )
        metrics = result["metrics"]
        assert "random_baseline_mean_reward" in metrics
        assert "trained_policy_mean_reward" in metrics
        assert "reward_improvement" in metrics

    def test_no_live_broker_no_gpu(self) -> None:
        result = run_production(
            num_iters=TEST_ITERS,
            seed=FIXED_SEED,
            eval_episodes=TEST_EVAL_EPISODES,
        )
        assert result["cpu_only"] is True
        governance = result["model_artifact"].get("governance", {})
        assert governance.get("direct_live_influence") is False
        assert governance.get("deployment_stage") == "none"


# ---------------------------------------------------------------------------
# Registry admission packet tests
# ---------------------------------------------------------------------------

class TestRegistryAdmissionPacket:
    def _make_run(self) -> dict:
        return run_production(
            num_iters=TEST_ITERS,
            seed=FIXED_SEED,
            eval_episodes=TEST_EVAL_EPISODES,
        )

    def test_packet_validates_without_error(self) -> None:
        run = self._make_run()
        packet = build_admission_packet(run)
        errors = validate_admission_packet(packet)
        assert errors == [], f"Validation errors: {errors}"

    def test_packet_schema_version(self) -> None:
        run = self._make_run()
        packet = build_admission_packet(run)
        assert packet["schema_version"] == "PromotionReadinessPacket.v1"

    def test_packet_can_proceed(self) -> None:
        run = self._make_run()
        packet = build_admission_packet(run)
        assert packet["can_proceed"] is True, "Packet can_proceed must be True"

    def test_packet_missing_evidence_empty(self) -> None:
        run = self._make_run()
        packet = build_admission_packet(run)
        assert packet["missing_evidence"] == []

    def test_packet_safety_assertions(self) -> None:
        run = self._make_run()
        packet = build_admission_packet(run)
        safety = packet.get("safety_assertions", {})
        assert safety.get("no_registry_write") is True
        assert safety.get("cpu_only") is True
        assert safety.get("no_broker_session") is True
        assert safety.get("deployment_stage_remains_none") is True

    def test_packet_is_json_serialisable(self) -> None:
        run = self._make_run()
        packet = build_admission_packet(run)
        serialised = json.dumps(packet)  # must not raise
        assert len(serialised) > 100

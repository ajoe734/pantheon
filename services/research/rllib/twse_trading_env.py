"""Gymnasium-compatible TWSE trading environment for OSS-RLLIB-V2-001.

Wraps a 5-instrument synthetic TWSE OHLCV universe with a discrete action
space (hold=0 / long=1 / short=2). When gymnasium is importable the class
inherits from gymnasium.Env and its spaces are proper Gymnasium objects.
Without gymnasium the same reset()/step()/close() contract is honoured via
a pure-Python fallback so the local PPO runner and unit tests can drive it
without any optional dependency.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

TASK_ID = "OSS-RLLIB-V2-001"

DEFAULT_INSTRUMENT_UNIVERSE: List[str] = [
    "2330.TW",
    "2317.TW",
    "2454.TW",
    "2412.TW",
    "1301.TW",
]
NUM_INSTRUMENTS = 5
EPISODE_DAYS = 120         # ~6 months of TWSE trading days per episode
INITIAL_BALANCE = 1.0      # normalised portfolio value at episode start

ACTION_HOLD = 0
ACTION_LONG = 1
ACTION_SHORT = 2
NUM_ACTIONS = 3

# Observation: 5 normalised close prices + current portfolio-return signal
OBS_DIM = NUM_INSTRUMENTS + 1  # = 6

# Synthetic GBM parameters per instrument (daily scale)
_P0 = [550.0, 100.0, 900.0, 80.0, 105.0]
_MU_DAILY = [0.000595, 0.000397, 0.000476, 0.000317, 0.000238]   # ~15-6 % ann.
_SIGMA_DAILY = [0.01575, 0.01890, 0.01764, 0.02205, 0.01260]     # ~25-20 % ann.


# ---------------------------------------------------------------------------
# Fallback space objects (used when gymnasium is not installed)
# ---------------------------------------------------------------------------

class _FallbackBox:
    """Minimal Box-like descriptor for dependency-free testing."""

    def __init__(self, low: float, high: float, shape: Tuple[int, ...]) -> None:
        self.low = low
        self.high = high
        self.shape = shape
        self.n = None  # Discrete uses .n, Box does not

    def sample(self, rng: Optional[random.Random] = None) -> List[float]:
        _rng = rng or random.Random()
        return [_rng.uniform(self.low, self.high) for _ in range(self.shape[0])]


class _FallbackDiscrete:
    """Minimal Discrete-like descriptor for dependency-free testing."""

    def __init__(self, n: int) -> None:
        self.n = n
        self.shape = ()

    def sample(self, rng: Optional[random.Random] = None) -> int:
        _rng = rng or random.Random()
        return _rng.randint(0, self.n - 1)


# ---------------------------------------------------------------------------
# Environment base — inherits from gymnasium.Env when available
# ---------------------------------------------------------------------------

def _resolve_base() -> type:
    try:
        gym = importlib.import_module("gymnasium")
        return gym.Env  # type: ignore[return-value]
    except ImportError:
        return object


_EnvBase: type = _resolve_base()


class TWSETradingEnv(_EnvBase):  # type: ignore[misc]
    """Gymnasium-compatible TWSE trading environment.

    Observation (6 floats):
        [close_0/base_0, ..., close_4/base_4, portfolio_return_since_start]

    Action (Discrete 3):
        0 = HOLD   — maintain current position
        1 = LONG   — open / maintain long position (all instruments equal weight)
        2 = SHORT  — open / maintain short position (all instruments equal weight)

    Reward:
        Proportional portfolio step-return:  (pv[t] - pv[t-1]) / initial_balance
    """

    metadata: Dict[str, Any] = {"render_modes": []}

    def __init__(
        self,
        *,
        seed: int = 42,
        episode_days: int = EPISODE_DAYS,
        instrument_universe: Sequence[str] = DEFAULT_INSTRUMENT_UNIVERSE,
    ) -> None:
        super().__init__()

        self._seed = int(seed)
        self._episode_days = int(episode_days)
        self._instruments: List[str] = list(instrument_universe)[:NUM_INSTRUMENTS]
        if len(self._instruments) < NUM_INSTRUMENTS:
            raise ValueError(
                f"instrument_universe must have at least {NUM_INSTRUMENTS} entries"
            )

        # Build spaces — gymnasiam.spaces when available, fallback otherwise
        try:
            gym = importlib.import_module("gymnasium")
            spaces = importlib.import_module("gymnasium.spaces")
            self.observation_space = spaces.Box(
                low=-10.0, high=10.0, shape=(OBS_DIM,), dtype="float32"
            )
            self.action_space = spaces.Discrete(NUM_ACTIONS)
        except ImportError:
            self.observation_space = _FallbackBox(-10.0, 10.0, (OBS_DIM,))
            self.action_space = _FallbackDiscrete(NUM_ACTIONS)

        # Episode state (populated by reset())
        self._price_series: List[List[float]] = []  # [instrument][day] close prices
        self._step_idx: int = 0
        self._base_prices: List[float] = list(_P0[:NUM_INSTRUMENTS])
        self._position: int = ACTION_HOLD
        self._portfolio_value: float = INITIAL_BALANCE
        self._rng: random.Random = random.Random(self._seed)

    # ------------------------------------------------------------------
    # Gymnasium API
    # ------------------------------------------------------------------

    def reset(
        self,
        *,
        seed: Optional[int] = None,
        options: Optional[Mapping[str, Any]] = None,
    ) -> Tuple[List[float], Dict[str, Any]]:
        if seed is not None:
            self._seed = int(seed)
        self._rng = random.Random(self._seed)
        self._price_series = _generate_price_series(
            self._episode_days, self._rng
        )
        self._base_prices = [col[0] for col in self._price_series]
        self._step_idx = 0
        self._position = ACTION_HOLD
        self._portfolio_value = INITIAL_BALANCE
        obs = self._build_obs()
        return obs, {}

    def step(
        self, action: int
    ) -> Tuple[List[float], float, bool, bool, Dict[str, Any]]:
        action = int(action)
        prev_pv = self._portfolio_value

        # Compute per-instrument step return at current time step
        if self._step_idx > 0:
            step_returns = [
                (self._price_series[i][self._step_idx] - self._price_series[i][self._step_idx - 1])
                / self._price_series[i][self._step_idx - 1]
                for i in range(NUM_INSTRUMENTS)
            ]
        else:
            step_returns = [0.0] * NUM_INSTRUMENTS

        mean_step_return = sum(step_returns) / NUM_INSTRUMENTS

        # Update portfolio value based on new position
        self._position = action
        if action == ACTION_LONG:
            self._portfolio_value = prev_pv * (1.0 + mean_step_return)
        elif action == ACTION_SHORT:
            self._portfolio_value = prev_pv * (1.0 - mean_step_return)
        # HOLD: portfolio value unchanged

        reward = (self._portfolio_value - prev_pv) / INITIAL_BALANCE

        self._step_idx += 1
        terminated = self._step_idx >= self._episode_days
        obs = self._build_obs()
        return obs, reward, terminated, False, {
            "portfolio_value": self._portfolio_value,
            "position": self._position,
            "step_idx": self._step_idx,
        }

    def close(self) -> None:
        pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _build_obs(self) -> List[float]:
        idx = min(self._step_idx, self._episode_days - 1)
        normed_closes = [
            self._price_series[i][idx] / self._base_prices[i]
            for i in range(NUM_INSTRUMENTS)
        ]
        portfolio_return = self._portfolio_value - INITIAL_BALANCE
        return normed_closes + [portfolio_return]

    def seed(self, seed: Optional[int] = None) -> List[int]:
        if seed is not None:
            self._seed = int(seed)
        return [self._seed]


# ---------------------------------------------------------------------------
# Synthetic data generation
# ---------------------------------------------------------------------------

def _generate_price_series(
    days: int, rng: random.Random
) -> List[List[float]]:
    """Generate GBM price series for NUM_INSTRUMENTS instruments."""
    series: List[List[float]] = []
    for i in range(NUM_INSTRUMENTS):
        mu = _MU_DAILY[i]
        sigma = _SIGMA_DAILY[i]
        price = _P0[i]
        col: List[float] = [price]
        for _ in range(days - 1):
            z = rng.gauss(0.0, 1.0)
            price = price * math.exp((mu - 0.5 * sigma ** 2) + sigma * z)
            col.append(max(price, 1.0))
        series.append(col)
    return series


def make_env_config(
    seed: int = 42,
    episode_days: int = EPISODE_DAYS,
    instrument_universe: Sequence[str] = DEFAULT_INSTRUMENT_UNIVERSE,
) -> Dict[str, Any]:
    """Return an env_config dict suitable for Ray/RLlib env registration."""
    return {
        "seed": seed,
        "episode_days": episode_days,
        "instrument_universe": list(instrument_universe),
    }


ENV_NAME = "TWSETradingEnv-v1"


def register_env() -> None:
    """Register TWSETradingEnv with Ray Tune if ray.tune is importable."""
    try:
        tune = importlib.import_module("ray.tune")
        tune.register_env(ENV_NAME, lambda cfg: TWSETradingEnv(**cfg))
    except ImportError:
        pass


__all__ = [
    "ACTION_HOLD",
    "ACTION_LONG",
    "ACTION_SHORT",
    "DEFAULT_INSTRUMENT_UNIVERSE",
    "ENV_NAME",
    "NUM_ACTIONS",
    "NUM_INSTRUMENTS",
    "OBS_DIM",
    "TWSETradingEnv",
    "make_env_config",
    "register_env",
]

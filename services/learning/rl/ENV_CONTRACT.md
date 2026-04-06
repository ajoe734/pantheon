# RLlib Environment Contract

**Scope**: Define the exact interface between LEAN execution, RLlib trainer, and Ray Tune search.  
**Purpose**: Enable deterministic, reproducible training runs across the Pantheon collaboration.  
**Status**: Reference specification

---

## 1. Environment Interface (RLlib Convention)

### Python Class Template

```python
from gym import Env
from gym.spaces import Box, Discrete
import numpy as np

class PantheonPortfolioEnv(Env):
    """
    RLlib-compatible environment for portfolio management via RL policy.
    
    Inherits from gym.Env to work with RLlib's standard training API.
    """
    
    def __init__(self, config: dict):
        """
        Initialize environment from config dict.
        
        Args:
            config: {
                "problem": "exit_timing",
                "tickers": ["MSFT", "AAPL"],
                "data_path": "s3://pantheon-data/portfolio_exit_v1.parquet",
                "start_date": "2023-01-01",
                "end_date": "2025-06-30",
                "lookback_window": 20,
                "episode_length": 252,
                "initial_cash": 100000,
                "max_position_size": 0.3,
                "transaction_cost_pct": 0.001,
                "slippage_bps": 5,
                "reward_fn_config": {
                    "sharpe_weight": 1.0,
                    "dd_penalty": 0.5,
                    "cost_penalty": 0.1
                }
            }
        """
        self.config = config
        self.lookback_window = config.get("lookback_window", 20)
        self.episode_length = config.get("episode_length", 252)
        self.n_assets = len(config.get("tickers", []))
        self.transaction_cost_pct = config.get("transaction_cost_pct", 0.001)
        self.slippage_bps = config.get("slippage_bps", 5)
        
        # Load historical OHLCV data
        self.data = self._load_data()
        self.data_indices = self._get_episode_indices()
        
        # Observation space: (lookback_window, num_features)
        # Features: [open, high, low, close, volume, holdings, cash, portfolio_value]
        num_features = 4 + 1 + 3  # OHLCV + 4 portfolio state features
        self.observation_space = Box(
            low=0.0,
            high=1.0,
            shape=(self.lookback_window * self.n_assets, num_features),
            dtype=np.float32
        )
        
        # Action space: discrete portfolio actions per asset
        # {SELL_ALL, SELL_HALF, HOLD, BUY_HALF, BUY_ALL}
        self.action_space = Discrete(5 ** self.n_assets)
        
        self.reset()
    
    def reset(self):
        """
        Reset environment to initial state of a new episode.
        
        Returns: Initial observation.
        """
        # Sample random episode from data
        self.episode_idx = np.random.choice(len(self.data_indices))
        start_idx, end_idx = self.data_indices[self.episode_idx]
        
        self.current_step = 0
        self.episode_start_idx = start_idx
        self.data_idx = start_idx + self.lookback_window
        
        # Initialize portfolio state
        self.holdings = np.zeros(self.n_assets)
        self.cash = self.config.get("initial_cash", 100000)
        self.portfolio_values = [self.cash]
        
        return self._get_observation()
    
    def step(self, action):
        """
        Execute one step of environment interaction.
        
        Args:
            action: integer in [0, 5^n_assets) encoding portfolio action
            
        Returns:
            (observation, reward, done, info)
        """
        # Decode action to position deltas
        position_deltas = self._decode_action(action)
        
        # Execute trades (with slippage, transaction costs)
        new_holdings, cost = self._execute_trades(position_deltas)
        
        # Update cash
        self.cash -= cost
        
        # Step forward in data
        self.data_idx += 1
        self.current_step += 1
        
        # Compute reward
        reward = self._compute_reward(new_holdings, cost)
        
        # Check done
        done = (self.current_step >= self.episode_length or 
                self.data_idx >= self.data_indices[self.episode_idx][1])
        
        # Log info
        info = {
            "portfolio_value": self._get_portfolio_value(new_holdings),
            "transaction_cost": cost,
            "returns": self._get_returns(new_holdings),
        }
        
        self.holdings = new_holdings
        
        return self._get_observation(), reward, done, info
    
    def _get_observation(self) -> np.ndarray:
        """
        Construct normalized observation from current state.
        
        Returns: (lookback_window * n_assets * num_features,) array
        """
        # Fetch lookback window of OHLCV data
        start = self.data_idx - self.lookback_window
        end = self.data_idx
        
        # data shape: (time, n_assets, 4) for OHLCV
        ohlcv_window = self.data[start:end]  # (lookback_window, n_assets, 4)
        
        # Compute portfolio state features
        portfolio_value = self._get_portfolio_value(self.holdings)
        holdings_normalized = self.holdings / (portfolio_value + 1e-8)
        cash_ratio = self.cash / (portfolio_value + 1e-8)
        
        # Construct observation: concatenate OHLCV with portfolio state
        # Flatten and normalize to [0, 1]
        obs_list = []
        for i in range(self.n_assets):
            ohlcv = ohlcv_window[:, i, :]  # (lookback_window, 4)
            # Normalize prices by last close
            last_close = ohlcv[-1, 3]
            ohlcv_norm = ohlcv / (last_close + 1e-8)
            
            # Append portfolio state (broadcasted per asset)
            portfolio_state = np.array([
                holdings_normalized[i],
                cash_ratio,
                portfolio_value,
                ohlcv_window[-1, i, 3]  # Last close (reference)
            ])
            
            obs_list.append(np.concatenate([
                ohlcv_norm.flatten(),
                np.tile(portfolio_state, (self.lookback_window, 1)).flatten()
            ]))
        
        obs = np.concatenate(obs_list).astype(np.float32)
        # Clip to [0, 1]
        obs = np.clip(obs, 0.0, 1.0)
        
        return obs
    
    def _decode_action(self, action: int) -> np.ndarray:
        """
        Decode integer action to position deltas per asset.
        
        Args:
            action: int in [0, 5^n_assets)
            
        Returns:
            position_deltas: (n_assets,) array of target position changes (%)
        """
        # Map: 0=SELL_ALL (-1.0), 1=SELL_HALF (-0.5), 2=HOLD (0), 
        #      3=BUY_HALF (0.5), 4=BUY_ALL (1.0)
        action_map = np.array([-1.0, -0.5, 0.0, 0.5, 1.0])
        
        deltas = []
        for i in range(self.n_assets):
            asset_action = (action // (5 ** i)) % 5
            deltas.append(action_map[asset_action])
        
        return np.array(deltas)
    
    def _execute_trades(self, position_deltas: np.ndarray) -> tuple:
        """
        Execute portfolio trades with slippage and transaction costs.
        
        Args:
            position_deltas: (n_assets,) target position changes
            
        Returns:
            (new_holdings, total_cost)
        """
        portfolio_value = self._get_portfolio_value(self.holdings)
        
        # Compute target positions (as % of portfolio)
        target_values = position_deltas * portfolio_value * 0.5  # Limit to 50% swing per step
        target_values = np.clip(
            target_values,
            -portfolio_value * self.config.get("max_position_size", 0.5),
            portfolio_value * self.config.get("max_position_size", 0.5)
        )
        
        # Current market prices (last close from data)
        prices = self.data[self.data_idx - 1, :, 3]  # Close price
        
        # Compute shares to buy/sell
        current_values = self.holdings * prices
        shares_delta = (target_values - current_values) / (prices + 1e-8)
        
        # Compute slippage cost
        slippage_cost = np.sum(np.abs(shares_delta * prices)) * (self.slippage_bps / 10000)
        
        # Compute transaction cost
        transaction_cost = np.sum(np.abs(shares_delta * prices)) * self.transaction_cost_pct
        
        total_cost = slippage_cost + transaction_cost
        
        # Update holdings
        new_holdings = self.holdings + shares_delta
        new_holdings = np.clip(new_holdings, 0.0, None)  # No short selling
        
        return new_holdings, total_cost
    
    def _compute_reward(self, new_holdings: np.ndarray, cost: float) -> float:
        """
        Compute reward from portfolio change and costs.
        
        Uses: reward = return - cost_penalty * cost
        
        Returns: scalar reward
        """
        old_pv = self._get_portfolio_value(self.holdings)
        new_pv = self._get_portfolio_value(new_holdings) - cost
        
        returns = (new_pv - old_pv) / (old_pv + 1e-8)
        
        reward = returns - self.config.get("cost_penalty", 0.1) * (cost / old_pv)
        
        return float(reward)
    
    def _get_portfolio_value(self, holdings: np.ndarray) -> float:
        """Get total portfolio value at current step."""
        prices = self.data[self.data_idx - 1, :, 3]
        return float(np.sum(holdings * prices) + self.cash)
    
    def _get_returns(self, holdings: np.ndarray) -> float:
        """Get cumulative returns."""
        current_value = self._get_portfolio_value(holdings)
        initial_value = self.config.get("initial_cash", 100000)
        return (current_value - initial_value) / initial_value
    
    def _load_data(self) -> np.ndarray:
        """Load OHLCV data from source (S3, parquet, etc.)."""
        # Implementation: fetch from data_path
        # Returns: (time_steps, n_assets, 4) array [open, high, low, close]
        raise NotImplementedError("Implement data loading from config['data_path']")
    
    def _get_episode_indices(self) -> list:
        """
        Compute valid episode start/end indices for training.
        
        Returns: list of (start_idx, end_idx) tuples
        """
        max_steps = self.episode_length + self.lookback_window
        total_steps = len(self.data)
        
        indices = []
        for start in range(0, total_steps - max_steps, 100):  # Stride: 100
            end = start + max_steps
            indices.append((start, end))
        
        return indices
```

---

## 2. Data Format and Normalization

### Input Data Structure

```
OHLCV Parquet:
  columns: [timestamp, ticker, open, high, low, close, volume, ...]
  index: timestamp (datetime)
  
Portfolio State DataFrame:
  columns: [timestamp, ticker, holdings, avg_cost, current_value, ...]
  index: timestamp (datetime)
```

### Normalization Rules

1. **Price Data (OHLCV)**:
   - Divide each bar by its close price.
   - Clamp to [0.5, 2.0] to handle gap opens/closes.
   - Result: Normalized bar shape around 1.0.

2. **Volume**:
   - Log-scale: log(volume + 1).
   - Normalize by rolling mean (20-day window).
   - Clamp to [0, 1] for feature stability.

3. **Portfolio State**:
   - Holdings: [0, max_position_size] (e.g., [0, 0.5] for 50% max per asset).
   - Cash: [0, 1] where 1 = all cash.
   - Portfolio Value: [0, ∞] in dollar terms; normalize by initial capital.

---

## 3. Training Configuration (Ray Tune)

### Example Config for PPO Trainer

```yaml
# config_ppo_portfolio.yaml
algorithm: PPO
framework: torch  # or "tf2" for TensorFlow
num_workers: 4
num_gpus: 0  # Set to 1 if GPU available
batch_mode: "truncate_episodes"
sgd_minibatch_size: 128
train_batch_size: 4000

# Environment
env: "PantheonPortfolioEnv"
env_config:
  problem: "exit_timing"
  tickers: ["MSFT", "AAPL", "NVDA"]
  data_path: "s3://pantheon-data/portfolio_exit_training_2023_2025.parquet"
  start_date: "2023-01-01"
  end_date: "2025-06-30"
  lookback_window: 20
  episode_length: 252
  initial_cash: 100000
  max_position_size: 0.3
  transaction_cost_pct: 0.001
  slippage_bps: 5
  reward_fn_config:
    sharpe_weight: 1.0
    dd_penalty: 0.5
    cost_penalty: 0.1

# Model
model:
  fcnet_hiddens: [256, 256]
  fcnet_activation: "relu"
  vf_share_layers: False

# PPO hyperparameters
lr: 5e-4
gamma: 0.99
lambda: 0.97
clip_param: 0.2
entropy_coeff: 0.002
vf_clip_param: 10.0
grad_clip: 0.5

# Training
num_sgd_iter: 20
sample_batch_size: 200
num_rollout_workers: 4
callbacks: {}

# Stopping criteria
stop:
  episode_reward_mean: 1.0  # Placeholder; customize per problem
  timesteps_total: 1000000
```

---

## 4. Validation and Testing Split

### Temporal Split

```
Training:    2023-01 to 2025-06 (66% of data)
Validation:  2025-07 to 2025-12 (17% of data)
Test:        2026-01 to 2026-03 (17% of data, held-out from training)
```

### Offline Evaluation

After training completes:

1. **Validation Metric**:
   ```
   validation_sharpe = compute_sharpe(
       returns=trainer.evaluate(
           env_config={...validation data...}
       )
   )
   ```

2. **Test Metric** (after successful validation):
   ```
   test_sharpe = compute_sharpe(
       returns=trainer.evaluate(
           env_config={...test data...}
       )
   )
   requirement: test_sharpe >= 0.8 * validation_sharpe
   ```

---

## 5. State Space and Action Space Details

### State Space Example (4-asset portfolio)

Dimension: 20 * 4 * 8 = 640 features

```
For each asset i in {0, 1, 2, 3}:
  For each lookback step t in {1, ..., 20}:
    [O_i[t], H_i[t], L_i[t], C_i[t], V_i[t],
     holdings[i], cash_ratio, portfolio_value]
```

### Action Space Example (3-asset portfolio)

Dimension: 5^3 = 125 possible actions

```
Action 0: [SELL_ALL, SELL_ALL, SELL_ALL]
Action 1: [SELL_ALL, SELL_ALL, SELL_HALF]
...
Action 62: [HOLD, HOLD, HOLD]
...
Action 124: [BUY_ALL, BUY_ALL, BUY_ALL]
```

---

## 6. Reproducibility Checksum

To ensure training reproducibility:

1. **Fix Random Seed**:
   ```python
   np.random.seed(42)
   torch.manual_seed(42)
   ray.tune.run(..., config={..., "seed": 42})
   ```

2. **Checksum Data**:
   ```python
   import hashlib
   data_hash = hashlib.sha256(data.to_numpy().tobytes()).hexdigest()
   # Store in metadata: data_hash = "abc123..."
   ```

3. **Version Control**:
   - Store config YAML in git.
   - Log output metrics to JSON.
   - Archive trained model artifacts to S3 with checksums.

---

## References

- RLlib Documentation: https://docs.ray.io/en/latest/rllib/
- Gym Environment API: https://gym.openai.com/docs/
- PATH_DEFINITION.md: High-level RL integration roadmap

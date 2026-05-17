"""
TWSE FinRL StockTradingEnv wrapper.
"""
from finrl.meta.env_stock_trading.env_stocktrading import StockTradingEnv
import pandas as pd
import numpy as np

class TWSESerialEnv(StockTradingEnv):
    def __init__(self, df, **kwargs):
        # The StockTradingEnv constructor usually expects:
        # df, stock_dim, hmax, initial_amount, buy_cost_pct, sell_cost_pct, reward_scaling, etc.
        # Based on typical FinRL usage.
        
        # We assume df is already preprocessed OHLCV
        super().__init__(df, **kwargs)

    def reset(self):
        return super().reset()

    def step(self, actions):
        return super().step(actions)

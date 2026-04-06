
import logging
import sys
from unittest.mock import MagicMock
from services.execution.lean_runtime.executor import execute

# Setup logging to see what's happening
logging.basicConfig(level=logging.INFO, stream=sys.stdout)

def test_exit_short_truncation():
    print("\n--- Testing EXIT+SHORT truncation ---")
    mock_algo = MagicMock()
    # Mock holdings as -10.5 shares (e.g. Crypto)
    mock_algo.Portfolio = {
        "BTCUSDT": MagicMock(Quantity=-10.5)
    }
    
    signal = {
        "signal_id": "test-1",
        "action": "EXIT",
        "direction": "SHORT",
        "symbol": "BTCUSDT",
        "quantity": 0, # EXIT doesn't use quantity in current implementation
        "quantity_type": "SHARES",
        "timestamp": "2026-04-05T12:00:00Z",
        "version": "1.0"
    }
    
    execute(signal, mock_algo)
    
    # Check what order was placed
    # Current implementation: algo.MarketOrder(lean_symbol, abs(int(holdings)))
    # For -10.5, int(-10.5) is -10, abs is 10. We missed 0.5!
    args, kwargs = mock_algo.MarketOrder.call_args
    print(f"MarketOrder called with: {args}")
    if args[1] == 10:
        print("FAIL: Truncated 10.5 to 10")
    else:
        print(f"SUCCESS: Order quantity is {args[1]}")

def test_exit_long_inconsistency():
    print("\n--- Testing EXIT+LONG inconsistency ---")
    mock_algo = MagicMock()
    # Mock holdings as -10 (We are SHORT)
    mock_algo.Portfolio = {
        "AAPL.US": MagicMock(Quantity=-10)
    }
    
    signal = {
        "signal_id": "test-2",
        "action": "EXIT",
        "direction": "LONG",
        "symbol": "AAPL.US",
        "quantity": 0,
        "quantity_type": "SHARES",
        "timestamp": "2026-04-05T12:00:00Z",
        "version": "1.0"
    }
    
    execute(signal, mock_algo)
    
    # Current implementation: always calls algo.Liquidate(lean_symbol)
    # This will close the SHORT position even though signal was EXIT+LONG!
    if mock_algo.Liquidate.called:
        print("FAIL: Liquidate called on EXIT+LONG while holding SHORT position")
    else:
        print("SUCCESS: Liquidate NOT called")

def test_sell_long_consistency():
    print("\n--- Testing SELL+LONG consistency ---")
    mock_algo = MagicMock()
    
    signal = {
        "signal_id": "test-3",
        "action": "SELL",
        "direction": "LONG",
        "symbol": "AAPL.US",
        "quantity": 0.5,
        "quantity_type": "PERCENT_PORTFOLIO",
        "timestamp": "2026-04-05T12:00:00Z",
        "version": "1.0"
    }
    
    execute(signal, mock_algo)
    
    # Current implementation: always calls Liquidate
    # Should probably call SetHoldings(symbol, 0) for PERCENT_PORTFOLIO
    if mock_algo.Liquidate.called:
        print("INFO: SELL+LONG calls Liquidate (robust but inconsistent with docstring)")
    if mock_algo.SetHoldings.called:
        print("INFO: SELL+LONG calls SetHoldings")

if __name__ == "__main__":
    test_exit_short_truncation()
    test_exit_long_inconsistency()
    test_sell_long_consistency()

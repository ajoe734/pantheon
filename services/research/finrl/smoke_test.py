import pytest
from services.research.finrl.adapter import train

def test_train_smoke():
    # 60 days deterministic synthetic OHLCV
    records = []
    for i in range(60):
        records.append({
            "instrument": "AAPL",
            "date": f"2026-05-{i+1:02d}",
            "open": 100.0 + i,
            "high": 105.0 + i,
            "low": 95.0 + i,
            "close": 102.0 + i,
            "volume": 1000.0 + i
        })
        records.append({
            "instrument": "GOOG",
            "date": f"2026-05-{i+1:02d}",
            "open": 200.0 + i,
            "high": 205.0 + i,
            "low": 195.0 + i,
            "close": 202.0 + i,
            "volume": 2000.0 + i
        })
    
    strategy_spec_ref = {"records": records}
    
    result = train(strategy_spec_ref)
    
    assert result["status"] == "completed"
    assert "model_artifact_ref" in result
    assert "run_id" in result
    assert "metrics" in result
    assert result["metrics"]["mean_reward_proxy"] > 0
    print(f"Smoke test passed: {result}")

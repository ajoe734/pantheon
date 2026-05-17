import sys
from pathlib import Path
# When running in container, /app is already in PYTHONPATH
sys.path.append("/app")

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

    # Use 'stub' backend to avoid FinRL package requirement during smoke test
    result = train(strategy_spec_ref, backend="stub")

    assert result["status"] == "completed"
    assert "model_artifact_ref" in result
    assert "artifact_type" in result
    assert result["artifact_type"] == "model_artifact"
    assert "run_id" in result
    assert "metrics" in result

    # Acceptance criteria
    assert result["metrics"].get("mean_reward_proxy", 0) > 0
    assert result["metrics"].get("sharpe", 0) > 0
    assert result["metrics"].get("num_steps", 0) <= 1000

    # Check for ExperimentRun-shaped dict
    assert "model_artifact_ref" in result

    print(f"Smoke test passed: {result}")

if __name__ == "__main__":
    test_train_smoke()

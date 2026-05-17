import sys
from pathlib import Path

_SERVICE_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SERVICE_DIR.parent.parent.parent
for _p in (str(_REPO_ROOT), str(_SERVICE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

try:
    from services.research.finrl.adapter import train
except ImportError:
    from adapter import train  # service-local fallback for flat tmpdir / direct container run


def run_test(backend):
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

    result = train(strategy_spec_ref, backend=backend)

    assert result["status"] == "completed"
    assert "model_artifact_ref" in result
    assert result["artifact_type"] == "model_artifact"
    assert "run_id" in result
    assert "metrics" in result

    # Acceptance criteria
    assert result["metrics"].get("mean_reward_proxy", 0) > 0
    assert result["metrics"].get("sharpe", 0) > 0
    assert result["metrics"].get("num_steps", 0) <= 1000

    print(f"Smoke test passed for {backend}: {result}")


def test_train_smoke_stub():
    run_test("stub")


def test_train_smoke_dqn():
    run_test("finrl_dqn")


def test_train_smoke_ppo():
    run_test("finrl_ppo")


if __name__ == "__main__":
    test_train_smoke_stub()
    test_train_smoke_dqn()
    test_train_smoke_ppo()

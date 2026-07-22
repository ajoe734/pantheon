from pathlib import Path


def test_dev_compose_uses_postgres_action_ledger_and_clock_drift_guard() -> None:
    root = Path(__file__).resolve().parents[4]
    for name in ("docker-compose.yml", "docker-compose.control.yml"):
        text = (root / name).read_text(encoding="utf-8")
        assert "PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_BACKEND" in text
        assert "PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_BACKEND:-postgres" in text
        assert "PANTHEON_TRADE_JOURNEY_ACTION_LEDGER_DSN" in text
        assert "PANTHEON_TRADE_JOURNEY_CLOCK_DRIFT_SECONDS" in text

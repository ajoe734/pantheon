from acceptance.daily_bars_volume import evaluate, main


def test_evaluate_passes_when_only_old_days_are_over_the_threshold():
    ok, detail = evaluate({"days_over_threshold": 3, "days_over_threshold_newer_than_fix": 0, "since": "2026-11-26",
                           "checked_days": 60, "stored_days": 484})
    assert ok and "newer than 2026-11-26: 0" in detail


def test_evaluate_fails_for_a_day_newer_than_the_fix():
    assert evaluate({"days_over_threshold": 1, "days_over_threshold_newer_than_fix": 1})[0] is False


def test_main_skips_when_the_service_url_is_unset(monkeypatch):
    monkeypatch.delenv("ACCEPTANCE_SOURCE_INGEST_URL", raising=False)
    assert main() == 77

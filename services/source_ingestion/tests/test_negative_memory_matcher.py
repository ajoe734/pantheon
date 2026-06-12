from __future__ import annotations

from services.source_ingestion.negative_memory import (
    NegativeMemoryWarningLevel,
    is_blocking_negative_memory_match,
    match_negative_memory,
    negative_memory_record_from_seed,
)


def _candidate() -> dict:
    return {
        "seed_id": "seed-twse-lightgbm-momentum",
        "hypothesis": "LightGBM TWSE equity momentum ranks 5-day forward returns.",
        "asset_class": ["equity"],
        "market_scope": ["TWSE"],
        "holding_period": "5 trading days",
        "required_data": ["point-in-time OHLCV"],
        "backend_hint": "qlib",
        "feature_hints": ["lightgbm", "momentum", "volatility"],
        "label_hints": ["5_day_forward_return"],
        "risk_notes": ["medium"],
        "source_ids": ["src-lightgbm-paper"],
        "metadata": {"strategy_family": "momentum", "execution_route": "none"},
    }


def test_blocking_match_for_failed_experiment_with_high_keyword_overlap() -> None:
    match = match_negative_memory(
        _candidate(),
        [
            {
                "experiment_run_id": "exp-failed-twse-lightgbm-momentum",
                "status": "failed",
                "title": "Failed LightGBM TWSE momentum experiment",
                "summary": "LightGBM TWSE equity momentum failed on 5-day forward return labels.",
                "asset_class": ["equity"],
                "market_scope": ["TWSE"],
                "feature_hints": ["lightgbm", "momentum", "volatility"],
                "label_hints": ["5_day_forward_return"],
                "failure_reason": "Lookahead bias and unstable OOS drawdown.",
            }
        ],
    )

    assert match.warning_level == NegativeMemoryWarningLevel.BLOCKING
    assert match.similarity >= 0.7
    assert match.matched_memory_id == "exp-failed-twse-lightgbm-momentum"
    assert match.matched_memory_kind == "failed_experiment"
    assert is_blocking_negative_memory_match(match.to_dict()) is True


def test_warning_match_surfaces_partial_postmortem_overlap_without_blocking() -> None:
    match = match_negative_memory(
        _candidate(),
        [
            {
                "postmortem_id": "pm-twse-liquidity-slippage",
                "status": "published",
                "title": "TWSE liquidity postmortem",
                "summary": "TWSE equity momentum had liquidity slippage during crowded sessions.",
                "market_scope": ["TWSE"],
                "asset_class": ["equity"],
                "feature_hints": ["momentum", "liquidity"],
                "root_cause": "Crowding and slippage, not a full strategy duplicate.",
            }
        ],
    )

    assert match.warning_level == NegativeMemoryWarningLevel.WARNING
    assert 0.35 <= match.similarity < 0.7
    assert match.matched_memory_id == "pm-twse-liquidity-slippage"
    assert match.matched_memory_kind == "postmortem"


def test_no_match_returns_info_level_result() -> None:
    match = match_negative_memory(
        _candidate(),
        [
            {
                "strategy_id": "strat-crypto-sentiment-retired",
                "status": "retired",
                "title": "Crypto sentiment strategy retired",
                "summary": "BTC news sentiment model was retired after venue coverage changed.",
                "market_scope": ["crypto"],
                "asset_class": ["crypto"],
                "feature_hints": ["sentiment", "news"],
            }
        ],
    )

    assert match.warning_level == NegativeMemoryWarningLevel.INFO
    assert match.similarity == 0.0
    assert is_blocking_negative_memory_match(match) is False


def test_rejected_seed_projects_as_negative_memory_record() -> None:
    record = negative_memory_record_from_seed(
        {
            "seed_id": "seed-rejected",
            "status": "rejected",
            "hypothesis": "Rejected seed.",
            "metadata": {"execution_route": "none"},
        }
    )

    assert record is not None
    assert record["negative_memory_kind"] == "rejected_candidate"
    assert record["negative_memory_id"] == "seed-rejected"

"""Tests for SourceHealth, SourceUsageDaily, and RetirementRecommendation.

Acceptance criteria verified:
  1. SourceHealth model captures required fields.
  2. SourceUsageDaily model captures all aggregation dimensions.
  3. Low usage + high failure + no dependencies → propose_disable.
  4. High cost + low yield + replacement → propose_replace.
  5. Critical active dependency blocks retirement recommendation.
  6. Disabled observation window is enforced before propose_retire.
  7. New source with no usage records → keep_probation.
  8. High strategy seed yield → keep_increase_schedule.
"""

from __future__ import annotations

import pytest

from services.source_ingestion.source_health import (
    SourceHealth,
    SourceHealthError,
    SourceHealthStatus,
    SourceHealthStore,
    SourceUsageDaily,
    SourceUsageDailyStore,
)
from services.source_ingestion.retirement_engine import (
    RecommendationType,
    RetirementRecommendation,
    compute_recommendation,
    compute_recommendations,
)


# ---------------------------------------------------------------------------
# SourceHealth model tests
# ---------------------------------------------------------------------------


def test_source_health_required_fields():
    h = SourceHealth(source_id="src-001", source_kind="data_source")
    assert h.source_id == "src-001"
    assert h.source_kind == "data_source"
    assert h.status == "ok"
    assert h.row_count_last_run == 0
    assert h.error_rate_7d == 0.0


def test_source_health_all_fields():
    h = SourceHealth(
        source_id="src-002",
        source_kind="strategy_seed_source",
        status="stale",
        last_success_at="2026-06-01T00:00:00Z",
        last_failure_at="2026-06-08T00:00:00Z",
        latest_watermark="2026-06-01",
        row_count_last_run=100,
        rejected_count_last_run=5,
        schema_hash="abc123",
        staleness_seconds=7 * 86400,
        error_rate_7d=0.3,
        cost_estimate_30d=250.0,
    )
    d = h.to_dict()
    assert d["source_id"] == "src-002"
    assert d["source_kind"] == "strategy_seed_source"
    assert d["status"] == "stale"
    assert d["row_count_last_run"] == 100
    assert d["error_rate_7d"] == 0.3
    assert d["cost_estimate_30d"] == 250.0
    assert d["schema_hash"] == "abc123"


def test_source_health_invalid_kind():
    with pytest.raises(SourceHealthError, match="source_kind must be one of"):
        SourceHealth(source_id="src-x", source_kind="invalid_kind")


def test_source_health_invalid_status():
    with pytest.raises(SourceHealthError, match="status must be one of"):
        SourceHealth(source_id="src-x", source_kind="data_source", status="unknown_status")


def test_source_health_roundtrip():
    h = SourceHealth(
        source_id="src-rt",
        source_kind="data_source",
        status="degraded",
        error_rate_7d=0.6,
        cost_estimate_30d=500.0,
        staleness_seconds=3600,
    )
    h2 = SourceHealth.from_dict(h.to_dict())
    assert h2.source_id == h.source_id
    assert h2.status == h.status
    assert h2.error_rate_7d == h.error_rate_7d


def test_source_health_missing_source_id():
    with pytest.raises(SourceHealthError, match="source_id is required"):
        SourceHealth(source_id="", source_kind="data_source")


# ---------------------------------------------------------------------------
# SourceUsageDaily model tests
# ---------------------------------------------------------------------------


def test_source_usage_daily_fields():
    r = SourceUsageDaily(
        date="2026-06-09",
        source_id="src-001",
        source_kind="data_source",
        ingest_run_count=10,
        query_count=200,
        search_hit_count=50,
        persona_match_count=5,
        strategy_seed_yield_count=0,
        strategy_promotion_count=0,
        experiment_dependency_count=3,
        active_strategy_dependency_count=2,
        cost_estimate=12.5,
    )
    d = r.to_dict()
    assert d["date"] == "2026-06-09"
    assert d["ingest_run_count"] == 10
    assert d["query_count"] == 200
    assert d["active_strategy_dependency_count"] == 2
    assert d["cost_estimate"] == 12.5
    assert d["record_id"] == "2026-06-09::src-001"


def test_source_usage_daily_roundtrip():
    r = SourceUsageDaily(
        date="2026-06-01",
        source_id="src-seed",
        source_kind="strategy_seed_source",
        strategy_seed_yield_count=3,
    )
    r2 = SourceUsageDaily.from_dict(r.to_dict())
    assert r2.source_id == r.source_id
    assert r2.strategy_seed_yield_count == 3


def test_source_usage_invalid_kind():
    with pytest.raises(SourceHealthError):
        SourceUsageDaily(date="2026-06-09", source_id="src-x", source_kind="bad_kind")


def test_source_usage_negative_clamped():
    r = SourceUsageDaily(date="2026-06-09", source_id="s", source_kind="data_source", query_count=-5)
    assert r.query_count == 0


# ---------------------------------------------------------------------------
# SourceHealthStore / SourceUsageDailyStore tests
# ---------------------------------------------------------------------------


def test_health_store_upsert_and_list():
    store = SourceHealthStore()
    h1 = SourceHealth(source_id="s1", source_kind="data_source", status="ok")
    h2 = SourceHealth(source_id="s2", source_kind="strategy_seed_source", status="stale")
    store.upsert(h1)
    store.upsert(h2)
    all_records = store.list()
    assert len(all_records) == 2
    ds_only = store.list(source_kind="data_source")
    assert len(ds_only) == 1
    assert ds_only[0].source_id == "s1"


def test_health_store_get():
    store = SourceHealthStore()
    h = SourceHealth(source_id="sx", source_kind="data_source")
    store.upsert(h)
    assert store.get("sx") is h
    assert store.get("nonexistent") is None


def test_health_store_upsert_overwrites():
    store = SourceHealthStore()
    h1 = SourceHealth(source_id="s", source_kind="data_source", status="ok")
    h2 = SourceHealth(source_id="s", source_kind="data_source", status="stale")
    store.upsert(h1)
    store.upsert(h2)
    assert store.get("s").status == "stale"
    assert len(store.list()) == 1


def test_usage_store_aggregate_empty():
    store = SourceUsageDailyStore()
    agg = store.aggregate_for_source("no-such-source")
    assert agg["record_count"] == 0
    assert agg["total_query_count"] == 0
    assert agg["max_active_strategy_dependency_count"] == 0
    assert agg["total_cost_estimate"] is None


def test_usage_store_aggregate_sums():
    store = SourceUsageDailyStore()
    for i in range(3):
        r = SourceUsageDaily(
            date=f"2026-06-0{i + 1}",
            source_id="src-agg",
            source_kind="data_source",
            query_count=10,
            ingest_run_count=2,
            active_strategy_dependency_count=i + 1,
            cost_estimate=5.0,
        )
        store.upsert(r)
    agg = store.aggregate_for_source("src-agg")
    assert agg["record_count"] == 3
    assert agg["total_query_count"] == 30
    assert agg["total_ingest_run_count"] == 6
    assert agg["max_active_strategy_dependency_count"] == 3
    assert abs(agg["total_cost_estimate"] - 15.0) < 0.001


def test_usage_store_list_filters():
    store = SourceUsageDailyStore()
    r1 = SourceUsageDaily(date="2026-06-09", source_id="s1", source_kind="data_source")
    r2 = SourceUsageDaily(date="2026-06-09", source_id="s2", source_kind="strategy_seed_source")
    store.upsert(r1)
    store.upsert(r2)
    assert len(store.list(source_kind="data_source")) == 1
    assert len(store.list(date="2026-06-09")) == 2
    assert len(store.list(source_id="s1")) == 1


# ---------------------------------------------------------------------------
# RetirementRecommendation tests
# ---------------------------------------------------------------------------


def _make_health(
    source_id="src-test",
    source_kind="data_source",
    status="ok",
    error_rate=0.0,
    staleness=None,
    cost_30d=None,
) -> SourceHealth:
    return SourceHealth(
        source_id=source_id,
        source_kind=source_kind,
        status=status,
        error_rate_7d=error_rate,
        staleness_seconds=staleness,
        cost_estimate_30d=cost_30d,
    )


def _empty_usage(source_id="src-test", records=0) -> dict:
    if records == 0:
        return {
            "source_id": source_id,
            "days": 30,
            "record_count": 0,
            "total_ingest_run_count": 0,
            "total_query_count": 0,
            "total_search_hit_count": 0,
            "total_persona_match_count": 0,
            "total_strategy_seed_yield_count": 0,
            "total_strategy_promotion_count": 0,
            "total_experiment_dependency_count": 0,
            "max_active_strategy_dependency_count": 0,
            "total_cost_estimate": None,
        }
    return {**_empty_usage(source_id, 0), "record_count": records}


def _usage_with(source_id="src-test", **kwargs) -> dict:
    base = _empty_usage(source_id, records=5)
    base.update(kwargs)
    return base


def test_propose_disable_high_failure_low_usage():
    """High failure + low usage + no deps → propose_disable."""
    health = _make_health(error_rate=0.8)
    usage = _usage_with(total_query_count=1, total_search_hit_count=1)
    rec = compute_recommendation(health, usage)
    assert rec.recommendation == RecommendationType.PROPOSE_DISABLE


def test_propose_disable_blocked_by_active_deps():
    """Active strategy dependency blocks propose_disable."""
    health = _make_health(error_rate=0.9)
    usage = _usage_with(
        total_query_count=0,
        max_active_strategy_dependency_count=2,
    )
    rec = compute_recommendation(health, usage)
    assert rec.recommendation != RecommendationType.PROPOSE_DISABLE
    assert rec.retirement_blocked is True


def test_propose_replace_high_cost_low_yield():
    """High cost + low yield + replacement → propose_replace."""
    health = _make_health(cost_30d=2000.0)
    usage = _usage_with(total_strategy_seed_yield_count=0)
    rec = compute_recommendation(health, usage, has_replacement=True)
    assert rec.recommendation == RecommendationType.PROPOSE_REPLACE


def test_propose_replace_blocked_without_replacement():
    """High cost + low yield but NO replacement → no propose_replace."""
    health = _make_health(cost_30d=2000.0)
    usage = _usage_with(total_strategy_seed_yield_count=0)
    rec = compute_recommendation(health, usage, has_replacement=False)
    assert rec.recommendation != RecommendationType.PROPOSE_REPLACE


def test_keep_probation_no_records():
    """No usage records → keep_probation."""
    health = _make_health()
    usage = _empty_usage()
    rec = compute_recommendation(health, usage)
    assert rec.recommendation == RecommendationType.KEEP_PROBATION


def test_keep_increase_schedule_high_seed_yield():
    """High strategy seed yield → keep_increase_schedule."""
    health = _make_health(source_kind="strategy_seed_source")
    usage = _usage_with(total_strategy_seed_yield_count=5)
    rec = compute_recommendation(health, usage)
    assert rec.recommendation == RecommendationType.KEEP_INCREASE_SCHEDULE


def test_observe_window_not_elapsed_no_retire():
    """Disabled + observation window NOT elapsed → no_action (waiting)."""
    health = _make_health(status="disabled", staleness=10 * 86400)  # 10 days < 30
    usage = _empty_usage()
    rec = compute_recommendation(health, usage, observation_window_seconds=30 * 86400)
    assert rec.recommendation == RecommendationType.NO_ACTION
    assert "observation window" in rec.reason.lower()


def test_observe_window_elapsed_propose_retire():
    """Disabled + observation window elapsed + no deps → propose_retire."""
    health = _make_health(status="disabled", staleness=31 * 86400)  # 31 days > 30
    usage = _usage_with(max_active_strategy_dependency_count=0)
    rec = compute_recommendation(health, usage, observation_window_seconds=30 * 86400)
    assert rec.recommendation == RecommendationType.PROPOSE_RETIRE


def test_disabled_with_active_deps_blocked():
    """Disabled + active dependency → alert_backfill, retirement_blocked."""
    health = _make_health(status="disabled", staleness=60 * 86400)
    usage = _usage_with(max_active_strategy_dependency_count=1)
    rec = compute_recommendation(health, usage, observation_window_seconds=30 * 86400)
    assert rec.recommendation == RecommendationType.ALERT_BACKFILL
    assert rec.retirement_blocked is True
    assert rec.retirement_blocker_reason is not None


def test_already_retired_no_action():
    """Already retired source → no_action."""
    health = _make_health(status="retired")
    usage = _usage_with()
    rec = compute_recommendation(health, usage)
    assert rec.recommendation == RecommendationType.NO_ACTION


def test_no_action_normal_source():
    """Healthy source with moderate usage → no_action."""
    health = _make_health(error_rate=0.1)
    usage = _usage_with(total_query_count=100, total_search_hit_count=50)
    rec = compute_recommendation(health, usage)
    assert rec.recommendation == RecommendationType.NO_ACTION


def test_compute_recommendations_batch():
    """compute_recommendations works across multiple sources."""
    records = [
        _make_health("s1", error_rate=0.9),  # → propose_disable
        _make_health("s2"),                  # no usage → keep_probation
        _make_health("s3", status="retired"),  # → no_action
    ]

    def agg(sid: str) -> dict:
        if sid == "s1":
            return _usage_with(sid, total_query_count=0, total_search_hit_count=0)
        return _empty_usage(sid)

    recs = compute_recommendations(records, agg)
    assert len(recs) == 3
    rec_map = {r.source_id: r for r in recs}
    assert rec_map["s1"].recommendation == RecommendationType.PROPOSE_DISABLE
    assert rec_map["s2"].recommendation == RecommendationType.KEEP_PROBATION
    assert rec_map["s3"].recommendation == RecommendationType.NO_ACTION


def test_recommendation_to_dict():
    health = _make_health(error_rate=0.8)
    usage = _usage_with(total_query_count=0)
    rec = compute_recommendation(health, usage)
    d = rec.to_dict()
    assert "recommendation" in d
    assert "reason" in d
    assert "evidence" in d
    assert "retirement_blocked" in d


def test_usage_jsonl_persistence(tmp_path):
    """SourceUsageDailyStore persists records across instances."""
    path = tmp_path / "usage.jsonl"
    s1 = SourceUsageDailyStore.from_jsonl(path)
    r = SourceUsageDaily(date="2026-06-09", source_id="s-pst", source_kind="data_source", query_count=42)
    s1.upsert(r)

    s2 = SourceUsageDailyStore.from_jsonl(path)
    loaded = s2.get("2026-06-09", "s-pst")
    assert loaded is not None
    assert loaded.query_count == 42


def test_health_jsonl_persistence(tmp_path):
    """SourceHealthStore persists records across instances."""
    path = tmp_path / "health.jsonl"
    s1 = SourceHealthStore.from_jsonl(path)
    h = SourceHealth(source_id="s-pst2", source_kind="data_source", error_rate_7d=0.25)
    s1.upsert(h)

    s2 = SourceHealthStore.from_jsonl(path)
    loaded = s2.get("s-pst2")
    assert loaded is not None
    assert loaded.error_rate_7d == pytest.approx(0.25)

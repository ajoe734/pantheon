from __future__ import annotations

from services.source_ingestion.active_universe import (
    ActiveUniverseMember,
    UniverseTier,
    build_active_universe_update_plan,
)


def test_active_universe_plan_limits_detail_connectors_to_core_and_candidates() -> None:
    plan = build_active_universe_update_plan(
        [
            ActiveUniverseMember(symbol="2330", tier=UniverseTier.CORE, reason="holding"),
            ActiveUniverseMember(symbol="2317", tier=UniverseTier.CANDIDATE, reason="watchlist"),
            ActiveUniverseMember(symbol="6488", tier=UniverseTier.ARCHIVE, reason="removed from research"),
        ]
    )

    broker_update = next(
        update for update in plan["connector_updates"] if update["connector_id"] == "tw-yahoo-broker-top15"
    )
    rss_update = next(update for update in plan["connector_updates"] if update["connector_id"] == "tw-yahoo-stock-rss")

    assert plan["schema_version"] == "active_universe_update_plan.v1"
    assert broker_update["symbols"] == ["2330", "2317"]
    assert broker_update["dataset"] == "tw_broker_top"
    assert rss_update["symbols"] == ["2330", "2317"]
    assert plan["summary"]["archive_detail_updates_skipped"] == ["6488"]
    assert plan["summary"]["core_count"] == 1
    assert plan["summary"]["candidate_count"] == 1
    assert plan["summary"]["archive_count"] == 1


def test_active_universe_plan_accepts_rule_overrides_and_symbol_caps() -> None:
    plan = build_active_universe_update_plan(
        [
            {"symbol": "2330", "tier": "core_universe"},
            {"symbol": "2317", "tier": "candidate_universe"},
        ],
        rules=[
            {
                "connector_id": "tw-yahoo-broker-top15",
                "dataset": "tw_broker_top",
                "eligible_tiers": ["core_universe", "candidate_universe"],
                "cadence": "daily_after_close",
                "priority": 1,
                "max_symbols_per_run": 1,
            }
        ],
    )

    assert plan["connector_updates"][0]["symbols"] == ["2330"]
    assert plan["connector_updates"][0]["symbol_count"] == 1

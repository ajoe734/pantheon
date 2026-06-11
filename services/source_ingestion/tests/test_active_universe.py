from __future__ import annotations

from services.source_ingestion.active_universe import (
    ActiveUniverseMember,
    UniverseTier,
    UniverseTransition,
    active_universe_policy_payload,
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
        update for update in plan["connector_updates"] if update["connector_id"] == "tw-finmind-broker-daily-report"
    )
    dataset_update = next(update for update in plan["connector_updates"] if update["connector_id"] == "tw-finmind-datasets")
    rss_update = next(update for update in plan["connector_updates"] if update["connector_id"] == "tw-yahoo-stock-rss")
    yahoo_fallback = next(
        update for update in plan["connector_updates"] if update["connector_id"] == "tw-yahoo-broker-top15"
    )
    tej_backfill = next(
        update for update in plan["connector_updates"] if update["connector_id"] == "tw-tej-research-datasets"
    )
    official_baseline = next(
        update for update in plan["connector_updates"] if update["connector_id"] == "tw-twse-tpex-official-market"
    )
    mops_events = next(
        update
        for update in plan["connector_updates"]
        if update["connector_id"] == "tw-mops-official-disclosures" and update["dataset"] == "tw_material_events"
    )
    mops_revenue = next(
        update
        for update in plan["connector_updates"]
        if update["connector_id"] == "tw-mops-official-disclosures" and update["dataset"] == "tw_monthly_revenue"
    )
    mops_financials = next(
        update
        for update in plan["connector_updates"]
        if update["connector_id"] == "tw-mops-official-disclosures" and update["dataset"] == "tw_financial_statement"
    )

    assert plan["schema_version"] == "active_universe_update_plan.v1"
    assert plan["policy_ref"] == "active_universe_scheduling_policy.v1"
    assert broker_update["symbols"] == ["2330", "2317"]
    assert broker_update["dataset"] == "tw_broker_top"
    assert broker_update["metadata"]["source_dataset"] == "TaiwanStockTradingDailyReport"
    assert dataset_update["symbols"] == ["2330", "2317"]
    assert dataset_update["dataset"] == "tw_daily_price_and_chip"
    assert rss_update["symbols"] == ["2330", "2317"]
    assert yahoo_fallback["metadata"]["fallback_for_connector_id"] == "tw-finmind-broker-daily-report"
    assert tej_backfill["symbols"] == ["2330", "2317"]
    assert tej_backfill["metadata"]["purchased_table_allowlist_required"] is True
    assert tej_backfill["metadata"]["run_by_default"] is False
    assert official_baseline["symbols"] == ["2330", "2317", "6488"]
    assert official_baseline["metadata"]["archive_behavior"] == "daily_price_only"
    assert mops_events["symbols"] == ["2330", "2317", "6488"]
    assert mops_events["metadata"]["archive_behavior"] == "material_events_only"
    assert mops_events["metadata"]["normalized_target"] == "tw_material_event"
    assert mops_revenue["symbols"] == ["2330"]
    assert mops_revenue["metadata"]["normalized_target"] == "tw_monthly_revenue"
    assert mops_financials["symbols"] == ["2330"]
    assert mops_financials["metadata"]["restatement_correction_gap_report"] == "mops_restatement_correction_gap_report.v1"
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


def test_active_universe_policy_summarizes_archive_baseline_and_detail_skip_rules() -> None:
    policy = active_universe_policy_payload()

    assert policy["schema_version"] == "active_universe_scheduling_policy.v1"
    assert {tier["tier"] for tier in policy["tiers"]} == {
        "core_universe",
        "candidate_universe",
        "archive_universe",
    }
    assert policy["transition_event_schema"]["schema_version"] == "universe_transition.v1"
    assert "tw-finmind-broker-daily-report" not in policy["summary"]["archive_baseline_connector_ids"]
    assert "tw-twse-tpex-official-market" in policy["summary"]["archive_baseline_connector_ids"]


def test_universe_transition_records_required_tier_change_fields() -> None:
    transition = UniverseTransition(
        symbol="2330",
        market="tw",
        from_tier=UniverseTier.CANDIDATE,
        to_tier=UniverseTier.CORE,
        reason="promoted by research queue",
        triggered_by="actor:research-orchestrator",
        effective_at="2026-06-09T09:00:00Z",
    )

    assert transition.to_dict() == {
        "symbol": "2330",
        "market": "TW",
        "from_tier": "candidate_universe",
        "to_tier": "core_universe",
        "reason": "promoted by research queue",
        "triggered_by": "actor:research-orchestrator",
        "effective_at": "2026-06-09T09:00:00Z",
        "metadata": {},
    }

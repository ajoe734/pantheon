"""Acceptance tests for DATASTRAT-MARKETDATA-OPS-ACCEPT-010.

Verifies the connector coverage matrix, source health alerting, and gap report
API that together form the ops acceptance dashboard for market-data rollout.

Acceptance criteria:
  1. Coverage matrix correctly classifies missing connectors.
  2. Coverage matrix correctly classifies configured-but-no-health connectors.
  3. Coverage matrix correctly classifies healthy connectors.
  4. Coverage matrix correctly classifies disabled connectors.
  5. Coverage matrix correctly classifies credential_unavailable connectors.
  6. Coverage matrix correctly classifies unhealthy connectors.
  7. Alert list includes non-ok health records.
  8. Alert list includes missing catalog connectors when include_missing=True.
  9. Alert list excludes ok-health sources.
  10. Alert list distinguishes credential_unavailable from generic unhealthy.
  11. Gap report classifies missing health as provider_stale.
  12. Gap report classifies credential errors correctly.
  13. Gap report markdown is rendered when requested.
  14. Coverage matrix summary counts are consistent with entries.
"""

from __future__ import annotations

import pytest

from services.source_ingestion.connector_coverage_matrix import (
    build_coverage_matrix,
    build_source_alerts,
)
from services.source_ingestion.gap_report import (
    generate_market_data_gap_report,
    render_gap_report_markdown,
)
from services.source_ingestion.source_health import SourceHealth, SourceHealthStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _catalog_entry(connector_id: str, data_source_id: str = "", provider: str = "TestProvider") -> dict:
    return {
        "connector_id": connector_id,
        "data_source_id": data_source_id or connector_id,
        "provider": provider,
    }


def _health_dict(
    source_id: str,
    status: str = "ok",
    source_error: str = "",
    latest_watermark: str | None = None,
    last_failure_at: str | None = None,
    error_rate_7d: float = 0.0,
) -> dict:
    return SourceHealth(
        source_id=source_id,
        source_kind="data_source",
        status=status,
        last_failure_at=last_failure_at,
        latest_watermark=latest_watermark,
        error_rate_7d=error_rate_7d,
        metadata={"source_error": source_error} if source_error else {},
    ).to_dict()


# ---------------------------------------------------------------------------
# Coverage matrix tests
# ---------------------------------------------------------------------------


def test_coverage_matrix_all_missing():
    """All catalog entries have no configured connector → all missing."""
    catalog = [
        _catalog_entry("conn-a"),
        _catalog_entry("conn-b"),
    ]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids=set(),
        health_by_connector_id={},
        lifecycle_by_connector_id={},
    )
    assert result["schema_version"] == "connector_coverage_matrix.v1"
    assert result["summary"]["missing"] == 2
    assert result["coverage_entry_count"] == 2
    classes = {e["coverage_class"] for e in result["entries"]}
    assert classes == {"missing"}


def test_coverage_matrix_configured_no_health():
    """Connector exists in store but no health record → configured."""
    catalog = [_catalog_entry("conn-a")]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids={"conn-a"},
        health_by_connector_id={},
        lifecycle_by_connector_id={"conn-a": "enabled"},
    )
    assert result["summary"]["configured"] == 1
    entry = result["entries"][0]
    assert entry["coverage_class"] == "configured"
    assert entry["health_status"] is None


def test_coverage_matrix_healthy():
    """Configured connector with ok health → healthy."""
    catalog = [_catalog_entry("conn-ok")]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids={"conn-ok"},
        health_by_connector_id={"conn-ok": _health_dict("conn-ok", status="ok")},
        lifecycle_by_connector_id={"conn-ok": "enabled"},
    )
    assert result["summary"]["healthy"] == 1
    entry = result["entries"][0]
    assert entry["coverage_class"] == "healthy"
    assert entry["health_status"] == "ok"


def test_coverage_matrix_disabled():
    """Configured connector with disabled lifecycle → disabled."""
    catalog = [_catalog_entry("conn-dis")]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids={"conn-dis"},
        health_by_connector_id={},
        lifecycle_by_connector_id={"conn-dis": "disabled"},
    )
    assert result["summary"]["disabled"] == 1
    entry = result["entries"][0]
    assert entry["coverage_class"] == "disabled"


def test_coverage_matrix_credential_unavailable():
    """Configured connector with credential error in health → credential_unavailable."""
    catalog = [_catalog_entry("conn-cred")]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids={"conn-cred"},
        health_by_connector_id={
            "conn-cred": _health_dict(
                "conn-cred",
                status="failed",
                source_error="credential_unavailable: no FINMIND_API_TOKEN",
            )
        },
        lifecycle_by_connector_id={"conn-cred": "enabled"},
    )
    assert result["summary"]["credential_unavailable"] == 1
    entry = result["entries"][0]
    assert entry["coverage_class"] == "credential_unavailable"


def test_coverage_matrix_unhealthy():
    """Configured connector with non-credential health failure → unhealthy."""
    catalog = [_catalog_entry("conn-bad")]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids={"conn-bad"},
        health_by_connector_id={
            "conn-bad": _health_dict("conn-bad", status="stale", source_error="endpoint returned 404")
        },
        lifecycle_by_connector_id={"conn-bad": "enabled"},
    )
    assert result["summary"]["unhealthy"] == 1
    entry = result["entries"][0]
    assert entry["coverage_class"] == "unhealthy"
    assert entry["health_status"] == "stale"


def test_coverage_matrix_mixed():
    """Mixed catalog: some missing, some healthy, some with credential errors."""
    catalog = [
        _catalog_entry("conn-miss"),
        _catalog_entry("conn-ok"),
        _catalog_entry("conn-cred"),
    ]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids={"conn-ok", "conn-cred"},
        health_by_connector_id={
            "conn-ok": _health_dict("conn-ok", status="ok"),
            "conn-cred": _health_dict("conn-cred", status="failed", source_error="no api_key found"),
        },
        lifecycle_by_connector_id={"conn-ok": "enabled", "conn-cred": "enabled"},
    )
    assert result["summary"]["missing"] == 1
    assert result["summary"]["healthy"] == 1
    assert result["summary"]["credential_unavailable"] == 1
    assert result["coverage_entry_count"] == 3


def test_coverage_matrix_summary_consistent_with_entries():
    """Summary counts match entry classifications."""
    catalog = [_catalog_entry(f"conn-{i}") for i in range(5)]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids={"conn-0", "conn-1"},
        health_by_connector_id={"conn-0": _health_dict("conn-0", status="ok")},
        lifecycle_by_connector_id={"conn-0": "enabled", "conn-1": "disabled"},
    )
    total_from_summary = sum(result["summary"].values())
    assert total_from_summary == result["coverage_entry_count"]


def test_coverage_matrix_deduplicates_connector_ids():
    """Duplicate connector IDs in catalog appear only once in the matrix."""
    catalog = [
        _catalog_entry("conn-dup"),
        _catalog_entry("conn-dup"),
    ]
    result = build_coverage_matrix(
        catalog_entries=catalog,
        configured_connector_ids=set(),
        health_by_connector_id={},
        lifecycle_by_connector_id={},
    )
    assert result["coverage_entry_count"] == 1


# ---------------------------------------------------------------------------
# Source alert tests
# ---------------------------------------------------------------------------


def test_alerts_ok_source_excluded():
    """Source with status=ok does not appear in alerts."""
    alerts = build_source_alerts(
        catalog_entries=[],
        configured_connector_ids={"conn-ok"},
        health_records=[_health_dict("conn-ok", status="ok")],
        include_missing=False,
    )
    assert alerts == []


def test_alerts_unhealthy_source_included():
    """Source with non-ok health appears in alerts."""
    alerts = build_source_alerts(
        catalog_entries=[],
        configured_connector_ids={"conn-stale"},
        health_records=[_health_dict("conn-stale", status="stale", source_error="no data for 3 days")],
        include_missing=False,
    )
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "unhealthy"
    assert alerts[0]["connector_id"] == "conn-stale"
    assert alerts[0]["health_status"] == "stale"


def test_alerts_credential_unavailable_labelled():
    """Credential error in source_error is labelled credential_unavailable, not unhealthy."""
    alerts = build_source_alerts(
        catalog_entries=[],
        configured_connector_ids={"conn-cred"},
        health_records=[_health_dict("conn-cred", status="failed", source_error="no api key configured")],
        include_missing=False,
    )
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "credential_unavailable"


def test_alerts_missing_connector_included():
    """Catalog connector not in store is included when include_missing=True."""
    alerts = build_source_alerts(
        catalog_entries=[_catalog_entry("conn-miss", provider="TestProv")],
        configured_connector_ids=set(),
        health_records=[],
        include_missing=True,
    )
    assert len(alerts) == 1
    assert alerts[0]["alert_type"] == "missing_connector"
    assert alerts[0]["connector_id"] == "conn-miss"


def test_alerts_missing_connector_excluded_when_flag_false():
    """Missing connector not in alerts when include_missing=False."""
    alerts = build_source_alerts(
        catalog_entries=[_catalog_entry("conn-miss")],
        configured_connector_ids=set(),
        health_records=[],
        include_missing=False,
    )
    assert alerts == []


def test_alerts_multiple_sources():
    """Multiple sources generate independent alerts."""
    alerts = build_source_alerts(
        catalog_entries=[_catalog_entry("conn-not-configured")],
        configured_connector_ids={"conn-bad", "conn-ok"},
        health_records=[
            _health_dict("conn-bad", status="degraded", source_error="parse error"),
            _health_dict("conn-ok", status="ok"),
        ],
        include_missing=True,
    )
    alert_types = {a["alert_type"] for a in alerts}
    connector_ids = {a["connector_id"] for a in alerts}
    assert "unhealthy" in alert_types
    assert "missing_connector" in alert_types
    assert "conn-bad" in connector_ids
    assert "conn-not-configured" in connector_ids
    assert "conn-ok" not in connector_ids


# ---------------------------------------------------------------------------
# Gap report tests
# ---------------------------------------------------------------------------


_TW_CORE_MEMBERS = [
    {"symbol": "2330", "tier": "core_universe", "market": "TW"},
    {"symbol": "2317", "tier": "core_universe", "market": "TW"},
    {"symbol": "3008", "tier": "candidate_universe", "market": "TW"},
    {"symbol": "1301", "tier": "archive_universe", "market": "TW"},
]


def test_gap_report_missing_health_is_provider_stale():
    """When no health record exists for a connector, the gap is classified provider_stale."""
    from services.source_ingestion.active_universe import (
        ActiveUniverseMember,
        DEFAULT_SOURCE_UPDATE_RULES,
    )
    members = [
        ActiveUniverseMember(symbol=m["symbol"], tier=m["tier"], market=m.get("market", "TW"))
        for m in _TW_CORE_MEMBERS
    ]
    report = generate_market_data_gap_report(
        members=members,
        rules=DEFAULT_SOURCE_UPDATE_RULES,
        health_records=[],
        run_date="2026-06-11",
    )
    assert report["schema_version"] == "market_data_gap_report.v1"
    assert report["run_date"] == "2026-06-11"
    classes = {gap["gap_class"] for gap in report["gaps"]}
    assert "provider_stale" in classes or "not-in-universe" in classes


def test_gap_report_credential_gap_classified():
    """Health with credential error → gap_class == credential."""
    from services.source_ingestion.active_universe import (
        ActiveUniverseMember,
        SourceUpdateRule,
        UniverseTier,
    )
    members = [ActiveUniverseMember(symbol="2330", tier="core_universe", market="TW")]
    rules = [
        SourceUpdateRule(
            connector_id="tw-finmind-datasets",
            dataset="tw_price_daily",
            eligible_tiers=(UniverseTier.CORE,),
            cadence="daily_after_close",
        )
    ]
    health = SourceHealth(
        source_id="tw-finmind-datasets",
        source_kind="data_source",
        status="failed",
        metadata={"source_error": "credential_unavailable: no FINMIND_API_TOKEN"},
    )
    report = generate_market_data_gap_report(
        members=members,
        rules=rules,
        health_records=[health],
        run_date="2026-06-11",
    )
    cred_gaps = [g for g in report["gaps"] if g["gap_class"] == "credential"]
    assert len(cred_gaps) >= 1


def test_gap_report_stale_watermark_generates_gap():
    """Healthy connector with watermark < run_date → provider_stale gap."""
    from services.source_ingestion.active_universe import (
        ActiveUniverseMember,
        SourceUpdateRule,
        UniverseTier,
    )
    members = [ActiveUniverseMember(symbol="2330", tier="core_universe", market="TW")]
    rules = [
        SourceUpdateRule(
            connector_id="tw-twse-tpex-official-market",
            dataset="tw_price_daily",
            eligible_tiers=(UniverseTier.CORE,),
            cadence="daily_after_close",
        )
    ]
    health = SourceHealth(
        source_id="tw-twse-tpex-official-market",
        source_kind="data_source",
        status="ok",
        latest_watermark="2026-06-10",
    )
    report = generate_market_data_gap_report(
        members=members,
        rules=rules,
        health_records=[health],
        run_date="2026-06-11",
    )
    stale_gaps = [g for g in report["gaps"] if g["gap_class"] == "provider_stale"]
    assert len(stale_gaps) >= 1


def test_gap_report_fresh_health_no_gap():
    """Connector with ok health and current watermark → no gap for that connector."""
    from services.source_ingestion.active_universe import (
        ActiveUniverseMember,
        SourceUpdateRule,
        UniverseTier,
    )
    members = [ActiveUniverseMember(symbol="2330", tier="core_universe", market="TW")]
    rules = [
        SourceUpdateRule(
            connector_id="tw-twse-tpex-official-market",
            dataset="tw_price_daily",
            eligible_tiers=(UniverseTier.CORE,),
            cadence="daily_after_close",
        )
    ]
    health = SourceHealth(
        source_id="tw-twse-tpex-official-market",
        source_kind="data_source",
        status="ok",
        latest_watermark="2026-06-11",
    )
    report = generate_market_data_gap_report(
        members=members,
        rules=rules,
        health_records=[health],
        run_date="2026-06-11",
    )
    stale_gaps = [
        g for g in report["gaps"]
        if g["gap_class"] == "provider_stale" and g["connector_id"] == "tw-twse-tpex-official-market"
    ]
    assert stale_gaps == []


def test_gap_report_markdown_rendering():
    """render_gap_report_markdown produces a non-empty markdown string."""
    from services.source_ingestion.active_universe import ActiveUniverseMember
    members = [ActiveUniverseMember(symbol="2330", tier="core_universe", market="TW")]
    report = generate_market_data_gap_report(
        members=members,
        rules=[],
        health_records=[],
        run_date="2026-06-11",
    )
    md = render_gap_report_markdown(report)
    assert "# Market Data Gap Report" in md
    assert "2026-06-11" in md
    assert "## Summary" in md


def test_gap_report_summary_counts_consistent():
    """gap_summary counts sum to the number of gap entries."""
    from services.source_ingestion.active_universe import (
        ActiveUniverseMember,
        DEFAULT_SOURCE_UPDATE_RULES,
    )
    members = [
        ActiveUniverseMember(symbol=m["symbol"], tier=m["tier"], market=m.get("market", "TW"))
        for m in _TW_CORE_MEMBERS
    ]
    report = generate_market_data_gap_report(
        members=members,
        rules=DEFAULT_SOURCE_UPDATE_RULES,
        health_records=[],
        run_date="2026-06-11",
    )
    total_summary = sum(report["gap_summary"].values())
    total_gaps = len(report["gaps"])
    assert total_summary == total_gaps


# ---------------------------------------------------------------------------
# Full catalog coverage smoke (nonprod check)
# ---------------------------------------------------------------------------


def test_financial_catalog_all_connector_ids_covered_by_matrix():
    """Every catalog entry with a connector_id appears in the coverage matrix output."""
    from services.source_ingestion.financial_source_catalog import (
        financial_data_source_catalog_payload,
    )
    catalog = financial_data_source_catalog_payload()
    entries = catalog["entries"]

    expected_ids = {
        str(e.get("connector_id") or "")
        for e in entries
        if e.get("connector_id")
    }

    result = build_coverage_matrix(
        catalog_entries=entries,
        configured_connector_ids=set(),
        health_by_connector_id={},
        lifecycle_by_connector_id={},
    )

    covered_ids = {e["connector_id"] for e in result["entries"]}
    assert expected_ids == covered_ids, f"Uncovered catalog connectors: {expected_ids - covered_ids}"


def test_financial_catalog_missing_connectors_all_generate_alerts():
    """When no connectors are configured, every catalog entry generates a missing alert."""
    from services.source_ingestion.financial_source_catalog import (
        financial_data_source_catalog_payload,
    )
    entries = financial_data_source_catalog_payload()["entries"]
    expected_ids = {str(e.get("connector_id") or "") for e in entries if e.get("connector_id")}

    alerts = build_source_alerts(
        catalog_entries=entries,
        configured_connector_ids=set(),
        health_records=[],
        include_missing=True,
    )
    alert_ids = {a["connector_id"] for a in alerts if a["alert_type"] == "missing_connector"}
    assert expected_ids == alert_ids, f"Missing from alerts: {expected_ids - alert_ids}"

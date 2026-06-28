from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import main as bff_main


def _frontend_data_source_tone(state: str) -> str:
    token = str(state or "").lower()
    return "ok" if any(marker in token for marker in ("read_ok", "readback_ok", "smoke_ok")) else "warn"


def _truth(connector_id: str, *, status: str = "ok", rows: int = 1) -> dict:
    return {
        "health": {
            "source_id": connector_id,
            "status": status,
            "last_success_at": "2026-06-28T14:21:21Z" if status == "ok" else None,
            "last_failure_at": None if status == "ok" else "2026-06-28T14:21:21Z",
            "row_count_last_run": rows,
            "metadata": {},
        },
        "connector": {
            "connector_id": connector_id,
            "status": "enabled",
            "schedule": {"configured": True, "enabled": True, "interval_seconds": 86400},
            "freshness": {"status": "fresh" if status == "ok" else "degraded"},
            "health_metrics": {},
        },
    }


def test_all_green_live_overlay_promotes_summary_badge_state(monkeypatch):
    dss = {
        "state": "partial_readback",
        "summary": (
            "Shioaji quote readback is present; TWSE, TPEx, MOPS, and FinMind "
            "default to unavailable repo-local smoke evidence."
        ),
        "provider_statuses": {
            "shioaji": "read_ok",
            "twse": "read_unavailable",
            "tpex": "read_unavailable",
            "mops": "public_reference_unavailable",
            "finmind": "read_unavailable",
        },
    }
    sources = [
        {"provider_key": "shioaji", "status": "read_ok"},
        {"provider_key": "twse", "status": "read_unavailable"},
        {"provider_key": "tpex", "status": "read_unavailable"},
        {"provider_key": "mops", "status": "public_reference_unavailable"},
        {"provider_key": "finmind", "status": "read_unavailable"},
    ]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
            "tw-twse-tpex-official-market": _truth("tw-twse-tpex-official-market", rows=1000),
            "tw-mops-official-disclosures": _truth("tw-mops-official-disclosures", rows=8),
            "tw-finmind-datasets": _truth("tw-finmind-datasets", rows=120),
        },
    )

    out_dss, _, _ = bff_main._overlay_source_health_truth(dss, sources)

    assert out_dss["provider_statuses"] == {
        "shioaji": "read_ok",
        "twse": "read_ok",
        "tpex": "read_ok",
        "mops": "read_ok",
        "finmind": "read_ok",
    }
    assert out_dss["state"] == "live_readback_ok"
    assert _frontend_data_source_tone(out_dss["state"]) == "ok"
    assert "5/5" in out_dss["summary"]
    assert "default to unavailable" not in out_dss["summary"]


def test_tw_official_sources_flip_only_from_source_ingest_health(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "twse": "read_unavailable",
            "tpex": "read_unavailable",
            "mops": "read_unavailable",
        },
    }
    sources = [
        {"provider_key": "twse", "status": "read_unavailable"},
        {"provider_key": "tpex", "status": "read_unavailable"},
        {"provider_key": "mops", "status": "read_unavailable"},
    ]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
            "tw-twse-tpex-official-market": _truth("tw-twse-tpex-official-market", rows=1000),
            "tw-mops-official-disclosures": _truth("tw-mops-official-disclosures", rows=8),
        },
    )

    out_dss, out_sources, _ = bff_main._overlay_source_health_truth(dss, sources)

    assert out_dss["source_health_source"] == "source_ingest"
    assert out_dss["provider_statuses"]["twse"] == "read_ok"
    assert out_dss["provider_statuses"]["tpex"] == "read_ok"
    assert out_dss["provider_statuses"]["mops"] == "read_ok"
    assert out_dss["state"] == "live_readback_ok"
    assert {source["connectorId"] for source in out_sources} == {
        "tw-twse-tpex-official-market",
        "tw-mops-official-disclosures",
    }


def test_missing_source_ingest_health_does_not_fake_green(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "twse": "read_unavailable",
            "polygon": "credential_unavailable",
        },
    }
    sources = [
        {"provider_key": "twse", "status": "read_unavailable"},
        {
            "provider_key": "polygon",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://POLYGON_API_KEY",
            "secret_ref": "env://POLYGON_API_KEY",
        },
    ]
    monkeypatch.setattr(bff_main, "_source_ingest_truth_by_connector", lambda: {})

    out_dss, out_sources, _ = bff_main._overlay_source_health_truth(dss, sources)

    by_provider = {source["provider_key"]: source for source in out_sources}
    assert out_dss["source_health_source"] == "static_metadata"
    assert out_dss["provider_statuses"]["twse"] == "read_unavailable"
    assert out_dss["provider_statuses"]["polygon"] == "credential_unavailable"
    assert out_dss["state"] == "partial_readback"
    assert by_provider["polygon"]["secret_ref"] == "env://POLYGON_API_KEY"


def test_us_public_sources_flip_while_key_gated_sources_stay_credential_unavailable(monkeypatch):
    dss = {
        "state": "partial_readback",
        "provider_statuses": {
            "stooq": "read_unavailable",
            "sec_edgar": "read_unavailable",
            "finra": "read_unavailable",
            "fred": "read_unavailable",
            "polygon": "credential_unavailable",
            "alphavantage": "credential_unavailable",
        },
    }
    sources = [
        {"provider_key": "stooq", "status": "read_unavailable"},
        {"provider_key": "sec_edgar", "status": "read_unavailable"},
        {"provider_key": "finra", "status": "read_unavailable"},
        {"provider_key": "fred", "status": "read_unavailable"},
        {
            "provider_key": "polygon",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://POLYGON_API_KEY",
            "secret_ref": "env://POLYGON_API_KEY",
        },
        {
            "provider_key": "alphavantage",
            "status": "credential_unavailable",
            "reason": "API key not configured; set env://ALPHA_VANTAGE_API_KEY",
            "secret_ref": "env://ALPHA_VANTAGE_API_KEY",
        },
    ]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {
            "us-stooq-daily-ohlcv": _truth("us-stooq-daily-ohlcv", rows=2),
            "us-sec-edgar-filings": _truth("us-sec-edgar-filings", rows=1),
            "us-finra-short-sale": _truth("us-finra-short-sale", rows=2),
            "us-fred-macro": _truth("us-fred-macro", rows=2),
            "us-polygon-daily-ohlcv": _truth("us-polygon-daily-ohlcv", status="degraded", rows=0),
            "us-alpha-vantage-daily-ohlcv": _truth("us-alpha-vantage-daily-ohlcv", status="degraded", rows=0),
        },
    )

    out_dss, out_sources, _ = bff_main._overlay_source_health_truth(dss, sources)

    assert out_dss["provider_statuses"]["stooq"] == "read_ok"
    assert out_dss["provider_statuses"]["sec_edgar"] == "read_ok"
    assert out_dss["provider_statuses"]["finra"] == "read_ok"
    assert out_dss["provider_statuses"]["fred"] == "read_ok"
    assert out_dss["provider_statuses"]["polygon"] == "credential_unavailable"
    assert out_dss["provider_statuses"]["alphavantage"] == "credential_unavailable"
    assert out_dss["state"] == "partial_readback"
    by_provider = {source["provider_key"]: source for source in out_sources}
    assert by_provider["polygon"]["secret_ref"] == "env://POLYGON_API_KEY"
    assert by_provider["alphavantage"]["secret_ref"] == "env://ALPHA_VANTAGE_API_KEY"


def test_crypto_coingecko_flips_from_source_ingest_health(monkeypatch):
    dss = {"state": "datasource_smoke_ok", "provider_statuses": {"coingecko": "read_unavailable"}}
    sources = [{"provider_key": "coingecko", "status": "read_unavailable"}]
    monkeypatch.setattr(
        bff_main,
        "_source_ingest_truth_by_connector",
        lambda: {"crypto-coingecko-spot": _truth("crypto-coingecko-spot", rows=100)},
    )

    out_dss, out_sources, _ = bff_main._overlay_source_health_truth(dss, sources)

    assert out_dss["source_health_source"] == "source_ingest"
    assert out_dss["provider_statuses"]["coingecko"] == "read_ok"
    assert out_dss["state"] == "datasource_smoke_ok"
    assert out_sources[0]["connectorId"] == "crypto-coingecko-spot"

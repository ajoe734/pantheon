from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from services.source_ingestion.configured import ConfiguredConnectorFetcher, JsonlConfiguredConnectorStore
from services.source_ingestion.connectors import (
    COINGECKO_SPOT_CONNECTOR_ID,
    CoinGeckoSpotMarketAdapter,
)
from services.source_ingestion.provider_adapters import provider_adapter_tokens


OHLC_TS = int(datetime(2026, 6, 10, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
OHLC_PAYLOAD = [
    [OHLC_TS, 67000.0, 68100.0, 66500.0, 67950.0],
]
PRICE_PAYLOAD = {
    "bitcoin": {
        "usd": 67950.0,
        "usd_market_cap": 1340000000000.0,
        "usd_24h_vol": 28000000000.0,
        "usd_24h_change": 1.25,
        "last_updated_at": int(datetime(2026, 6, 10, 0, 5, tzinfo=timezone.utc).timestamp()),
    }
}


def _completed_result(*, connector_id: str, normalized_count: int = 2):
    run = SimpleNamespace(
        ingest_run_id=f"ingest-{connector_id}",
        status=SimpleNamespace(value="completed"),
        finished_at=datetime(2026, 6, 10, 0, 10, tzinfo=timezone.utc),
        normalized_count=normalized_count,
        rejected_count=0,
    )
    return SimpleNamespace(run=run, watermark=SimpleNamespace(value="2026-06-10"))


def test_coingecko_adapter_normalizes_ohlc_price_and_evidence_packet() -> None:
    adapter = CoinGeckoSpotMarketAdapter(max_records=10)
    connector = adapter.connector()
    ohlc_records = adapter.records_from_ohlc_payload(
        "bitcoin",
        OHLC_PAYLOAD,
        vs_currency="usd",
        days="1",
        trace_id="trace-coingecko-ohlc",
    )
    price_records = adapter.records_from_simple_price_payload(
        PRICE_PAYLOAD,
        coin_ids=["bitcoin"],
        vs_currency="usd",
        trace_id="trace-coingecko-price",
    )
    packet = adapter.evidence_packet_from_records([*ohlc_records, *price_records])

    assert connector.connector_id == COINGECKO_SPOT_CONNECTOR_ID
    assert connector.auth_type.value == "none"
    assert connector.metadata["order_capable_provider"] is False
    assert connector.metadata["direct_execution_allowed"] is False

    ohlc_row = ohlc_records[0].metadata["normalized_row"]
    assert ohlc_records[0].connector_id == COINGECKO_SPOT_CONNECTOR_ID
    assert ohlc_records[0].metadata["dataset"] == "crypto_spot_ohlc"
    assert ohlc_row["symbol_canonical"] == "BTC.CRYPTO"
    assert ohlc_row["close"] == 67950.0
    assert ohlc_records[0].metadata["governance"]["direct_execution_allowed"] is False

    price_row = price_records[0].metadata["normalized_row"]
    assert price_records[0].metadata["dataset"] == "crypto_spot_price"
    assert price_row["price"] == 67950.0
    assert price_row["volume_24h"] == 28000000000.0
    assert price_row["as_of_time"] == "2026-06-10T00:05:00Z"

    assert packet["schema_version"] == "coingecko_source_evidence_packet.v1"
    assert packet["connector_id"] == COINGECKO_SPOT_CONNECTOR_ID
    assert packet["record_count"] == 2
    assert packet["datasets"] == ["crypto_spot_ohlc", "crypto_spot_price"]
    assert packet["governance"]["broker_consumption"] == "not_direct_action"


def test_provider_owned_dispatch_allowlists_coingecko_payload(tmp_path) -> None:
    store = JsonlConfiguredConnectorStore(tmp_path / "connectors.jsonl")
    adapter = CoinGeckoSpotMarketAdapter(max_records=5)
    fetch = adapter.fetch_config()
    fetch = {
        **fetch,
        "request": {
            "dataset": "crypto_spot_ohlc_and_price",
            "coin_ids": ["bitcoin"],
            "vs_currency": "usd",
            "ohlc_days": "1",
            "ohlc_payload": OHLC_PAYLOAD,
            "price_payload": PRICE_PAYLOAD,
        },
    }
    store.upsert_config(adapter.connector(), fetch)

    batch = ConfiguredConnectorFetcher(store).fetch_batch(COINGECKO_SPOT_CONNECTOR_ID, None, trace_id="trace-dispatch")

    assert "CoinGeckoSpotMarketAdapter.records_from_payload" in provider_adapter_tokens()
    assert len(batch.records) == 2
    assert {record.metadata["dataset"] for record in batch.records} == {
        "crypto_spot_ohlc",
        "crypto_spot_price",
    }
    assert batch.records[0].metadata["provider_owned_adapter"] == "CoinGeckoSpotMarketAdapter.records_from_payload"


def test_coingecko_adapter_builds_source_health_snapshot() -> None:
    health = CoinGeckoSpotMarketAdapter().source_health_from_result(
        _completed_result(connector_id=COINGECKO_SPOT_CONNECTOR_ID)
    )

    assert health.status == "ok"
    assert health.source_id == COINGECKO_SPOT_CONNECTOR_ID
    assert health.metadata["provider"] == "CoinGecko"
    assert health.metadata["normalized_datasets"] == ["crypto_spot_ohlc", "crypto_spot_price"]

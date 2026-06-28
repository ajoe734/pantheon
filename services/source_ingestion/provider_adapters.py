"""Allowlisted provider-owned adapter dispatch for source ingestion.

The configured connector store persists adapter names as data. This module is
the only place that maps those names to committed adapter classes, so a
connector config cannot import arbitrary code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services.research.adapters.taiwan_market_client import MopsRouteSpec, TaiwanMarketClient, TejTableSpec

from .connectors.base import SourceConnector, SourceEvidenceError, SourceRecord
from .connectors.finmind_taiwan import (
    FinMindTaiwanBrokerBulkBackfillAdapter,
    FinMindTaiwanBrokerDailyReportAdapter,
    FinMindTaiwanDatasetAdapter,
)
from .connectors.taiwan_market import MopsSourceIngestAdapter, TejSourceIngestAdapter
from .connectors.taiwan_official import TaiwanOfficialMarketDatasetAdapter
from .connectors.us_public import (
    FinraShortSaleAdapter,
    FredMacroSeriesAdapter,
    SecEdgarFilingAdapter,
    StooqDailyOhlcvAdapter,
)
from .connectors.us_paid_broker import (
    AlphaVantageUsEquityDailyAdapter,
    IbkrBrokerReadbackAdapter,
    PolygonUsEquityDailyAdapter,
    ShioajiBrokerReadbackAdapter,
)
from .connectors.yahoo_taiwan import AnueTaiwanRssAdapter, YahooTaiwanBrokerTopAdapter, YahooTaiwanRssAdapter


Handler = Callable[[Any, Mapping[str, Any], str], tuple[SourceRecord, ...]]


@dataclass(frozen=True)
class ProviderAdapterSpec:
    token: str
    adapter_cls: type
    handler: Handler
    config_keys: tuple[str, ...] = ()

    def build(self, connector: SourceConnector, adapter_config: Mapping[str, Any]) -> Any:
        kwargs = {key: adapter_config[key] for key in self.config_keys if key in adapter_config}
        kwargs["connector_id"] = connector.connector_id
        return self.adapter_cls(**kwargs)


def provider_adapter_tokens() -> tuple[str, ...]:
    return tuple(sorted((*ALLOWED_PROVIDER_ADAPTERS, *PROVIDER_ADAPTER_ALIASES)))


def is_provider_adapter_allowed(token: str) -> bool:
    return _canonical_provider_adapter_token(token) in ALLOWED_PROVIDER_ADAPTERS


def validate_provider_adapter_token(token: str) -> str:
    normalized = _canonical_provider_adapter_token(token)
    if normalized not in ALLOWED_PROVIDER_ADAPTERS:
        allowed = ", ".join(provider_adapter_tokens())
        raw = str(token or "").strip()
        raise SourceEvidenceError(f"provider-owned adapter is not allowlisted: {raw or '<missing>'}; allowed={allowed}")
    return normalized


def _canonical_provider_adapter_token(token: str) -> str:
    normalized = str(token or "").strip()
    return PROVIDER_ADAPTER_ALIASES.get(normalized, normalized)


def execute_provider_owned_adapter(
    *,
    connector: SourceConnector,
    fetch: Mapping[str, Any],
    trace_id: str,
    job_parameters: Mapping[str, Any] | None = None,
) -> tuple[SourceRecord, ...]:
    token = validate_provider_adapter_token(str(fetch.get("adapter") or fetch.get("provider_owned_fetcher") or ""))
    spec = ALLOWED_PROVIDER_ADAPTERS[token]
    adapter_config = _mapping(fetch.get("adapter_config"))
    adapter = spec.build(connector, adapter_config)
    request = {
        **_mapping(fetch.get("request")),
        **_mapping(job_parameters),
    }
    records = spec.handler(adapter, request, trace_id)
    max_records = int(fetch.get("max_records") or 100)
    if max_records < 1:
        raise SourceEvidenceError("fetch.max_records must be > 0 for provider_owned_adapter")
    return tuple(_attach_run_metadata(record, token=token, request=request) for record in records[:max_records])


def _mapping(value: Any) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if not isinstance(value, Mapping):
        raise SourceEvidenceError("provider-owned adapter config/request must be an object")
    return dict(value)


def _require(value: Any, field_name: str) -> Any:
    if value in (None, "", [], {}):
        raise SourceEvidenceError(f"provider-owned adapter request.{field_name} is required")
    return value


def _string_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _single_symbol(request: Mapping[str, Any]) -> str | None:
    symbol = str(request.get("symbol") or "").strip().upper()
    if symbol:
        return symbol
    symbols = _string_list(request.get("symbols"))
    if len(symbols) == 1:
        return symbols[0].upper()
    return None


def _bool(value: Any, *, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def _request_value(request: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in request and request[key] not in (None, ""):
            return request[key]
    return None


def _read_json_payload_file(file_path: str, *, field_name: str) -> Any:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        raise SourceEvidenceError(f"{field_name} requires payload or an existing JSON readback file")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceEvidenceError(f"{field_name} could not read JSON readback file") from exc


def _attach_run_metadata(record: SourceRecord, *, token: str, request: Mapping[str, Any]) -> SourceRecord:
    metadata = dict(record.metadata)
    metadata.setdefault("provider_owned_adapter", token)
    if request:
        job = {
            key: value
            for key, value in request.items()
            if key
            in {
                "dataset",
                "run_date",
                "date",
                "symbol",
                "symbols",
                "market",
                "cadence",
                "batch_index",
                "batch_count",
            }
        }
        if job:
            metadata.setdefault("ingest_job", job)
            if "dataset" in job:
                metadata.setdefault("dataset", job["dataset"])
            if "run_date" in job:
                metadata.setdefault("as_of_date", job["run_date"])
            if "date" in job:
                metadata.setdefault("as_of_date", job["date"])
            symbol = _single_symbol(job)
            if symbol:
                metadata.setdefault("symbol", symbol)
    return SourceRecord(
        source_id=record.source_id,
        connector_id=record.connector_id,
        source_type=record.source_type.value,
        title=record.title,
        content_ref=record.content_ref,
        status=record.status.value,
        metadata=metadata,
        trace_id=record.trace_id,
        created_at=record.created_at,
    )


def _finmind_dataset(adapter: FinMindTaiwanDatasetAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    return adapter.records_from_data_payload(
        str(_require(request.get("dataset"), "dataset")),
        _require(request.get("payload"), "payload"),
        api_endpoint=request.get("api_endpoint"),
        trace_id=trace_id,
    )


def _finmind_broker(
    adapter: FinMindTaiwanBrokerDailyReportAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> tuple[SourceRecord, ...]:
    return adapter.records_from_daily_report_payload(
        _require(request.get("payload"), "payload"),
        symbol=_single_symbol(request),
        trade_date=request.get("trade_date") or request.get("date") or request.get("run_date"),
        source_url=request.get("source_url"),
        available_time=request.get("available_time"),
        trace_id=trace_id,
    )


def _finmind_storage(
    adapter: FinMindTaiwanBrokerBulkBackfillAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> tuple[SourceRecord, ...]:
    return adapter.records_from_storage_objects_payload(
        _require(request.get("payload"), "payload"),
        dataset=str(request.get("dataset") or "TaiwanStockTradingDailyReport"),
        date=str(_require(request.get("date") or request.get("run_date"), "date")),
        trace_id=trace_id,
    )


def _yahoo_broker(adapter: YahooTaiwanBrokerTopAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    return adapter.records_from_html(
        str(_require(_single_symbol(request), "symbol")),
        str(_require(request.get("html_text") or request.get("payload"), "html_text")),
        source_url=request.get("source_url"),
        available_time=request.get("available_time"),
        trace_id=trace_id,
    )


def _yahoo_rss(adapter: YahooTaiwanRssAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    return adapter.records_from_rss(
        str(_require(request.get("rss_xml") or request.get("payload"), "rss_xml")),
        feed_url=request.get("feed_url"),
        trace_id=trace_id,
    )


def _anue_rss(adapter: AnueTaiwanRssAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    return adapter.records_from_rss(
        str(_require(request.get("rss_xml") or request.get("payload"), "rss_xml")),
        feed_url=request.get("feed_url"),
        trace_id=trace_id,
    )


def _mops_route(payload: Mapping[str, Any]) -> MopsRouteSpec:
    return MopsRouteSpec(
        route_id=str(payload["route_id"]),
        title_zh=str(payload["title_zh"]),
        category=str(payload["category"]),
        source_type=str(payload["source_type"]),
        endpoint_path=str(payload["endpoint_path"]),
        method=str(payload.get("method") or "POST"),
        default_params=dict(payload.get("default_params") or {}),
        required_params=tuple(str(item) for item in payload.get("required_params") or ()),
        export_csv_path=payload.get("export_csv_path"),
        allow_fetch=bool(payload.get("allow_fetch", True)),
        tags=tuple(str(item) for item in payload.get("tags") or ()),
    )


def _taiwan_official(
    adapter: TaiwanOfficialMarketDatasetAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> tuple[SourceRecord, ...]:
    dataset = str(request.get("dataset") or "tw_price_daily")
    venues = _string_list(request.get("venues") or request.get("venue")) or ["TWSE", "TPEx"]
    payloads = _mapping(request.get("payloads")) if request.get("payloads") not in (None, "") else {}
    single_payload = request.get("payload")
    timeout_seconds = float(request.get("timeout_seconds") or 20.0)
    records: list[SourceRecord] = []
    for venue in venues:
        payload = payloads.get(venue) or payloads.get(str(venue).upper()) or single_payload
        if payload in (None, ""):
            payload = adapter.fetch_payload(dataset, venue, timeout_seconds=timeout_seconds)
        records.extend(
            adapter.records_from_payload(
                dataset,
                venue,
                payload,
                source_dataset=request.get("source_dataset"),
                api_endpoint=request.get("api_endpoint"),
                trade_date=request.get("trade_date") or request.get("date") or request.get("run_date"),
                available_time=request.get("available_time"),
                universe_tier=str(request.get("universe_tier") or "core_universe"),
                trace_id=trace_id,
            )
        )
    return tuple(records)


def _mops(adapter: MopsSourceIngestAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    client = TaiwanMarketClient()
    if request.get("route") not in (None, ""):
        route = _mops_route(_mapping(request.get("route")))
    else:
        route = client.mops_route(str(request.get("route_id") or "t05sr01_1"))
    if request.get("payload") not in (None, ""):
        payload = _mapping(request.get("payload"))
    else:
        payload = client.fetch_mops_route(route.route_id, params=_mapping(request.get("params")))
    return adapter.records_from_payload(route, payload, trace_id=trace_id)


def _tej_table(payload: Mapping[str, Any]) -> TejTableSpec:
    db_code = str(payload.get("db_code") or "")
    table_code = str(payload.get("table_code") or "")
    dataset_code = str(payload.get("dataset_code") or "")
    if dataset_code and (not db_code or not table_code) and "/" in dataset_code:
        db_code, table_code = dataset_code.split("/", 1)
    return TejTableSpec(
        db_code=db_code,
        table_code=table_code,
        title_zh=str(payload.get("title_zh") or table_code),
        group_name=str(payload.get("group_name") or "unknown"),
        source_category=str(payload.get("source_category") or "market"),
        data_range=payload.get("data_range"),
        description=payload.get("description"),
    )


def _tej(adapter: TejSourceIngestAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    table = _tej_table(_mapping(_require(request.get("table"), "table")))
    rows = _require(request.get("rows") or request.get("payload"), "rows")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
        raise SourceEvidenceError("provider-owned adapter request.rows must be a list")
    return adapter.records_from_rows(table, rows, trace_id=trace_id)


def _sec_edgar(adapter: SecEdgarFilingAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    return adapter.records_from_payload(
        str(_require(request.get("dataset"), "dataset")),
        _mapping(_require(request.get("payload"), "payload")),
        symbol=request.get("symbol"),
        cik=request.get("cik"),
        trace_id=trace_id,
    )


def _fred(adapter: FredMacroSeriesAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    series_id = str(_require(request.get("series_id"), "series_id"))
    if request.get("csv_text") not in (None, ""):
        return adapter.records_from_csv(series_id, str(request.get("csv_text")), trace_id=trace_id)
    return adapter.records_from_observations_payload(
        series_id,
        _mapping(_require(request.get("payload"), "payload")),
        source_url=request.get("source_url"),
        fetch_mode=str(request.get("fetch_mode") or "api_or_fixture"),
        trace_id=trace_id,
    )


def _finra_short_sale(
    adapter: FinraShortSaleAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> tuple[SourceRecord, ...]:
    return adapter.records_from_short_volume_text(
        str(_require(request.get("text") or request.get("payload"), "text")),
        trade_date=request.get("trade_date") or request.get("date") or request.get("run_date"),
        source_url=request.get("source_url"),
        trace_id=trace_id,
    )


def _stooq_daily(adapter: StooqDailyOhlcvAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    return adapter.records_from_csv(
        str(_require(request.get("symbol"), "symbol")),
        str(_require(request.get("csv_text") or request.get("payload"), "csv_text")),
        source_url=request.get("source_url"),
        trace_id=trace_id,
    )


def _polygon_daily(
    adapter: PolygonUsEquityDailyAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> tuple[SourceRecord, ...]:
    symbol = str(_require(_single_symbol(request), "symbol"))
    adjusted = _bool(request.get("adjusted"), default=True)
    payload = _request_value(request, "payload", "aggs_payload")
    if payload is None:
        date = request.get("date") or request.get("run_date")
        payload = adapter.fetch_daily_aggs(
            symbol,
            start_date=str(_require(request.get("start_date") or date, "start_date")),
            end_date=str(_require(request.get("end_date") or date, "end_date")),
            adjusted=adjusted,
        )
    return adapter.records_from_aggs_payload(
        symbol,
        payload,
        source_url=request.get("source_url"),
        adjusted=adjusted,
        trace_id=trace_id,
    )


def _alpha_vantage_daily(
    adapter: AlphaVantageUsEquityDailyAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> tuple[SourceRecord, ...]:
    symbol = str(_require(_single_symbol(request), "symbol"))
    payload = _request_value(request, "payload", "time_series_payload")
    if payload is None:
        payload = adapter.fetch_daily_time_series(
            symbol,
            outputsize=str(request.get("outputsize") or "compact"),
        )
    return adapter.records_from_time_series_payload(
        symbol,
        payload,
        source_url=request.get("source_url"),
        trace_id=trace_id,
    )


def _readback_file_path(
    adapter: IbkrBrokerReadbackAdapter | ShioajiBrokerReadbackAdapter,
    request: Mapping[str, Any],
) -> str:
    return str(
        request.get("file_path")
        or request.get("readback_file_path")
        or adapter.resolve_readback_path()
        or ""
    ).strip()


def _ibkr_readback(
    adapter: IbkrBrokerReadbackAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> tuple[SourceRecord, ...]:
    file_path = _readback_file_path(adapter, request)
    if not file_path:
        raise SourceEvidenceError(
            "IBKR readback requires file_path, readback_file_path, or IBKR_READBACK_FILE_PATH"
        )
    payload = _request_value(request, "payload", "rows", "readback")
    if payload is None:
        payload = _read_json_payload_file(file_path, field_name="IBKR readback")
    return adapter.records_from_readback_file(file_path, payload, trace_id=trace_id)


def _shioaji_readback(
    adapter: ShioajiBrokerReadbackAdapter,
    request: Mapping[str, Any],
    trace_id: str,
) -> tuple[SourceRecord, ...]:
    file_path = _readback_file_path(adapter, request)
    if not file_path:
        raise SourceEvidenceError(
            "Shioaji readback requires file_path, readback_file_path, or SHIOAJI_READBACK_FILE_PATH"
        )
    payload = _request_value(request, "payload", "rows", "readback")
    if payload is None:
        payload = _read_json_payload_file(file_path, field_name="Shioaji readback")
    return adapter.records_from_readback_file(file_path, payload, trace_id=trace_id)


PROVIDER_ADAPTER_ALIASES: dict[str, str] = {
    "TaiwanOfficialMarketDatasetAdapter": "TaiwanOfficialMarketDatasetAdapter.records_from_payload",
    "MopsSourceIngestAdapter": "MopsSourceIngestAdapter.records_from_payload",
}


ALLOWED_PROVIDER_ADAPTERS: dict[str, ProviderAdapterSpec] = {
    "TaiwanOfficialMarketDatasetAdapter.records_from_payload": ProviderAdapterSpec(
        token="TaiwanOfficialMarketDatasetAdapter.records_from_payload",
        adapter_cls=TaiwanOfficialMarketDatasetAdapter,
        handler=_taiwan_official,
        config_keys=("max_records",),
    ),
    "FinMindTaiwanDatasetAdapter.records_from_data_payload": ProviderAdapterSpec(
        token="FinMindTaiwanDatasetAdapter.records_from_data_payload",
        adapter_cls=FinMindTaiwanDatasetAdapter,
        handler=_finmind_dataset,
        config_keys=("secret_ref_id", "max_records", "entitlement_tier"),
    ),
    "FinMindTaiwanBrokerDailyReportAdapter.records_from_daily_report_payload": ProviderAdapterSpec(
        token="FinMindTaiwanBrokerDailyReportAdapter.records_from_daily_report_payload",
        adapter_cls=FinMindTaiwanBrokerDailyReportAdapter,
        handler=_finmind_broker,
        config_keys=("secret_ref_id", "max_rank", "entitlement_tier"),
    ),
    "FinMindTaiwanBrokerBulkBackfillAdapter.records_from_storage_objects_payload": ProviderAdapterSpec(
        token="FinMindTaiwanBrokerBulkBackfillAdapter.records_from_storage_objects_payload",
        adapter_cls=FinMindTaiwanBrokerBulkBackfillAdapter,
        handler=_finmind_storage,
        config_keys=("secret_ref_id",),
    ),
    "YahooTaiwanBrokerTopAdapter.records_from_html": ProviderAdapterSpec(
        token="YahooTaiwanBrokerTopAdapter.records_from_html",
        adapter_cls=YahooTaiwanBrokerTopAdapter,
        handler=_yahoo_broker,
        config_keys=("max_rank",),
    ),
    "YahooTaiwanRssAdapter.records_from_rss": ProviderAdapterSpec(
        token="YahooTaiwanRssAdapter.records_from_rss",
        adapter_cls=YahooTaiwanRssAdapter,
        handler=_yahoo_rss,
        config_keys=("feed_url", "max_records"),
    ),
    "AnueTaiwanRssAdapter.records_from_rss": ProviderAdapterSpec(
        token="AnueTaiwanRssAdapter.records_from_rss",
        adapter_cls=AnueTaiwanRssAdapter,
        handler=_anue_rss,
        config_keys=("feed_url", "max_records"),
    ),
    "MopsSourceIngestAdapter.records_from_payload": ProviderAdapterSpec(
        token="MopsSourceIngestAdapter.records_from_payload",
        adapter_cls=MopsSourceIngestAdapter,
        handler=_mops,
        config_keys=("max_records",),
    ),
    "TejSourceIngestAdapter.records_from_rows": ProviderAdapterSpec(
        token="TejSourceIngestAdapter.records_from_rows",
        adapter_cls=TejSourceIngestAdapter,
        handler=_tej,
        config_keys=("secret_ref_id", "max_records"),
    ),
    "SecEdgarFilingAdapter.records_from_payload": ProviderAdapterSpec(
        token="SecEdgarFilingAdapter.records_from_payload",
        adapter_cls=SecEdgarFilingAdapter,
        handler=_sec_edgar,
        config_keys=("user_agent", "user_agent_env", "max_records"),
    ),
    "FredMacroSeriesAdapter.records_from_observations_payload": ProviderAdapterSpec(
        token="FredMacroSeriesAdapter.records_from_observations_payload",
        adapter_cls=FredMacroSeriesAdapter,
        handler=_fred,
        config_keys=("secret_ref_id", "max_records"),
    ),
    "FinraShortSaleAdapter.records_from_short_volume_text": ProviderAdapterSpec(
        token="FinraShortSaleAdapter.records_from_short_volume_text",
        adapter_cls=FinraShortSaleAdapter,
        handler=_finra_short_sale,
        config_keys=("max_records", "expected_publication_delay_hours"),
    ),
    "StooqDailyOhlcvAdapter.records_from_csv": ProviderAdapterSpec(
        token="StooqDailyOhlcvAdapter.records_from_csv",
        adapter_cls=StooqDailyOhlcvAdapter,
        handler=_stooq_daily,
        config_keys=("max_records", "connector_status", "disabled_reason"),
    ),
    "PolygonUsEquityDailyAdapter.records_from_aggs_payload": ProviderAdapterSpec(
        token="PolygonUsEquityDailyAdapter.records_from_aggs_payload",
        adapter_cls=PolygonUsEquityDailyAdapter,
        handler=_polygon_daily,
        config_keys=("secret_ref_id", "max_records"),
    ),
    "AlphaVantageUsEquityDailyAdapter.records_from_time_series_payload": ProviderAdapterSpec(
        token="AlphaVantageUsEquityDailyAdapter.records_from_time_series_payload",
        adapter_cls=AlphaVantageUsEquityDailyAdapter,
        handler=_alpha_vantage_daily,
        config_keys=("secret_ref_id", "max_records", "connector_status", "disabled_reason"),
    ),
    "IbkrBrokerReadbackAdapter.records_from_readback_file": ProviderAdapterSpec(
        token="IbkrBrokerReadbackAdapter.records_from_readback_file",
        adapter_cls=IbkrBrokerReadbackAdapter,
        handler=_ibkr_readback,
        config_keys=("readback_file_env",),
    ),
    "ShioajiBrokerReadbackAdapter.records_from_readback_file": ProviderAdapterSpec(
        token="ShioajiBrokerReadbackAdapter.records_from_readback_file",
        adapter_cls=ShioajiBrokerReadbackAdapter,
        handler=_shioaji_readback,
        config_keys=("readback_file_env",),
    ),
}

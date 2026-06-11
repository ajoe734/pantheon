"""Allowlisted provider-owned adapter dispatch for source ingestion.

The configured connector store persists adapter names as data. This module is
the only place that maps those names to committed adapter classes, so a
connector config cannot import arbitrary code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

from services.research.adapters.taiwan_market_client import MopsRouteSpec, TejTableSpec

from .connectors.base import SourceConnector, SourceEvidenceError, SourceRecord
from .connectors.finmind_taiwan import (
    FinMindTaiwanBrokerBulkBackfillAdapter,
    FinMindTaiwanBrokerDailyReportAdapter,
    FinMindTaiwanDatasetAdapter,
)
from .connectors.taiwan_market import MopsSourceIngestAdapter, TejSourceIngestAdapter
from .connectors.us_public import (
    FinraShortSaleAdapter,
    FredMacroSeriesAdapter,
    SecEdgarFilingAdapter,
    StooqDailyOhlcvAdapter,
)
from .connectors.yahoo_taiwan import YahooTaiwanBrokerTopAdapter, YahooTaiwanRssAdapter


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
    return tuple(sorted(ALLOWED_PROVIDER_ADAPTERS))


def is_provider_adapter_allowed(token: str) -> bool:
    return str(token or "").strip() in ALLOWED_PROVIDER_ADAPTERS


def validate_provider_adapter_token(token: str) -> str:
    normalized = str(token or "").strip()
    if normalized not in ALLOWED_PROVIDER_ADAPTERS:
        allowed = ", ".join(provider_adapter_tokens())
        raise SourceEvidenceError(f"provider-owned adapter is not allowlisted: {normalized or '<missing>'}; allowed={allowed}")
    return normalized


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


def _mops(adapter: MopsSourceIngestAdapter, request: Mapping[str, Any], trace_id: str) -> tuple[SourceRecord, ...]:
    route = _mops_route(_mapping(_require(request.get("route"), "route")))
    return adapter.records_from_payload(route, _mapping(_require(request.get("payload"), "payload")), trace_id=trace_id)


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


ALLOWED_PROVIDER_ADAPTERS: dict[str, ProviderAdapterSpec] = {
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
}

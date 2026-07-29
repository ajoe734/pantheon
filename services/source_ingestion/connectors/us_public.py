"""US public market-data adapters for SEC EDGAR, FRED, FINRA, and Stooq."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any, Mapping, Sequence

from services.external_egress import open_external_url

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceEvidenceError,
    SourceMetadata,
    SourceRecord,
)
from ..source_health import SourceHealth, SourceHealthStatus


SEC_EDGAR_CONNECTOR_ID = "us-sec-edgar-filings"
FRED_CONNECTOR_ID = "us-fred-macro"
FINRA_SHORT_SALE_CONNECTOR_ID = "us-finra-short-sale"
STOOQ_DAILY_OHLCV_CONNECTOR_ID = "us-stooq-daily-ohlcv"

SEC_FILING_EVENT_SCHEMA_HASH = "sec_filing_event.v1"
SEC_COMPANY_FACT_SCHEMA_HASH = "sec_company_fact.v1"
FRED_OBSERVATION_SCHEMA_HASH = "macro_fred_observation.v1"
FINRA_SHORT_VOLUME_SCHEMA_HASH = "us_short_volume_daily.v1"
US_SHORT_INTEREST_SCHEMA_HASH = "us_short_interest.v1"
STOOQ_DAILY_OHLCV_SCHEMA_HASH = "us_price_daily.v1"

SEC_COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
FRED_API_URL = "https://api.stlouisfed.org/fred/series/observations"
FRED_CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FINRA_SHORT_VOLUME_URL = "https://cdn.finra.org/equity/regsho/daily/CNMSshvol{date}.txt"
STOOQ_DAILY_URL = "https://stooq.com/q/d/l/"

FRED_STARTER_SERIES: tuple[dict[str, Any], ...] = (
    {
        "series_id": "GDP",
        "title": "Gross Domestic Product",
        "frequency": "quarterly",
        "release_lag_days": 45,
        "staleness_seconds": 15552000,
        "feature_target": "features/macro_regime_features",
    },
    {
        "series_id": "CPIAUCSL",
        "title": "Consumer Price Index for All Urban Consumers",
        "frequency": "monthly",
        "release_lag_days": 21,
        "staleness_seconds": 3888000,
        "feature_target": "features/macro_regime_features",
    },
    {
        "series_id": "UNRATE",
        "title": "Unemployment Rate",
        "frequency": "monthly",
        "release_lag_days": 14,
        "staleness_seconds": 3888000,
        "feature_target": "features/macro_regime_features",
    },
    {
        "series_id": "FEDFUNDS",
        "title": "Effective Federal Funds Rate",
        "frequency": "monthly",
        "release_lag_days": 7,
        "staleness_seconds": 3888000,
        "feature_target": "features/macro_regime_features",
    },
    {
        "series_id": "DGS10",
        "title": "Market Yield on U.S. Treasury Securities at 10-Year Constant Maturity",
        "frequency": "daily",
        "release_lag_days": 2,
        "staleness_seconds": 432000,
        "feature_target": "features/macro_regime_features",
    },
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _first(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, "", ".", "-", "--", "N/A"):
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {".", "-", "--"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    number = _float_or_none(value)
    if number is None:
        return None
    return int(number)


def _ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator


def _cik10(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise SourceEvidenceError("CIK is required")
    digits = "".join(ch for ch in text if ch.isdigit())
    if not digits:
        raise SourceEvidenceError("CIK must contain digits")
    return digits.zfill(10)


def _symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def _sec_archive_url(cik: str, accession_number: str, primary_document: str | None) -> str | None:
    if not primary_document:
        return None
    compact_accession = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{compact_accession}/{primary_document}"


def _rows_from_payload(payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, Mapping):
        for key in ("data", "results", "items", "records"):
            value = payload.get(key)
            if isinstance(value, list):
                return tuple(dict(row) for row in value if isinstance(row, Mapping))
        return (dict(payload),)
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return tuple(dict(row) for row in payload if isinstance(row, Mapping))
    return tuple()


def _csv_rows(text: str, *, delimiter: str = ",") -> tuple[dict[str, str], ...]:
    reader = csv.DictReader(StringIO(text.strip()), delimiter=delimiter)
    return tuple({str(key): str(value or "").strip() for key, value in row.items()} for row in reader)


def _date_from_finra(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _request_json(url: str, *, user_agent: str, timeout_seconds: float = 20.0) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
    )
    with open_external_url(
        request,
        caller="source_ingest.us_public",
        timeout=timeout_seconds,
    ) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, Mapping):
        raise SourceEvidenceError("provider response must be a JSON object")
    return payload


def _request_text(
    url: str,
    *,
    user_agent: str = "pantheon-source-ingest/0.1",
    timeout_seconds: float = 20.0,
) -> str:
    request = urllib.request.Request(
        url,
        headers={"Accept": "text/csv,text/plain,*/*", "User-Agent": user_agent},
    )
    with open_external_url(
        request,
        caller="source_ingest.us_public",
        timeout=timeout_seconds,
    ) as response:
        return response.read().decode("utf-8", errors="replace")


def _finished_at(result: Any) -> str | None:
    run = getattr(result, "run", None)
    finished_at = getattr(run, "finished_at", None)
    if hasattr(finished_at, "isoformat"):
        return finished_at.isoformat().replace("+00:00", "Z")
    return finished_at


def _health_from_result(
    *,
    connector_id: str,
    schema_hash: str,
    result: Any,
    metadata: Mapping[str, Any] | None = None,
) -> SourceHealth:
    watermark = getattr(result, "watermark", None)
    run = getattr(result, "run", None)
    status = getattr(run, "status", "failed")
    run_status = status.value if hasattr(status, "value") else str(status)
    finished = _finished_at(result)
    return SourceHealth(
        source_id=connector_id,
        source_kind="data_source",
        status=SourceHealthStatus.OK.value if run_status == "completed" else SourceHealthStatus.FAILED.value,
        last_success_at=finished if run_status == "completed" else None,
        last_failure_at=finished if run_status != "completed" else None,
        latest_watermark=getattr(watermark, "value", None),
        row_count_last_run=int(getattr(run, "normalized_count", 0) or 0),
        rejected_count_last_run=int(getattr(run, "rejected_count", 0) or 0),
        schema_hash=schema_hash,
        metadata={
            "connector_id": connector_id,
            "ingest_run_id": getattr(run, "ingest_run_id", None),
            **dict(metadata or {}),
        },
    )


@dataclass(frozen=True)
class SecEdgarFilingAdapter(SourceConnectorProvider):
    """SEC EDGAR submissions and company-facts adapter."""

    connector_id: str = SEC_EDGAR_CONNECTOR_ID
    user_agent: str | None = None
    user_agent_env: str = "SEC_EDGAR_USER_AGENT"
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="filing",
            provider="SEC EDGAR",
            license_scope="public_official_reference",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="public_official_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/sec-edgar-public-official-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=10,
                burst=2,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/sec-edgar-compliant-user-agent",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="SEC EDGAR filings and company facts",
                homepage_url="https://www.sec.gov/edgar",
                docs_url="https://www.sec.gov/search-filings/edgar-application-programming-interfaces",
                owner="U.S. Securities and Exchange Commission",
                tags=("us", "sec", "edgar", "filing", "official_reference"),
            ),
            metadata={
                "source_class": "official_reference",
                "official_reference_truth": True,
                "normalized_datasets": ["sec_filing_event", "sec_company_fact"],
                "schema_hashes": [SEC_FILING_EVENT_SCHEMA_HASH, SEC_COMPANY_FACT_SCHEMA_HASH],
                "requires_configured_user_agent": True,
                "user_agent_env": self.user_agent_env,
                "symbol_mapping_source": SEC_COMPANY_TICKERS_URL,
                "feature_targets": ["features/filing_event_flags"],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "SecEdgarFilingAdapter.records_from_payload",
            "adapter_config": {
                "user_agent_env": self.user_agent_env,
                "max_records": self.max_records,
            },
            "request": {},
            "max_records": self.max_records,
            "next_watermark": None,
            "datasets": ["sec_filing_event", "sec_company_fact"],
        }

    def configured_user_agent(self) -> str:
        value = str(self.user_agent or os.getenv(self.user_agent_env, "")).strip()
        if not value or "@" not in value:
            raise SourceEvidenceError(
                f"SEC EDGAR fetch requires {self.user_agent_env} with app identity and contact email"
            )
        return value

    def fetch_company_tickers(self) -> Mapping[str, Any]:
        return _request_json(SEC_COMPANY_TICKERS_URL, user_agent=self.configured_user_agent())

    def fetch_submissions(self, cik: str) -> Mapping[str, Any]:
        return _request_json(SEC_SUBMISSIONS_URL.format(cik=_cik10(cik)), user_agent=self.configured_user_agent())

    def fetch_companyfacts(self, cik: str) -> Mapping[str, Any]:
        return _request_json(SEC_COMPANYFACTS_URL.format(cik=_cik10(cik)), user_agent=self.configured_user_agent())

    def ticker_mapping_from_payload(self, payload: Mapping[str, Any] | Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
        if isinstance(payload, Mapping) and payload and all(isinstance(value, Mapping) for value in payload.values()):
            rows = tuple(dict(value) for value in payload.values())
        else:
            rows = _rows_from_payload(payload)
        mapping: dict[str, dict[str, Any]] = {}
        for row in rows:
            ticker = _symbol(_first(row, "ticker", "symbol"))
            if not ticker:
                continue
            mapping[ticker] = {
                "symbol": ticker,
                "cik": _cik10(_first(row, "cik_str", "cik")),
                "company_name": _text(_first(row, "title", "name", "company_name")) or None,
                "raw_row": dict(row),
            }
        return mapping

    def records_from_payload(
        self,
        dataset: str,
        payload: Mapping[str, Any],
        *,
        symbol: str | None = None,
        cik: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        if dataset == "sec_filing_event":
            return self.records_from_submissions_payload(payload, symbol=symbol, cik=cik, trace_id=trace_id)
        if dataset == "sec_company_fact":
            return self.records_from_companyfacts_payload(payload, symbol=symbol, cik=cik, trace_id=trace_id)
        raise SourceEvidenceError(f"unsupported SEC EDGAR dataset: {dataset}")

    def records_from_submissions_payload(
        self,
        payload: Mapping[str, Any],
        *,
        symbol: str | None = None,
        cik: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        cik10 = _cik10(cik or payload.get("cik") or payload.get("centralIndexKey"))
        symbol_value = _symbol(symbol or payload.get("ticker") or payload.get("symbol"))
        entity_name = _text(payload.get("name")) or None
        recent = payload.get("filings", {}).get("recent", {}) if isinstance(payload.get("filings"), Mapping) else {}
        if not isinstance(recent, Mapping):
            return tuple()
        accession_numbers = list(recent.get("accessionNumber") or [])
        records: list[SourceRecord] = []
        for index, accession_number in enumerate(accession_numbers[: self.max_records]):
            accession = _text(accession_number)
            if not accession:
                continue
            row = {key: values[index] if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)) and index < len(values) else None for key, values in recent.items()}
            form_type = _text(row.get("form"))
            filing_date = _text(row.get("filingDate"))
            accepted_at = _text(row.get("acceptanceDateTime")) or filing_date
            primary_document = _text(row.get("primaryDocument")) or None
            normalized_row = {
                "schema_version": SEC_FILING_EVENT_SCHEMA_HASH,
                "target_table": "sec_filing_event",
                "provider": "SEC EDGAR",
                "cik": cik10,
                "symbol": symbol_value or None,
                "symbol_canonical": f"{symbol_value}.US" if symbol_value else None,
                "company_name": entity_name,
                "accession_number": accession,
                "form_type": form_type or None,
                "filing_date": filing_date or None,
                "report_date": _text(row.get("reportDate")) or None,
                "accepted_at": accepted_at or None,
                "available_time": accepted_at or filing_date or _utc_now(),
                "primary_document": primary_document,
                "primary_document_url": _sec_archive_url(cik10, accession, primary_document),
                "items": _text(row.get("items")) or None,
                "file_number": _text(row.get("fileNumber")) or None,
                "film_number": _text(row.get("filmNumber")) or None,
                "raw_row": dict(row),
            }
            row_hash = _stable_hash({"dataset": "sec_filing_event", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"sec-edgar:filing:{cik10}:{accession}",
                    connector_id=self.connector_id,
                    source_type="filing",
                    title=f"SEC {form_type or 'filing'} {symbol_value or cik10} {filing_date}".strip(),
                    content_ref=f"sec-edgar://submissions/{cik10}/{accession}",
                    metadata={
                        "source_class": "official_reference",
                        "provider": "SEC EDGAR",
                        "dataset": "sec_filing_event",
                        "symbol": symbol_value or None,
                        "cik": cik10,
                        "event_time": accepted_at or filing_date or _utc_now(),
                        "available_time": accepted_at or filing_date or _utc_now(),
                        "normalized_row": normalized_row,
                        "raw_row": dict(row),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "public_official_reference",
                        "schema_hash": SEC_FILING_EVENT_SCHEMA_HASH,
                        "feature_targets": ["features/filing_event_flags"],
                        "row_hash": row_hash,
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def records_from_companyfacts_payload(
        self,
        payload: Mapping[str, Any],
        *,
        symbol: str | None = None,
        cik: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        cik10 = _cik10(cik or payload.get("cik"))
        symbol_value = _symbol(symbol or payload.get("ticker") or payload.get("symbol"))
        entity_name = _text(payload.get("entityName") or payload.get("name")) or None
        facts = payload.get("facts") if isinstance(payload.get("facts"), Mapping) else {}
        records: list[SourceRecord] = []
        for taxonomy, concepts in facts.items():
            if not isinstance(concepts, Mapping):
                continue
            for concept, concept_payload in concepts.items():
                if not isinstance(concept_payload, Mapping):
                    continue
                units = concept_payload.get("units") if isinstance(concept_payload.get("units"), Mapping) else {}
                for unit, observations in units.items():
                    if not isinstance(observations, Sequence) or isinstance(observations, (str, bytes, bytearray)):
                        continue
                    for observation in observations:
                        if not isinstance(observation, Mapping):
                            continue
                        normalized_row = {
                            "schema_version": SEC_COMPANY_FACT_SCHEMA_HASH,
                            "target_table": "sec_company_fact",
                            "provider": "SEC EDGAR",
                            "cik": cik10,
                            "symbol": symbol_value or None,
                            "symbol_canonical": f"{symbol_value}.US" if symbol_value else None,
                            "company_name": entity_name,
                            "taxonomy": str(taxonomy),
                            "concept": str(concept),
                            "label": _text(concept_payload.get("label")) or None,
                            "description": _text(concept_payload.get("description")) or None,
                            "unit": str(unit),
                            "value": observation.get("val"),
                            "fiscal_year": observation.get("fy"),
                            "fiscal_period": observation.get("fp"),
                            "form_type": observation.get("form"),
                            "filed_date": observation.get("filed"),
                            "start_date": observation.get("start"),
                            "end_date": observation.get("end"),
                            "frame": observation.get("frame"),
                            "available_time": observation.get("filed") or _utc_now(),
                            "raw_row": dict(observation),
                        }
                        row_hash = _stable_hash({"dataset": "sec_company_fact", "row": normalized_row})
                        records.append(
                            SourceRecord(
                                source_id=f"sec-edgar:fact:{cik10}:{taxonomy}:{concept}:{unit}:{row_hash}",
                                connector_id=self.connector_id,
                                source_type="filing",
                                title=f"SEC fact {symbol_value or cik10} {concept} {observation.get('end') or ''}".strip(),
                                content_ref=f"sec-edgar://companyfacts/{cik10}/{taxonomy}/{concept}/{row_hash}",
                                metadata={
                                    "source_class": "official_reference",
                                    "provider": "SEC EDGAR",
                                    "dataset": "sec_company_fact",
                                    "symbol": symbol_value or None,
                                    "cik": cik10,
                                    "event_time": observation.get("end") or observation.get("filed") or _utc_now(),
                                    "available_time": observation.get("filed") or _utc_now(),
                                    "normalized_row": normalized_row,
                                    "raw_row": dict(observation),
                                    "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                                    "access_scope": ["public", "research"],
                                    "license_scope": "public_official_reference",
                                    "schema_hash": SEC_COMPANY_FACT_SCHEMA_HASH,
                                    "row_hash": row_hash,
                                },
                                trace_id=trace_id,
                            )
                        )
                        if len(records) >= self.max_records:
                            return tuple(records)
        return tuple(records)

    def source_health_from_result(self, result: Any) -> SourceHealth:
        return _health_from_result(
            connector_id=self.connector_id,
            schema_hash=SEC_FILING_EVENT_SCHEMA_HASH,
            result=result,
            metadata={"normalized_datasets": ["sec_filing_event", "sec_company_fact"]},
        )


@dataclass(frozen=True)
class FredMacroSeriesAdapter(SourceConnectorProvider):
    """FRED macro series adapter with required API-key mode and CSV fixture parsing."""

    connector_id: str = FRED_CONNECTOR_ID
    secret_ref_id: str = "env://FRED_API_KEY"
    max_records: int = 100
    starter_series: Sequence[Mapping[str, Any]] = field(default_factory=lambda: FRED_STARTER_SERIES)
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="macro",
            provider="FRED",
            license_scope="public_macro_reference",
            auth_type=AuthType.API_KEY,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.API_KEY, secret_ref=self.secret_ref_id),
            license_policy=LicensePolicy(
                license_scope="public_macro_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/fred-public-macro-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=60,
                burst=5,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/fred-public-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="FRED macro series",
                homepage_url="https://fred.stlouisfed.org/",
                docs_url="https://fred.stlouisfed.org/docs/api/fred/",
                owner="Federal Reserve Bank of St. Louis",
                tags=("us", "fred", "macro", "public_reference"),
            ),
            metadata={
                "source_class": "public_macro_reference",
                "normalized_datasets": ["macro_fred_observation"],
                "schema_hash": FRED_OBSERVATION_SCHEMA_HASH,
                "symbol_scope": "global_no_symbol_filter",
                "secret_ref_id": self.secret_ref_id,
                "required_secret_ref_id": self.secret_ref_id,
                "keyed_api_url": FRED_API_URL,
                "csv_fixture_parser_supported": True,
                "starter_series": [dict(item) for item in self.starter_series],
                "feature_targets": ["features/macro_regime_features"],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "FredMacroSeriesAdapter.records_from_observations_payload",
            "adapter_config": {
                "secret_ref_id": self.secret_ref_id,
                "max_records": self.max_records,
            },
            "request": {
                "series_ids": [str(item["series_id"]).upper() for item in self.starter_series],
                "fetch_mode": "keyed_api",
            },
            "max_records": self.max_records,
            "next_watermark": None,
            "dataset": "macro_fred_observation",
            "symbol_scope": "global_no_symbol_filter",
            "starter_series": [dict(item) for item in self.starter_series],
        }

    def secret_env_name(self) -> str:
        if self.secret_ref_id.startswith("env://"):
            return self.secret_ref_id.removeprefix("env://")
        return "FRED_API_KEY"

    def resolve_api_key(self) -> str | None:
        return os.getenv(self.secret_env_name(), "").strip() or None

    def fetch_api_observations(self, series_id: str, *, api_key: str | None = None) -> Mapping[str, Any]:
        key = api_key or self.resolve_api_key()
        if not key:
            raise SourceEvidenceError(
                f"FRED API fetch requires {self.secret_env_name()} via {self.secret_ref_id}; none found in environment"
            )
        params = {"series_id": series_id, "file_type": "json"}
        params["api_key"] = key
        url = f"{FRED_API_URL}?{urllib.parse.urlencode(params)}"
        return _request_json(url, user_agent="pantheon-source-ingest/0.1")

    def fetch_csv_observations(self, series_id: str) -> str:
        url = f"{FRED_CSV_URL}?{urllib.parse.urlencode({'id': series_id})}"
        return _request_text(url)

    def series_config(self, series_id: str) -> dict[str, Any]:
        normalized = str(series_id).strip().upper()
        for config in self.starter_series:
            if str(config.get("series_id") or "").upper() == normalized:
                return dict(config)
        return {
            "series_id": normalized,
            "frequency": "unknown",
            "release_lag_days": None,
            "staleness_seconds": None,
            "feature_target": "features/macro_regime_features",
        }

    def records_from_csv(self, series_id: str, csv_text: str, *, trace_id: str = "") -> tuple[SourceRecord, ...]:
        rows = _csv_rows(csv_text)
        if not rows:
            return tuple()
        header = list(rows[0])
        date_key = next((key for key in header if key.lower() in {"date", "observation_date"}), header[0])
        value_key = next((key for key in header if key != date_key), series_id)
        observations = [
            {
                "date": row.get(date_key),
                "value": row.get(value_key),
                "realtime_start": None,
                "realtime_end": None,
            }
            for row in rows
        ]
        return self.records_from_observations_payload(
            series_id,
            {"observations": observations},
            source_url=f"{FRED_CSV_URL}?id={series_id}",
            fetch_mode="public_csv_fallback",
            trace_id=trace_id,
        )

    def records_from_observations_payload(
        self,
        series_id: str,
        payload: Mapping[str, Any],
        *,
        source_url: str | None = None,
        fetch_mode: str = "api_or_fixture",
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        config = self.series_config(series_id)
        observations = payload.get("observations") if isinstance(payload.get("observations"), list) else []
        records: list[SourceRecord] = []
        for observation in observations[: self.max_records]:
            if not isinstance(observation, Mapping):
                continue
            value = _float_or_none(observation.get("value"))
            if value is None:
                continue
            date = _text(observation.get("date"))
            normalized_row = {
                "schema_version": FRED_OBSERVATION_SCHEMA_HASH,
                "target_table": "macro_fred_observation",
                "provider": "FRED",
                "series_id": str(series_id).upper(),
                "observation_date": date,
                "value": value,
                "realtime_start": observation.get("realtime_start"),
                "realtime_end": observation.get("realtime_end"),
                "frequency": config.get("frequency"),
                "release_lag_days": config.get("release_lag_days"),
                "available_time": observation.get("realtime_start") or date,
                "raw_row": dict(observation),
            }
            row_hash = _stable_hash({"dataset": "macro_fred_observation", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"fred:{str(series_id).upper()}:{date}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="macro",
                    title=f"FRED {str(series_id).upper()} {date}",
                    content_ref=f"fred://series/{str(series_id).upper()}/{date}",
                    metadata={
                        "source_class": "public_macro_reference",
                        "provider": "FRED",
                        "dataset": "macro_fred_observation",
                        "series_id": str(series_id).upper(),
                        "symbol_scope": "global_no_symbol_filter",
                        "event_time": date,
                        "available_time": observation.get("realtime_start") or date,
                        "latest_watermark_key": f"fred:{str(series_id).upper()}",
                        "staleness_seconds": config.get("staleness_seconds"),
                        "fetch_mode": fetch_mode,
                        "source_url": source_url,
                        "normalized_row": normalized_row,
                        "raw_row": dict(observation),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "public_macro_reference",
                        "schema_hash": FRED_OBSERVATION_SCHEMA_HASH,
                        "feature_targets": [config.get("feature_target") or "features/macro_regime_features"],
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def source_health_from_result(self, result: Any, *, series_id: str | None = None) -> SourceHealth:
        metadata = {"symbol_scope": "global_no_symbol_filter"}
        if series_id:
            metadata["latest_watermark_key"] = f"fred:{series_id.upper()}"
            metadata["staleness_seconds"] = self.series_config(series_id).get("staleness_seconds")
        return _health_from_result(
            connector_id=self.connector_id,
            schema_hash=FRED_OBSERVATION_SCHEMA_HASH,
            result=result,
            metadata=metadata,
        )


@dataclass(frozen=True)
class FinraShortSaleAdapter(SourceConnectorProvider):
    """FINRA daily short-volume adapter."""

    connector_id: str = FINRA_SHORT_SALE_CONNECTOR_ID
    max_records: int = 100
    expected_publication_delay_hours: int = 26
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="FINRA",
            license_scope="public_short_sale_reference",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="public_short_sale_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/finra-regsho-public-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=30,
                burst=3,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/finra-regsho-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="FINRA short sale volume",
                homepage_url="https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data",
                docs_url="https://www.finra.org/finra-data/browse-catalog/short-sale-volume-data",
                owner="FINRA",
                tags=("us", "finra", "short_sale", "market_data"),
            ),
            metadata={
                "source_class": "public_short_sale_reference",
                "normalized_datasets": ["us_short_volume_daily"],
                "licensed_followup_datasets": ["us_short_interest"],
                "schema_hash": FINRA_SHORT_VOLUME_SCHEMA_HASH,
                "expected_publication_delay_hours": self.expected_publication_delay_hours,
                "feature_targets": ["features/us_short_volume_pressure"],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "FinraShortSaleAdapter.records_from_short_volume_text",
            "adapter_config": {
                "max_records": self.max_records,
                "expected_publication_delay_hours": self.expected_publication_delay_hours,
            },
            "request": {},
            "max_records": self.max_records,
            "next_watermark": None,
            "dataset": "us_short_volume_daily",
        }

    def short_volume_url(self, trade_date: str) -> str:
        compact = trade_date.replace("-", "")
        return FINRA_SHORT_VOLUME_URL.format(date=compact)

    def default_trade_date(self, *, now: datetime | None = None) -> str:
        current = now or datetime.now(timezone.utc)
        candidate = (current - timedelta(hours=self.expected_publication_delay_hours)).date()
        while candidate.weekday() >= 5:
            candidate = candidate - timedelta(days=1)
        return candidate.isoformat()

    def candidate_trade_dates(self, *, start_date: str | None = None, count: int = 5) -> tuple[str, ...]:
        if count < 1:
            raise SourceEvidenceError("FINRA candidate date count must be positive")
        if start_date:
            candidate = datetime.fromisoformat(str(start_date).replace("Z", "+00:00")[:10]).date()
        else:
            candidate = datetime.fromisoformat(self.default_trade_date()).date()
        dates: list[str] = []
        while len(dates) < count:
            if candidate.weekday() < 5:
                dates.append(candidate.isoformat())
            candidate = candidate - timedelta(days=1)
        return tuple(dates)

    def fetch_short_volume_text(self, trade_date: str) -> str:
        return _request_text(self.short_volume_url(trade_date))

    def records_from_short_volume_text(
        self,
        text: str,
        *,
        trade_date: str | None = None,
        source_url: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        rows = _csv_rows(text, delimiter="|")
        records: list[SourceRecord] = []
        for row in rows[: self.max_records]:
            symbol_value = _symbol(_first(row, "Symbol", "symbol"))
            if not symbol_value:
                continue
            date = _date_from_finra(_first(row, "Date", "date") or trade_date)
            short_volume = _int_or_none(_first(row, "ShortVolume", "short_volume"))
            short_exempt_volume = _int_or_none(_first(row, "ShortExemptVolume", "short_exempt_volume"))
            total_volume = _int_or_none(_first(row, "TotalVolume", "total_volume"))
            market = _text(_first(row, "Market", "market")) or None
            normalized_row = {
                "schema_version": FINRA_SHORT_VOLUME_SCHEMA_HASH,
                "target_table": "us_short_volume_daily",
                "provider": "FINRA",
                "symbol": symbol_value,
                "symbol_canonical": f"{symbol_value}.US",
                "trade_date": date,
                "market": market,
                "short_volume": short_volume,
                "short_exempt_volume": short_exempt_volume,
                "total_volume": total_volume,
                "short_volume_ratio": _ratio(short_volume, total_volume),
                "available_time": date,
                "raw_row": dict(row),
            }
            row_hash = _stable_hash({"dataset": "us_short_volume_daily", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"finra:short-volume:{symbol_value}:{date}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"FINRA short volume {symbol_value} {date}",
                    content_ref=f"finra://short-volume/{date}/{symbol_value}",
                    metadata={
                        "source_class": "public_short_sale_reference",
                        "provider": "FINRA",
                        "dataset": "us_short_volume_daily",
                        "symbol": symbol_value,
                        "symbol_canonical": f"{symbol_value}.US",
                        "trade_date": date,
                        "event_time": date,
                        "available_time": date,
                        "source_url": source_url,
                        "normalized_row": normalized_row,
                        "raw_row": dict(row),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "public_short_sale_reference",
                        "schema_hash": FINRA_SHORT_VOLUME_SCHEMA_HASH,
                        "feature_targets": ["features/us_short_volume_pressure"],
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def missing_file_health(self, trade_date: str, *, now: datetime | None = None) -> SourceHealth:
        current = now or datetime.now(timezone.utc)
        expected = datetime.fromisoformat(f"{trade_date}T00:00:00+00:00") + timedelta(
            hours=self.expected_publication_delay_hours
        )
        pending = current < expected
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status=SourceHealthStatus.OK.value if pending else SourceHealthStatus.DEGRADED.value,
            latest_watermark=None,
            schema_hash=FINRA_SHORT_VOLUME_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "trade_date": trade_date,
                "missing_file_reason": "expected_publication_pending" if pending else "missing_after_publication_window",
                "expected_publication_after": expected.isoformat().replace("+00:00", "Z"),
            },
        )

    def source_health_from_result(self, result: Any) -> SourceHealth:
        return _health_from_result(
            connector_id=self.connector_id,
            schema_hash=FINRA_SHORT_VOLUME_SCHEMA_HASH,
            result=result,
            metadata={"expected_publication_delay_hours": self.expected_publication_delay_hours},
        )


@dataclass(frozen=True)
class StooqDailyOhlcvAdapter(SourceConnectorProvider):
    """Stooq daily OHLCV adapter.

    The connector remains disabled by default until a runtime smoke verifies a
    working Stooq endpoint from the target environment.
    """

    connector_id: str = STOOQ_DAILY_OHLCV_CONNECTOR_ID
    max_records: int = 100
    connector_status: ConnectorStatus | str = ConnectorStatus.DISABLED
    disabled_reason: str = "stooq_endpoint_unverified_2026-06-11"
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="Stooq",
            license_scope="public_market_reference",
            auth_type=AuthType.NONE,
            supported_modes=(ConnectorMode.BATCH,),
            status=self.connector_status,
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="public_market_reference",
                allowed_use=("research_data", "backtest_data", "feature_generation", "monitoring", "audit_evidence"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/stooq-public-market-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=15,
                burst=2,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/stooq-public-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="Stooq daily OHLCV",
                homepage_url="https://stooq.com/",
                docs_url="https://stooq.com/db/",
                owner="Stooq",
                tags=("us", "stooq", "ohlcv", "market_data", "public_fallback"),
            ),
            metadata={
                "source_class": "public_market_reference",
                "normalized_datasets": ["us_price_daily"],
                "schema_hash": STOOQ_DAILY_OHLCV_SCHEMA_HASH,
                "source_priority": "disabled_public_fallback_until_runtime_smoke",
                "disabled_reason": self.disabled_reason,
                "corporate_action_adjustment_policy": "stooq_adjusted_close_not_assumed; adjusted fields require provider verification",
                "feature_targets": ["features/us_returns"],
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "provider_owned_adapter",
            "adapter": "StooqDailyOhlcvAdapter.records_from_csv",
            "adapter_config": {
                "max_records": self.max_records,
                "connector_status": str(self.connector_status.value if hasattr(self.connector_status, "value") else self.connector_status),
                "disabled_reason": self.disabled_reason,
            },
            "request": {},
            "max_records": self.max_records,
            "next_watermark": None,
            "dataset": "us_price_daily",
            "allow_empty": True,
            "empty_reason": self.disabled_reason,
        }

    def daily_url(self, symbol: str, *, start_date: str | None = None, end_date: str | None = None) -> str:
        stooq_symbol = f"{symbol.lower()}.us" if "." not in symbol else symbol.lower()
        params = {"s": stooq_symbol, "i": "d"}
        if start_date:
            params["d1"] = start_date.replace("-", "")
        if end_date:
            params["d2"] = end_date.replace("-", "")
        return f"{STOOQ_DAILY_URL}?{urllib.parse.urlencode(params)}"

    def fetch_daily_csv(self, symbol: str, *, start_date: str | None = None, end_date: str | None = None) -> str:
        return _request_text(self.daily_url(symbol, start_date=start_date, end_date=end_date))

    def records_from_csv(
        self,
        symbol: str,
        csv_text: str,
        *,
        source_url: str | None = None,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        symbol_value = _symbol(symbol)
        rows = _csv_rows(csv_text)
        records: list[SourceRecord] = []
        for row in rows[: self.max_records]:
            date = _text(_first(row, "Date", "date"))
            if not date:
                continue
            normalized_row = {
                "schema_version": STOOQ_DAILY_OHLCV_SCHEMA_HASH,
                "target_table": "us_price_daily",
                "provider": "Stooq",
                "symbol": symbol_value,
                "symbol_canonical": f"{symbol_value}.US",
                "trade_date": date,
                "open": _float_or_none(_first(row, "Open", "open")),
                "high": _float_or_none(_first(row, "High", "high")),
                "low": _float_or_none(_first(row, "Low", "low")),
                "close": _float_or_none(_first(row, "Close", "close")),
                "volume": _int_or_none(_first(row, "Volume", "volume")),
                "currency": "USD",
                "adjustment_policy": "provider_close_unadjusted_until_corporate_action_policy_verified",
                "available_time": date,
                "raw_row": dict(row),
            }
            row_hash = _stable_hash({"dataset": "us_price_daily", "row": normalized_row})
            records.append(
                SourceRecord(
                    source_id=f"stooq:daily:{symbol_value}:{date}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"Stooq daily OHLCV {symbol_value} {date}",
                    content_ref=f"stooq://daily/{symbol_value}/{date}",
                    metadata={
                        "source_class": "public_market_reference",
                        "provider": "Stooq",
                        "dataset": "us_price_daily",
                        "symbol": symbol_value,
                        "symbol_canonical": f"{symbol_value}.US",
                        "trade_date": date,
                        "event_time": date,
                        "available_time": date,
                        "source_url": source_url,
                        "normalized_row": normalized_row,
                        "raw_row": dict(row),
                        "body": json.dumps(normalized_row, ensure_ascii=False, sort_keys=True),
                        "access_scope": ["public", "research"],
                        "license_scope": "public_market_reference",
                        "schema_hash": STOOQ_DAILY_OHLCV_SCHEMA_HASH,
                        "feature_targets": ["features/us_returns"],
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

    def disabled_source_health(self) -> SourceHealth:
        return SourceHealth(
            source_id=self.connector_id,
            source_kind="data_source",
            status=SourceHealthStatus.DISABLED.value,
            latest_watermark=None,
            schema_hash=STOOQ_DAILY_OHLCV_SCHEMA_HASH,
            metadata={
                "connector_id": self.connector_id,
                "disabled_reason": self.disabled_reason,
                "provider": "Stooq",
                "dataset": "us_price_daily",
            },
        )

    def source_health_from_result(self, result: Any) -> SourceHealth:
        return _health_from_result(
            connector_id=self.connector_id,
            schema_hash=STOOQ_DAILY_OHLCV_SCHEMA_HASH,
            result=result,
            metadata={"disabled_reason": self.disabled_reason},
        )

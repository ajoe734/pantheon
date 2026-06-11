"""Taiwan market source-ingest adapters for MOPS and TEJ."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from services.research.adapters.taiwan_market_client import MopsRouteSpec, TaiwanMarketClient, TejTableSpec

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    LicensePolicy,
    RateLimitPolicy,
    SourceConnector,
    SourceConnectorProvider,
    SourceMetadata,
    SourceRecord,
)


def _stable_hash(payload: Mapping[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()[:16]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text if text else default


def _first_text(row: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return _text(value)
    return ""


def _number(value: Any) -> int | float | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if not text or text in {"-", "--", "N/A"}:
        return None
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _clean_values(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in row.items() if value not in (None, "")}


def _route_normalized_target(route: MopsRouteSpec) -> str:
    tags = {str(tag) for tag in route.tags}
    if "monthly_revenue" in tags:
        return "tw_monthly_revenue"
    if route.category == "company_master":
        return "tw_company_master"
    if route.category == "corporate_actions":
        return "tw_corporate_action"
    if route.category == "financials" or tags.intersection(
        {
            "balance_sheet",
            "cash_flow",
            "financial_analysis",
            "financial_report",
            "financial_report_book",
            "income_statement",
            "restatement",
        }
    ):
        return "tw_financial_statement"
    if "material_information" in tags or route.category == "disclosure":
        return "tw_material_event"
    return "tw_disclosure_event"


def _route_schedule_profile(route: MopsRouteSpec) -> dict[str, Any]:
    target = _route_normalized_target(route)
    if target == "tw_material_event":
        return {
            "cadence": "event_poll_10m_to_30m",
            "universe_tiers": ["core_universe", "candidate_universe", "archive_universe"],
            "archive_behavior": "material_events_only",
        }
    if target in {"tw_monthly_revenue", "tw_financial_statement"}:
        return {
            "cadence": "daily_scan_monthly_quarterly_event_facts",
            "universe_tiers": ["core_universe"],
            "candidate_behavior": "defer_until_promoted_to_core",
            "archive_behavior": "skip_except_material_events",
        }
    if target == "tw_company_master":
        return {
            "cadence": "daily_reference_scan",
            "universe_tiers": ["core_universe", "candidate_universe", "archive_universe"],
            "archive_behavior": "reference_identity_only",
        }
    if target == "tw_corporate_action":
        return {
            "cadence": "daily_scan_event_facts",
            "universe_tiers": ["core_universe", "candidate_universe"],
            "archive_behavior": "skip_except_material_events",
        }
    return {
        "cadence": "manual_or_gap_inventory",
        "universe_tiers": ["core_universe"],
        "archive_behavior": "skip",
    }


def _fiscal_fields(row: Mapping[str, Any]) -> dict[str, str | None]:
    period = _first_text(row, "資料年月", "年月", "fiscal_period", "period")
    fiscal_year = _first_text(row, "年度", "會計年度", "年", "year", "fiscal_year")
    fiscal_quarter = _first_text(row, "季別", "季度", "季", "season", "quarter", "fiscal_quarter")
    fiscal_month = _first_text(row, "月份", "月", "month", "fiscal_month")
    if period and (not fiscal_year or not fiscal_month):
        match = re.match(r"^\s*(\d{2,4})[/-](\d{1,2})\s*$", period)
        if match:
            fiscal_year = fiscal_year or match.group(1)
            fiscal_month = fiscal_month or match.group(2).zfill(2)
    return {
        "fiscal_year": fiscal_year or None,
        "fiscal_quarter": fiscal_quarter or None,
        "fiscal_month": fiscal_month or None,
        "fiscal_period": period or None,
    }


def _statement_type(route: MopsRouteSpec) -> str:
    tags = set(route.tags)
    if "balance_sheet" in tags:
        return "balance_sheet"
    if "income_statement" in tags:
        return "income_statement"
    if "cash_flow" in tags:
        return "cash_flow"
    if "financial_analysis" in tags:
        return "financial_analysis"
    if "restatement" in tags or "correction" in tags:
        return "restatement_or_correction"
    return "financial_report"


def _corporate_action_type(route: MopsRouteSpec) -> str:
    tags = set(route.tags)
    if "ex_dividend" in tags:
        return "ex_dividend"
    if "dividend" in tags:
        return "dividend"
    return route.route_id


def _normalized_record(route: MopsRouteSpec, row: Mapping[str, Any], payload: Mapping[str, Any]) -> dict[str, Any]:
    target = _route_normalized_target(route)
    company_id = _first_text(row, "公司代號", "companyId", "stockId", "stock_id", "symbol")
    company_name = _first_text(row, "公司名稱", "公司簡稱", "companyName", "companyAbbreviation", "name")
    announcement_date = _first_text(
        row,
        "營收發布日期",
        "公告日期",
        "申報日期",
        "出表日期",
        "發言日期",
        "日期",
        "announce_date",
        "date",
    )
    event_time = " ".join(
        part
        for part in (
            _first_text(row, "發言日期", "日期", "announce_date", "date"),
            _first_text(row, "發言時間", "time"),
        )
        if part
    )
    available_time = _text(payload.get("datetime")) or announcement_date or event_time
    subject = _first_text(row, "主旨", "subject", "title") or route.title_zh
    normalized: dict[str, Any] = {
        "schema_version": "mops_normalized_record.v1",
        "target_table": target,
        "raw_route_id": route.route_id,
        "route_title_zh": route.title_zh,
        "route_category": route.category,
        "company_id": company_id or None,
        "company_name": company_name or None,
        "announcement_date": announcement_date or None,
        "available_time": available_time or None,
        "event_time": event_time or announcement_date or None,
        "subject": subject or None,
        "raw_row": dict(row),
        "values": _clean_values(row),
        **_fiscal_fields(row),
    }
    if target == "tw_monthly_revenue":
        normalized["monthly_revenue"] = {
            "current_month_revenue": _number(
                _first_text(row, "當月營收", "本月營收", "current_month_revenue", "revenue")
            ),
            "previous_month_revenue": _number(_first_text(row, "上月營收", "previous_month_revenue")),
            "previous_year_month_revenue": _number(
                _first_text(row, "去年當月營收", "去年同月營收", "previous_year_month_revenue")
            ),
            "month_over_month_pct": _number(_first_text(row, "上月比較增減(%)", "mom_pct")),
            "year_over_year_pct": _number(_first_text(row, "去年同月增減(%)", "yoy_pct")),
            "current_year_accumulated_revenue": _number(
                _first_text(row, "當月累計營收", "本年累計營收", "current_year_accumulated_revenue")
            ),
            "previous_year_accumulated_revenue": _number(
                _first_text(row, "去年累計營收", "previous_year_accumulated_revenue")
            ),
            "accumulated_year_over_year_pct": _number(_first_text(row, "前期比較增減(%)", "accumulated_yoy_pct")),
            "note": _first_text(row, "備註", "note") or None,
        }
    elif target == "tw_financial_statement":
        normalized["financial_statement"] = {
            "statement_type": _statement_type(route),
            "restatement_or_correction_route": bool({"restatement", "correction"}.intersection(route.tags)),
            "line_items": _clean_values(row),
        }
    elif target == "tw_company_master":
        normalized["company_master"] = {
            "market": _first_text(row, "市場別", "上市櫃別", "market") or None,
            "industry": _first_text(row, "產業別", "industry") or None,
            "chairperson": _first_text(row, "董事長", "chairperson") or None,
            "spokesperson": _first_text(row, "發言人", "spokesperson") or None,
        }
    elif target == "tw_corporate_action":
        normalized["corporate_action"] = {
            "action_type": _corporate_action_type(route),
            "ex_date": _first_text(row, "除權息日期", "除息日", "除權日", "ex_date") or None,
            "shareholder_meeting_date": _first_text(row, "股東會日期", "shareholder_meeting_date") or None,
            "cash_dividend": _number(_first_text(row, "現金股利", "cash_dividend")),
            "stock_dividend": _number(_first_text(row, "股票股利", "stock_dividend")),
        }
    else:
        normalized["material_event"] = {
            "event_subject": subject or None,
            "spokesperson": _first_text(row, "發言人", "spokesperson") or None,
            "body": _first_text(row, "說明", "內容", "body") or subject or None,
        }
    return normalized


def _route_update_strategy(routes: Sequence[MopsRouteSpec]) -> tuple[dict[str, Any], ...]:
    return tuple(
        {
            "route_id": route.route_id,
            "title_zh": route.title_zh,
            "normalized_target": _route_normalized_target(route),
            "required_params": list(route.required_params),
            "default_params": dict(route.default_params),
            "allow_fetch": route.allow_fetch,
            **_route_schedule_profile(route),
        }
        for route in routes
    )


def _restatement_gap_report(routes: Sequence[MopsRouteSpec]) -> dict[str, Any]:
    restatement_routes = [
        route
        for route in routes
        if {"restatement", "correction"}.intersection({str(tag) for tag in route.tags})
        or "更補正" in route.title_zh
    ]
    return {
        "schema_version": "mops_restatement_correction_gap_report.v1",
        "represented_routes": [
            {
                "route_id": route.route_id,
                "title_zh": route.title_zh,
                "normalized_target": _route_normalized_target(route),
                "tags": list(route.tags),
                "status": "inventoried_for_daily_gap_scan",
            }
            for route in restatement_routes
        ],
        "gap_notes": [
            "MOPS restatement/correction routes are represented as financial-statement gap scan routes.",
            "Route-specific fetch parameters remain bounded by each route's required_params.",
        ],
    }


@dataclass(frozen=True)
class MopsSourceIngestAdapter(SourceConnectorProvider):
    connector_id: str = "tw-mops-official-disclosures"
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="filing",
            provider="MOPS",
            license_scope="official_reference",
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(
                license_scope="official_reference",
                allowed_use=("research", "search_index", "audit_evidence"),
                attribution_required=True,
                policy_ref="source-ingest://license/mops-official-reference",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=30,
                burst=3,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/mops-official-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="MOPS official disclosures",
                homepage_url=TaiwanMarketClient.MOPS_HOME_URL,
                owner="Taiwan Stock Exchange",
                tags=("taiwan", "mops", "official_reference", "filing"),
            ),
            metadata={
                "source_class": "official_reference",
                "official_reference_truth": True,
                "recommended_route_count": len(TaiwanMarketClient.MOPS_RECOMMENDED_ROUTES),
                "normalized_targets": sorted(
                    {_route_normalized_target(route) for route in TaiwanMarketClient.MOPS_RECOMMENDED_ROUTES}
                ),
                "schedule_strategy": {
                    "material_events": "event_poll_10m_to_30m for core, candidate, and archive symbols",
                    "monthly_revenue": "daily_scan for core symbols",
                    "financial_statements": "daily_scan for core symbols and restatement/correction gaps",
                    "company_master": "daily_reference_scan where routes support company identity rows",
                    "corporate_actions": "daily_scan_event_facts where dividend/ex-date routes support rows",
                },
                "backup_source": {
                    "provider": "TEJ API",
                    "role": "vendor_research_backfill_only",
                    "does_not_replace_official_reference_truth": True,
                },
                "restatement_correction_gap_report": _restatement_gap_report(
                    TaiwanMarketClient.MOPS_RECOMMENDED_ROUTES
                ),
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        routes = TaiwanMarketClient.MOPS_RECOMMENDED_ROUTES
        return {
            "mode": "static_records",
            "records": [],
            "next_watermark": None,
            "provider_owned_fetcher": "MopsSourceIngestAdapter.records_from_payload",
            "recommended_routes": [route.to_dict() for route in routes],
            "normalized_targets": sorted({_route_normalized_target(route) for route in routes}),
            "route_update_strategy": list(_route_update_strategy(routes)),
            "restatement_correction_gap_report": _restatement_gap_report(routes),
        }

    def records_from_payload(
        self,
        route: MopsRouteSpec,
        payload: Mapping[str, Any],
        *,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        client = TaiwanMarketClient()
        rows = client.mops_result_rows(payload)
        records: list[SourceRecord] = []
        for row in rows[: self.max_records]:
            normalized = _normalized_record(route, row, payload)
            symbol = _text(normalized.get("company_id"))
            subject = _text(normalized.get("subject"), route.title_zh)
            event_time = _text(normalized.get("event_time"))
            announcement_date = _text(normalized.get("announcement_date"))
            available_time = _text(normalized.get("available_time"))
            fiscal_period = _text(normalized.get("fiscal_period"))
            company_name = _text(normalized.get("company_name"))
            key_payload = {
                "route": route.route_id,
                "symbol": symbol,
                "event_time": event_time,
                "announcement_date": announcement_date,
                "fiscal_period": fiscal_period,
                "subject": subject,
                "row": row,
            }
            row_hash = _stable_hash(key_payload)
            target = str(normalized["target_table"])
            content_ref = (
                f"mops://{route.route_id}/{symbol or 'market'}/"
                f"{announcement_date or fiscal_period or event_time or row_hash}/{row_hash}"
            )
            records.append(
                SourceRecord(
                    source_id=f"mops:{route.route_id}:{symbol or 'market'}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="filing",
                    title=f"{route.title_zh} {symbol} {company_name} {subject}".strip(),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "official_reference",
                        "provider": "MOPS",
                        "route_id": route.route_id,
                        "route_title_zh": route.title_zh,
                        "category": route.category,
                        "symbol": symbol or None,
                        "company_name": company_name or None,
                        "event_time": event_time or None,
                        "announcement_date": announcement_date or None,
                        "available_time": available_time or None,
                        "fiscal_year": normalized.get("fiscal_year"),
                        "fiscal_quarter": normalized.get("fiscal_quarter"),
                        "fiscal_month": normalized.get("fiscal_month"),
                        "fiscal_period": normalized.get("fiscal_period"),
                        "subject": subject,
                        "normalized_target": target,
                        "normalized_table": target,
                        "normalized_record": normalized,
                        "schema_hash": f"{target}.v1",
                        "schedule_profile": _route_schedule_profile(route),
                        "restatement_or_correction_route": bool(
                            {"restatement", "correction"}.intersection(route.tags)
                        ),
                        "raw_row": dict(row),
                        "body": subject,
                        "access_scope": ["public", "research"],
                        "license_scope": "official_reference",
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)


@dataclass(frozen=True)
class TejSourceIngestAdapter(SourceConnectorProvider):
    connector_id: str = "tw-tej-research-datasets"
    secret_ref_id: str = "env://TEJ_API_KEY"
    max_records: int = 100
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type="market",
            provider="TEJ API",
            license_scope="vendor_research",
            auth_type=AuthType.API_KEY,
            secret_ref_id=self.secret_ref_id,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.API_KEY,
                secret_ref={"secret_ref_id": self.secret_ref_id},
                auth_scope=("tej:read", "source_ingest:read"),
            ),
            license_policy=LicensePolicy(
                license_scope="vendor_research",
                allowed_use=("research", "backtest", "search_index"),
                attribution_required=True,
                redistribution_allowed=False,
                policy_ref="source-ingest://license/tej-vendor-research",
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=30,
                burst=3,
                retry_after_seconds=60,
                concurrency=1,
                policy_ref="source-ingest://policy/tej-api-low-rate",
            ),
            source_metadata=self.source_metadata
            or SourceMetadata(
                display_name="TEJ API Taiwan research datasets",
                homepage_url="https://api.tej.com.tw",
                docs_url="https://api.tej.com.tw/document_rest.html",
                owner="TEJ",
                tags=("taiwan", "tej", "research_grade", "market"),
            ),
            metadata={
                "source_class": "research_grade",
                "does_not_replace_official_disclosure_truth": True,
                "trial_table_count": 25,
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "static_records",
            "records": [],
            "next_watermark": None,
            "provider_owned_fetcher": "TejSourceIngestAdapter.records_from_rows",
            "secret_ref_id": self.secret_ref_id,
        }

    def records_from_rows(
        self,
        table: TejTableSpec,
        rows: Sequence[Mapping[str, Any]],
        *,
        trace_id: str = "",
    ) -> tuple[SourceRecord, ...]:
        records: list[SourceRecord] = []
        for row in list(rows)[: self.max_records]:
            symbol = _text(row.get("coid") or row.get("symbol") or row.get("股票代號"))
            as_of_date = _text(row.get("mdate") or row.get("date") or row.get("資料日"))
            row_hash = _stable_hash({"dataset": table.dataset_code, "row": dict(row)})
            content_ref = f"tej://{table.dataset_code}/{symbol or 'market'}/{as_of_date or row_hash}/{row_hash}"
            records.append(
                SourceRecord(
                    source_id=f"tej:{table.dataset_code}:{symbol or 'market'}:{row_hash}",
                    connector_id=self.connector_id,
                    source_type="market",
                    title=f"TEJ {table.title_zh} {symbol} {as_of_date}".strip(),
                    content_ref=content_ref,
                    metadata={
                        "source_class": "research_grade",
                        "provider": "TEJ API",
                        "dataset_code": table.dataset_code,
                        "table_code": table.table_code,
                        "table_title_zh": table.title_zh,
                        "source_category": table.source_category,
                        "symbol": symbol or None,
                        "as_of_date": as_of_date or None,
                        "event_time": as_of_date or None,
                        "available_time": as_of_date or None,
                        "raw_row": dict(row),
                        "body": json.dumps(dict(row), ensure_ascii=False, sort_keys=True),
                        "access_scope": ["research"],
                        "license_scope": "vendor_research",
                    },
                    trace_id=trace_id,
                )
            )
        return tuple(records)

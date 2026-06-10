"""Taiwan market source-ingest adapters for MOPS and TEJ."""

from __future__ import annotations

import hashlib
import json
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
                **dict(self.connector_metadata),
            },
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "static_records",
            "records": [],
            "next_watermark": None,
            "provider_owned_fetcher": "MopsSourceIngestAdapter.records_from_payload",
            "recommended_routes": [route.to_dict() for route in TaiwanMarketClient.MOPS_RECOMMENDED_ROUTES],
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
            symbol = _text(
                row.get("公司代號")
                or row.get("companyId")
                or row.get("stockId")
                or row.get("stock_id")
                or row.get("symbol")
            )
            subject = _text(row.get("主旨") or row.get("subject") or row.get("title"), route.title_zh)
            event_date = _text(row.get("發言日期") or row.get("日期") or row.get("date") or payload.get("datetime"))
            event_time = _text(row.get("發言時間") or row.get("time"))
            company_name = _text(row.get("公司名稱") or row.get("公司簡稱") or row.get("companyName") or row.get("companyAbbreviation"))
            key_payload = {
                "route": route.route_id,
                "symbol": symbol,
                "event_date": event_date,
                "event_time": event_time,
                "subject": subject,
                "row": row,
            }
            row_hash = _stable_hash(key_payload)
            content_ref = f"mops://{route.route_id}/{symbol or 'market'}/{event_date or row_hash}/{row_hash}"
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
                        "event_time": event_date,
                        "available_time": payload.get("datetime") or event_date,
                        "subject": subject,
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

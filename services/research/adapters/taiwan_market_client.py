"""Governed Taiwan market adapters for TWSE, TPEx, MOPS, and TEJ.

The module exposes a small client plus normalization helpers so Pantheon
can separate official-reference truth from vendor-grade research data.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


@dataclass(frozen=True)
class TaiwanSourceSpec:
    key: str
    base_url: str
    source_class: str
    governance_context: str


@dataclass
class TaiwanGovernanceMetadata:
    source_key: str
    source_class: str
    api_endpoint: str
    retrieved_at: str
    governance_context: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaiwanListingRecord:
    symbol: str
    company_name: str
    market: str
    venue: str
    isin: Optional[str]
    industry: Optional[str]
    listing_date: Optional[str]
    source_metadata: dict[str, Any]
    governance_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaiwanDisclosureRecord:
    symbol: str
    company_name: Optional[str]
    filing_code: str
    disclosure_date: str
    fiscal_period: Optional[str]
    source_metadata: dict[str, Any]
    governance_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaiwanResearchDatasetRecord:
    dataset_code: str
    symbol: str
    as_of_date: str
    values: dict[str, Any]
    source_metadata: dict[str, Any]
    governance_metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaiwanMarketClient:
    """Thin client for governed Taiwan structured sources."""

    SOURCE_SPECS = {
        "twse": TaiwanSourceSpec(
            key="twse",
            base_url="https://openapi.twse.com.tw",
            source_class="official_reference",
            governance_context="Official listed-market reference source",
        ),
        "tpex": TaiwanSourceSpec(
            key="tpex",
            base_url="https://www.tpex.org.tw/openapi",
            source_class="official_reference",
            governance_context="Official OTC / TPEx reference source",
        ),
        "mops": TaiwanSourceSpec(
            key="mops",
            base_url="https://mops.twse.com.tw/mops/api",
            source_class="official_reference",
            governance_context="Official Taiwan disclosure source",
        ),
        "tej": TaiwanSourceSpec(
            key="tej",
            base_url="https://api.tej.com.tw",
            source_class="research_grade",
            governance_context="Governed vendor research/reference source; does not replace official disclosure truth",
        ),
    }

    def __init__(self, rate_limit_delay: float = 0.2):
        self.rate_limit_delay = rate_limit_delay
        self._last_request_time = 0.0

    def get_json(
        self,
        source_key: str,
        resource_path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> Any:
        spec = self._spec(source_key)
        url = f"{spec.base_url.rstrip('/')}/{resource_path.lstrip('/')}"
        if params:
            url = f"{url}?{urlencode(params)}"

        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)

        request = Request(url, headers=headers or {})
        self._last_request_time = time.time()
        with urlopen(request, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def build_metadata(self, source_key: str, api_endpoint: str) -> TaiwanGovernanceMetadata:
        spec = self._spec(source_key)
        return TaiwanGovernanceMetadata(
            source_key=spec.key,
            source_class=spec.source_class,
            api_endpoint=api_endpoint,
            retrieved_at=_utc_now_iso(),
            governance_context=spec.governance_context,
        )

    def normalize_twse_listing(self, record: dict[str, Any], *, api_endpoint: str = "twse://listing") -> TaiwanListingRecord:
        metadata = self.build_metadata("twse", api_endpoint)
        symbol = str(_first_present(record, "Code", "股票代號", "SECCODE", "symbol") or "")
        company_name = str(_first_present(record, "Name", "公司名稱", "CompanyName", "name") or "")
        if not symbol or not company_name:
            raise ValueError("TWSE listing requires symbol and company_name")
        return TaiwanListingRecord(
            symbol=symbol,
            company_name=company_name,
            market="TW",
            venue="TWSE",
            isin=_first_present(record, "ISIN", "國際證券辨識號碼", "isin"),
            industry=_first_present(record, "Industry", "產業別", "industry"),
            listing_date=_first_present(record, "ListingDate", "上市日", "list_date"),
            source_metadata={"raw_record": dict(record)},
            governance_metadata=metadata.to_dict(),
        )

    def normalize_tpex_listing(self, record: dict[str, Any], *, api_endpoint: str = "tpex://listing") -> TaiwanListingRecord:
        metadata = self.build_metadata("tpex", api_endpoint)
        symbol = str(_first_present(record, "SecuritiesCompanyCode", "股票代號", "Code", "symbol") or "")
        company_name = str(_first_present(record, "CompanyName", "公司名稱", "Name", "name") or "")
        if not symbol or not company_name:
            raise ValueError("TPEx listing requires symbol and company_name")
        return TaiwanListingRecord(
            symbol=symbol,
            company_name=company_name,
            market="TW",
            venue="TPEx",
            isin=_first_present(record, "ISIN", "國際證券辨識號碼", "isin"),
            industry=_first_present(record, "Industry", "產業別", "industry"),
            listing_date=_first_present(record, "ListingDate", "上櫃日", "list_date"),
            source_metadata={"raw_record": dict(record)},
            governance_metadata=metadata.to_dict(),
        )

    def normalize_mops_disclosure(
        self,
        record: dict[str, Any],
        *,
        api_endpoint: str = "mops://disclosure",
    ) -> TaiwanDisclosureRecord:
        metadata = self.build_metadata("mops", api_endpoint)
        symbol = str(_first_present(record, "company_id", "公司代號", "symbol") or "")
        filing_code = str(_first_present(record, "filing_code", "announce_type", "公告類型", "category") or "")
        disclosure_date = str(_first_present(record, "announce_date", "發言日期", "date") or "")
        if not symbol or not filing_code or not disclosure_date:
            raise ValueError("MOPS disclosure requires symbol, filing_code, and disclosure_date")
        return TaiwanDisclosureRecord(
            symbol=symbol,
            company_name=_first_present(record, "company_name", "公司名稱", "name"),
            filing_code=filing_code,
            disclosure_date=disclosure_date,
            fiscal_period=_first_present(record, "fiscal_period", "會計年度", "period"),
            source_metadata={"raw_record": dict(record)},
            governance_metadata=metadata.to_dict(),
        )

    def normalize_tej_dataset(
        self,
        record: dict[str, Any],
        *,
        dataset_code: str,
        api_endpoint: str = "tej://dataset",
    ) -> TaiwanResearchDatasetRecord:
        metadata = self.build_metadata("tej", api_endpoint)
        symbol = str(_first_present(record, "coid", "symbol", "股票代號") or "")
        as_of_date = str(_first_present(record, "mdate", "date", "資料日") or "")
        if not symbol or not as_of_date:
            raise ValueError("TEJ dataset requires symbol and as_of_date")
        values = {key: value for key, value in record.items() if key not in {"coid", "symbol", "股票代號", "mdate", "date", "資料日"}}
        return TaiwanResearchDatasetRecord(
            dataset_code=dataset_code,
            symbol=symbol,
            as_of_date=as_of_date,
            values=values,
            source_metadata={"raw_record": dict(record)},
            governance_metadata=metadata.to_dict(),
        )

    def normalize_tpex_or_twse_listing(self, venue: str, record: dict[str, Any], *, api_endpoint: str) -> TaiwanListingRecord:
        normalized_venue = venue.strip().upper()
        if normalized_venue == "TWSE":
            return self.normalize_twse_listing(record, api_endpoint=api_endpoint)
        if normalized_venue in {"TPEX", "TWO", "OTC"}:
            return self.normalize_tpex_listing(record, api_endpoint=api_endpoint)
        raise ValueError(f"unsupported Taiwan listing venue: {venue}")

    def _spec(self, source_key: str) -> TaiwanSourceSpec:
        normalized = source_key.strip().lower()
        if normalized not in self.SOURCE_SPECS:
            raise ValueError(f"unsupported Taiwan source: {source_key}")
        return self.SOURCE_SPECS[normalized]

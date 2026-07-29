"""Governed Taiwan market adapters for TWSE, TPEx, MOPS, FinMind, and TEJ.

The module exposes a small client plus normalization helpers so Pantheon
can separate official-reference truth from vendor-grade research data.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import urlencode
from urllib.request import Request

from services.external_egress import open_external_url


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _first_present(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value not in (None, ""):
            return value
    return None


def _coerce_mops_value(value: Any) -> Any:
    if isinstance(value, Mapping) and set(value).issuperset({"value", "isHidden"}):
        return value.get("value")
    return value


def _mops_title_name(title: Any) -> str:
    if isinstance(title, Mapping):
        return str(title.get("main") or title.get("name") or "").strip()
    return str(title or "").strip()


@dataclass(frozen=True)
class TaiwanSourceSpec:
    key: str
    base_url: str
    source_class: str
    governance_context: str


@dataclass(frozen=True)
class MopsRouteSpec:
    route_id: str
    title_zh: str
    category: str
    source_type: str
    endpoint_path: str
    method: str = "POST"
    default_params: Mapping[str, Any] = field(default_factory=dict)
    required_params: Sequence[str] = field(default_factory=tuple)
    export_csv_path: str | None = None
    allow_fetch: bool = True
    tags: Sequence[str] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route_id": self.route_id,
            "title_zh": self.title_zh,
            "category": self.category,
            "source_type": self.source_type,
            "endpoint_path": self.endpoint_path,
            "method": self.method,
            "default_params": dict(self.default_params),
            "required_params": list(self.required_params),
            "export_csv_path": self.export_csv_path,
            "allow_fetch": self.allow_fetch,
            "tags": list(self.tags),
        }


@dataclass(frozen=True)
class TejTableSpec:
    db_code: str
    table_code: str
    title_zh: str
    group_name: str
    source_category: str
    data_range: str | None = None
    description: str | None = None
    license_scope: str = "vendor_research"
    entitlement_tag: str | None = None
    point_in_time_available: bool = True

    @property
    def dataset_code(self) -> str:
        return f"{self.db_code}/{self.table_code}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_code": self.dataset_code,
            "db_code": self.db_code,
            "table_code": self.table_code,
            "title_zh": self.title_zh,
            "group_name": self.group_name,
            "source_category": self.source_category,
            "data_range": self.data_range,
            "description": self.description,
            "license_scope": self.license_scope,
            "entitlement_tag": self.entitlement_tag,
            "point_in_time_available": self.point_in_time_available,
        }


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

    MOPS_HOME_URL = "https://mops.twse.com.tw/mops/"
    MOPS_ASSET_INDEX_URL = "https://mops.twse.com.tw/mops/assets/index.js"
    TEJ_TRIAL_CATALOG_PATH = "web/api/TRAIL"

    MOPS_RECOMMENDED_ROUTES: tuple[MopsRouteSpec, ...] = (
        MopsRouteSpec(
            route_id="t05sr01_1",
            title_zh="即時重大訊息",
            category="disclosure",
            source_type="filing",
            endpoint_path="home_page/t05sr01_1",
            default_params={"count": 8, "marketKind": ""},
            tags=("mops", "material_information", "homepage"),
        ),
        MopsRouteSpec(
            route_id="t05st02",
            title_zh="當日重大訊息",
            category="disclosure",
            source_type="filing",
            endpoint_path="t05st02",
            required_params=("year", "month", "day"),
            tags=("mops", "material_information"),
        ),
        MopsRouteSpec(
            route_id="t05st01",
            title_zh="歷史重大訊息",
            category="disclosure",
            source_type="filing",
            endpoint_path="t05st01",
            required_params=("companyId", "year", "month", "firstDay", "lastDay"),
            tags=("mops", "material_information"),
        ),
        MopsRouteSpec(
            route_id="t146sb10",
            title_zh="最新公告",
            category="disclosure",
            source_type="filing",
            endpoint_path="home_page/t146sb10",
            default_params={"count": 8, "marketKind": ""},
            tags=("mops", "announcement", "homepage"),
        ),
        MopsRouteSpec(
            route_id="t51sb10_q1",
            title_zh="重大訊息主旨全文檢索",
            category="disclosure",
            source_type="filing",
            endpoint_path="t51sb10_q1",
            tags=("mops", "material_information", "search"),
        ),
        MopsRouteSpec(
            route_id="t05st03",
            title_zh="公司基本資料",
            category="company_master",
            source_type="market",
            endpoint_path="t05st03",
            required_params=("companyId",),
            export_csv_path="t05st03/export/csv",
            tags=("mops", "company_master"),
        ),
        MopsRouteSpec(
            route_id="t146sb05",
            title_zh="公司總覽",
            category="company_master",
            source_type="market",
            endpoint_path="t146sb05",
            tags=("mops", "company_master"),
        ),
        MopsRouteSpec(
            route_id="t51sb10",
            title_zh="最新財務/營收報表",
            category="operations",
            source_type="filing",
            endpoint_path="home_page/t51sb10",
            default_params={"count": 8, "marketKind": ""},
            tags=("mops", "revenue", "financials", "homepage"),
        ),
        MopsRouteSpec(
            route_id="t05st10_ifrs",
            title_zh="月營業收入資訊",
            category="operations",
            source_type="filing",
            endpoint_path="t05st10_ifrs",
            required_params=("companyId", "year", "month"),
            tags=("mops", "monthly_revenue"),
        ),
        MopsRouteSpec(
            route_id="t05st08",
            title_zh="各項產品業務營收統計表",
            category="operations",
            source_type="filing",
            endpoint_path="t05st08",
            tags=("mops", "revenue_breakdown"),
        ),
        MopsRouteSpec(
            route_id="t138sb01",
            title_zh="自結損益公告",
            category="operations",
            source_type="filing",
            endpoint_path="t138sb01",
            tags=("mops", "self_reported_profit"),
        ),
        MopsRouteSpec(
            route_id="t163sb01",
            title_zh="財務報告公告",
            category="financials",
            source_type="filing",
            endpoint_path="t163sb01",
            required_params=("companyId", "year", "season"),
            tags=("mops", "financial_report"),
        ),
        MopsRouteSpec(
            route_id="t57sb01_q1",
            title_zh="財務報告書",
            category="financials",
            source_type="filing",
            endpoint_path="t57sb01_q1",
            required_params=("companyId", "year"),
            tags=("mops", "financial_report_book"),
        ),
        MopsRouteSpec(
            route_id="t164sb03",
            title_zh="資產負債表",
            category="financials",
            source_type="filing",
            endpoint_path="t164sb03",
            required_params=("companyId", "year", "season"),
            tags=("mops", "balance_sheet"),
        ),
        MopsRouteSpec(
            route_id="t164sb04",
            title_zh="綜合損益表",
            category="financials",
            source_type="filing",
            endpoint_path="t164sb04",
            required_params=("companyId", "year", "season"),
            tags=("mops", "income_statement"),
        ),
        MopsRouteSpec(
            route_id="t164sb05",
            title_zh="現金流量表",
            category="financials",
            source_type="filing",
            endpoint_path="t164sb05",
            required_params=("companyId", "year", "season"),
            tags=("mops", "cash_flow"),
        ),
        MopsRouteSpec(
            route_id="t51sb02",
            title_zh="財務分析資料",
            category="financials",
            source_type="filing",
            endpoint_path="t51sb02",
            tags=("mops", "financial_analysis"),
        ),
        MopsRouteSpec(
            route_id="t56sb31_q1",
            title_zh="財務報告更補正查詢",
            category="financials",
            source_type="filing",
            endpoint_path="t56sb31_q1",
            tags=("mops", "restatement", "correction"),
        ),
        MopsRouteSpec(
            route_id="t05st09_2",
            title_zh="股利分派情形",
            category="corporate_actions",
            source_type="filing",
            endpoint_path="t05st09_2",
            tags=("mops", "dividend"),
        ),
        MopsRouteSpec(
            route_id="t108sb19",
            title_zh="除權息公告",
            category="corporate_actions",
            source_type="filing",
            endpoint_path="t108sb19",
            tags=("mops", "ex_dividend"),
        ),
        MopsRouteSpec(
            route_id="t108sb27",
            title_zh="除權息公告",
            category="corporate_actions",
            source_type="filing",
            endpoint_path="t108sb27",
            tags=("mops", "ex_dividend"),
        ),
        MopsRouteSpec(
            route_id="t108sb16_q1",
            title_zh="股東常會/臨時會公告",
            category="governance",
            source_type="filing",
            endpoint_path="t108sb16_q1",
            tags=("mops", "shareholder_meeting"),
        ),
        MopsRouteSpec(
            route_id="t150sb04",
            title_zh="股東會議案決議情形",
            category="governance",
            source_type="filing",
            endpoint_path="t150sb04",
            tags=("mops", "shareholder_meeting"),
        ),
        MopsRouteSpec(
            route_id="stapap1",
            title_zh="董監事持股餘額",
            category="governance_ownership",
            source_type="filing",
            endpoint_path="stapap1",
            required_params=("companyId", "year", "month"),
            export_csv_path="stapap1/export/csv",
            tags=("mops", "insider_ownership"),
        ),
        MopsRouteSpec(
            route_id="query6_1",
            title_zh="內部人持股異動事後申報表",
            category="governance_ownership",
            source_type="filing",
            endpoint_path="query6_1",
            required_params=("companyId", "year", "month"),
            export_csv_path="query6_1/export/csv",
            tags=("mops", "insider_ownership_change"),
        ),
        MopsRouteSpec(
            route_id="t56sb12_q1",
            title_zh="內部人持股轉讓日報表",
            category="governance_ownership",
            source_type="filing",
            endpoint_path="t56sb12_q1",
            tags=("mops", "insider_transfer"),
        ),
        MopsRouteSpec(
            route_id="t56sb21_q1",
            title_zh="內部人持股未轉讓日報表",
            category="governance_ownership",
            source_type="filing",
            endpoint_path="t56sb21_q1",
            tags=("mops", "insider_transfer"),
        ),
        MopsRouteSpec(
            route_id="STAMAK03_1",
            title_zh="內部人設質解質公告",
            category="governance_ownership",
            source_type="filing",
            endpoint_path="STAMAK03_1",
            tags=("mops", "insider_pledge"),
        ),
    )

    TEJ_TRIAL_TABLE_CATEGORIES = {
        "AIND": "company_master",
        "TAMT": "governance",
        "TASALE": "operations",
        "TAGIN": "margin_balance",
        "TAPRCD": "daily_price",
        "TAQFII": "foreign_ownership",
        "TATINST1": "institutional_flow",
        "TAIACC": "financials",
        "TAIM1A": "financials",
        "TAIM1AA": "financials",
        "TAIM1AQ": "financials",
        "TAIM1AQA": "financials",
        "TAFUTR": "derivatives",
        "TAOPBAS": "derivatives",
        "TAOPTION": "derivatives",
    }

    TEJ_PAID_BACKFILL_TABLES: tuple[TejTableSpec, ...] = (
        TejTableSpec(
            db_code="TWN",
            table_code="APRCD1",
            title_zh="TQuant historical daily price",
            group_name="TQuant historical market data",
            source_category="daily_price",
            data_range="licensed_history",
            description="Paid TEJ/TQuant daily price history for gaps older than public or FinMind coverage.",
            entitlement_tag="tej-tquant-history",
        ),
        TejTableSpec(
            db_code="TWN",
            table_code="AMTOP1",
            title_zh="Broker major participant summary",
            group_name="Taiwan broker trading",
            source_category="broker_top",
            data_range="licensed_history",
            description="Paid broker major participant gap-fill candidate, only when licensed.",
            entitlement_tag="tej-broker-amtop1",
        ),
        TejTableSpec(
            db_code="TWN",
            table_code="ABSR20",
            title_zh="Top20 branch summary",
            group_name="Taiwan broker trading",
            source_category="broker_top",
            data_range="licensed_history",
            description="Paid top20 branch summary gap-fill candidate, only when licensed.",
            entitlement_tag="tej-broker-absr20",
        ),
    )

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
            governance_context="Governed vendor research/reference backfill source; does not replace official disclosure truth",
        ),
        "finmind": TaiwanSourceSpec(
            key="finmind",
            base_url="https://api.finmindtrade.com/api/v4",
            source_class="research_grade",
            governance_context="Low-cost primary Taiwan research API/cache layer; official sources remain identity and disclosure truth",
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

        request_headers = {
            "Accept": "application/json",
            "User-Agent": "pantheon-research-adapter/0.1",
            **dict(headers or {}),
        }
        request = Request(url, headers=request_headers)
        self._last_request_time = time.time()
        with open_external_url(request, caller="research.taiwan_market_client", timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def post_json(
        self,
        source_key: str,
        resource_path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        spec = self._spec(source_key)
        url = f"{spec.base_url.rstrip('/')}/{resource_path.lstrip('/')}"
        body = json.dumps(dict(payload or {})).encode("utf-8")

        elapsed = time.time() - self._last_request_time
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)

        request_headers = {"Content-Type": "application/json", **dict(headers or {})}
        request = Request(
            url,
            data=body,
            method="POST",
            headers=request_headers,
        )
        self._last_request_time = time.time()
        with open_external_url(request, caller="research.taiwan_market_client", timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))

    def mops_route_inventory(self, *, index_js_text: str | None = None) -> tuple[MopsRouteSpec, ...]:
        known = {route.route_id: route for route in self.MOPS_RECOMMENDED_ROUTES}
        routes = list(self.MOPS_RECOMMENDED_ROUTES)
        if index_js_text:
            for route_id in self.extract_mops_asset_routes(index_js_text):
                if route_id in known:
                    continue
                routes.append(
                    MopsRouteSpec(
                        route_id=route_id,
                        title_zh=route_id,
                        category="discovered_from_spa_bundle",
                        source_type="filing",
                        endpoint_path=route_id,
                        allow_fetch=False,
                        tags=("mops", "spa_discovered"),
                    )
                )
        return tuple(routes)

    @staticmethod
    def extract_mops_asset_routes(index_js_text: str) -> tuple[str, ...]:
        helper_assets = {
            "_plugin-vue_export-helper",
            "breadcrumb",
            "home",
            "moreFun",
            "searchBar",
            "toolBar",
        }
        route_ids = set()
        for match in re.finditer(r'"assets/([^"]+)\.js"', index_js_text):
            route_id = match.group(1)
            if route_id in helper_assets or route_id.startswith("_"):
                continue
            route_ids.add(route_id)
        return tuple(sorted(route_ids))

    def mops_route(self, route_id: str) -> MopsRouteSpec:
        normalized = str(route_id).strip()
        for route in self.MOPS_RECOMMENDED_ROUTES:
            if route.route_id == normalized:
                return route
        raise ValueError(f"unsupported or unallowlisted MOPS route: {route_id}")

    def fetch_mops_route(self, route_id: str, params: Mapping[str, Any] | None = None) -> Any:
        route = self.mops_route(route_id)
        if not route.allow_fetch:
            raise ValueError(f"MOPS route is inventory-only and not fetch-allowlisted: {route_id}")
        payload = {**dict(route.default_params), **dict(params or {})}
        missing = [key for key in route.required_params if payload.get(key) in (None, "")]
        if missing:
            raise ValueError(f"MOPS route {route_id} requires params: {', '.join(missing)}")
        return self.post_json(
            "mops",
            route.endpoint_path,
            payload=payload,
            headers={
                "Accept": "application/json",
                "Origin": "https://mops.twse.com.tw",
                "Referer": self.MOPS_HOME_URL,
                "User-Agent": "pantheon-research-adapter/0.1",
            },
        )

    def mops_result_rows(self, payload: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        result = payload.get("result") if isinstance(payload.get("result"), Mapping) else {}
        if not isinstance(result, Mapping):
            return tuple()
        data = result.get("data")
        titles = result.get("titles")
        if isinstance(data, list):
            title_names = [_mops_title_name(title) for title in titles] if isinstance(titles, list) else []
            rows: list[dict[str, Any]] = []
            for raw_row in data:
                if isinstance(raw_row, Mapping):
                    rows.append({str(key): _coerce_mops_value(value) for key, value in raw_row.items()})
                elif isinstance(raw_row, list):
                    row: dict[str, Any] = {}
                    for idx, value in enumerate(raw_row):
                        key = title_names[idx] if idx < len(title_names) and title_names[idx] else f"column_{idx}"
                        row[key] = _coerce_mops_value(value)
                    rows.append(row)
            return tuple(rows)
        if isinstance(result, Mapping):
            return ({str(key): _coerce_mops_value(value) for key, value in result.items()},)
        return tuple()

    def normalize_mops_disclosures_from_payload(
        self,
        route_id: str,
        payload: Mapping[str, Any],
    ) -> tuple[TaiwanDisclosureRecord, ...]:
        route = self.mops_route(route_id)
        disclosures: list[TaiwanDisclosureRecord] = []
        for row in self.mops_result_rows(payload):
            symbol = str(_first_present(row, "公司代號", "companyId", "stockId", "stock_id", "symbol") or "")
            disclosure_date = str(_first_present(row, "發言日期", "日期", "date", "announce_date") or payload.get("datetime") or "")
            if not symbol or not disclosure_date:
                continue
            source = {
                "company_id": symbol,
                "company_name": _first_present(row, "公司名稱", "公司簡稱", "companyName", "companyAbbreviation", "name"),
                "filing_code": route.route_id,
                "announce_date": disclosure_date,
                "fiscal_period": _first_present(row, "資料年月", "會計年度", "年度", "period"),
            }
            normalized = self.normalize_mops_disclosure(source, api_endpoint=f"mops://{route.endpoint_path}")
            normalized.source_metadata.update(
                {
                    "route_id": route.route_id,
                    "route_title_zh": route.title_zh,
                    "category": route.category,
                    "subject": _first_present(row, "主旨", "subject", "title"),
                    "raw_row": dict(row),
                }
            )
            disclosures.append(normalized)
        return tuple(disclosures)

    def fetch_tej_trial_table_catalog(self) -> tuple[TejTableSpec, ...]:
        payload = self.get_json("tej", self.TEJ_TRIAL_CATALOG_PATH)
        return self.tej_trial_table_inventory_from_payload(payload)

    def tej_paid_backfill_table_catalog(
        self,
        *,
        purchased_table_allowlist: Sequence[str] | None = None,
    ) -> tuple[TejTableSpec, ...]:
        if not purchased_table_allowlist:
            return self.TEJ_PAID_BACKFILL_TABLES
        allowlist = {str(item).strip().upper() for item in purchased_table_allowlist if str(item).strip()}
        return tuple(
            spec
            for spec in self.TEJ_PAID_BACKFILL_TABLES
            if spec.dataset_code.upper() in allowlist or spec.table_code.upper() in allowlist
        )

    def tej_trial_table_inventory_from_payload(self, payload: Mapping[str, Any]) -> tuple[TejTableSpec, ...]:
        tables = payload.get("tables")
        if not isinstance(tables, list):
            return tuple()
        specs: list[TejTableSpec] = []
        for table in tables:
            if not isinstance(table, Mapping):
                continue
            table_code = str(table.get("tableName") or "").strip()
            if not table_code:
                continue
            specs.append(
                TejTableSpec(
                    db_code="TRAIL",
                    table_code=table_code,
                    title_zh=str(table.get("cName") or table_code).strip(),
                    group_name=str(table.get("groupName") or "").strip(),
                    source_category=self.TEJ_TRIAL_TABLE_CATEGORIES.get(table_code, "vendor_reference"),
                    data_range=str(table.get("dataRange") or "").strip() or None,
                    description=str(table.get("description") or "").strip() or None,
                    entitlement_tag="tej-trial-catalog",
                )
            )
        return tuple(specs)

    def fetch_tej_table_metadata(self, dataset_code: str, *, api_key: str) -> Any:
        return self.get_json("tej", f"api/datatables/{dataset_code.strip('/')}/metadata", params={"api_key": api_key})

    def fetch_tej_dataset(
        self,
        dataset_code: str,
        *,
        api_key: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        query = {"api_key": api_key, **dict(params or {})}
        return self.get_json("tej", f"api/datatables/{dataset_code.strip('/')}.json", params=query)

    def fetch_finmind_dataset(
        self,
        dataset_code: str,
        *,
        token: str,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        query = {"dataset": dataset_code, "token": token, **dict(params or {})}
        return self.get_json("finmind", "data", params=query)

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
        table_code: str | None = None,
        license_scope: str = "vendor_research",
        available_time: str | None = None,
        point_in_time_available: bool = True,
    ) -> TaiwanResearchDatasetRecord:
        metadata = self.build_metadata("tej", api_endpoint)
        symbol = str(_first_present(record, "coid", "symbol", "股票代號") or "")
        as_of_date = str(_first_present(record, "mdate", "date", "資料日") or "")
        if not symbol or not as_of_date:
            raise ValueError("TEJ dataset requires symbol and as_of_date")
        resolved_table_code = str(table_code or dataset_code.strip("/").split("/")[-1]).strip()
        resolved_available_time = str(
            available_time
            or _first_present(record, "available_time", "available_at", "pub_date", "mdate", "date", "資料日")
            or as_of_date
        )
        values = {
            key: value
            for key, value in record.items()
            if key not in {"coid", "symbol", "股票代號", "mdate", "date", "資料日", "available_time", "available_at"}
        }
        return TaiwanResearchDatasetRecord(
            dataset_code=dataset_code,
            symbol=symbol,
            as_of_date=as_of_date,
            values=values,
            source_metadata={
                "raw_record": dict(record),
                "provider": "TEJ API",
                "dataset_code": dataset_code,
                "table_code": resolved_table_code,
                "license_scope": str(license_scope),
                "available_time": resolved_available_time,
                "point_in_time_available": bool(point_in_time_available),
                "access_scope": ["research"],
            },
            governance_metadata=metadata.to_dict(),
        )

    def normalize_finmind_dataset(
        self,
        record: dict[str, Any],
        *,
        dataset_code: str,
        api_endpoint: str = "finmind://dataset",
    ) -> TaiwanResearchDatasetRecord:
        metadata = self.build_metadata("finmind", api_endpoint)
        symbol = str(_first_present(record, "stock_id", "data_id", "symbol", "coid", "股票代號") or "")
        as_of_date = str(_first_present(record, "date", "mdate", "published_at", "資料日") or "")
        if not symbol or not as_of_date:
            raise ValueError("FinMind dataset requires symbol and as_of_date")
        values = {
            key: value
            for key, value in record.items()
            if key not in {"stock_id", "data_id", "symbol", "coid", "股票代號", "date", "mdate", "published_at", "資料日"}
        }
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

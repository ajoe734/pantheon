"""Source connector models."""

from .base import (
    AuthPolicy,
    AuthType,
    ConnectorMode,
    ConnectorStatus,
    IngestEvent,
    IngestRun,
    IngestRunStatus,
    LicensePolicy,
    RateLimitPolicy,
    SecretRef,
    SourceConnector,
    SourceConnectorProvider,
    SourceEvidenceError,
    SourceMetadata,
    SourceRecord,
    SourceRecordStatus,
    SourceType,
)
from .examples import ExternalFeedProviderExample, StaticRecordsProviderExample, example_provider_catalog
from .finmind_taiwan import (
    FinMindTaiwanBrokerBulkBackfillAdapter,
    FinMindTaiwanBrokerDailyReportAdapter,
    FinMindTaiwanDatasetAdapter,
)
from .paper import OpenAlexPaperIngestAdapter
from .repo_allowlist import RepoAllowlistEntry, RepoAllowlistProvider
from .taiwan_market import MopsSourceIngestAdapter, TejSourceIngestAdapter
from .taiwan_official import (
    TAIWAN_OFFICIAL_ENDPOINTS,
    TW_OFFICIAL_CONNECTOR_ID,
    TaiwanOfficialMarketDatasetAdapter,
)
from .us_public import (
    FINRA_SHORT_SALE_CONNECTOR_ID,
    FRED_CONNECTOR_ID,
    SEC_EDGAR_CONNECTOR_ID,
    STOOQ_DAILY_OHLCV_CONNECTOR_ID,
    FinraShortSaleAdapter,
    FredMacroSeriesAdapter,
    SecEdgarFilingAdapter,
    StooqDailyOhlcvAdapter,
)
from .yahoo_taiwan import (
    YahooTaiwanBrokerTopAdapter,
    YahooTaiwanRssAdapter,
    parse_yahoo_broker_trading_html,
)

__all__ = [
    "AuthPolicy",
    "AuthType",
    "ConnectorMode",
    "ConnectorStatus",
    "IngestEvent",
    "IngestRun",
    "IngestRunStatus",
    "LicensePolicy",
    "RateLimitPolicy",
    "SecretRef",
    "SourceConnector",
    "SourceConnectorProvider",
    "SourceEvidenceError",
    "SourceMetadata",
    "SourceRecord",
    "SourceRecordStatus",
    "SourceType",
    "ExternalFeedProviderExample",
    "FINRA_SHORT_SALE_CONNECTOR_ID",
    "FinMindTaiwanBrokerBulkBackfillAdapter",
    "FinMindTaiwanBrokerDailyReportAdapter",
    "FinMindTaiwanDatasetAdapter",
    "FinraShortSaleAdapter",
    "FRED_CONNECTOR_ID",
    "FredMacroSeriesAdapter",
    "OpenAlexPaperIngestAdapter",
    "RepoAllowlistEntry",
    "RepoAllowlistProvider",
    "SEC_EDGAR_CONNECTOR_ID",
    "STOOQ_DAILY_OHLCV_CONNECTOR_ID",
    "SecEdgarFilingAdapter",
    "StaticRecordsProviderExample",
    "StooqDailyOhlcvAdapter",
    "TAIWAN_OFFICIAL_ENDPOINTS",
    "TW_OFFICIAL_CONNECTOR_ID",
    "MopsSourceIngestAdapter",
    "TaiwanOfficialMarketDatasetAdapter",
    "TejSourceIngestAdapter",
    "YahooTaiwanBrokerTopAdapter",
    "YahooTaiwanRssAdapter",
    "parse_yahoo_broker_trading_html",
    "example_provider_catalog",
]

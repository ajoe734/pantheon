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
    "FinMindTaiwanBrokerBulkBackfillAdapter",
    "FinMindTaiwanBrokerDailyReportAdapter",
    "FinMindTaiwanDatasetAdapter",
    "OpenAlexPaperIngestAdapter",
    "RepoAllowlistEntry",
    "RepoAllowlistProvider",
    "StaticRecordsProviderExample",
    "MopsSourceIngestAdapter",
    "TejSourceIngestAdapter",
    "YahooTaiwanBrokerTopAdapter",
    "YahooTaiwanRssAdapter",
    "parse_yahoo_broker_trading_html",
    "example_provider_catalog",
]

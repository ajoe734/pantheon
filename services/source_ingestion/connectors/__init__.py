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
from .paper import OpenAlexPaperIngestAdapter
from .repo_allowlist import RepoAllowlistEntry, RepoAllowlistProvider
from .taiwan_market import MopsSourceIngestAdapter, TejSourceIngestAdapter

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
    "OpenAlexPaperIngestAdapter",
    "RepoAllowlistEntry",
    "RepoAllowlistProvider",
    "StaticRecordsProviderExample",
    "MopsSourceIngestAdapter",
    "TejSourceIngestAdapter",
    "example_provider_catalog",
]

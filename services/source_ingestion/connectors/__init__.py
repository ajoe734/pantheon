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
    "StaticRecordsProviderExample",
    "example_provider_catalog",
]

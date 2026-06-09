"""Provider examples for the source connector SDK contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

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
from .paper import OpenAlexPaperIngestAdapter
from .repo_allowlist import RepoAllowlistProvider
from .taiwan_market import MopsSourceIngestAdapter, TejSourceIngestAdapter
from .yahoo_taiwan import YahooTaiwanBrokerTopAdapter, YahooTaiwanRssAdapter


@dataclass(frozen=True)
class StaticRecordsProviderExample:
    connector_id: str
    source_type: str
    provider: str
    license_scope: str
    records: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    next_watermark: str | None = None
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=self.source_type,
            provider=self.provider,
            license_scope=self.license_scope,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(auth_type=AuthType.NONE),
            license_policy=LicensePolicy(license_scope=self.license_scope, allowed_use=("research", "evaluation")),
            rate_limit_policy=RateLimitPolicy(policy_ref="source-ingest://policy/static-records-local"),
            source_metadata=self.source_metadata,
            metadata={"provider_example": "static_records", **dict(self.connector_metadata)},
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "static_records",
            "records": [dict(record) for record in self.records],
            "next_watermark": self.next_watermark,
        }


@dataclass(frozen=True)
class ExternalFeedProviderExample:
    connector_id: str
    source_type: str
    provider: str
    license_scope: str
    url: str
    allowed_url_prefixes: Sequence[str]
    max_records: int = 100
    max_bytes: int = 1_000_000
    timeout_seconds: float = 5.0
    default_access_scope: Sequence[str] = ("public",)
    secret_ref_id: str | None = None
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None
    connector_metadata: Mapping[str, Any] = field(default_factory=dict)

    def connector(self) -> SourceConnector:
        return SourceConnector(
            connector_id=self.connector_id,
            source_type=self.source_type,
            provider=self.provider,
            license_scope=self.license_scope,
            auth_type=AuthType.API_KEY if self.secret_ref_id else AuthType.NONE,
            secret_ref_id=self.secret_ref_id,
            supported_modes=(ConnectorMode.BATCH,),
            auth_policy=AuthPolicy(
                auth_type=AuthType.API_KEY if self.secret_ref_id else AuthType.NONE,
                secret_ref={"secret_ref_id": self.secret_ref_id} if self.secret_ref_id else None,
                auth_scope=("source_ingest:read",) if self.secret_ref_id else (),
            ),
            license_policy=LicensePolicy(
                license_scope=self.license_scope,
                allowed_use=("research", "search_index"),
                attribution_required=True,
            ),
            rate_limit_policy=RateLimitPolicy(
                requests_per_minute=60,
                burst=10,
                retry_after_seconds=60,
                policy_ref="source-ingest://policy/external-feed-default",
            ),
            source_metadata=self.source_metadata,
            metadata={"provider_example": "external_feed", **dict(self.connector_metadata)},
        )

    def fetch_config(self) -> Mapping[str, Any]:
        return {
            "mode": "external_feed",
            "url": self.url,
            "allowed_url_prefixes": list(self.allowed_url_prefixes),
            "timeout_seconds": self.timeout_seconds,
            "max_bytes": self.max_bytes,
            "max_records": self.max_records,
            "default_access_scope": list(self.default_access_scope),
        }


def example_provider_catalog() -> tuple[SourceConnectorProvider, ...]:
    return (
        StaticRecordsProviderExample(
            connector_id="example-static-notes",
            source_type="internal_note",
            provider="Pantheon Static Notes",
            license_scope="internal",
            records=(
                {
                    "source_id": "example-static-note-1",
                    "title": "Static provider example",
                    "content_ref": "memory://source-ingest/examples/static-note-1",
                },
            ),
            source_metadata=SourceMetadata(
                display_name="Static records example",
                owner="pantheon-source-ingest",
                tags=("example", "static_records"),
            ),
        ),
        OpenAlexPaperIngestAdapter(
            connector_id="example-openalex-feed",
            max_records=25,
            source_metadata=SourceMetadata(
                display_name="OpenAlex paper ingest example",
                homepage_url="https://openalex.org",
                docs_url="https://docs.openalex.org",
                owner="OpenAlex",
                tags=("example", "external_feed", "paper"),
            ),
        ),
        ExternalFeedProviderExample(
            connector_id="example-news-feed",
            source_type="news",
            provider="Example News Vendor",
            license_scope="vendor",
            url="https://news.example.test/feed.json",
            allowed_url_prefixes=("https://news.example.test/",),
            max_records=25,
            default_access_scope=("research",),
            source_metadata=SourceMetadata(
                display_name="Example governed news feed",
                owner="Example News Vendor",
                tags=("example", "external_feed", "news"),
            ),
            connector_metadata={
                "entitlement_tags": ["example-news-research"],
                "access_scope": ["research"],
            },
        ),
        StaticRecordsProviderExample(
            connector_id="example-social-feed",
            source_type="social",
            provider="Example Social Vendor",
            license_scope="vendor",
            records=(
                {
                    "source_id": "example-social-post-1",
                    "title": "Example social post",
                    "content_ref": "social://example/post-1",
                    "metadata": {
                        "platform": "example-social",
                        "author_id_hash": "sha256:exampleauthor",
                        "post_id": "post-1",
                        "published_at": "2026-05-01T12:00:00Z",
                        "event_time": "2026-05-01T12:00:00Z",
                        "available_time": "2026-05-01T12:00:30Z",
                        "trust_score": 0.62,
                        "platform_policy_ref": "source-ingest://policy/example-social",
                        "entitlement_tags": ["example-social-research"],
                        "access_scope": ["research"],
                        "body": "Example social evidence.",
                    },
                },
            ),
            source_metadata=SourceMetadata(
                display_name="Example governed social feed",
                owner="Example Social Vendor",
                tags=("example", "static_records", "social"),
            ),
            connector_metadata={
                "entitlement_tags": ["example-social-research"],
                "access_scope": ["research"],
            },
        ),
        StaticRecordsProviderExample(
            connector_id="example-alpha-db",
            source_type="alpha_db",
            provider="Example Alpha Vendor",
            license_scope="restricted",
            records=(
                {
                    "source_id": "example-alpha-signal-1",
                    "title": "Example alpha signal",
                    "content_ref": "alpha-db://example/signal-1/v1",
                    "metadata": {
                        "alpha_vendor_id": "example-alpha",
                        "signal_id": "signal-1",
                        "signal_version": "v1",
                        "field_schema": {"score": "float"},
                        "universe": ["US_EQUITY"],
                        "as_of_time": "2026-05-01T12:00:00Z",
                        "event_time": "2026-05-01T12:00:00Z",
                        "available_time": "2026-05-01T12:05:00Z",
                        "allowed_use": ["research", "experiment"],
                        "entitlement_tags": ["example-alpha-research"],
                        "access_scope": ["research"],
                        "body": "Example alpha signal evidence.",
                    },
                },
            ),
            source_metadata=SourceMetadata(
                display_name="Example governed alpha DB",
                owner="Example Alpha Vendor",
                tags=("example", "static_records", "alpha_db"),
            ),
            connector_metadata={
                "entitlement_tags": ["example-alpha-research"],
                "access_scope": ["research"],
            },
        ),
        RepoAllowlistProvider(
            connector_id="example-github-repo-allowlist",
            allowlist=(
                {
                    "repo_full_name": "QuantConnect/Lean",
                    "allowed_paths": ("Algorithm.Python/", "Research/", "Documentation/"),
                    "ref": "master",
                    "purpose": "official LEAN research examples and bridge reference",
                },
            ),
            source_metadata=SourceMetadata(
                display_name="GitHub repo allowlist example",
                homepage_url="https://github.com/QuantConnect/Lean",
                docs_url="https://docs.github.com/rest",
                owner="pantheon-source-ingest",
                tags=("example", "repo", "allowlist"),
            ),
        ),
        MopsSourceIngestAdapter(
            connector_id="example-tw-mops-official-disclosures",
            source_metadata=SourceMetadata(
                display_name="MOPS official Taiwan disclosures",
                homepage_url="https://mops.twse.com.tw/mops/",
                owner="Taiwan Stock Exchange",
                tags=("example", "taiwan", "mops", "official_reference", "filing"),
            ),
        ),
        TejSourceIngestAdapter(
            connector_id="example-tw-tej-research-datasets",
            source_metadata=SourceMetadata(
                display_name="TEJ API Taiwan research datasets",
                homepage_url="https://api.tej.com.tw",
                docs_url="https://api.tej.com.tw/document_rest.html",
                owner="TEJ",
                tags=("example", "taiwan", "tej", "research_grade", "market"),
            ),
        ),
        YahooTaiwanBrokerTopAdapter(
            connector_id="example-tw-yahoo-broker-top15",
            source_metadata=SourceMetadata(
                display_name="Yahoo Taiwan top broker trading",
                homepage_url="https://tw.stock.yahoo.com/",
                owner="Yahoo Taiwan Stock",
                tags=("example", "taiwan", "yahoo", "broker_top", "public_web_summary"),
            ),
        ),
        YahooTaiwanRssAdapter(
            connector_id="example-tw-yahoo-stock-rss",
            source_metadata=SourceMetadata(
                display_name="Yahoo Taiwan stock RSS",
                homepage_url="https://tw.stock.yahoo.com/",
                owner="Yahoo Taiwan Stock",
                tags=("example", "taiwan", "yahoo", "rss", "news"),
            ),
        ),
    )


def source_records_from_payloads(
    connector: SourceConnector,
    records: Sequence[Mapping[str, Any]],
) -> tuple[SourceRecord, ...]:
    source_records: list[SourceRecord] = []
    for record in records:
        payload: dict[str, Any] = {
            "source_id": str(record["source_id"]),
            "connector_id": str(record.get("connector_id") or connector.connector_id),
            "source_type": str(record.get("source_type") or connector.source_type.value),
            "title": str(record["title"]),
            "content_ref": str(record["content_ref"]),
            "status": str(record.get("status") or "normalized"),
            "metadata": dict(record.get("metadata") or {}),
            "trace_id": str(record.get("trace_id") or ""),
        }
        if record.get("created_at"):
            payload["created_at"] = record.get("created_at")
        source_records.append(SourceRecord(**payload))
    return tuple(source_records)

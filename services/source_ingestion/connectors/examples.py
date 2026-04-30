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
    SourceMetadata,
    SourceRecord,
)


@dataclass(frozen=True)
class StaticRecordsProviderExample:
    connector_id: str
    source_type: str
    provider: str
    license_scope: str
    records: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    next_watermark: str | None = None
    source_metadata: SourceMetadata | Mapping[str, Any] | None = None

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
            metadata={"provider_example": "static_records"},
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
            metadata={"provider_example": "external_feed"},
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


def example_provider_catalog() -> tuple[StaticRecordsProviderExample, ExternalFeedProviderExample]:
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
        ExternalFeedProviderExample(
            connector_id="example-openalex-feed",
            source_type="paper",
            provider="OpenAlex feed example",
            license_scope="open",
            url="https://api.openalex.org/works",
            allowed_url_prefixes=("https://api.openalex.org/",),
            max_records=25,
            source_metadata=SourceMetadata(
                display_name="OpenAlex external feed example",
                homepage_url="https://openalex.org",
                docs_url="https://docs.openalex.org",
                owner="OpenAlex",
                tags=("example", "external_feed", "paper"),
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

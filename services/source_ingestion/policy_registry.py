"""Crawler/indexer policy registry projections for source ingestion."""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Any, Mapping

from .connectors import SourceConnector, SourceType


POLICY_REGISTRY_SCHEMA_VERSION = "source_crawler_indexer_policy_registry.v1"
CRAWLER_POLICY_SCHEMA_VERSION = "source_crawler_adapter_policy.v1"

_PIT_SOURCE_TYPES = {
    SourceType.NEWS.value,
    SourceType.SOCIAL.value,
    SourceType.ALPHA_DB.value,
    SourceType.MACRO.value,
    SourceType.MARKET.value,
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def crawler_policy_for_connector(
    connector: SourceConnector,
    *,
    fetch: Mapping[str, Any] | None,
    schedule: Mapping[str, Any] | None,
    freshness: Mapping[str, Any] | None,
    state: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project a configured connector into a bounded crawler/indexer policy."""

    fetch_payload = dict(fetch or {})
    schedule_payload = dict(schedule or {})
    freshness_payload = dict(freshness or {})
    state_payload = dict(state or {})
    fetch_mode = str(fetch_payload.get("mode") or "unconfigured")
    source_type = connector.source_type.value
    allowed_prefixes = _string_list(fetch_payload.get("allowed_url_prefixes"))
    hosts = sorted(
        {
            urllib.parse.urlparse(prefix).netloc
            for prefix in allowed_prefixes
            if urllib.parse.urlparse(prefix).netloc
        }
    )
    lifecycle_status = str(connector.status.value)
    schedule_enabled = bool(schedule_payload.get("enabled") is True and int(schedule_payload.get("interval_seconds") or 0) > 0)
    pit_required = source_type in _PIT_SOURCE_TYPES
    entitlement_tags = _string_list(connector.metadata.get("entitlement_tags"))
    entitlement_ref = str(connector.metadata.get("entitlement_ref") or "").strip()
    if entitlement_ref and entitlement_ref not in entitlement_tags:
        entitlement_tags.append(entitlement_ref)

    return {
        "schema_version": CRAWLER_POLICY_SCHEMA_VERSION,
        "connector_id": connector.connector_id,
        "source_type": source_type,
        "provider": connector.provider,
        "lifecycle": {
            "status": lifecycle_status,
            "schedule_enabled": schedule_enabled,
            "freshness_status": freshness_payload.get("status"),
            "is_due": bool(freshness_payload.get("is_due", False)),
            "ready_for_scheduled_crawl": lifecycle_status == "enabled" and fetch_mode != "unconfigured" and schedule_enabled,
            "state": state_payload,
        },
        "crawler": {
            "adapter_type": _adapter_type(fetch_mode),
            "bounded": True,
            "fetch_mode": fetch_mode,
            "allowlist_enforced": fetch_mode == "external_feed",
            "allowed_url_prefix_count": len(allowed_prefixes),
            "allowed_url_hosts": hosts,
            "max_records": fetch_payload.get("max_records") or len(fetch_payload.get("records") or []),
            "max_bytes": fetch_payload.get("max_bytes"),
            "timeout_seconds": fetch_payload.get("timeout_seconds"),
            "respect_robots_txt": bool(fetch_payload.get("respect_robots_txt", True)),
            "request_documents_compat": False,
        },
        "guards": {
            "license": connector.license_policy.to_dict(),
            "rate_limit": connector.rate_limit_policy.to_dict(),
            "entitlement_required": pit_required or source_type in {"news", "social", "alpha_db"},
            "entitlement_tags": entitlement_tags,
            "pit_required": pit_required,
            "pit_fields": ["event_time", "available_time", "ingest_time"] if pit_required else [],
            "audit_required": True,
            "durable_sink": "SourceRecord/EvidenceBundle/KnowledgeObject",
            "direct_execution_allowed": False,
            "direct_order_routing_allowed": False,
        },
        "indexer": {
            "refresh_trigger": "ingest_completion_or_schedule",
            "materialized_index_required": True,
            "normal_search_path": "durable_index",
        },
    }


def policy_registry_payload(
    connector_policies: list[dict[str, Any]],
    *,
    max_records_per_job: int,
    scheduler_max_concurrency: int,
    frontier_max_attempts: int,
    search_ingest_notify_url: str | None,
    posture: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": POLICY_REGISTRY_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "policy_classes": [
            "SourceAdmissionPolicy",
            "CrawlerAllowlistPolicy",
            "LicenseEntitlementPolicy",
            "RateLimitPolicy",
            "PointInTimePolicy",
            "AuditPolicy",
            "IndexerRefreshPolicy",
        ],
        "default_guards": {
            "bounded_crawler_adapters_only": True,
            "max_records_per_job": max_records_per_job,
            "scheduler_max_concurrency": scheduler_max_concurrency,
            "frontier_max_attempts": frontier_max_attempts,
            "audit_required": True,
            "durable_evidence_store_required": True,
            "search_refresh_notification_configured": bool(search_ingest_notify_url),
            "source_search_posture": dict(posture),
        },
        "connector_policies": connector_policies,
        "summary": {
            "connector_policy_count": len(connector_policies),
            "external_allowlist_policy_count": sum(
                1 for policy in connector_policies if policy.get("crawler", {}).get("allowlist_enforced") is True
            ),
            "pit_policy_count": sum(1 for policy in connector_policies if policy.get("guards", {}).get("pit_required") is True),
            "scheduled_policy_count": sum(
                1 for policy in connector_policies if policy.get("lifecycle", {}).get("schedule_enabled") is True
            ),
        },
    }


def _adapter_type(fetch_mode: str) -> str:
    if fetch_mode == "external_feed":
        return "bounded_external_feed_crawler"
    if fetch_mode == "static_records":
        return "bounded_static_records_adapter"
    return "unconfigured"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []

"""Connector coverage matrix for market-data source acceptance.

Compares the planned connector IDs from the financial data-source catalog
against the connectors that are actually configured and healthy in the
runtime. Produces a structured coverage report used by the ops acceptance
dashboard and the weekly gap report.

Coverage classes:
  missing               - In catalog, not configured in connector store.
  configured            - Configured, enabled, no health record yet.
  disabled              - Configured but lifecycle status is disabled.
  credential_unavailable - Configured, enabled, health shows credential error.
  unhealthy             - Configured, enabled, health status is not ok and not credential.
  healthy               - Configured, enabled, health status is ok.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


COVERAGE_CLASSES = (
    "missing",
    "configured",
    "disabled",
    "credential_unavailable",
    "unhealthy",
    "healthy",
)

_CREDENTIAL_KEYWORDS = frozenset(
    ("credential", "api key", "api_key", "token", "secret", "unauthorized", "credential_unavailable")
)


def _is_credential_error(source_error: str, health_status: str) -> bool:
    text = source_error.lower()
    return any(kw in text for kw in _CREDENTIAL_KEYWORDS) or health_status == "credential_unavailable"


@dataclass(frozen=True)
class CoverageEntry:
    connector_id: str
    data_source_id: str
    provider: str
    coverage_class: str
    health_status: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "data_source_id": self.data_source_id,
            "provider": self.provider,
            "coverage_class": self.coverage_class,
            "health_status": self.health_status,
            "reason": self.reason,
        }


def _classify_entry(
    connector_id: str,
    data_source_id: str,
    provider: str,
    configured_ids: set[str],
    lifecycle_by_id: Mapping[str, str],
    health_by_id: Mapping[str, Mapping[str, Any]],
) -> CoverageEntry:
    if connector_id not in configured_ids:
        return CoverageEntry(
            connector_id=connector_id,
            data_source_id=data_source_id,
            provider=provider,
            coverage_class="missing",
            reason="connector not configured in runtime",
        )

    lifecycle = lifecycle_by_id.get(connector_id, "enabled")
    if lifecycle == "disabled":
        return CoverageEntry(
            connector_id=connector_id,
            data_source_id=data_source_id,
            provider=provider,
            coverage_class="disabled",
            reason="connector lifecycle status is disabled",
        )

    health = health_by_id.get(connector_id)
    if health is None:
        return CoverageEntry(
            connector_id=connector_id,
            data_source_id=data_source_id,
            provider=provider,
            coverage_class="configured",
            reason="no health record yet",
        )

    status = str(health.get("status") or "ok")
    metadata = health.get("metadata") or {}
    source_error = str(metadata.get("source_error") or "")

    if _is_credential_error(source_error, status):
        return CoverageEntry(
            connector_id=connector_id,
            data_source_id=data_source_id,
            provider=provider,
            coverage_class="credential_unavailable",
            health_status=status,
            reason=source_error or "credential unavailable",
        )
    if status == "ok":
        return CoverageEntry(
            connector_id=connector_id,
            data_source_id=data_source_id,
            provider=provider,
            coverage_class="healthy",
            health_status=status,
            reason="",
        )
    return CoverageEntry(
        connector_id=connector_id,
        data_source_id=data_source_id,
        provider=provider,
        coverage_class="unhealthy",
        health_status=status,
        reason=source_error or status,
    )


def build_coverage_matrix(
    *,
    catalog_entries: Sequence[Mapping[str, Any]],
    configured_connector_ids: set[str],
    health_by_connector_id: Mapping[str, Mapping[str, Any]],
    lifecycle_by_connector_id: Mapping[str, str],
) -> dict[str, Any]:
    """Build coverage matrix comparing planned catalog connectors vs configured runtime connectors.

    Args:
        catalog_entries: List of DataSourceEntry dicts from the financial source catalog.
        configured_connector_ids: Set of connector IDs present in the connector store.
        health_by_connector_id: Maps connector_id -> SourceHealth.to_dict() for known sources.
        lifecycle_by_connector_id: Maps connector_id -> lifecycle status string.

    Returns:
        Coverage matrix payload dict with schema_version, summary, and entries.
    """
    seen: set[str] = set()
    entries: list[CoverageEntry] = []

    for catalog_entry in catalog_entries:
        connector_id = str(catalog_entry.get("connector_id") or "").strip()
        if not connector_id or connector_id in seen:
            continue
        seen.add(connector_id)

        entry = _classify_entry(
            connector_id=connector_id,
            data_source_id=str(catalog_entry.get("data_source_id") or ""),
            provider=str(catalog_entry.get("provider") or ""),
            configured_ids=configured_connector_ids,
            lifecycle_by_id=lifecycle_by_connector_id,
            health_by_id=health_by_connector_id,
        )
        entries.append(entry)

    summary: dict[str, int] = {cls: 0 for cls in COVERAGE_CLASSES}
    for entry in entries:
        summary[entry.coverage_class] = summary.get(entry.coverage_class, 0) + 1

    return {
        "schema_version": "connector_coverage_matrix.v1",
        "catalog_entry_count": len(catalog_entries),
        "coverage_entry_count": len(entries),
        "summary": summary,
        "entries": [e.to_dict() for e in entries],
    }


def build_source_alerts(
    *,
    catalog_entries: Sequence[Mapping[str, Any]],
    configured_connector_ids: set[str],
    health_records: Sequence[Mapping[str, Any]],
    include_missing: bool = True,
) -> list[dict[str, Any]]:
    """Build a list of source alerts for sources that require operator attention.

    An alert is raised when:
    - A configured source has health status != ok (stale, degraded, failed).
    - A catalog-planned connector is not configured in the runtime (missing).

    Credential-unavailable sources are included but labelled separately so
    operators can distinguish actionable runtime failures from expected
    credential-not-yet-installed states.

    Args:
        catalog_entries: DataSourceEntry dicts from the financial source catalog.
        configured_connector_ids: Set of connector IDs in the connector store.
        health_records: List of SourceHealth.to_dict() for all sources.
        include_missing: When True, catalog entries with no configured connector are included.

    Returns:
        List of alert dicts, each with alert_type, connector_id, and diagnostics.
    """
    alerts: list[dict[str, Any]] = []

    for health in health_records:
        status = str(health.get("status") or "ok")
        if status == "ok":
            continue
        metadata = dict(health.get("metadata") or {})
        source_error = str(metadata.get("source_error") or "")
        alert_type = "credential_unavailable" if _is_credential_error(source_error, status) else "unhealthy"
        alerts.append({
            "alert_type": alert_type,
            "connector_id": str(health.get("source_id") or ""),
            "health_status": status,
            "source_error": source_error,
            "latest_watermark": health.get("latest_watermark"),
            "last_failure_at": health.get("last_failure_at"),
            "error_rate_7d": health.get("error_rate_7d", 0.0),
        })

    if include_missing:
        for catalog_entry in catalog_entries:
            connector_id = str(catalog_entry.get("connector_id") or "").strip()
            if connector_id and connector_id not in configured_connector_ids:
                alerts.append({
                    "alert_type": "missing_connector",
                    "connector_id": connector_id,
                    "data_source_id": str(catalog_entry.get("data_source_id") or ""),
                    "provider": str(catalog_entry.get("provider") or ""),
                    "health_status": None,
                    "source_error": "connector not configured in runtime",
                })

    return alerts

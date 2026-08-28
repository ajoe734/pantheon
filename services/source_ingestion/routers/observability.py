"""Source observability router for Source Ingestion.

Covers health, usage tracking, usage aggregation, retirement recommendations,
health-usage snapshot, coverage matrix, source alerts, and market data gap reports.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException

from ..active_universe import DEFAULT_SOURCE_UPDATE_RULES
from ..api_models import (
    GapReportRequest,
    UpsertHealthRequest,
    UpsertUsageRequest,
)
from ..connector_coverage_matrix import build_coverage_matrix, build_source_alerts
from ..financial_source_catalog import financial_data_source_catalog_payload
from ..gap_report import generate_market_data_gap_report, render_gap_report_markdown
from ..retirement_engine import RecommendationType, compute_recommendations
from ..source_health import (
    SourceHealth,
    SourceHealthError,
    SourceUsageDaily,
)

if TYPE_CHECKING:
    from ..runtime import SourceIngestionRuntime


def create_observability_router(runtime: SourceIngestionRuntime) -> APIRouter:
    router = APIRouter(tags=["source-observability"])

    @router.get("/api/source-ingest/health")
    def list_source_health(source_kind: str | None = None) -> dict[str, Any]:
        """List source health records, optionally filtered by source_kind."""
        records = runtime.source_health_store.list(source_kind=source_kind)
        return {
            "health_records": [r.to_dict() for r in records],
            "count": len(records),
        }

    @router.get("/api/source-ingest/health/{source_id}")
    def get_source_health(source_id: str) -> dict[str, Any]:
        health = runtime.source_health_store.get(source_id)
        if health is None:
            raise HTTPException(status_code=404, detail=f"No health record for source: {source_id}")
        return health.to_dict()

    @router.put("/api/source-ingest/health/{source_id}", status_code=200)
    def upsert_source_health(source_id: str, request: UpsertHealthRequest) -> dict[str, Any]:
        if request.source_id != source_id:
            raise HTTPException(status_code=400, detail="source_id in body must match path parameter")
        try:
            health = SourceHealth.from_dict(request.model_dump())
            runtime.source_health_store.upsert(health)
            return health.to_dict()
        except SourceHealthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/source-ingest/usage")
    def list_source_usage(
        source_id: str | None = None,
        source_kind: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """List daily usage records, optionally filtered."""
        records = runtime.source_usage_store.list(source_id=source_id, source_kind=source_kind, date=date)
        return {
            "usage_records": [r.to_dict() for r in records],
            "count": len(records),
        }

    @router.post("/api/source-ingest/usage", status_code=201)
    def upsert_source_usage(request: UpsertUsageRequest) -> dict[str, Any]:
        """Upsert a daily usage record for a source."""
        try:
            record = SourceUsageDaily.from_dict(request.model_dump())
            runtime.source_usage_store.upsert(record)
            return record.to_dict()
        except SourceHealthError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.get("/api/source-ingest/health/{source_id}/usage-aggregate")
    def get_source_usage_aggregate(source_id: str, days: int = 30) -> dict[str, Any]:
        """Aggregate usage for one source over the most recent N days."""
        return runtime.source_usage_store.aggregate_for_source(source_id, days=max(1, min(days, 365)))

    @router.get("/api/source-ingest/retirement-recommendations")
    def list_retirement_recommendations(
        source_kind: str | None = None,
        low_usage_threshold: int = 5,
        high_failure_threshold: float = 0.5,
        high_cost_threshold_30d: float = 1000.0,
        low_yield_threshold: int = 1,
        observation_window_days: int = 30,
    ) -> dict[str, Any]:
        """Compute retirement recommendations for all tracked sources.

        Recommendations are pure computations over stored health and usage data.
        They do not mutate any state. To act on a recommendation, create a
        SourceChangeProposal through /api/source-change-proposals.
        """
        health_records = runtime.source_health_store.list(source_kind=source_kind)
        recommendations = compute_recommendations(
            health_records,
            lambda sid: runtime.source_usage_store.aggregate_for_source(sid),
            low_usage_threshold=low_usage_threshold,
            high_failure_threshold=high_failure_threshold,
            high_cost_threshold_30d=high_cost_threshold_30d,
            low_yield_threshold=low_yield_threshold,
            observation_window_seconds=observation_window_days * 86400,
        )
        return {
            "recommendations": [r.to_dict() for r in recommendations],
            "count": len(recommendations),
            "summary": {
                rt.value: sum(1 for r in recommendations if r.recommendation == rt)
                for rt in RecommendationType
            },
        }

    @router.get("/api/source-ingest/health-usage-snapshot")
    def get_health_usage_snapshot() -> dict[str, Any]:
        """Composite snapshot of source health, usage aggregates, and retirement recommendations.

        Designed to be consumed by the BFF ops surface without requiring
        multiple round-trips. Returns health records enriched with their
        30-day usage aggregate and computed recommendation.
        """
        health_records = runtime.source_health_store.list()
        recommendations = compute_recommendations(
            health_records,
            lambda sid: runtime.source_usage_store.aggregate_for_source(sid),
        )
        rec_map = {r.source_id: r for r in recommendations}

        enriched: list[dict[str, Any]] = []
        for health in health_records:
            rec = rec_map.get(health.source_id)
            usage = runtime.source_usage_store.aggregate_for_source(health.source_id)
            enriched.append({
                "health": health.to_dict(),
                "usage_aggregate_30d": usage,
                "recommendation": rec.to_dict() if rec else None,
            })

        return {
            "source_count": len(enriched),
            "sources": enriched,
            "recommendation_summary": {
                rt.value: sum(1 for r in recommendations if r.recommendation == rt)
                for rt in RecommendationType
            },
        }

    @router.get("/api/source-ingest/coverage-matrix")
    def source_coverage_matrix() -> dict[str, Any]:
        """Coverage matrix: planned financial-catalog connectors vs configured runtime connectors."""
        catalog = financial_data_source_catalog_payload()
        catalog_entries = catalog["entries"]

        configured_by_id = {config.connector.connector_id: config for config in runtime.connector_store.list_configs()}
        configured_ids = set(configured_by_id)

        health_by_id: dict[str, Any] = {h.source_id: h.to_dict() for h in runtime.source_health_store.list()}
        lifecycle_by_id: dict[str, str] = {
            cid: configured_by_id[cid].connector.status.value
            for cid in configured_ids
        }

        return build_coverage_matrix(
            catalog_entries=catalog_entries,
            configured_connector_ids=configured_ids,
            health_by_connector_id=health_by_id,
            lifecycle_by_connector_id=lifecycle_by_id,
        )

    @router.get("/api/source-ingest/alerts")
    def list_source_alerts(include_missing: bool = True) -> dict[str, Any]:
        """List sources that require operator attention."""
        health_records = [h.to_dict() for h in runtime.source_health_store.list()]
        configured_ids = {config.connector.connector_id for config in runtime.connector_store.list_configs()}
        catalog_entries = financial_data_source_catalog_payload()["entries"]

        alerts = build_source_alerts(
            catalog_entries=catalog_entries,
            configured_connector_ids=configured_ids,
            health_records=health_records,
            include_missing=include_missing,
        )

        summary: dict[str, int] = {}
        for alert in alerts:
            at = str(alert.get("alert_type") or "unknown")
            summary[at] = summary.get(at, 0) + 1

        return {
            "alert_count": len(alerts),
            "alerts": alerts,
            "summary": summary,
        }

    @router.post("/api/source-ingest/gap-report")
    def generate_gap_report(request: GapReportRequest) -> dict[str, Any]:
        """Generate a market-data gap report for the given active-universe members and date."""
        try:
            rules = [rule.to_domain() for rule in request.rules] if request.rules else DEFAULT_SOURCE_UPDATE_RULES
            health_records = runtime.source_health_store.list()
            report = generate_market_data_gap_report(
                members=[member.to_domain() for member in request.members],
                rules=rules,
                health_records=health_records,
                run_date=request.run_date,
                default_max_symbols_per_job=request.default_max_symbols_per_job,
            )
            payload: dict[str, Any] = {"report": report}
            if request.render_markdown:
                payload["markdown"] = render_gap_report_markdown(report)
            return payload
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router

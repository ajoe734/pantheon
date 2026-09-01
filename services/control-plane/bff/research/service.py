"""Typed read service for the Research router.

The service deliberately depends on the narrow
``ResearchKnowledgeSourcePort``-compatible interface prepared by
``ACG-RS-RESEARCH-SOURCE-20260828``.  It neither imports ``main`` nor
recreates its former process-local read overlays, so it is safe to mount from
the BFF composition root in a later cutover.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


PageSlice = Callable[[List[Dict[str, Any]], Optional[str], int], Tuple[List[Dict[str, Any]], Optional[str]]]
SnapshotMeta = Callable[[str], Dict[str, Any]]
UtcNow = Callable[[], str]

_ANALYSIS_STATUSES = frozenset({"queued", "running", "completed", "failed"})
_DATE_RANGES = frozenset({"24h", "7d", "30d", "90d"})
_ARTIFACT_STATUSES = frozenset({"pending", "sealed", "superseded", "failed"})


class ResearchValidationError(ValueError):
    """A client input error that the HTTP adapter turns into a BFF error."""

    def __init__(
        self,
        message: str,
        *,
        field: str,
        status_code: int = 422,
        error_code: str = "VALIDATION_FAILED",
    ) -> None:
        super().__init__(message)
        self.field = field
        self.status_code = status_code
        self.error_code = error_code


class ResearchNotFoundError(LookupError):
    """Raised when a durable typed record is absent."""

    def __init__(self, label: str, entity_id: str) -> None:
        super().__init__(f"{label} {entity_id} does not exist")
        self.label = label
        self.entity_id = entity_id


@dataclass(frozen=True)
class ResearchRouterService:
    """Read-only projections shared by typed and BFF compatibility routes."""

    port_getter: Callable[[], Any]
    utc_now: UtcNow
    snapshot_meta: SnapshotMeta
    page_slice: PageSlice

    def _port(self) -> Any:
        return self.port_getter()

    def _surface(self, dataset: str, *, snapshot_at: str, has_data: bool) -> Dict[str, Any]:
        port = self._port()
        source_fn = getattr(port, "dataset_source", None)
        source = str(source_fn(dataset) or "missing") if callable(source_fn) else "missing"
        surface_fn = getattr(port, "dataset_surface_status", None)
        if callable(surface_fn):
            return dict(surface_fn(dataset, snapshot_at=snapshot_at, source=source, has_data=has_data))
        if source in {"missing", "unavailable"} or not has_data:
            return {
                "status": "unavailable",
                "source": source,
                "message": f"{dataset} has no readable source records.",
            }
        return {"status": "ok", "source": source}

    @staticmethod
    def _validate_optional(value: Optional[str], *, allowed: frozenset[str], field: str) -> Optional[str]:
        if value in (None, ""):
            return None
        normalized = str(value).strip().lower()
        if normalized not in allowed:
            raise ResearchValidationError(
                f"{field} must be one of {sorted(allowed)}", field=field
            )
        return normalized

    @staticmethod
    def _validate_statuses(status_csv: Optional[str]) -> Optional[List[str]]:
        if status_csv in (None, ""):
            return None
        statuses = [str(value).strip().lower() for value in str(status_csv).split(",") if str(value).strip()]
        if not statuses:
            return None
        invalid = [value for value in statuses if value not in _ANALYSIS_STATUSES]
        if invalid:
            raise ResearchValidationError(
                f"status must contain only values from {sorted(_ANALYSIS_STATUSES)}",
                field="status",
            )
        return statuses

    def list_analyses(
        self,
        *,
        ticket_id: Optional[str] = None,
        experiment_id: Optional[str] = None,
        status: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        detail_path: str = "/api/v1/research/analyses",
    ) -> Dict[str, Any]:
        statuses = self._validate_statuses(status)
        normalized_date_range = self._validate_optional(
            date_range, allowed=_DATE_RANGES, field="date_range"
        )
        snapshot_at = self.utc_now()
        port = self._port()
        records = list(
            port.list_research_analyses(
                ticket_id=ticket_id,
                experiment_id=experiment_id,
                statuses=statuses,
                date_range=normalized_date_range,
            )
            or []
        )
        surface = self._surface(
            "research_analyses", snapshot_at=snapshot_at, has_data=bool(records)
        )
        if surface.get("status") == "unavailable":
            page_items: List[Dict[str, Any]] = []
            next_page_token = None
            total = 0
        else:
            total = len(records)
            page_items, next_page_token = self.page_slice(records, page_token, page_size)
        items = [self._analysis_summary_with_links(item, detail_path=detail_path) for item in page_items]
        meta = self.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"analysis_results": surface}
        return {
            "data": items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": meta,
        }

    def get_analysis(
        self,
        analysis_id: str,
        *,
        detail_path: str = "/api/v1/research/analyses",
    ) -> Dict[str, Any]:
        clean_id = str(analysis_id or "").strip()
        record = self._port().get_research_analysis(clean_id)
        if not record:
            raise ResearchNotFoundError("Research analysis", clean_id)
        snapshot_at = self.utc_now()
        payload = dict(record)
        ticket_ref = str(payload.get("ticket_id") or "")
        experiment_ref = payload.get("experiment_id")
        payload["links"] = {
            "self": f"{detail_path}/{clean_id}",
            "workbench_detail": f"/research/analyze/{clean_id}",
            "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
            "linked_experiment_detail": (
                f"/research/experiments/{experiment_ref}" if experiment_ref else None
            ),
        }
        meta = self.snapshot_meta(snapshot_at)
        meta["surfaces"] = {
            "analysis_results": self._surface(
                "research_analyses", snapshot_at=snapshot_at, has_data=True
            )
        }
        payload["meta"] = meta
        return payload

    @staticmethod
    def _analysis_summary_with_links(
        item: Dict[str, Any], *, detail_path: str
    ) -> Dict[str, Any]:
        payload = dict(item)
        analysis_id = str(payload.get("analysis_id") or "")
        ticket_ref = str(payload.get("ticket_id") or "")
        payload["links"] = {
            "self": f"{detail_path}/{analysis_id}",
            "workbench_detail": f"/research/analyze/{analysis_id}",
            "linked_ticket_detail": f"/research/tickets/{ticket_ref}",
        }
        return payload

    def list_artifacts(
        self,
        *,
        artifact_type: Optional[str] = None,
        status: Optional[str] = None,
        tags: Optional[str] = None,
        author: Optional[str] = None,
        date_range: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        normalized_status = self._validate_optional(
            status, allowed=_ARTIFACT_STATUSES, field="status"
        )
        normalized_date_range = self._validate_optional(
            date_range, allowed=_DATE_RANGES, field="date_range"
        )
        tag_values = [value.strip() for value in str(tags or "").split(",") if value.strip()] or None
        snapshot_at = self.utc_now()
        records = list(
            self._port().list_research_artifacts(
                artifact_type=artifact_type,
                status=normalized_status,
                tags=tag_values,
                author=author,
                date_range=normalized_date_range,
            )
            or []
        )
        surface = self._surface(
            "research_artifacts", snapshot_at=snapshot_at, has_data=bool(records)
        )
        if surface.get("status") == "unavailable":
            page_items: List[Dict[str, Any]] = []
            next_page_token = None
            total = 0
        else:
            total = len(records)
            page_items, next_page_token = self.page_slice(records, page_token, page_size)
        meta = self.snapshot_meta(snapshot_at)
        meta["surfaces"] = {"artifact_list": surface}
        return {
            "artifacts": [dict(item) for item in page_items],
            "next_page_token": next_page_token,
            "total_count": total,
            "meta": meta,
        }

    def get_artifact(self, artifact_id: str) -> Dict[str, Any]:
        clean_id = str(artifact_id or "").strip()
        record = self._port().get_research_artifact(clean_id)
        if not record:
            raise ResearchNotFoundError("Research artifact", clean_id)
        snapshot_at = self.utc_now()
        payload = dict(record)
        meta = self.snapshot_meta(snapshot_at)
        meta["surfaces"] = {
            "artifact_detail": self._surface(
                "research_artifacts", snapshot_at=snapshot_at, has_data=True
            )
        }
        payload["meta"] = meta
        return payload

    def compare_artifacts(self, artifact_ids: str) -> Dict[str, Any]:
        requested_ids = [value.strip() for value in str(artifact_ids or "").split(",") if value.strip()]
        if not 2 <= len(requested_ids) <= 4:
            raise ResearchValidationError(
                "artifact_ids must include between 2 and 4 artifact ids",
                field="artifact_ids",
                status_code=400,
            )
        port = self._port()
        artifacts = []
        for artifact_id in requested_ids:
            artifact = port.get_research_artifact(artifact_id)
            if not artifact:
                raise ResearchNotFoundError("Research artifact", artifact_id)
            artifacts.append(artifact)
        non_comparable = [
            {
                "artifact_id": artifact.get("artifact_id"),
                "status": artifact.get("status"),
                "reason": "Only sealed and superseded artifacts may be compared.",
            }
            for artifact in artifacts
            if not (artifact.get("allowedActions") or {}).get("canCompare")
        ]
        if non_comparable:
            raise ResearchValidationError(
                "One or more artifacts cannot be compared",
                field="artifact_status",
                error_code="OPERATION_NOT_ALLOWED",
            )
        snapshot_at = self.utc_now()
        payload = dict(port.compare_research_artifacts(requested_ids) or {})
        meta = self.snapshot_meta(snapshot_at)
        meta["computed_at"] = snapshot_at
        meta["surfaces"] = {
            "artifact_compare": self._surface(
                "research_artifacts", snapshot_at=snapshot_at, has_data=True
            )
        }
        payload["meta"] = meta
        return payload

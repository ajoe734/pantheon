"""Replayable search reference store for governed evidence and structured alpha search."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from services.knowledge.evidence.models import EvidenceValidationError


@dataclass(frozen=True)
class SearchIndexSnapshot:
    request_id: str
    trace_id: str
    filters_applied: Mapping[str, Any]
    result_refs: list[dict[str, Any]]
    created_at: str
    schema_version: str = "governed_search_refs.v2"
    retrieval_mode: str = "keyword"
    rejected_items_count: int = 0
    rejected_by_reason: Mapping[str, int] = field(default_factory=dict)
    fingerprints: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.request_id).strip():
            raise EvidenceValidationError("search snapshot request_id is required")
        if not str(self.trace_id).strip():
            raise EvidenceValidationError("search snapshot trace_id is required")
        object.__setattr__(self, "filters_applied", dict(self.filters_applied or {}))
        object.__setattr__(self, "result_refs", [dict(ref) for ref in (self.result_refs or ())])
        object.__setattr__(self, "rejected_by_reason", dict(self.rejected_by_reason or {}))
        object.__setattr__(self, "fingerprints", dict(self.fingerprints or {}))
        for ref in self.result_refs:
            for field_name in ("result_id", "evidence_bundle_id", "citations", "matched_items"):
                if field_name not in ref:
                    raise EvidenceValidationError(f"search result ref missing {field_name}")
            if "answer_context" in ref or "raw_payload" in ref:
                raise EvidenceValidationError("search result refs must not persist raw answer payloads")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "trace_id": self.trace_id,
            "retrieval_mode": self.retrieval_mode,
            "filters_applied": dict(self.filters_applied),
            "result_refs": [dict(ref) for ref in self.result_refs],
            "rejected_items_count": self.rejected_items_count,
            "rejected_by_reason": dict(self.rejected_by_reason),
            "fingerprints": dict(self.fingerprints),
            "created_at": self.created_at,
        }

    @classmethod
    def from_response(cls, response: Any) -> "SearchIndexSnapshot":
        return cls(
            request_id=response.request_id,
            trace_id=response.trace_id,
            retrieval_mode=getattr(response, "retrieval_mode", "keyword"),
            filters_applied=dict(response.filters_applied),
            result_refs=[
                {
                    "result_id": result.result_id,
                    "evidence_bundle_id": result.evidence_bundle_id,
                    "citations": list(result.citations),
                    "matched_items": list(result.matched_items),
                    "relevance_score": result.relevance_score,
                }
                for result in response.results
            ],
            rejected_items_count=getattr(response, "rejected_items_count", 0),
            rejected_by_reason=dict(getattr(response, "rejected_by_reason", {})),
            fingerprints=dict(getattr(response, "fingerprints", {})),
            created_at=response.created_at,
        )

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SearchIndexSnapshot":
        return cls(
            request_id=str(data["request_id"]),
            trace_id=str(data["trace_id"]),
            filters_applied=dict(data.get("filters_applied", {})),
            result_refs=[dict(ref) for ref in data.get("result_refs", [])],
            created_at=str(data["created_at"]),
            schema_version=str(data.get("schema_version", "governed_search_refs.v2")),
            retrieval_mode=str(data.get("retrieval_mode", "keyword")),
            rejected_items_count=int(data.get("rejected_items_count", 0)),
            rejected_by_reason=dict(data.get("rejected_by_reason", {})),
            fingerprints=dict(data.get("fingerprints", {})),
        )


class JsonlSearchIndexStore:
    """Append-only search refs store; replay returns latest snapshot per request."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._snapshots: dict[str, SearchIndexSnapshot] = {}
        self.reload()

    def append_snapshot(self, snapshot: SearchIndexSnapshot) -> SearchIndexSnapshot:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
        self._snapshots[snapshot.request_id] = snapshot
        return snapshot

    def reload(self) -> None:
        self._snapshots = {}
        if not self.path.exists():
            return
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                snapshot = SearchIndexSnapshot.from_dict(json.loads(line))
            except json.JSONDecodeError as exc:
                raise EvidenceValidationError(f"Invalid search index JSONL at {self.path}:{line_no}: {exc.msg}") from exc
            self._snapshots[snapshot.request_id] = snapshot

    def get_snapshot(self, request_id: str) -> SearchIndexSnapshot | None:
        return self._snapshots.get(request_id)

    def list_snapshots(self) -> list[SearchIndexSnapshot]:
        return list(self._snapshots.values())

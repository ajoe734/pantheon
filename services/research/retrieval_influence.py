"""Lineage and retrieval influence proof tracking for memory-to-research inspiration."""
from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class ResearchRetrievalInfluenceError(ValueError):
    """Raised when retrieval influence record validation fails."""


VALID_INFLUENCE_STATES = {
    "confirmed_influence",
    "no_influence",
    "contradicted",
    "influence_unknown",
}


@dataclass
class ResearchRetrievalInfluenceRecord:
    retrieval_id: str
    task_id: str
    run_id: str
    persona_id: str
    query_snapshot: Dict[str, Any]
    selected_memory_refs: List[str]
    selected_evidence_refs: List[str] = field(default_factory=list)
    counter_evidence_query: Optional[str] = None
    counter_evidence_results: List[Dict[str, Any]] = field(default_factory=list)
    influence_assessment: str = ""
    influence_weight: Optional[float] = None
    influence_state: str = "influence_unknown"
    model_ranker_version: str = "v1.0"
    resulting_seed_ref: Optional[str] = None
    created_at: str = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.retrieval_id:
            raise ResearchRetrievalInfluenceError("retrieval_id is required")
        if not self.task_id or not self.run_id:
            raise ResearchRetrievalInfluenceError("task_id and run_id are required")
        if not self.persona_id:
            raise ResearchRetrievalInfluenceError("persona_id is required")
        if self.influence_state not in VALID_INFLUENCE_STATES:
            raise ResearchRetrievalInfluenceError(
                f"Invalid influence_state: {self.influence_state!r}. Must be one of {sorted(VALID_INFLUENCE_STATES)}."
            )
        if self.influence_weight is not None:
            try:
                weight = float(self.influence_weight)
                if not (0.0 <= weight <= 1.0):
                    raise ValueError
                self.influence_weight = round(weight, 4)
            except (ValueError, TypeError) as exc:
                raise ResearchRetrievalInfluenceError("influence_weight must be a float between 0.0 and 1.0") from exc

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ResearchRetrievalInfluenceRecord:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


def project_lineage_inspiration_edge(
    edge: Dict[str, Any],
    target_artifact_id: str,
) -> Optional[Dict[str, Any]]:
    """Convert a raw lineage edge to an inspiration edge without synthesizing constant 1.0 weight."""
    from_id = str(edge.get("from_artifact_id") or "").strip()
    to_id = str(edge.get("to_artifact_id") or "").strip()
    source_artifact_id = from_id if to_id == target_artifact_id else to_id
    relationship_type = str(edge.get("edge_type") or edge.get("relationship") or "").strip()
    if not source_artifact_id or not relationship_type:
        return None

    raw_weight = edge.get("influence_weight")
    if raw_weight is not None:
        try:
            influence_weight: Optional[float] = round(float(raw_weight), 4)
            influence_state = str(edge.get("influence_state") or "confirmed_influence")
        except (ValueError, TypeError):
            influence_weight = None
            influence_state = "influence_unknown"
    else:
        influence_weight = None
        influence_state = str(edge.get("influence_state") or "influence_unknown")

    return {
        "lineage_edge_id": edge.get("id"),
        "source_artifact_id": source_artifact_id,
        "relationship_type": relationship_type,
        "influence_weight": influence_weight,
        "influence_state": influence_state,
    }


class ResearchRetrievalInfluenceStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "research_retrieval_influence.json"
        self._lock = threading.Lock()
        self._records: Dict[str, ResearchRetrievalInfluenceRecord] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        text = self.path.read_text(encoding="utf-8").strip()
        if not text:
            return
        try:
            raw = json.loads(text)
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(v, dict):
                        self._records[k] = ResearchRetrievalInfluenceRecord.from_dict(v)
        except Exception:
            pass

    def _save(self) -> None:
        payload = {k: v.to_dict() for k, v in self._records.items()}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def create_record(self, record: ResearchRetrievalInfluenceRecord) -> ResearchRetrievalInfluenceRecord:
        with self._lock:
            self._records[record.retrieval_id] = record
            self._save()
            return record

    def get_record(self, retrieval_id: str) -> Optional[ResearchRetrievalInfluenceRecord]:
        with self._lock:
            return self._records.get(retrieval_id)

    def list_records(
        self,
        run_id: Optional[str] = None,
        task_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[ResearchRetrievalInfluenceRecord]:
        with self._lock:
            records = list(self._records.values())
        if run_id:
            records = [r for r in records if r.run_id == run_id]
        if task_id:
            records = [r for r in records if r.task_id == task_id]
        if persona_id:
            records = [r for r in records if r.persona_id == persona_id]
        return sorted(records, key=lambda r: r.created_at, reverse=True)


_default_influence_store: Optional[ResearchRetrievalInfluenceStore] = None
_influence_lock = threading.Lock()


def build_research_retrieval_influence_store(data_dir: str | Path) -> ResearchRetrievalInfluenceStore:
    return ResearchRetrievalInfluenceStore(data_dir)


def get_influence_store() -> ResearchRetrievalInfluenceStore:
    global _default_influence_store
    with _influence_lock:
        if _default_influence_store is None:
            data_dir = os.getenv("RESEARCH_ORCHESTRATOR_DATA_DIR", "/tmp/pantheon/research-orchestrator")
            _default_influence_store = ResearchRetrievalInfluenceStore(data_dir)
        return _default_influence_store


def reset_influence_store() -> None:
    global _default_influence_store
    with _influence_lock:
        _default_influence_store = None

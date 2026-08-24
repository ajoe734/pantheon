"""Transactional research memory outbox for reviewed evidence-to-memory writeback."""
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


class ResearchMemoryEligibilityError(ValueError):
    """Raised when research memory writeback quality, license, or review gates fail."""


STUB_ADAPTERS = {"stub", "handoff_only", "manual"}
PROHIBITED_LICENSE_TERMS = {"prohibited", "no_derivative", "no_derivatives"}
PROHIBITED_ALLOWED_USE_TERMS = {"no_derivative", "no_derivatives", "no_memory", "raw_only"}


def _first_text(*values: Any) -> Optional[str]:
    for value in values:
        if value not in (None, "", [], {}):
            text = str(value).strip()
            if text:
                return text
    return None


def _evidence_source_refs(*values: Any) -> List[Any]:
    refs: List[Any] = []
    for value in values:
        if value in (None, "", [], {}):
            continue
        items = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, dict):
                if item not in refs:
                    refs.append(item)
            elif isinstance(item, str) and item.strip():
                if item.strip() not in refs:
                    refs.append(item.strip())
    return refs


def validate_research_memory_writeback_eligibility(
    run: Dict[str, Any],
    artifact: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify terminal completed status, evidence eligibility, lineage, review, and license."""
    params = params or {}
    run_status = str(run.get("status") or "").strip().lower()
    if run_status != "completed":
        raise ResearchMemoryEligibilityError(
            f"Run status must be 'completed' for memory writeback (current: '{run_status}')"
        )

    # 1. Artifact Quality Check
    storage_status = str(
        artifact.get("storage_status")
        or (artifact.get("quality") or {}).get("storage_status")
        or ""
    ).strip().lower()
    if storage_status not in {"resolvable", "external"}:
        raise ResearchMemoryEligibilityError(
            f"Artifact storage status '{storage_status}' is not eligible for memory writeback"
        )

    checksum_status = str(
        artifact.get("checksum_status")
        or (artifact.get("quality") or {}).get("checksum_status")
        or ""
    ).strip().lower()
    if checksum_status != "valid":
        raise ResearchMemoryEligibilityError(
            f"Artifact checksum status '{checksum_status}' is not valid for memory writeback"
        )

    # 2. Evidence and Dataset Lineage Check
    evidence_refs = _evidence_source_refs(
        artifact.get("source_evidence_refs"),
        (artifact.get("metadata") or {}).get("source_evidence_refs"),
        (artifact.get("metadata") or {}).get("evidence_refs"),
        (artifact.get("registry_hints") or {}).get("source_evidence_refs"),
        (artifact.get("quality") or {}).get("source_evidence_refs"),
        params.get("evidence_refs"),
    )
    if not evidence_refs:
        raise ResearchMemoryEligibilityError(
            "Research artifact must carry source_evidence_refs or evidence_refs for memory writeback"
        )

    dataset_refs = _evidence_source_refs(
        artifact.get("source_dataset_refs"),
        (artifact.get("metadata") or {}).get("source_dataset_refs"),
        (artifact.get("registry_hints") or {}).get("source_dataset_refs"),
        run.get("source_dataset_refs"),
        run.get("dataset_version_id"),
        params.get("dataset_refs"),
    )
    if not dataset_refs:
        raise ResearchMemoryEligibilityError(
            "Research artifact must carry dataset lineage (source_dataset_refs or dataset_version_id)"
        )

    # 3. Publication / Reviewer Gate Policy Check
    artifact_state = str(
        params.get("artifact_state")
        or (artifact.get("registry_writeback") or {}).get("artifact_state")
        or (artifact.get("registry_hints") or {}).get("artifact_state")
        or (artifact.get("metadata") or {}).get("artifact_state")
        or artifact.get("artifact_state")
        or "draft"
    ).strip().lower()
    if artifact_state == "draft" and (artifact.get("registry_hints") or {}).get("artifact_state"):
        artifact_state = str((artifact.get("registry_hints") or {}).get("artifact_state")).strip().lower()

    if artifact_state not in {"candidate", "reviewed", "published"}:
        raise ResearchMemoryEligibilityError(
            f"Artifact state '{artifact_state}' is not eligible (must be candidate, reviewed, or published)"
        )

    adapter = str(run.get("adapter") or "").strip().lower()
    producer_mode = str(
        artifact.get("producer_mode")
        or (artifact.get("quality") or {}).get("producer_mode")
        or adapter
    ).strip().lower()
    if producer_mode in STUB_ADAPTERS:
        # Check if artifact has explicit candidate qualification
        eligible_flag = (
            artifact.get("evidence_eligible")
            or (artifact.get("quality") or {}).get("evidence_eligible")
            or (artifact.get("metadata") or {}).get("evidence_eligible")
            or (artifact.get("registry_hints") or {}).get("evidence_eligible")
            or params.get("evidence_eligible")
        )
        if not bool(eligible_flag):
            raise ResearchMemoryEligibilityError(
                "Stub adapter output without candidate-grade evidence is ineligible for memory writeback"
            )

    # 4. License and Allowed-Use Check
    metadata = artifact.get("metadata") or {}
    license_scope = _first_text(
        params.get("license_scope"),
        metadata.get("license_scope"),
        metadata.get("license"),
        (artifact.get("registry_hints") or {}).get("license_scope"),
        "permissive",
    )
    if license_scope and license_scope.lower().strip() in PROHIBITED_LICENSE_TERMS:
        raise ResearchMemoryEligibilityError(
            f"License scope '{license_scope}' prohibits derived memory writeback"
        )

    allowed_use_raw = params.get("allowed_use") or metadata.get("allowed_use") or []
    allowed_use = [str(u).strip() for u in (allowed_use_raw if isinstance(allowed_use_raw, list) else [allowed_use_raw]) if str(u).strip()]
    if allowed_use and any(u.lower().strip() in PROHIBITED_ALLOWED_USE_TERMS for u in allowed_use):
        raise ResearchMemoryEligibilityError(
            "Allowed-use policy prohibits derived memory writeback"
        )

    return {
        "eligible": True,
        "run_id": run["run_id"],
        "task_id": run["task_id"],
        "artifact_id": artifact.get("artifact_id"),
        "evidence_refs": evidence_refs,
        "dataset_refs": dataset_refs,
        "license_scope": license_scope,
        "allowed_use": allowed_use or ["research", "derived_memory"],
        "artifact_state": artifact_state,
        "validated_at": utc_now(),
    }


@dataclass
class ResearchMemoryOutboxRecord:
    outbox_id: str
    run_id: str
    task_id: str
    artifact_id: Optional[str]
    source_event_type: str
    source_event_id: str
    sponsor_persona_id: str
    summary: str
    headline: str
    confidence: float
    evidence_refs: List[Any]
    dataset_refs: List[str]
    license_scope: Optional[str]
    allowed_use: List[str]
    supersedes: List[str]
    contradicts: List[str]
    expires_at: Optional[str]
    trace_id: str
    status: str = "pending"  # pending, in_flight, delivered, failed, dead_letter
    retry_count: int = 0
    max_retries: int = 5
    last_error: Optional[str] = None
    last_attempt_at: Optional[str] = None
    delivered_at: Optional[str] = None
    receipt: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ResearchMemoryOutboxRecord:
        known = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


class ResearchMemoryOutboxStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.data_dir / "research_memory_outbox.json"
        self._lock = threading.Lock()
        self._records: Dict[str, ResearchMemoryOutboxRecord] = {}
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
                        self._records[k] = ResearchMemoryOutboxRecord.from_dict(v)
        except Exception:
            pass

    def _save(self) -> None:
        payload = {k: v.to_dict() for k, v in self._records.items()}
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def create_record(self, record: ResearchMemoryOutboxRecord) -> ResearchMemoryOutboxRecord:
        with self._lock:
            existing = self._find_by_source_unlocked(record.source_event_type, record.source_event_id)
            if existing:
                return existing
            self._records[record.outbox_id] = record
            self._save()
            return record

    def get_record(self, outbox_id: str) -> Optional[ResearchMemoryOutboxRecord]:
        with self._lock:
            return self._records.get(outbox_id)

    def _find_by_source_unlocked(self, source_event_type: str, source_event_id: str) -> Optional[ResearchMemoryOutboxRecord]:
        for rec in self._records.values():
            if rec.source_event_type == source_event_type and rec.source_event_id == source_event_id:
                return rec
        return None

    def find_by_source_event(self, source_event_type: str, source_event_id: str) -> Optional[ResearchMemoryOutboxRecord]:
        with self._lock:
            return self._find_by_source_unlocked(source_event_type, source_event_id)

    def list_records(self, status: Optional[str] = None, run_id: Optional[str] = None) -> List[ResearchMemoryOutboxRecord]:
        with self._lock:
            records = list(self._records.values())
        if status:
            records = [r for r in records if r.status.lower() == status.lower()]
        if run_id:
            records = [r for r in records if r.run_id == run_id]
        return sorted(records, key=lambda r: r.created_at, reverse=True)

    def update_record(self, record: ResearchMemoryOutboxRecord) -> ResearchMemoryOutboxRecord:
        with self._lock:
            record.updated_at = utc_now()
            self._records[record.outbox_id] = record
            self._save()
            return record


_default_outbox_store: Optional[ResearchMemoryOutboxStore] = None
_outbox_lock = threading.Lock()


def build_research_memory_outbox_store(data_dir: str | Path) -> ResearchMemoryOutboxStore:
    return ResearchMemoryOutboxStore(data_dir)


def get_outbox_store() -> ResearchMemoryOutboxStore:
    global _default_outbox_store
    with _outbox_lock:
        if _default_outbox_store is None:
            data_dir = os.getenv("RESEARCH_ORCHESTRATOR_DATA_DIR", "/tmp/pantheon/research-orchestrator")
            _default_outbox_store = ResearchMemoryOutboxStore(data_dir)
        return _default_outbox_store


def reset_outbox_store() -> None:
    global _default_outbox_store
    with _outbox_lock:
        _default_outbox_store = None

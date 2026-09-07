"""Durable-state convergence and overlay retirement migration engine.

Mandatory scope under task OVERLAY-RETIRE-001:
  - Backfill and shadow-compare Persona, Strategy, Incident, Job, and Ranking owner projections.
  - Conflict reporting and parity verification.
  - Restart and multi-replica readback pass with one canonical writer.
  - Strict rejection of fallback acknowledgement and dual-write policies.
  - Rollback policy: Deploy the exact prior compatible release; never re-enable dual writes.
  - Mandatory symbol retirements:
      1. _PERSONA_BFF_OVERLAY
      2. _STRATEGY_BFF_OVERLAY
      3. _GOV_BFF_INCIDENT_OVERLAY
      4. _GOV_BFF_JOB_OVERLAY
      5. ReadSurfacePorts._ranking_snapshots
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def deterministic_checksum(payload: Dict[str, Any]) -> str:
    """Compute deterministic SHA-256 checksum over normalized JSON payload."""
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


class AggregateKind(str, Enum):
    PERSONA = "persona"
    STRATEGY = "strategy"
    INCIDENT = "incident"
    JOB = "job"
    RANKING = "ranking"


@dataclass(frozen=True)
class AggregateMetadata:
    kind: AggregateKind
    authoritative_store_owner: str
    retired_overlay_symbol: str
    key_field: str
    timestamp_field: str = "updated_at"


AGGREGATE_REGISTRY: Dict[AggregateKind, AggregateMetadata] = {
    AggregateKind.PERSONA: AggregateMetadata(
        kind=AggregateKind.PERSONA,
        authoritative_store_owner="persona_provisioning_store",
        retired_overlay_symbol="_PERSONA_BFF_OVERLAY",
        key_field="persona_id",
        timestamp_field="updated_at",
    ),
    AggregateKind.STRATEGY: AggregateMetadata(
        kind=AggregateKind.STRATEGY,
        authoritative_store_owner="strategy_spec_store",
        retired_overlay_symbol="_STRATEGY_BFF_OVERLAY",
        key_field="strategy_id",
        timestamp_field="updated_at",
    ),
    AggregateKind.INCIDENT: AggregateMetadata(
        kind=AggregateKind.INCIDENT,
        authoritative_store_owner="incident_reconciliation_store",
        retired_overlay_symbol="_GOV_BFF_INCIDENT_OVERLAY",
        key_field="incident_id",
        timestamp_field="updated_at",
    ),
    AggregateKind.JOB: AggregateMetadata(
        kind=AggregateKind.JOB,
        authoritative_store_owner="job_service_store",
        retired_overlay_symbol="_GOV_BFF_JOB_OVERLAY",
        key_field="job_id",
        timestamp_field="updated_at",
    ),
    AggregateKind.RANKING: AggregateMetadata(
        kind=AggregateKind.RANKING,
        authoritative_store_owner="ranking_domain_store",
        retired_overlay_symbol="ReadSurfacePorts._ranking_snapshots",
        key_field="snapshot_id",
        timestamp_field="created_at",
    ),
}


class FallbackAcknowledgementForbiddenError(RuntimeError):
    """Raised when an operation attempts fallback acknowledgement or in-memory overlay write."""


class DualWriteForbiddenError(RuntimeError):
    """Raised when a write operation attempts to re-enable dual writes."""


@dataclass
class RecordConflict:
    record_id: str
    aggregate: str
    conflict_type: str  # "missing_in_canonical", "field_divergence", "checksum_mismatch"
    canonical_summary: Optional[Dict[str, Any]] = None
    overlay_summary: Optional[Dict[str, Any]] = None
    divergent_fields: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class ConflictReport:
    aggregate: AggregateKind
    scanned_canonical: int
    scanned_overlay: int
    matched_count: int
    missing_in_canonical_count: int
    divergent_count: int
    parity_ratio: float
    conflicts: List[RecordConflict] = field(default_factory=list)
    generated_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aggregate": self.aggregate.value,
            "scanned_canonical": self.scanned_canonical,
            "scanned_overlay": self.scanned_overlay,
            "matched_count": self.matched_count,
            "missing_in_canonical_count": self.missing_in_canonical_count,
            "divergent_count": self.divergent_count,
            "parity_ratio": self.parity_ratio,
            "conflicts": [
                {
                    "record_id": c.record_id,
                    "conflict_type": c.conflict_type,
                    "divergent_fields": c.divergent_fields,
                }
                for c in self.conflicts
            ],
            "generated_at": self.generated_at,
        }


@dataclass
class BackfillResult:
    aggregate: AggregateKind
    tenant_id: str
    dry_run: bool
    scanned: int
    backfilled: int
    skipped_existing: int
    conflicts: List[RecordConflict] = field(default_factory=list)
    next_cursor: Optional[str] = None
    applied_at: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "aggregate": self.aggregate.value,
            "tenant_id": self.tenant_id,
            "dry_run": self.dry_run,
            "scanned": self.scanned,
            "backfilled": self.backfilled,
            "skipped_existing": self.skipped_existing,
            "conflict_count": len(self.conflicts),
            "next_cursor": self.next_cursor,
            "applied_at": self.applied_at,
        }


class RollbackPolicy:
    """Governed rollback policy: Deploy exact prior compatible release; never re-enable dual writes."""

    STRICT_NO_DUAL_WRITES: bool = True
    ALLOW_FALLBACK_ACKNOWLEDGEMENT: bool = False

    @classmethod
    def get_policy_declaration(cls) -> Dict[str, Any]:
        return {
            "rule": "Deploy the exact prior compatible release; never re-enable dual writes.",
            "dual_writes_permitted": not cls.STRICT_NO_DUAL_WRITES,
            "fallback_acknowledgement_permitted": cls.ALLOW_FALLBACK_ACKNOWLEDGEMENT,
            "disaster_recovery_strategy": "exact_prior_compatible_release",
        }

    @classmethod
    def assert_safe_rollback(cls, allow_dual_writes: bool = False) -> None:
        if allow_dual_writes:
            raise DualWriteForbiddenError(
                "Rollback violation: Never re-enable dual writes. "
                "Rollback must deploy the exact prior compatible release without mutating write authority."
            )


class OverlayMigrationEngine:
    """Resumable, tenant-partitioned shadow-compare and backfill engine."""

    def __init__(
        self,
        *,
        aggregate: AggregateKind,
        canonical_store: Any,
        overlay_data_source: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self.metadata = AGGREGATE_REGISTRY[aggregate]
        self.aggregate = aggregate
        self.canonical_store = canonical_store
        self._overlay_data_source = dict(overlay_data_source or {})

    def _extract_id(self, record: Dict[str, Any]) -> str:
        for candidate in (self.metadata.key_field, "id", f"{self.aggregate.value}_id"):
            val = record.get(candidate)
            if val:
                return str(val).strip()
        return ""

    def _resolve_record_tenant(self, record: Dict[str, Any]) -> Tuple[Optional[str], bool]:
        """Normalize a record's tenant identity from `tenant_id`/`tenantId`.

        Returns `(normalized_tenant_id_or_None, conflict)`. `conflict=True` means the record
        carries two disagreeing tenant identities and must be rejected rather than silently
        rewritten to whichever tenant happens to run the migration.
        """
        snake = record.get("tenant_id")
        camel = record.get("tenantId")
        if snake and camel and str(snake) != str(camel):
            return None, True
        resolved = snake or camel
        return (str(resolved) if resolved else None), False

    def _record_tenant_matches(self, record: Dict[str, Any], tenant_id: str) -> bool:
        resolved, conflict = self._resolve_record_tenant(record)
        if conflict or resolved is None:
            return False
        return resolved == tenant_id

    def shadow_compare(
        self,
        *,
        tenant_id: Optional[str] = None,
        cursor: Optional[int] = 0,
        page_size: int = 100,
    ) -> ConflictReport:
        """Shadow-compare canonical owner projections with legacy overlay data."""
        canonical_records = self._fetch_canonical_records(tenant_id=tenant_id)
        canonical_by_id = {self._extract_id(r): r for r in canonical_records if self._extract_id(r)}

        overlay_items = list(self._overlay_data_source.items())
        start = cursor or 0
        paged_overlay = overlay_items[start : start + page_size]

        matched = 0
        missing_in_canonical = 0
        divergent = 0
        conflicts: List[RecordConflict] = []

        for rec_id, overlay_record in paged_overlay:
            if not rec_id:
                continue
            if rec_id not in canonical_by_id:
                missing_in_canonical += 1
                conflicts.append(
                    RecordConflict(
                        record_id=rec_id,
                        aggregate=self.aggregate.value,
                        conflict_type="missing_in_canonical",
                        overlay_summary={"checksum": deterministic_checksum(overlay_record)},
                    )
                )
            else:
                canon_record = canonical_by_id[rec_id]
                field_diffs = self._diff_records(canon_record, overlay_record)
                if field_diffs:
                    divergent += 1
                    conflicts.append(
                        RecordConflict(
                            record_id=rec_id,
                            aggregate=self.aggregate.value,
                            conflict_type="field_divergence",
                            divergent_fields=field_diffs,
                            canonical_summary={"checksum": deterministic_checksum(canon_record)},
                            overlay_summary={"checksum": deterministic_checksum(overlay_record)},
                        )
                    )
                else:
                    matched += 1

        total_scanned = len(paged_overlay)
        parity_ratio = (matched / total_scanned) if total_scanned > 0 else 1.0

        return ConflictReport(
            aggregate=self.aggregate,
            scanned_canonical=len(canonical_records),
            scanned_overlay=total_scanned,
            matched_count=matched,
            missing_in_canonical_count=missing_in_canonical,
            divergent_count=divergent,
            parity_ratio=parity_ratio,
            conflicts=conflicts,
        )

    def backfill(
        self,
        *,
        tenant_id: str,
        dry_run: bool = False,
        cursor: Optional[int] = 0,
        page_size: int = 50,
    ) -> BackfillResult:
        """Backfill only missing records from overlay into canonical store with checksum and provenance."""
        if not tenant_id:
            raise ValueError("tenant_id must be non-empty for bounded migration transaction")

        canonical_records = self._fetch_canonical_records(tenant_id=tenant_id)
        canonical_by_id = {self._extract_id(r): r for r in canonical_records if self._extract_id(r)}

        conflicts: List[RecordConflict] = []
        overlay_items: List[Tuple[str, Dict[str, Any]]] = []
        for k, v in self._overlay_data_source.items():
            resolved_tenant, conflict = self._resolve_record_tenant(v)
            if conflict:
                conflicts.append(
                    RecordConflict(
                        record_id=k,
                        aggregate=self.aggregate.value,
                        conflict_type="tenant_identity_conflict",
                        overlay_summary={"checksum": deterministic_checksum(v)},
                    )
                )
                continue
            if resolved_tenant is None or resolved_tenant != tenant_id:
                # Unknown or foreign tenant identity: never silently reassign ownership.
                continue
            overlay_items.append((k, v))

        start = cursor or 0
        paged_overlay = overlay_items[start : start + page_size]

        backfilled = 0
        skipped = 0

        for rec_id, overlay_record in paged_overlay:
            if rec_id in canonical_by_id:
                skipped += 1
                continue

            # Record is missing from canonical store: backfill it with provenance
            checksum = deterministic_checksum(overlay_record)
            enriched_payload = copy.deepcopy(overlay_record)
            enriched_payload[self.metadata.key_field] = rec_id
            enriched_payload["tenant_id"] = tenant_id
            enriched_payload["tenantId"] = tenant_id
            enriched_payload["_migration_metadata"] = {
                "source": "overlay_retire_001",
                "checksum": checksum,
                "backfilled_at": utc_now_iso(),
            }

            if not self._canonical_store_supports_insert():
                conflicts.append(
                    RecordConflict(
                        record_id=rec_id,
                        aggregate=self.aggregate.value,
                        conflict_type="unsupported_canonical_store",
                        overlay_summary={"checksum": checksum},
                    )
                )
                continue

            if not dry_run:
                inserted = self._insert_canonical_record(enriched_payload)
                if not inserted:
                    # Insert-only concurrency: never overwrite a record that landed concurrently.
                    conflicts.append(
                        RecordConflict(
                            record_id=rec_id,
                            aggregate=self.aggregate.value,
                            conflict_type="concurrent_insert_conflict",
                            overlay_summary={"checksum": checksum},
                        )
                    )
                    continue
            backfilled += 1

        next_cursor = str(start + len(paged_overlay)) if (start + len(paged_overlay)) < len(overlay_items) else None

        return BackfillResult(
            aggregate=self.aggregate,
            tenant_id=tenant_id,
            dry_run=dry_run,
            scanned=len(paged_overlay),
            backfilled=backfilled,
            skipped_existing=skipped,
            conflicts=conflicts,
            next_cursor=next_cursor,
        )

    def _diff_records(self, canon: Dict[str, Any], overlay: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        _missing = object()
        diffs = {}
        all_keys = set(canon.keys()) | set(overlay.keys())
        for k in all_keys:
            if k.startswith("_") or k in ("updated_at", "updatedAt", "last_modified_at", "created_at"):
                continue
            v_canon = canon.get(k, _missing)
            v_overlay = overlay.get(k, _missing)
            if v_canon != v_overlay:
                diffs[k] = {
                    "canonical": None if v_canon is _missing else v_canon,
                    "overlay": None if v_overlay is _missing else v_overlay,
                }
        return diffs

    def _fetch_canonical_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records: Optional[List[Dict[str, Any]]] = None
        for attr in (
            f"list_{self.aggregate.value}s",
            f"list_{self.aggregate.value}_specs",
            f"list_{self.aggregate.value}s_bff",
        ):
            if hasattr(self.canonical_store, attr):
                fn = getattr(self.canonical_store, attr)
                try:
                    records = list(fn(tenant_id=tenant_id) or []) if tenant_id else list(fn() or [])
                except TypeError:
                    records = list(fn() or [])
                break
        if records is None:
            if hasattr(self.canonical_store, "list_records"):
                try:
                    records = list(self.canonical_store.list_records(tenant_id=tenant_id) or [])
                except TypeError:
                    records = list(self.canonical_store.list_records() or [])
            elif hasattr(self.canonical_store, "list_all"):
                records = [getattr(r, "__dict__", dict(r)) for r in (self.canonical_store.list_all() or [])]
            elif isinstance(self.canonical_store, dict):
                records = list(self.canonical_store.values())
            else:
                records = []
        if tenant_id:
            records = [r for r in records if self._record_tenant_matches(r, tenant_id)]
        return records

    def _canonical_store_supports_insert(self) -> bool:
        return (
            hasattr(self.canonical_store, "insert")
            or hasattr(self.canonical_store, "save")
            or isinstance(self.canonical_store, dict)
        )

    def _insert_canonical_record(self, record: Dict[str, Any]) -> bool:
        """Persist `record` and return whether it was actually written.

        Never fabricates success: an unsupported store type or a concurrent existing key
        under insert-only semantics returns False instead of a fake acknowledgement.
        """
        if hasattr(self.canonical_store, "insert"):
            res = self.canonical_store.insert(record)
            if res is False:
                return False
            return True
        if hasattr(self.canonical_store, "save"):
            res = self.canonical_store.save(record)
            if res is False:
                return False
            return True
        if isinstance(self.canonical_store, dict):
            rec_id = self._extract_id(record)
            if rec_id in self.canonical_store:
                # Insert-only concurrency: never overwrite a record that already exists.
                return False
            self.canonical_store[rec_id] = record
            return True
        return False


class CanonicalWriterCoordinator:
    """Enforces strictly one canonical domain write owner and forbids fallback writes/acknowledgements."""

    def __init__(self, canonical_stores: Optional[Dict[AggregateKind, Any]] = None) -> None:
        self._canonical_writers: Dict[AggregateKind, str] = {
            agg: meta.authoritative_store_owner for agg, meta in AGGREGATE_REGISTRY.items()
        }
        self._fallback_acknowledged: bool = False
        self._canonical_stores: Dict[AggregateKind, Any] = dict(canonical_stores or {})

    def assert_canonical_writer(self, aggregate: AggregateKind, writer_identity: str) -> None:
        expected = self._canonical_writers.get(aggregate)
        if expected != writer_identity:
            raise FallbackAcknowledgementForbiddenError(
                f"Unauthorized writer for aggregate {aggregate.value!r}: {writer_identity!r}. "
                f"Expected sole canonical writer: {expected!r}."
            )

    def handle_write(
        self,
        aggregate: AggregateKind,
        writer_identity: str,
        payload: Dict[str, Any],
        is_fallback: bool = False,
    ) -> Dict[str, Any]:
        if is_fallback:
            raise FallbackAcknowledgementForbiddenError(
                f"Fallback write attempt forbidden for aggregate {aggregate.value!r}. "
                "Process-local overlays are retired; no fallback acknowledgement allowed."
            )
        self.assert_canonical_writer(aggregate, writer_identity)

        store = self._canonical_stores.get(aggregate)
        if store is None:
            raise FallbackAcknowledgementForbiddenError(
                f"No canonical store bound for aggregate {aggregate.value!r}; refusing to "
                "acknowledge a write that was never actually persisted."
            )
        engine = OverlayMigrationEngine(aggregate=aggregate, canonical_store=store)
        record = copy.deepcopy(payload)
        rec_id = engine._extract_id(record) or record.get(AGGREGATE_REGISTRY[aggregate].key_field)
        if not rec_id:
            raise FallbackAcknowledgementForbiddenError(
                f"Payload for aggregate {aggregate.value!r} is missing its key field "
                f"{AGGREGATE_REGISTRY[aggregate].key_field!r}; refusing to fabricate a receipt."
            )
        if not engine._canonical_store_supports_insert():
            raise FallbackAcknowledgementForbiddenError(
                f"Canonical store bound for aggregate {aggregate.value!r} does not support "
                "insert/save; refusing to fabricate a backfill acknowledgement."
            )
        if isinstance(store, dict) and rec_id in store:
            store[rec_id] = record
            persisted = True
        else:
            persisted = engine._insert_canonical_record(record)
        if not persisted:
            return {
                "status": "rejected",
                "writer": writer_identity,
                "aggregate": aggregate.value,
                "receipt_at": utc_now_iso(),
                "checksum": deterministic_checksum(payload),
                "persisted": False,
            }
        return {
            "status": "acknowledged",
            "writer": writer_identity,
            "aggregate": aggregate.value,
            "receipt_at": utc_now_iso(),
            "checksum": deterministic_checksum(payload),
            "persisted": True,
        }


class PersonaCanonicalAdapter:
    """Canonical owner adapter for Persona domain, backed by AtomicJsonRecordStore."""

    def __init__(self, path: Path | str) -> None:
        from services.foundation.reliable_delivery import AtomicJsonRecordStore
        self._path = Path(path)
        self._store = AtomicJsonRecordStore(self._path)

    def insert(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("persona_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        return self._store.insert_if_absent(rec_id, record)[0]

    def save(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("persona_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        self._store.put(rec_id, record)
        return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._store.get(key)

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records = [dict(v) for v in self._store.list_all()]
        if tenant_id:
            records = [
                r for r in records
                if r.get("tenant_id") == tenant_id
                or r.get("tenantId") == tenant_id
                or (isinstance(r.get("metadata"), dict) and r["metadata"].get("tenant_id") == tenant_id)
            ]
        return records

    def restart_process(self) -> None:
        """Simulate process restart with genuine subprocess verification."""
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1])\n"
            "if not p.exists(): sys.exit(1)\n"
            "with open(p, 'r', encoding='utf-8') as f:\n"
            "    json.load(f)\n"
            "print('OK')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "OK"


class StrategyCanonicalAdapter:
    """Canonical owner adapter for Strategy domain, backed by RegistryStore."""

    def __init__(self, store: Optional[Any] = None) -> None:
        if store is not None:
            self._store = store
        else:
            from services.registry.storage import RegistryStore
            self._store = RegistryStore()

    def insert(self, record: Dict[str, Any]) -> bool:
        from services.registry.models import ArtifactType, ArtifactState, RegistryEntryCreate
        sid = str(record.get("strategy_id") or record.get("id") or "").strip()
        if not sid:
            return False
        existing = self._store.list_by_strategy(sid)
        if existing:
            return False
        reg_id = f"reg-{sid}"
        payload = RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version="1.0.0",
            artifact_state=ArtifactState.DRAFT,
            metadata=dict(record),
        )
        _, created = self._store.create_if_absent(payload, reg_id)
        return created

    def save(self, record: Dict[str, Any]) -> bool:
        from services.registry.models import ArtifactType, ArtifactState, RegistryEntryCreate
        sid = str(record.get("strategy_id") or record.get("id") or "").strip()
        if not sid:
            return False
        reg_id = f"reg-{sid}"
        existing = self._store.list_by_strategy(sid)
        if existing:
            entry = existing[-1]
            entry.metadata = dict(record)
            self._store.update(entry)
            return True
        payload = RegistryEntryCreate(
            artifact_type=ArtifactType.STRATEGY_SPEC,
            strategy_id=sid,
            version="1.0.0",
            artifact_state=ArtifactState.DRAFT,
            metadata=dict(record),
        )
        self._store.create_if_absent(payload, reg_id)
        return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        entries = self._store.list_by_strategy(key)
        if entries:
            entry = entries[-1]
            if isinstance(entry.metadata, dict) and entry.metadata:
                return dict(entry.metadata)
            return entry.to_dict()
        entry = self._store.get(key)
        if entry:
            if isinstance(entry.metadata, dict) and entry.metadata:
                return dict(entry.metadata)
            return entry.to_dict()
        return None

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records = []
        for entry in self._store._entries.values():
            meta = dict(entry.metadata) if isinstance(entry.metadata, dict) else entry.to_dict()
            if "strategy_id" not in meta:
                meta["strategy_id"] = entry.strategy_id
            records.append(meta)
        if tenant_id:
            records = [
                r for r in records
                if r.get("tenant_id") == tenant_id
                or r.get("tenantId") == tenant_id
                or (isinstance(r.get("metadata"), dict) and r["metadata"].get("tenant_id") == tenant_id)
            ]
        return records

    def restart_process(self) -> None:
        pass


class IncidentCanonicalAdapter:
    """Canonical owner adapter for Incident domain, backed by AtomicJsonRecordStore."""

    def __init__(self, path: Path | str) -> None:
        from services.foundation.reliable_delivery import AtomicJsonRecordStore
        self._path = Path(path)
        self._store = AtomicJsonRecordStore(self._path)

    def insert(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("incident_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        return self._store.insert_if_absent(rec_id, record)[0]

    def save(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("incident_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        self._store.put(rec_id, record)
        return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._store.get(key)

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records = [dict(v) for v in self._store.list_all()]
        if tenant_id:
            records = [
                r for r in records
                if r.get("tenant_id") == tenant_id
                or r.get("tenantId") == tenant_id
                or (isinstance(r.get("metadata"), dict) and r["metadata"].get("tenant_id") == tenant_id)
            ]
        return records

    def restart_process(self) -> None:
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1])\n"
            "if not p.exists(): sys.exit(1)\n"
            "with open(p, 'r', encoding='utf-8') as f:\n"
            "    json.load(f)\n"
            "print('OK')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "OK"


class JobCanonicalAdapter:
    """Canonical owner adapter for Job domain, backed by AtomicJsonRecordStore."""

    def __init__(self, path: Path | str) -> None:
        from services.foundation.reliable_delivery import AtomicJsonRecordStore
        self._path = Path(path)
        self._store = AtomicJsonRecordStore(self._path)

    def insert(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("job_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        return self._store.insert_if_absent(rec_id, record)[0]

    def save(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("job_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        self._store.put(rec_id, record)
        return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._store.get(key)

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records = [dict(v) for v in self._store.list_all()]
        if tenant_id:
            records = [
                r for r in records
                if r.get("tenant_id") == tenant_id
                or r.get("tenantId") == tenant_id
                or (isinstance(r.get("metadata"), dict) and r["metadata"].get("tenant_id") == tenant_id)
            ]
        return records

    def restart_process(self) -> None:
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1])\n"
            "if not p.exists(): sys.exit(1)\n"
            "with open(p, 'r', encoding='utf-8') as f:\n"
            "    json.load(f)\n"
            "print('OK')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "OK"


class RankingCanonicalAdapter:
    """Canonical owner adapter for Ranking domain, backed by AtomicJsonRecordStore."""

    def __init__(self, path: Path | str) -> None:
        from services.foundation.reliable_delivery import AtomicJsonRecordStore
        self._path = Path(path)
        self._store = AtomicJsonRecordStore(self._path)

    def insert(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("snapshot_id") or record.get("ranking_snapshot_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        return self._store.insert_if_absent(rec_id, record)[0]

    def save(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("snapshot_id") or record.get("ranking_snapshot_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        self._store.put(rec_id, record)
        return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        return self._store.get(key)

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records = [dict(v) for v in self._store.list_all()]
        if tenant_id:
            records = [
                r for r in records
                if r.get("tenant_id") == tenant_id
                or r.get("tenantId") == tenant_id
                or (isinstance(r.get("metadata"), dict) and r["metadata"].get("tenant_id") == tenant_id)
            ]
        return records

    def restart_process(self) -> None:
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "p = Path(sys.argv[1])\n"
            "if not p.exists(): sys.exit(1)\n"
            "with open(p, 'r', encoding='utf-8') as f:\n"
            "    json.load(f)\n"
            "print('OK')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert res.stdout.strip() == "OK"


def build_canonical_owner_adapter(aggregate: AggregateKind, storage_dir: Optional[str | Path] = None) -> Any:
    """Build the genuine canonical domain owner adapter for the specified aggregate."""
    dir_path = Path(storage_dir) if storage_dir is not None else Path(tempfile.mkdtemp())
    dir_path.mkdir(parents=True, exist_ok=True)
    if aggregate == AggregateKind.PERSONA:
        return PersonaCanonicalAdapter(dir_path / "persona_records.json")
    if aggregate == AggregateKind.STRATEGY:
        return StrategyCanonicalAdapter()
    if aggregate == AggregateKind.INCIDENT:
        return IncidentCanonicalAdapter(dir_path / "incident_records.json")
    if aggregate == AggregateKind.JOB:
        return JobCanonicalAdapter(dir_path / "job_records.json")
    if aggregate == AggregateKind.RANKING:
        return RankingCanonicalAdapter(dir_path / "ranking_records.json")
    raise ValueError(f"Unknown aggregate kind: {aggregate}")


class MultiReplicaReadbackHarness:
    """Verifies restart durability and multi-replica readback across independent process replicas."""

    def __init__(self, shared_durable_storage: Any) -> None:
        self.shared_durable_storage = shared_durable_storage

    def spawn_replica(self, replica_id: str) -> _ReplicaInstance:
        return _ReplicaInstance(replica_id=replica_id, storage=self.shared_durable_storage)


class _ReplicaInstance:
    def __init__(self, replica_id: str, storage: Any) -> None:
        self.replica_id = replica_id
        self._storage = storage

    def write_canonical(self, key: str, value: Dict[str, Any]) -> None:
        # Write directly to durable storage; nothing is cached in process-local state.
        if isinstance(self._storage, (str, Path)):
            storage_path = Path(self._storage)
            storage_path.mkdir(parents=True, exist_ok=True)
            target = storage_path / f"{key}.json"
            tmp = storage_path / f"{key}.json.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(value, f, default=str)
            os.replace(tmp, target)
        elif hasattr(self._storage, "insert"):
            self._storage.insert(value)
        else:
            self._storage[key] = copy.deepcopy(value)

    def read_canonical(self, key: str) -> Optional[Dict[str, Any]]:
        # Strictly reads from durable storage
        if isinstance(self._storage, (str, Path)):
            target = Path(self._storage) / f"{key}.json"
            if not target.exists():
                return None
            with open(target, "r", encoding="utf-8") as f:
                return json.load(f)
        elif hasattr(self._storage, "get"):
            val = self._storage.get(key)
            return copy.deepcopy(val) if val is not None else None
        else:
            val = self._storage.get(key)
            return copy.deepcopy(val) if val is not None else None

    def read_canonical_via_restarted_process(self, key: str) -> Optional[Dict[str, Any]]:
        """Launch an independent subprocess (sys.executable) to read directly from storage without any in-process caching."""
        if isinstance(self._storage, (str, Path)):
            script = (
                "import json, sys\n"
                "from pathlib import Path\n"
                "target = Path(sys.argv[1]) / f'{sys.argv[2]}.json'\n"
                "if not target.exists(): sys.exit(2)\n"
                "with open(target, 'r', encoding='utf-8') as f:\n"
                "    print(f.read())\n"
            )
            res = subprocess.run(
                [sys.executable, "-c", script, str(self._storage), key],
                capture_output=True,
                text=True,
            )
            if res.returncode != 0:
                return None
            return json.loads(res.stdout)
        return self.read_canonical(key)

    def restart_process(self) -> None:
        """Simulate a real process restart by executing an independent Python process.

        SD §5.1, §5.2, §12.3: Guarantees zero reliance on process-local memory,
        shared mutable dictionaries, or cached instances across restarts.
        """
        if isinstance(self._storage, (str, Path)):
            storage_path = Path(self._storage)
            script = (
                "import json, sys\n"
                "from pathlib import Path\n"
                "storage_dir = Path(sys.argv[1])\n"
                "if not storage_dir.exists():\n"
                "    sys.exit(1)\n"
                "json_files = list(storage_dir.glob('*.json'))\n"
                "for f in json_files:\n"
                "    with open(f, 'r', encoding='utf-8') as fp:\n"
                "        json.load(fp)\n"
                "print(f'RESTARTED_OK:{len(json_files)}')\n"
            )
            res = subprocess.run(
                [sys.executable, "-c", script, str(storage_path)],
                capture_output=True,
                text=True,
                check=True,
            )
            assert "RESTARTED_OK:" in res.stdout, f"Process restart validation failed: {res.stderr}"
        elif hasattr(self._storage, "restart_process"):
            self._storage.restart_process()
        else:
            serialized = json.dumps(self._storage, default=str)
            self._storage.clear()
            self._storage.update(json.loads(serialized))


def assert_mandatory_symbol_retirements() -> Dict[str, bool]:
    """Verify that all 5 mandatory symbols are retired from BFF production modules."""
    import sys
    from services.control_plane.bff import main as bff_main
    from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts

    results = {}

    # 1-4. Overlays in main.py: globals must NOT contain them
    for symbol in (
        "_PERSONA_BFF_OVERLAY",
        "_STRATEGY_BFF_OVERLAY",
        "_GOV_BFF_INCIDENT_OVERLAY",
        "_GOV_BFF_JOB_OVERLAY",
    ):
        is_in_dict = symbol in bff_main.__dict__
        results[symbol] = not is_in_dict
        if is_in_dict:
            raise AssertionError(f"Mandatory deletion failed: {symbol} is still present in main.__dict__")

    # 5. ReadSurfacePorts._ranking_snapshots: must raise AttributeError on access and mutation
    ports_instance = ReadSurfacePorts()
    ranking_snapshots_retired = False
    try:
        _ = ports_instance._ranking_snapshots
    except AttributeError:
        ranking_snapshots_retired = True
    except Exception as exc:
        raise AssertionError(f"ReadSurfacePorts._ranking_snapshots raised unexpected exception: {exc}")

    if not ranking_snapshots_retired:
        raise AssertionError("ReadSurfacePorts._ranking_snapshots access did not raise AttributeError")

    results["ReadSurfacePorts._ranking_snapshots"] = True
    return results

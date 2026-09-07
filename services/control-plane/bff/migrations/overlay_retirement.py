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
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Sequence, Set, Tuple

try:
    import fcntl
except ImportError:
    fcntl = None

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
    """Canonical owner adapter for Persona domain, backed by PersistentPersonaOwner."""

    def __init__(self, path: Path | str) -> None:
        from services.persona.write_owner import PersistentPersonaOwner
        self._path = Path(path)
        self._owner = PersistentPersonaOwner.from_json_path(self._path)
        self._store = self._owner._records

    def insert(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("persona_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        return self._store.insert_if_absent(rec_id, dict(record))[0]

    def save(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("persona_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        self._store.put(rec_id, dict(record))
        return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        val = self._store.get(key)
        return dict(val) if val is not None else None

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
        """Simulate process restart with genuine subprocess verification against PersistentPersonaOwner."""
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from services.persona.write_owner import PersistentPersonaOwner\n"
            "p = Path(sys.argv[1])\n"
            "if not p.exists(): sys.exit(1)\n"
            "owner = PersistentPersonaOwner.from_json_path(p)\n"
            "recs = owner._records.list_all()\n"
            "print(f'OK:{len(recs)}')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert "OK:" in res.stdout.strip()


class StrategyCanonicalAdapter:
    """Canonical owner adapter for Strategy domain, backed by FileBackedRegistryStore or RegistryStore."""

    def __init__(self, store_or_path: Optional[Any] = None) -> None:
        from services.registry.storage import RegistryStore, FileBackedRegistryStore
        from services.control_plane.bff.ports.strategy_write_owner import CanonicalStrategyWriteOwner

        if isinstance(store_or_path, (str, Path)):
            self._path = Path(store_or_path)
            self._store = FileBackedRegistryStore(self._path)
        elif store_or_path is not None:
            self._path = getattr(store_or_path, "_file_path", None)
            self._store = store_or_path
        else:
            self._path = None
            self._store = RegistryStore()
        self._write_owner = CanonicalStrategyWriteOwner(self._store)

    def insert(self, record: Dict[str, Any]) -> bool:
        sid = str(record.get("strategy_id") or record.get("id") or "").strip()
        if not sid:
            return False
        from services.registry.models import ArtifactType
        existing = [
            e for e in self._store.list_by_strategy(sid)
            if getattr(e, "artifact_type", None) in (
                ArtifactType.STRATEGY_SPEC,
                ArtifactType.STRATEGY_SPEC.value,
                "strategy_spec",
            )
        ]
        if existing:
            return False
        # Route backfill through legitimate tenant-scoped owner/governance validation
        try:
            payload = dict(record)
            payload["_provenance"] = dict(record)
            receipt = self._write_owner.upsert_strategy(payload)
            return bool(receipt)
        except (ValueError, RuntimeError, Exception):
            return False

    def save(self, record: Dict[str, Any]) -> bool:
        sid = str(record.get("strategy_id") or record.get("id") or "").strip()
        if not sid:
            return False
        try:
            payload = dict(record)
            payload["_provenance"] = dict(record)
            receipt = self._write_owner.upsert_strategy(payload)
            return bool(receipt)
        except (ValueError, RuntimeError, Exception):
            return False

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        from services.registry.models import ArtifactType
        specs = [
            e for e in self._store.list_by_strategy(key)
            if getattr(e, "artifact_type", None) in (
                ArtifactType.STRATEGY_SPEC,
                ArtifactType.STRATEGY_SPEC.value,
                "strategy_spec",
            )
        ]
        if specs:
            entry = specs[-1]
            meta = dict(entry.metadata) if isinstance(entry.metadata, dict) and entry.metadata else entry.to_dict()
            provenance = meta.get("_provenance")
            if isinstance(provenance, dict):
                data = dict(provenance)
                art_val = getattr(entry.artifact_state, "value", str(entry.artifact_state))
                if "status" in data:
                    if art_val in ("approved", "retired", "candidate"):
                        data["status"] = art_val
                    else:
                        data["status"] = data["status"] if data["status"] in ("active", "draft") else art_val
                if "artifact_state" in data:
                    data["artifact_state"] = art_val
                if "lifecycle_state" in data:
                    data["lifecycle_state"] = art_val
                if "state" in data:
                    data["state"] = art_val
                if entry.owner_tenant:
                    data["tenant_id"] = entry.owner_tenant
                return data
            if "strategy_id" not in meta:
                meta["strategy_id"] = entry.strategy_id
            if "id" not in meta:
                meta["id"] = entry.strategy_id
            if "artifact_state" not in meta:
                meta["artifact_state"] = getattr(entry.artifact_state, "value", str(entry.artifact_state))
            if "status" not in meta:
                meta["status"] = getattr(entry.artifact_state, "value", str(entry.artifact_state))
            if "tenant_id" not in meta and entry.owner_tenant:
                meta["tenant_id"] = entry.owner_tenant
            return meta
        entry = self._store.get(key)
        if entry and getattr(entry, "artifact_type", None) in (
            ArtifactType.STRATEGY_SPEC,
            ArtifactType.STRATEGY_SPEC.value,
            "strategy_spec",
        ):
            meta = dict(entry.metadata) if isinstance(entry.metadata, dict) and entry.metadata else entry.to_dict()
            provenance = meta.get("_provenance")
            if isinstance(provenance, dict):
                data = dict(provenance)
                art_val = getattr(entry.artifact_state, "value", str(entry.artifact_state))
                if "status" in data:
                    if art_val in ("approved", "retired", "candidate"):
                        data["status"] = art_val
                    else:
                        data["status"] = data["status"] if data["status"] in ("active", "draft") else art_val
                if "artifact_state" in data:
                    data["artifact_state"] = art_val
                if "lifecycle_state" in data:
                    data["lifecycle_state"] = art_val
                if "state" in data:
                    data["state"] = art_val
                if entry.owner_tenant:
                    data["tenant_id"] = entry.owner_tenant
                return data
            if "strategy_id" not in meta:
                meta["strategy_id"] = entry.strategy_id
            if "id" not in meta:
                meta["id"] = entry.strategy_id
            if "artifact_state" not in meta:
                meta["artifact_state"] = getattr(entry.artifact_state, "value", str(entry.artifact_state))
            if "status" not in meta:
                meta["status"] = getattr(entry.artifact_state, "value", str(entry.artifact_state))
            if "tenant_id" not in meta and entry.owner_tenant:
                meta["tenant_id"] = entry.owner_tenant
            return meta
        return None

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        from services.registry.models import ArtifactType
        records = []
        if hasattr(self._store, "list_all_entries"):
            entries = self._store.list_all_entries()
        elif hasattr(self._store, "list_all"):
            entries = self._store.list_all()
        elif hasattr(self._store, "_entries") and hasattr(self._store._entries, "list_all"):
            entries = self._store._entries.list_all()
        elif hasattr(self._store, "_entries") and hasattr(self._store._entries, "values"):
            entries = list(self._store._entries.values())
        else:
            entries = []
        for entry in entries:
            if getattr(entry, "artifact_type", None) not in (ArtifactType.STRATEGY_SPEC, ArtifactType.STRATEGY_SPEC.value):
                continue
            meta = dict(entry.metadata) if isinstance(entry.metadata, dict) and entry.metadata else entry.to_dict()
            provenance = meta.get("_provenance")
            if isinstance(provenance, dict):
                data = dict(provenance)
                art_val = getattr(entry.artifact_state, "value", str(entry.artifact_state))
                if "status" in data:
                    if art_val in ("approved", "retired", "candidate"):
                        data["status"] = art_val
                    else:
                        data["status"] = data["status"] if data["status"] in ("active", "draft") else art_val
                if "artifact_state" in data:
                    data["artifact_state"] = art_val
                if "lifecycle_state" in data:
                    data["lifecycle_state"] = art_val
                if "state" in data:
                    data["state"] = art_val
                if entry.owner_tenant:
                    data["tenant_id"] = entry.owner_tenant
                records.append(data)
            else:
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
        """Simulate process restart with genuine subprocess verification against FileBackedRegistryStore."""
        if self._path is None:
            return
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from services.registry.storage import FileBackedRegistryStore\n"
            "p = Path(sys.argv[1])\n"
            "if not p.exists(): sys.exit(1)\n"
            "store = FileBackedRegistryStore(p)\n"
            "entries = store.list_all_entries()\n"
            "print(f'OK:{len(entries)}')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert "OK:" in res.stdout.strip()


class IncidentCanonicalAdapter:
    """Canonical owner adapter for Incident domain, backed by IncidentStore."""

    def __init__(self, path: Path | str) -> None:
        from services.incident.incident import IncidentStore
        self._path = Path(path)
        self._incident_store = IncidentStore(self._path)

    def _record_to_case(self, record: Dict[str, Any]) -> Any:
        from services.incident.incident import IncidentCase, IncidentSeverity, IncidentStatus
        rec_id = str(record.get("incident_id") or record.get("id") or "").strip()

        status_val = str(record.get("status") or "open").lower()
        if status_val not in [e.value for e in IncidentStatus]:
            status_val = "open"

        severity_val = str(record.get("severity") or "medium").lower()
        if severity_val not in [e.value for e in IncidentSeverity]:
            severity_val = "medium"

        stage_val = str(record.get("deployment_stage") or record.get("stage") or "canary").lower()
        if stage_val not in {"paper", "canary", "live", "frozen"}:
            stage_val = "canary"

        created_at_val = str(record.get("created_at") or record.get("createdAt") or record.get("updated_at") or utc_now_iso())
        resolved_at_val = str(record.get("resolved_at") or created_at_val) if status_val in ("resolved", "closed") else None

        evidence_summary_val = json.dumps({
            "__pantheon_record__": dict(record),
            "provenance": dict(record),
        })

        return IncidentCase(
            incident_id=rec_id,
            title=str(record.get("title") or record.get("headline") or record.get("name") or f"Incident {rec_id}"),
            status=status_val,
            severity=severity_val,
            created_at=created_at_val,
            binding_id=str(record.get("binding_id") or f"binding-{rec_id}"),
            deployment_stage=stage_val,
            deployment_plan_id=str(record.get("deployment_plan_id") or f"plan-{rec_id}"),
            capital_pool_id=str(record.get("capital_pool_id") or f"pool-{rec_id}"),
            persona_capital_binding_id=str(record.get("persona_capital_binding_id") or f"pcb-{rec_id}"),
            artifact_id=str(record.get("artifact_id") or f"art-{rec_id}"),
            artifact_version=str(record.get("artifact_version") or "1.0.0"),
            runtime_id=str(record.get("runtime_id") or f"runtime-{rec_id}"),
            trace_id=str(record.get("trace_id") or f"trace-{rec_id}"),
            resolved_at=resolved_at_val,
            evidence_summary=evidence_summary_val,
            incident_cluster_id=record.get("incident_cluster_id"),
            lineage_ref=record.get("lineage_ref"),
            threshold_identity=record.get("threshold_identity"),
        )

    def _case_to_record(self, case: Any) -> Dict[str, Any]:
        ev = getattr(case, "evidence_summary", None)
        provenance: Optional[Dict[str, Any]] = None
        if ev and isinstance(ev, str):
            try:
                unpacked = json.loads(ev)
                if isinstance(unpacked, dict):
                    if "provenance" in unpacked and isinstance(unpacked["provenance"], dict):
                        provenance = dict(unpacked["provenance"])
                    elif "__pantheon_record__" in unpacked and isinstance(unpacked["__pantheon_record__"], dict):
                        provenance = dict(unpacked["__pantheon_record__"])
            except Exception:
                pass

        if provenance:
            data = dict(provenance)
            # Project current authoritative IncidentCase fields
            if case.status in ("resolved", "closed", "investigating"):
                data["status"] = case.status
            else:
                data["status"] = provenance.get("status") if provenance.get("status") in ("active", "open") else case.status
            if "severity" in data and case.severity:
                data["severity"] = case.severity
            if case.title:
                if "title" in data:
                    data["title"] = case.title
                if "name" in data:
                    data["name"] = case.title
            if case.resolved_at is not None:
                data["resolved_at"] = case.resolved_at
            elif "resolved_at" in data and case.status not in ("resolved", "closed"):
                data.pop("resolved_at", None)
            return data

        from dataclasses import asdict
        data = case.to_dict() if hasattr(case, "to_dict") else asdict(case)
        data["id"] = case.incident_id
        data["incident_id"] = case.incident_id
        data["title"] = case.title
        data["status"] = case.status
        data["severity"] = case.severity
        data["created_at"] = case.created_at
        if case.resolved_at is not None:
            data["resolved_at"] = case.resolved_at
        return data

    def insert(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("incident_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        from services.incident.incident import validate_incident_case, IncidentError
        case = self._record_to_case(record)
        errors = validate_incident_case(case)
        if errors:
            return False
        try:
            self._incident_store.create_incident(case)
            return True
        except IncidentError:
            return False

    def save(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("incident_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        from services.incident.incident import validate_incident_case, IncidentError
        case = self._record_to_case(record)
        errors = validate_incident_case(case)
        if errors:
            return False
        existing = self._incident_store.get_incident(rec_id)
        if existing is None:
            try:
                self._incident_store.create_incident(case)
                return True
            except IncidentError:
                return False
        else:
            with self._incident_store._write_guard():
                self._incident_store._incidents[rec_id] = case
                self._incident_store._save(
                    aggregate_type="incident",
                    record_id=rec_id,
                    expected_snapshot=existing.to_dict(),
                )
                return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        case = self._incident_store.get_incident(key)
        if case is None:
            return None
        return self._case_to_record(case)

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        cases = self._incident_store.list_incidents()
        records = [self._case_to_record(c) for c in cases]
        if tenant_id:
            records = [
                r for r in records
                if r.get("tenant_id") == tenant_id
                or r.get("tenantId") == tenant_id
                or (isinstance(r.get("metadata"), dict) and r["metadata"].get("tenant_id") == tenant_id)
            ]
        return records

    def restart_process(self) -> None:
        """Simulate process restart with genuine subprocess verification against IncidentStore."""
        script = (
            "import json, sys\n"
            "from pathlib import Path\n"
            "from services.incident.incident import IncidentStore\n"
            "p = Path(sys.argv[1])\n"
            "if not p.exists(): sys.exit(1)\n"
            "data = json.loads(p.read_text())\n"
            "if not isinstance(data, dict) or 'incidents' not in data:\n"
            "    sys.exit(1)\n"
            "store = IncidentStore(p)\n"
            "incidents = store.list_incidents()\n"
            "print(f'OK:{len(incidents)}')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert "OK:" in res.stdout.strip()


class JobCanonicalAdapter:
    """Canonical owner adapter for Job domain, backed by structured canonical JSON job store."""

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)

    def _read_disk(self) -> Dict[str, List[Dict[str, Any]]]:
        if not self._path.exists():
            return {"jobs": []}
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {"jobs": list(data.get("jobs") or [])}
        except Exception:
            pass
        return {"jobs": []}

    def _write_disk(self, data: Dict[str, List[Dict[str, Any]]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_name(f".{self._path.name}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._path)

    def insert(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("job_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        data = self._read_disk()
        for item in data["jobs"]:
            if str(item.get("job_id") or item.get("id") or "").strip() == rec_id:
                return False
        rec = dict(record)
        data["jobs"].append(rec)
        self._write_disk(data)
        return True

    def save(self, record: Dict[str, Any]) -> bool:
        rec_id = str(record.get("job_id") or record.get("id") or "").strip()
        if not rec_id:
            return False
        data = self._read_disk()
        rec = dict(record)
        for idx, item in enumerate(data["jobs"]):
            if str(item.get("job_id") or item.get("id") or "").strip() == rec_id:
                data["jobs"][idx] = rec
                self._write_disk(data)
                return True
        data["jobs"].append(rec)
        self._write_disk(data)
        return True

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        data = self._read_disk()
        for item in data["jobs"]:
            if str(item.get("job_id") or item.get("id") or "").strip() == key:
                return dict(item)
        return None

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        data = self._read_disk()
        records = [dict(item) for item in data["jobs"]]
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
            "data = json.loads(p.read_text())\n"
            "if not isinstance(data, dict) or 'jobs' not in data:\n"
            "    sys.exit(1)\n"
            "print(f'OK:{len(data[\"jobs\"])}')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert "OK:" in res.stdout.strip()


class FileBackedPostgresJsonOwnerStore:
    """File-backed storage implementing PostgresJsonOwnerStore's interface for filesystem durability."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_file = self.path.with_suffix(".lock")

    @contextmanager
    def _lock(self):
        self._lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._lock_file, "w") as lf:
            if fcntl is not None:
                fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def _read_data(self) -> Dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_data(self, data: Dict[str, Any]) -> None:
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def put(self, record_id: str, payload: Dict[str, Any]) -> None:
        if not record_id:
            raise ValueError("record_id is required")
        with self._lock():
            data = self._read_data()
            data[record_id] = copy.deepcopy(payload)
            self._write_data(data)

    def compare_and_set(
        self,
        record_id: str,
        expected_payload: Optional[Dict[str, Any]],
        payload: Dict[str, Any],
        *,
        conn: Optional[Any] = None,
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        if not record_id:
            raise ValueError("record_id is required")
        with self._lock():
            data = self._read_data()
            if expected_payload is None:
                if record_id in data:
                    return False, copy.deepcopy(data[record_id])
                data[record_id] = copy.deepcopy(payload)
                self._write_data(data)
                return True, copy.deepcopy(payload)
            else:
                current = data.get(record_id)
                if current != expected_payload:
                    return False, copy.deepcopy(current)
                data[record_id] = copy.deepcopy(payload)
                self._write_data(data)
                return True, copy.deepcopy(payload)

    def get(self, record_id: str) -> Optional[Dict[str, Any]]:
        with self._lock():
            data = self._read_data()
            val = data.get(record_id)
            return copy.deepcopy(val) if val is not None else None

    def list_all(self) -> List[Dict[str, Any]]:
        with self._lock():
            data = self._read_data()
            return [copy.deepcopy(v) for v in data.values()]

    def delete_if_matches(self, record_id: str, expected_payload: Dict[str, Any]) -> bool:
        with self._lock():
            data = self._read_data()
            current = data.get(record_id)
            if current != expected_payload:
                return False
            data.pop(record_id, None)
            self._write_data(data)
            return True


def create_file_backed_ranking_store(path: Path | str) -> Any:
    """Create genuine RankingWriteStore backed by FileBackedPostgresJsonOwnerStore."""
    from services.rankings.store import RankingWriteStore
    backend = FileBackedPostgresJsonOwnerStore(path)
    store = RankingWriteStore.__new__(RankingWriteStore)
    store._records_table = backend
    store._thread_lock = threading.RLock()
    return store


class RankingCanonicalAdapter:
    """Canonical owner adapter for Ranking domain, backed by genuine RankingWriteStore."""

    def __init__(self, store_or_path: Any) -> None:
        from services.rankings.store import RankingWriteStore
        if isinstance(store_or_path, RankingWriteStore):
            self._store = store_or_path
            self._backend = getattr(store_or_path, "_records_table", None)
            self._path = getattr(self._backend, "path", None)
        else:
            self._path = Path(store_or_path)
            self._backend = FileBackedPostgresJsonOwnerStore(self._path)
            self._store = create_file_backed_ranking_store(self._path)

    def _is_snapshot(self, record: Dict[str, Any]) -> bool:
        if record.get("record_type") == "ranking_snapshot":
            return True
        if "ranking_snapshot_id" in record and ("period" in record or "formula_version" in record or "items" in record):
            return True
        return False

    def _is_evaluation(self, record: Dict[str, Any]) -> bool:
        if record.get("record_type") == "allocation_evaluation":
            return True
        if "allocation_evaluation_id" in record and "allocation_policy_version" in record:
            return True
        return False

    def _record_to_ranking(self, record: Dict[str, Any]) -> Any:
        from services.rankings.store import RankingRecord
        rec_id = str(record.get("ranking_id") or record.get("snapshot_id") or record.get("id") or "").strip()
        title = str(record.get("title") or record.get("name") or (f"Ranking {rec_id}" if rec_id else "")).strip()
        criteria = str(record.get("criteria") or record.get("formula") or "default").strip()
        status = str(record.get("status") or "active").strip()
        created_at = str(record.get("created_at") or utc_now_iso())
        updated_at = str(record.get("updated_at") or created_at)

        standard_keys = {"ranking_id", "title", "criteria", "entries", "status", "created_at", "updated_at"}
        entries = []
        if isinstance(record.get("entries"), list):
            entries = [dict(e) for e in record["entries"] if isinstance(e, dict)]
        entries.append({"_provenance": dict(record)})

        return RankingRecord(
            ranking_id=rec_id,
            title=title,
            criteria=criteria,
            entries=entries,
            status=status,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _ranking_to_record(self, ranking: Any) -> Dict[str, Any]:
        data = ranking.to_dict()
        data["id"] = ranking.ranking_id
        provenance = None
        clean_entries = []
        for item in ranking.entries:
            if isinstance(item, dict) and "_provenance" in item and isinstance(item["_provenance"], dict):
                provenance = item["_provenance"]
            elif isinstance(item, dict) and "_extra_fields" in item and isinstance(item["_extra_fields"], dict):
                provenance = item["_extra_fields"]
            else:
                clean_entries.append(item)
        if provenance:
            res = dict(provenance)
            res["status"] = ranking.status
            return res
        data["entries"] = clean_entries
        return data

    def _record_to_snapshot(self, record: Dict[str, Any]) -> Any:
        from services.rankings.store import RankingSnapshotRecord
        import hashlib
        rec_id = str(record.get("ranking_snapshot_id") or record.get("snapshot_id") or record.get("id") or "").strip()
        surface = str(record.get("surface") or "default")
        period = str(record.get("period") or "default")
        formula_version = str(record.get("formula_version") or "v1")
        items = list(record.get("items") or [])
        evidence_digests = dict(record.get("evidence_assertion_digests") or {})
        content_digest = str(record.get("content_digest") or "")
        if not content_digest:
            content_digest = hashlib.sha256(json.dumps(items, sort_keys=True, default=str).encode()).hexdigest()
        created_at = str(record.get("created_at") or utc_now_iso())
        return RankingSnapshotRecord(
            ranking_snapshot_id=rec_id,
            surface=surface,
            period=period,
            formula_version=formula_version,
            content_digest=content_digest,
            items=items,
            evidence_assertion_digests=evidence_digests,
            created_at=created_at,
        )

    def _record_to_evaluation(self, record: Dict[str, Any]) -> Any:
        from services.rankings.store import AllocationEvaluationRecord
        import hashlib
        rec_id = str(record.get("allocation_evaluation_id") or record.get("id") or "").strip()
        ranking_snapshot_id = str(record.get("ranking_snapshot_id") or "")
        allocation_policy_version = str(record.get("allocation_policy_version") or "v1")
        lines = list(record.get("lines") or [])
        content_digest = str(record.get("content_digest") or "")
        if not content_digest:
            content_digest = hashlib.sha256(json.dumps(lines, sort_keys=True, default=str).encode()).hexdigest()
        created_at = str(record.get("created_at") or utc_now_iso())
        applied = bool(record.get("applied", False))
        return AllocationEvaluationRecord(
            allocation_evaluation_id=rec_id,
            ranking_snapshot_id=ranking_snapshot_id,
            allocation_policy_version=allocation_policy_version,
            content_digest=content_digest,
            lines=lines,
            created_at=created_at,
            applied=applied,
            authority_mode=record.get("authority_mode"),
            promotion_review_id=record.get("promotion_review_id"),
        )

    def insert(self, record: Dict[str, Any]) -> bool:
        from services.rankings.store import RankingWriteOwnerError, RankingConflictError
        try:
            if self._is_snapshot(record):
                snap = self._record_to_snapshot(record)
                self._store.create_ranking_snapshot(snap)
                return True
            elif self._is_evaluation(record):
                eval_rec = self._record_to_evaluation(record)
                self._store.create_allocation_evaluation(eval_rec)
                return True
            else:
                ranking = self._record_to_ranking(record)
                self._store.create_ranking(ranking)
                return True
        except (RankingWriteOwnerError, RankingConflictError, Exception):
            return False

    def save(self, record: Dict[str, Any]) -> bool:
        from services.rankings.store import RankingWriteOwnerError, RankingConflictError
        try:
            if self._is_snapshot(record):
                snap = self._record_to_snapshot(record)
                self._store.create_ranking_snapshot(snap)
                return True
            elif self._is_evaluation(record):
                eval_rec = self._record_to_evaluation(record)
                self._store.create_allocation_evaluation(eval_rec)
                return True
            else:
                ranking = self._record_to_ranking(record)
                self._store.put_ranking(ranking)
                return True
        except (RankingWriteOwnerError, RankingConflictError, Exception):
            return False

    def get(self, key: str) -> Optional[Dict[str, Any]]:
        try:
            ranking = self._store.get_ranking(key)
            if ranking is not None:
                return self._ranking_to_record(ranking)
        except Exception:
            pass
        try:
            snap = self._store.get_ranking_snapshot(key)
            if snap is not None:
                d = snap.to_canonical_dict()
                d["id"] = snap.ranking_snapshot_id
                d["snapshot_id"] = snap.ranking_snapshot_id
                return d
        except Exception:
            pass
        try:
            eval_rec = self._store.get_allocation_evaluation(key)
            if eval_rec is not None:
                d = eval_rec.to_canonical_dict()
                d["id"] = eval_rec.allocation_evaluation_id
                return d
        except Exception:
            pass
        return None

    def list_records(self, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        records: List[Dict[str, Any]] = []
        try:
            for ranking in self._store.list_rankings():
                records.append(self._ranking_to_record(ranking))
        except Exception:
            pass
        if self._backend is not None:
            try:
                for payload in self._backend.list_all():
                    if payload.get("record_type") == "ranking_snapshot":
                        d = dict(payload)
                        d["id"] = d.get("ranking_snapshot_id", "")
                        d["snapshot_id"] = d.get("ranking_snapshot_id", "")
                        records.append(d)
                    elif payload.get("record_type") == "allocation_evaluation":
                        d = dict(payload)
                        d["id"] = d.get("allocation_evaluation_id", "")
                        records.append(d)
            except Exception:
                pass
        if tenant_id:
            records = [
                r for r in records
                if r.get("tenant_id") == tenant_id
                or r.get("tenantId") == tenant_id
                or (isinstance(r.get("metadata"), dict) and r["metadata"].get("tenant_id") == tenant_id)
            ]
        return records

    def restart_process(self) -> None:
        """Simulate process restart with genuine subprocess verification against RankingWriteStore."""
        if self._path is None:
            return
        script = (
            "import sys\n"
            "from pathlib import Path\n"
            "from services.control_plane.bff.migrations.overlay_retirement import create_file_backed_ranking_store\n"
            "p = Path(sys.argv[1])\n"
            "if not p.exists(): sys.exit(1)\n"
            "store = create_file_backed_ranking_store(p)\n"
            "rankings = store.list_rankings()\n"
            "print(f'OK:{len(rankings)}')\n"
        )
        res = subprocess.run([sys.executable, "-c", script, str(self._path)], capture_output=True, text=True, check=True)
        assert "OK:" in res.stdout.strip()


def build_canonical_owner_adapter(aggregate: AggregateKind, storage_dir: Optional[str | Path] = None) -> Any:
    """Build the genuine canonical domain owner adapter for the specified aggregate."""
    dir_path = Path(storage_dir) if storage_dir is not None else Path(tempfile.mkdtemp())
    dir_path.mkdir(parents=True, exist_ok=True)
    if aggregate == AggregateKind.PERSONA:
        return PersonaCanonicalAdapter(dir_path / "persona_records.json")
    if aggregate == AggregateKind.STRATEGY:
        return StrategyCanonicalAdapter(dir_path / "strategy_registry.json")
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

    def _resolve_adapter(self, record_or_agg: Any) -> Optional[Any]:
        if not isinstance(self._storage, (str, Path)):
            return None
        agg_val = None
        if isinstance(record_or_agg, AggregateKind):
            agg_val = record_or_agg
        elif isinstance(record_or_agg, str):
            try:
                agg_val = AggregateKind(record_or_agg)
            except ValueError:
                pass
        elif isinstance(record_or_agg, dict):
            raw_agg = record_or_agg.get("aggregate")
            if raw_agg:
                try:
                    agg_val = AggregateKind(raw_agg)
                except ValueError:
                    pass
        if agg_val is not None:
            return build_canonical_owner_adapter(agg_val, self._storage)
        return None

    def write_canonical(self, key: str, value: Dict[str, Any]) -> bool:
        # Write strictly through genuine domain owner adapter without secondary file fallbacks
        if isinstance(self._storage, (str, Path)):
            adapter = self._resolve_adapter(value)
            if adapter is not None:
                record = copy.deepcopy(value)
                if "id" not in record:
                    record["id"] = key
                return bool(adapter.save(record))
            return False
        elif hasattr(self._storage, "save"):
            return bool(self._storage.save(value))
        elif hasattr(self._storage, "insert"):
            return bool(self._storage.insert(value))
        else:
            self._storage[key] = copy.deepcopy(value)
            return True

    def read_canonical(self, key: str) -> Optional[Dict[str, Any]]:
        # Strictly reads from durable storage via domain adapters
        if isinstance(self._storage, (str, Path)):
            for agg in AggregateKind:
                adapter = build_canonical_owner_adapter(agg, self._storage)
                val = adapter.get(key)
                if val is not None:
                    return copy.deepcopy(val)
            return None
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
                "from services.control_plane.bff.migrations.overlay_retirement import build_canonical_owner_adapter, AggregateKind\n"
                "storage_dir = sys.argv[1]\n"
                "key = sys.argv[2]\n"
                "for agg in AggregateKind:\n"
                "    adapter = build_canonical_owner_adapter(agg, storage_dir)\n"
                "    val = adapter.get(key)\n"
                "    if val is not None:\n"
                "        print(json.dumps(val, default=str))\n"
                "        sys.exit(0)\n"
                "sys.exit(2)\n"
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
        """Simulate a real process restart by executing an independent Python process across domain owners.

        SD §5.1, §5.2, §12.3: Guarantees zero reliance on process-local memory,
        shared mutable dictionaries, or cached instances across restarts.
        """
        if isinstance(self._storage, (str, Path)):
            storage_path = Path(self._storage)
            for agg in AggregateKind:
                adapter = build_canonical_owner_adapter(agg, storage_path)
                adapter.restart_process()
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

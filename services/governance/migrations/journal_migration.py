"""Governed migration and disposition for legacy Decision Journal rows.

Per SA ADR-04 and SD §5.3:
- Migrates existing journal rows with immutable IDs, SHA-256 checksums, and conflict detection.
- Supports resumable, tenant-scoped dry-run and execution.
- Disposes legacy rows cleanly so zero duplicate writers or replication bridges remain.
- Verifies fresh query parity between source records and destination stores.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.governance.decision_journal import (
    CANONICAL_WRITE_AUTHORITY,
    DecisionJournalStores,
    create_entry,
    get_entry,
    list_entries,
)


def compute_journal_row_checksum(record: Dict[str, Any]) -> str:
    """Compute a deterministic SHA-256 checksum of journal content fields."""
    normalized = {
        "title": str(record.get("title") or "").strip(),
        "body": str(record.get("body") or record.get("decision") or "").strip(),
        "tags": sorted(list(record.get("tags") or [])),
        "visibility": str(record.get("visibility") or "private").strip().lower(),
        "linkedStrategyIds": sorted(list(record.get("linkedStrategyIds") or [])),
        "linkedPersonaIds": sorted(list(record.get("linkedPersonaIds") or [])),
    }
    dumped = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(dumped.encode("utf-8")).hexdigest()


@dataclass
class JournalMigrationItem:
    source_id: str
    entry_id: str
    checksum: str
    source_tenant: Optional[str]
    target_tenant: str
    target_actor: str
    status: str  # "migrated", "skipped_identical", "conflict", "dry_run_pending"
    error: Optional[str] = None
    disposed: bool = False


@dataclass
class JournalMigrationReport:
    total_scanned: int = 0
    total_migrated: int = 0
    total_skipped: int = 0
    total_conflicts: int = 0
    dry_run: bool = True
    target_tenant_id: str = ""
    audit_events_recorded: int = 0
    inventory: List[Dict[str, Any]] = field(default_factory=list)
    disposition_evidence: Dict[str, Any] = field(default_factory=dict)
    items: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class JournalMigrationEngine:
    """Resumable, tenant-scoped migration runner for decision journal entries."""

    def __init__(
        self,
        destination_stores: DecisionJournalStores,
        *,
        checkpoint_path: Optional[Path | str] = None,
    ) -> None:
        self.destination_stores = destination_stores
        self.checkpoint_path = Path(checkpoint_path) if checkpoint_path else None
        self._checkpoint: Dict[str, str] = {}
        if self.checkpoint_path and self.checkpoint_path.exists():
            try:
                self._checkpoint = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            except Exception:
                self._checkpoint = {}

    def _save_checkpoint(self, entry_id: str, checksum: str) -> None:
        if not self.checkpoint_path:
            return
        self._checkpoint[entry_id] = checksum
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.write_text(json.dumps(self._checkpoint, indent=2), encoding="utf-8")

    def run_migration(
        self,
        source_records: Sequence[Dict[str, Any]],
        *,
        target_tenant_id: str,
        default_actor_id: str = "system-migration",
        dry_run: bool = True,
        dispose_source: bool = False,
        source_store: Optional[Any] = None,
    ) -> JournalMigrationReport:
        report = JournalMigrationReport(
            total_scanned=len(source_records),
            dry_run=dry_run,
            target_tenant_id=target_tenant_id,
        )

        for record in source_records:
            entry_id = str(record.get("id") or record.get("entryId") or record.get("entry_id") or "").strip()
            if not entry_id:
                continue

            checksum = compute_journal_row_checksum(record)
            source_tenant = record.get("tenant_id") or record.get("tenantId")
            actor = str(
                record.get("author")
                or record.get("createdBy")
                or record.get("actor_id")
                or record.get("userId")
                or record.get("user_id")
                or default_actor_id
            ).strip()
            user_id = str(record.get("userId") or record.get("user_id") or actor).strip()
            created_at = str(record.get("createdAt") or record.get("created_at") or "2026-09-06T00:00:00Z")

            report.inventory.append({
                "id": entry_id,
                "checksum": checksum,
                "author": actor,
                "source_tenant": source_tenant,
            })

            # Check destination store
            existing = self.destination_stores.entries.get(entry_id)
            if existing is not None:
                existing_tenant = str(existing.get("tenant_id") or existing.get("tenantId") or "").strip()
                if existing_tenant and existing_tenant != target_tenant_id:
                    # Cross-tenant destination collision: entry ID already exists for another tenant
                    report.total_conflicts += 1
                    report.items.append(
                        asdict(
                            JournalMigrationItem(
                                source_id=entry_id,
                                entry_id=entry_id,
                                checksum=checksum,
                                source_tenant=source_tenant,
                                target_tenant=target_tenant_id,
                                target_actor=actor,
                                status="conflict",
                                error=f"Destination ID collision across tenants: existing record owned by {existing_tenant!r}, target is {target_tenant_id!r}",
                                disposed=False,
                            )
                        )
                    )
                    continue

                existing_checksum = compute_journal_row_checksum(existing)
                if existing_checksum == checksum:
                    report.total_skipped += 1
                    disposed_status = bool(dispose_source and not dry_run)
                    if disposed_status and source_store is not None:
                        if hasattr(source_store, "delete_decision_journal_entry"):
                            source_store.delete_decision_journal_entry(entry_id)
                        elif hasattr(source_store, "_journal") and isinstance(source_store._journal, dict):
                            source_store._journal.pop(entry_id, None)
                    report.items.append(
                        asdict(
                            JournalMigrationItem(
                                source_id=entry_id,
                                entry_id=entry_id,
                                checksum=checksum,
                                source_tenant=source_tenant,
                                target_tenant=target_tenant_id,
                                target_actor=actor,
                                status="skipped_identical",
                                disposed=disposed_status,
                            )
                        )
                    )
                    continue
                else:
                    report.total_conflicts += 1
                    report.items.append(
                        asdict(
                            JournalMigrationItem(
                                source_id=entry_id,
                                entry_id=entry_id,
                                checksum=checksum,
                                source_tenant=source_tenant,
                                target_tenant=target_tenant_id,
                                target_actor=actor,
                                status="conflict",
                                error=f"Checksum mismatch with existing entry in destination (existing: {existing_checksum})",
                                disposed=False,
                            )
                        )
                    )
                    continue

            # Checkpoint check for resumability
            if entry_id in self._checkpoint and self._checkpoint[entry_id] == checksum:
                report.total_skipped += 1
                disposed_status = bool(dispose_source and not dry_run)
                if disposed_status and source_store is not None:
                    if hasattr(source_store, "delete_decision_journal_entry"):
                        source_store.delete_decision_journal_entry(entry_id)
                    elif hasattr(source_store, "_journal") and isinstance(source_store._journal, dict):
                        source_store._journal.pop(entry_id, None)
                report.items.append(
                    asdict(
                        JournalMigrationItem(
                            source_id=entry_id,
                            entry_id=entry_id,
                            checksum=checksum,
                            source_tenant=source_tenant,
                            target_tenant=target_tenant_id,
                            target_actor=actor,
                            status="skipped_identical",
                            disposed=disposed_status,
                        )
                    )
                )
                continue

            if dry_run:
                report.total_migrated += 1
                report.items.append(
                    asdict(
                        JournalMigrationItem(
                            source_id=entry_id,
                            entry_id=entry_id,
                            checksum=checksum,
                            source_tenant=source_tenant,
                            target_tenant=target_tenant_id,
                            target_actor=actor,
                            status="dry_run_pending",
                            disposed=False,
                        )
                    )
                )
            else:
                title = str(record.get("title") or "Untitled Migration Entry").strip()
                body = str(record.get("body") or record.get("decision") or "").strip()
                tags = list(record.get("tags") or [])
                visibility = str(record.get("visibility") or "private").strip().lower()

                create_entry(
                    self.destination_stores,
                    entry_id=entry_id,
                    title=title,
                    body=body,
                    actor_id=actor,
                    tenant_id=target_tenant_id,
                    user_id=user_id,
                    created_at=created_at,
                    tags=tags,
                    linked_strategy_ids=list(record.get("linkedStrategyIds") or []),
                    linked_persona_ids=list(record.get("linkedPersonaIds") or []),
                    visibility=visibility,
                )
                if self.destination_stores.audit is not None:
                    audit_id = f"aud-mig-{uuid.uuid4().hex[:12]}"
                    try:
                        self.destination_stores.audit.put({
                            "audit_id": audit_id,
                            "auditId": audit_id,
                            "action": "governance.decision_journal.migrated",
                            "target": {"type": "DecisionJournalEntry", "id": entry_id},
                            "actorId": actor,
                            "actor_id": actor,
                            "tenantId": target_tenant_id,
                            "tenant_id": target_tenant_id,
                            "userId": user_id,
                            "user_id": user_id,
                            "recordedAt": created_at,
                            "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
                            "source_checksum": checksum,
                            "source_id": entry_id,
                            "disposed": bool(dispose_source),
                        })
                        report.audit_events_recorded += 1
                    except Exception:
                        pass

                self._save_checkpoint(entry_id, checksum)
                disposed_status = bool(dispose_source)
                if disposed_status and source_store is not None:
                    if hasattr(source_store, "delete_decision_journal_entry"):
                        source_store.delete_decision_journal_entry(entry_id)
                    elif hasattr(source_store, "_journal") and isinstance(source_store._journal, dict):
                        source_store._journal.pop(entry_id, None)

                report.total_migrated += 1
                report.items.append(
                    asdict(
                        JournalMigrationItem(
                            source_id=entry_id,
                            entry_id=entry_id,
                            checksum=checksum,
                            source_tenant=source_tenant,
                            target_tenant=target_tenant_id,
                            target_actor=actor,
                            status="migrated",
                            disposed=disposed_status,
                        )
                    )
                )

        report.disposition_evidence = {
            "disposed": bool(dispose_source and not dry_run),
            "target_tenant": target_tenant_id,
            "disposed_entry_ids": [it["entry_id"] for it in report.items if it.get("disposed")],
            "total_disposed": len([it for it in report.items if it.get("disposed")]),
            "source_store_type": type(source_store).__name__ if source_store is not None else None,
        }
        return report



def verify_migration_parity(
    source_records: Sequence[Dict[str, Any]],
    destination_stores: DecisionJournalStores,
    target_tenant_id: str,
) -> Tuple[bool, List[str]]:
    """Verify that every source record is faithfully readable from destination stores."""
    discrepancies: List[str] = []
    for record in source_records:
        entry_id = str(record.get("id") or record.get("entryId") or record.get("entry_id") or "").strip()
        dest = get_entry(destination_stores, entry_id, tenant_id=target_tenant_id)
        if dest is None:
            discrepancies.append(f"Entry {entry_id} not found in destination store for tenant {target_tenant_id}")
            continue

        src_title = str(record.get("title") or "").strip()
        if dest.get("title") != src_title:
            discrepancies.append(f"Title mismatch for {entry_id}: expected {src_title!r}, got {dest.get('title')!r}")

        src_body = str(record.get("body") or record.get("decision") or "").strip()
        if dest.get("body") != src_body:
            discrepancies.append(f"Body mismatch for {entry_id}")

        src_vis = str(record.get("visibility") or "private").strip().lower()
        if dest.get("visibility") != src_vis:
            discrepancies.append(f"Visibility mismatch for {entry_id}")

    return (len(discrepancies) == 0, discrepancies)

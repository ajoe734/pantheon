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


def _dispose_source_record(source_store: Optional[Any], entry_id: str) -> bool:
    """Verify durable legacy source removal via source_store delete and readback."""
    if source_store is None:
        return False
    deleted = False
    if hasattr(source_store, "delete_decision_journal_entry") and callable(source_store.delete_decision_journal_entry):
        try:
            source_store.delete_decision_journal_entry(entry_id)
            deleted = True
        except Exception:
            deleted = False
    elif hasattr(source_store, "_journal"):
        if isinstance(source_store._journal, dict):
            deleted = bool(source_store._journal.pop(entry_id, None) is not None)
        elif hasattr(source_store._journal, "delete") and callable(source_store._journal.delete):
            deleted = bool(source_store._journal.delete(entry_id))
    elif hasattr(source_store, "delete") and callable(source_store.delete):
        try:
            deleted = bool(source_store.delete(entry_id))
        except Exception:
            deleted = False

    if deleted:
        if hasattr(source_store, "get_journal_entry") and callable(source_store.get_journal_entry):
            try:
                return source_store.get_journal_entry(entry_id) is None
            except Exception:
                return False
        elif hasattr(source_store, "_journal") and isinstance(source_store._journal, dict):
            return entry_id not in source_store._journal
        return True
    return False


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

    def _find_migration_audit(
        self,
        entry_id: str,
        checksum: str,
        target_tenant_id: str,
    ) -> Optional[Dict[str, Any]]:
        if self.destination_stores.audit is None:
            return None
        try:
            records = self.destination_stores.audit.list_all()
            for rec in records:
                if not isinstance(rec, dict):
                    continue
                target = rec.get("target") or {}
                target_id = target.get("id") if isinstance(target, dict) else None
                rec_id = target_id or rec.get("source_id") or rec.get("entry_id")
                rec_tenant = str(rec.get("tenant_id") or rec.get("tenantId") or "").strip()
                rec_checksum = rec.get("source_checksum")
                if rec_id == entry_id and rec_tenant == target_tenant_id:
                    if rec_checksum is None or rec_checksum == checksum:
                        return rec
        except Exception:
            pass
        return None

    def _ensure_durable_migration_audit(
        self,
        entry_id: str,
        checksum: str,
        target_tenant_id: str,
        actor: str,
        user_id: str,
        created_at: str,
        *,
        action: str = "governance.decision_journal.migrated",
        disposed: bool = False,
    ) -> bool:
        if self.destination_stores.audit is None:
            return False
        existing = self._find_migration_audit(entry_id, checksum, target_tenant_id)
        if existing is not None:
            return True
        try:
            audit_id = f"aud-mig-{uuid.uuid4().hex[:12]}"
            audit_rec = {
                "audit_id": audit_id,
                "auditId": audit_id,
                "action": action,
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
                "disposed": disposed,
            }
            self.destination_stores.audit.put(audit_rec)
            return self._find_migration_audit(entry_id, checksum, target_tenant_id) is not None
        except Exception:
            return False

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

            # Check source tenant: reject source from another known tenant
            if source_tenant and str(source_tenant).strip() != target_tenant_id:
                report.total_conflicts += 1
                report.items.append(
                    asdict(
                        JournalMigrationItem(
                            source_id=entry_id,
                            entry_id=entry_id,
                            checksum=checksum,
                            source_tenant=str(source_tenant).strip(),
                            target_tenant=target_tenant_id,
                            target_actor=actor,
                            status="conflict",
                            error=f"Source tenant {source_tenant!r} does not match target tenant {target_tenant_id!r}",
                            disposed=False,
                        )
                    )
                )
                continue

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

                # Verify complete principal ownership across destination and source:
                existing_actor = str(existing.get("createdBy") or existing.get("actor_id") or "").strip()
                existing_user = str(existing.get("userId") or existing.get("user_id") or existing_actor).strip()
                source_actor = str(actor).strip()
                source_user = str(user_id).strip()
                if (existing_actor and source_actor and existing_actor != source_actor) or \
                   (existing_user and source_user and existing_user != source_user):
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
                                error=f"Principal ownership mismatch in destination: existing owned by {existing_actor!r}/{existing_user!r}, source is {source_actor!r}/{source_user!r}",
                                disposed=False,
                            )
                        )
                    )
                    continue

                existing_checksum = compute_journal_row_checksum(existing)
                if existing_checksum != checksum:
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

                if existing_tenant == target_tenant_id:
                    report.total_skipped += 1
                    disposed_status = False
                    if dispose_source and not dry_run and source_store is not None:
                        has_audit = self._ensure_durable_migration_audit(
                            entry_id,
                            checksum,
                            target_tenant_id,
                            actor,
                            user_id,
                            created_at,
                            disposed=True,
                        )
                        if has_audit:
                            disposed_status = _dispose_source_record(source_store, entry_id)
                    checkpoint_key = f"{target_tenant_id}:{actor}:{entry_id}"
                    self._save_checkpoint(checkpoint_key, checksum)
                    self._save_checkpoint(entry_id, checksum)
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
                    # Existing destination entry is an unscoped legacy row: scope and migrate it
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
                        continue
                    else:
                        migrated_row = dict(existing)
                        migrated_row["tenant_id"] = target_tenant_id
                        migrated_row["tenantId"] = target_tenant_id
                        migrated_row["createdBy"] = actor
                        migrated_row["actor_id"] = actor
                        migrated_row["userId"] = user_id
                        migrated_row["user_id"] = user_id
                        migrated_row["updatedAt"] = created_at
                        migrated_row["canonicalWriteAuthority"] = CANONICAL_WRITE_AUTHORITY
                        self.destination_stores.entries.put(migrated_row)

                        if self.destination_stores.audit is not None:
                            audit_id = f"aud-mig-{uuid.uuid4().hex[:12]}"
                            self.destination_stores.audit.put({
                                "audit_id": audit_id,
                                "action": "governance.decision_journal.legacy_scoped",
                                "target": {"type": "DecisionJournalEntry", "id": entry_id},
                                "actorId": actor,
                                "actor_id": actor,
                                "tenantId": target_tenant_id,
                                "tenant_id": target_tenant_id,
                                "userId": user_id,
                                "user_id": user_id,
                                "recordedAt": created_at,
                                "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
                                "diff": {
                                    "changes": [
                                        {"field": "tenant_id", "before": "", "after": target_tenant_id},
                                        {"field": "createdBy", "before": existing.get("createdBy") or "", "after": actor},
                                    ]
                                },
                            })

                        # Readback and checksum verification with destination scope
                        readback = get_entry(self.destination_stores, entry_id, tenant_id=target_tenant_id, actor_id=actor, user_id=user_id)
                        if readback is None or compute_journal_row_checksum(readback) != checksum:
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
                                        error="Destination readback/checksum verification failed after scoping legacy row",
                                        disposed=False,
                                    )
                                )
                            )
                            continue

                        checkpoint_key = f"{target_tenant_id}:{actor}:{entry_id}"
                        self._save_checkpoint(checkpoint_key, checksum)
                        self._save_checkpoint(entry_id, checksum)

                        disposed_status = False
                        if dispose_source and not dry_run and source_store is not None:
                            has_audit = self._ensure_durable_migration_audit(
                                entry_id,
                                checksum,
                                target_tenant_id,
                                actor,
                                user_id,
                                created_at,
                                action="governance.decision_journal.legacy_scoped",
                                disposed=True,
                            )
                            if has_audit:
                                disposed_status = _dispose_source_record(source_store, entry_id)

                        report.total_migrated += 1
                        report.audit_events_recorded += 1
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
                        continue

            # Checkpoint check for resumability with destination readback
            checkpoint_key = f"{target_tenant_id}:{actor}:{entry_id}"
            has_checkpoint = (checkpoint_key in self._checkpoint and self._checkpoint[checkpoint_key] == checksum) or \
                             (entry_id in self._checkpoint and self._checkpoint[entry_id] == checksum)
            if has_checkpoint:
                dest_entry = self.destination_stores.entries.get(entry_id)
                if dest_entry is not None:
                    dest_tenant = str(dest_entry.get("tenant_id") or dest_entry.get("tenantId") or "").strip()
                    dest_actor = str(dest_entry.get("createdBy") or dest_entry.get("actor_id") or "").strip()
                    dest_user = str(dest_entry.get("userId") or dest_entry.get("user_id") or dest_actor).strip()
                    if dest_tenant == target_tenant_id and \
                       (not dest_actor or dest_actor == actor or dest_user == user_id):
                        if compute_journal_row_checksum(dest_entry) == checksum:
                            report.total_skipped += 1
                            disposed_status = False
                            if dispose_source and not dry_run and source_store is not None:
                                has_audit = self._ensure_durable_migration_audit(
                                    entry_id,
                                    checksum,
                                    target_tenant_id,
                                    actor,
                                    user_id,
                                    created_at,
                                    disposed=True,
                                )
                                if has_audit:
                                    disposed_status = _dispose_source_record(source_store, entry_id)
                                    report.audit_events_recorded += 1
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
                        "disposed": bool(dispose_source and source_store is not None),
                    })
                    report.audit_events_recorded += 1

                # Readback and checksum verification
                readback = get_entry(self.destination_stores, entry_id, tenant_id=target_tenant_id, actor_id=actor, user_id=user_id)
                if readback is None or compute_journal_row_checksum(readback) != checksum:
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
                                error="Destination readback/checksum verification failed after migration",
                                disposed=False,
                            )
                        )
                    )
                    continue

                self._save_checkpoint(checkpoint_key, checksum)
                self._save_checkpoint(entry_id, checksum)
                disposed_status = False
                if dispose_source and source_store is not None:
                    has_audit = self._ensure_durable_migration_audit(
                        entry_id,
                        checksum,
                        target_tenant_id,
                        actor,
                        user_id,
                        created_at,
                        disposed=True,
                    )
                    if has_audit:
                        disposed_status = _dispose_source_record(source_store, entry_id)

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

        total_disp = len([it for it in report.items if it.get("disposed")])
        report.disposition_evidence = {
            "disposed": bool(dispose_source and not dry_run and source_store is not None and total_disp > 0),
            "target_tenant": target_tenant_id,
            "disposed_entry_ids": [it["entry_id"] for it in report.items if it.get("disposed")],
            "total_disposed": total_disp,
            "source_store_type": type(source_store).__name__ if source_store is not None else None,
            "verified_durable_removal": bool(source_store is not None and total_disp > 0),
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
        actor = str(record.get("createdBy") or record.get("author") or record.get("actor_id") or "migration-worker").strip()
        user_id = str(record.get("userId") or record.get("user_id") or actor).strip()
        dest = get_entry(destination_stores, entry_id, tenant_id=target_tenant_id, actor_id=actor, user_id=user_id)
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

"""Tests for Decision Journal durable owner per SA ADR-04 and SD §5.3 scorecard.

Covers:
- Transactional CAS & version increments
- Append-only audit trail with diff calculation
- Outbox event stream generation
- Tenant and user isolation (Actor A/B, Tenant A/B, same operator ID across tenants)
- Private supplied-ID collision rejection (never leaks other principal's record on insert-if-absent)
- Scope-bound idempotency and replay
- Legacy unscoped rows isolation
- Governed migration: dry-run, checksums, conflicts, resumability, and fresh query parity
- Fresh-process restart parity
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
import unittest.mock
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.governance.decision_journal import (
    CANONICAL_WRITE_AUTHORITY,
    CoordinatingJsonGovernanceRecordStore,
    DecisionJournalCollisionError,
    DecisionJournalConcurrencyError,
    DecisionJournalValidationError,
    build_decision_journal_stores,
    create_entry,
    domain_creation_idempotency_key,
    get_entry,
    list_audit_events,
    list_entries,
    list_outbox_events,
    patch_entry,
    patch_idempotency_key,
)
from services.governance.migrations.journal_migration import (
    JournalMigrationEngine,
    compute_journal_row_checksum,
    verify_migration_parity,
)


class TestDecisionJournalGovernanceOwner(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.stores = build_decision_journal_stores(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_create_entry_and_cas_versioning(self) -> None:
        created = create_entry(
            self.stores,
            entry_id="dje-001",
            title="Initial Freeze Decision",
            body="Freeze order placed on canary.",
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
            tags=["freeze", "canary"],
            visibility="private",
        )
        self.assertEqual(created["id"], "dje-001")
        self.assertEqual(created["version"], 1)
        self.assertEqual(created["canonicalWriteAuthority"], CANONICAL_WRITE_AUTHORITY)
        self.assertEqual(created["tenant_id"], "tenant-alpha")
        self.assertEqual(created["createdBy"], "operator-alice")

        # Patch entry -> version increments to 2
        patched = patch_entry(
            self.stores,
            "dje-001",
            patch={"title": "Updated Freeze Decision", "body": "Freeze order verified."},
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            idempotency_key="idem-patch-1",
            request_hash="hash-1",
            patched_at="2026-09-08T00:05:00Z",
        )
        self.assertIsNotNone(patched)
        self.assertEqual(patched["status"], "updated")
        self.assertEqual(patched["entry"]["version"], 2)
        self.assertEqual(patched["entry"]["title"], "Updated Freeze Decision")

        # Second patch -> version increments to 3
        patched_2 = patch_entry(
            self.stores,
            "dje-001",
            patch={"body": "Final verification complete."},
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            idempotency_key="idem-patch-2",
            request_hash="hash-2",
            patched_at="2026-09-08T00:10:00Z",
        )
        self.assertIsNotNone(patched_2)
        self.assertEqual(patched_2["entry"]["version"], 3)

    def test_append_only_audit_trail_and_diff(self) -> None:
        create_entry(
            self.stores,
            entry_id="dje-audit-01",
            title="Risk Check",
            body="Initial risk assessment.",
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T01:00:00Z",
            tags=["risk"],
        )
        patched = patch_entry(
            self.stores,
            "dje-audit-01",
            patch={"body": "Escalated risk assessment.", "tags": ["risk", "escalated"]},
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            idempotency_key="idem-audit-1",
            request_hash="hash-audit-1",
            patched_at="2026-09-08T01:05:00Z",
        )
        self.assertIsNotNone(patched)
        audit = patched["audit"]
        self.assertEqual(audit["action"], "governance.decision_journal.merge_patch")
        self.assertEqual(audit["target"]["id"], "dje-audit-01")
        self.assertIn("body", audit["diff"]["changedFields"])
        self.assertIn("tags", audit["diff"]["changedFields"])

        # Check listed audit events: fail-closed without actor, visible to authorized actor
        unscoped_audits = list_audit_events(self.stores, entry_id="dje-audit-01", tenant_id="tenant-alpha")
        self.assertEqual(unscoped_audits, [])

        audits = list_audit_events(self.stores, entry_id="dje-audit-01", tenant_id="tenant-alpha", actor_id="operator-alice")
        self.assertEqual(len(audits), 1)
        self.assertEqual(audits[0]["idempotencyKey"], "idem-audit-1")

    def test_outbox_event_generation(self) -> None:
        create_entry(
            self.stores,
            entry_id="dje-outbox-01",
            title="Outbox Test",
            body="Testing event generation.",
            actor_id="operator-bob",
            tenant_id="tenant-beta",
            created_at="2026-09-08T02:00:00Z",
        )
        patch_entry(
            self.stores,
            "dje-outbox-01",
            patch={"title": "Outbox Test Updated"},
            actor_id="operator-bob",
            tenant_id="tenant-beta",
            idempotency_key="idem-outbox-1",
            request_hash="hash-outbox-1",
            patched_at="2026-09-08T02:05:00Z",
        )
        events = list_outbox_events(self.stores, entry_id="dje-outbox-01", tenant_id="tenant-beta")
        self.assertEqual(len(events), 2)
        event_types = [e["event_type"] for e in events]
        self.assertIn("decision_journal.entry.created", event_types)
        self.assertIn("decision_journal.entry.updated", event_types)

    def test_tenant_and_user_isolation_matrix(self) -> None:
        # Actor A in Tenant A creates private entry
        create_entry(
            self.stores,
            entry_id="dje-iso-01",
            title="Alice Private",
            body="Alice secret notes",
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T03:00:00Z",
            visibility="private",
        )

        # Actor B in Tenant A lists -> should NOT see Alice's private entry
        b_list = list_entries(self.stores, tenant_id="tenant-alpha", actor_id="operator-bob")
        self.assertEqual(len(b_list), 0)

        # Actor B in Tenant A gets -> returns None
        b_get = get_entry(self.stores, "dje-iso-01", tenant_id="tenant-alpha", actor_id="operator-bob")
        self.assertIsNone(b_get)

        # Actor B in Tenant A attempts to patch -> returns None (fails closed)
        b_patch = patch_entry(
            self.stores,
            "dje-iso-01",
            patch={"title": "Bob Hijack"},
            actor_id="operator-bob",
            tenant_id="tenant-alpha",
            idempotency_key="idem-bob-patch",
            request_hash="hash-bob",
            patched_at="2026-09-08T03:05:00Z",
        )
        self.assertIsNone(b_patch)

        # Same operator ID (operator-alice) in Tenant B (cross-tenant isolation)
        alice_tenant_b_list = list_entries(self.stores, tenant_id="tenant-beta", actor_id="operator-alice")
        self.assertEqual(len(alice_tenant_b_list), 0)

        alice_tenant_b_get = get_entry(self.stores, "dje-iso-01", tenant_id="tenant-beta", actor_id="operator-alice")
        self.assertIsNone(alice_tenant_b_get)

        alice_tenant_b_patch = patch_entry(
            self.stores,
            "dje-iso-01",
            patch={"title": "Tenant B Alice Hijack"},
            actor_id="operator-alice",
            tenant_id="tenant-beta",
            idempotency_key="idem-tenant-b-alice",
            request_hash="hash-tb",
            patched_at="2026-09-08T03:10:00Z",
        )
        self.assertIsNone(alice_tenant_b_patch)

        # Alice in Tenant A can see and patch
        alice_get = get_entry(self.stores, "dje-iso-01", tenant_id="tenant-alpha", actor_id="operator-alice")
        self.assertIsNotNone(alice_get)
        self.assertEqual(alice_get["title"], "Alice Private")

    def test_reject_supplied_id_collision_across_actors_and_tenants(self) -> None:
        # Alice creates private entry in Tenant Alpha
        create_entry(
            self.stores,
            entry_id="dje-collision-01",
            title="Alice Entry",
            body="Secret content",
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T04:00:00Z",
            visibility="private",
        )

        # Bob in Tenant Alpha tries to create entry with same supplied ID
        with self.assertRaises(DecisionJournalCollisionError) as ctx_bob:
            create_entry(
                self.stores,
                entry_id="dje-collision-01",
                title="Bob Attempt",
                body="Attempted overwrite",
                actor_id="operator-bob",
                tenant_id="tenant-alpha",
                created_at="2026-09-08T04:01:00Z",
            )
        self.assertIn("collides with an existing record", str(ctx_bob.exception))

        # Alice in Tenant Beta (same operator ID, different tenant) tries to create with same ID
        with self.assertRaises(DecisionJournalCollisionError) as ctx_tenant:
            create_entry(
                self.stores,
                entry_id="dje-collision-01",
                title="Tenant Beta Alice Attempt",
                body="Attempted overwrite",
                actor_id="operator-alice",
                tenant_id="tenant-beta",
                created_at="2026-09-08T04:02:00Z",
            )
        self.assertIn("collides with an existing record", str(ctx_tenant.exception))

        # Legitimate idempotent create by same owner in same tenant succeeds without error
        recreated = create_entry(
            self.stores,
            entry_id="dje-collision-01",
            title="Alice Duplicate Request",
            body="Secret content",
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T04:03:00Z",
        )
        self.assertEqual(recreated["id"], "dje-collision-01")
        self.assertEqual(recreated["title"], "Alice Entry")  # Canonical preserved

    def test_scope_bound_idempotency(self) -> None:
        create_entry(
            self.stores,
            entry_id="dje-idem-01",
            title="Idempotency Baseline",
            body="Base body",
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T05:00:00Z",
        )

        # 1. Successful patch
        res1 = patch_entry(
            self.stores,
            "dje-idem-01",
            patch={"title": "Patched Title"},
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            idempotency_key="key-idem-test",
            request_hash="hash-matching",
            patched_at="2026-09-08T05:01:00Z",
        )
        self.assertEqual(res1["status"], "updated")

        # 2. Replay same key and same hash by same actor -> replayed
        res2 = patch_entry(
            self.stores,
            "dje-idem-01",
            patch={"title": "Patched Title"},
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            idempotency_key="key-idem-test",
            request_hash="hash-matching",
            patched_at="2026-09-08T05:02:00Z",
        )
        self.assertEqual(res2["status"], "replayed")

        # 3. Same key with different hash -> conflict
        res3 = patch_entry(
            self.stores,
            "dje-idem-01",
            patch={"title": "Conflicting Title"},
            actor_id="operator-alice",
            tenant_id="tenant-alpha",
            idempotency_key="key-idem-test",
            request_hash="hash-different",
            patched_at="2026-09-08T05:03:00Z",
        )
        self.assertEqual(res3["status"], "conflict")

    def test_legacy_unscoped_rows_isolation(self) -> None:
        # Simulate legacy row missing tenant_id and user_id
        legacy_record = {
            "id": "dje-legacy-01",
            "title": "Legacy Unscoped Entry",
            "body": "No tenant or actor bound.",
            "visibility": "private",
            "createdAt": "2026-09-01T00:00:00Z",
            "updatedAt": "2026-09-01T00:00:00Z",
            "version": 1,
            "canonicalWriteAuthority": CANONICAL_WRITE_AUTHORITY,
            "persistenceMode": "governance_json_store",
        }
        self.stores.entries.put(legacy_record)

        # Tenant-scoped query excludes unscoped legacy rows by default
        tenant_list = list_entries(self.stores, tenant_id="tenant-alpha", include_unscoped_legacy=False)
        self.assertEqual(len(tenant_list), 0)

        # Detail with tenant scope excludes unscoped legacy row
        tenant_get = get_entry(self.stores, "dje-legacy-01", tenant_id="tenant-alpha")
        self.assertIsNone(tenant_get)

        # When explicitly requested with include_unscoped_legacy=True
        unscoped_list = list_entries(self.stores, tenant_id="tenant-alpha", include_unscoped_legacy=True)
        self.assertEqual(len(unscoped_list), 1)
        self.assertEqual(unscoped_list[0]["id"], "dje-legacy-01")

    def test_resumable_migration_dry_run_checksums_and_parity(self) -> None:
        legacy_rows = [
            {
                "id": "leg-01",
                "title": "Legacy Entry 1",
                "body": "Description of legacy 1",
                "tags": ["legacy", "phase1"],
                "visibility": "team",
                "createdAt": "2026-09-01T10:00:00Z",
            },
            {
                "id": "leg-02",
                "title": "Legacy Entry 2",
                "body": "Description of legacy 2",
                "tags": ["legacy"],
                "visibility": "private",
                "createdBy": "operator-legacy",
                "createdAt": "2026-09-01T11:00:00Z",
            },
        ]

        checkpoint_file = Path(self.tmp_dir.name) / "migration_checkpoint.json"
        engine = JournalMigrationEngine(self.stores, checkpoint_path=checkpoint_file)

        # 1. Dry run
        dry_run_report = engine.run_migration(
            legacy_rows,
            target_tenant_id="tenant-target",
            dry_run=True,
        )
        self.assertTrue(dry_run_report.dry_run)
        self.assertEqual(dry_run_report.total_scanned, 2)
        self.assertEqual(dry_run_report.total_migrated, 2)
        self.assertEqual(dry_run_report.total_conflicts, 0)
        # Store should still be empty in destination
        self.assertEqual(len(list_entries(self.stores, tenant_id="tenant-target")), 0)

        # 2. Live execution
        live_report = engine.run_migration(
            legacy_rows,
            target_tenant_id="tenant-target",
            dry_run=False,
            dispose_source=True,
        )
        self.assertFalse(live_report.dry_run)
        self.assertEqual(live_report.total_migrated, 2)

        # 3. Verify parity
        parity_ok, discrepancies = verify_migration_parity(legacy_rows, self.stores, "tenant-target")
        self.assertTrue(parity_ok, f"Discrepancies: {discrepancies}")

        # 4. Resumability: rerun should skip identical rows
        rerun_report = engine.run_migration(
            legacy_rows,
            target_tenant_id="tenant-target",
            dry_run=False,
        )
        self.assertEqual(rerun_report.total_skipped, 2)
        self.assertEqual(rerun_report.total_migrated, 0)
        self.assertEqual(rerun_report.total_conflicts, 0)

        # 5. Conflict detection: conflicting row with different checksum
        conflict_rows = [
            {
                "id": "leg-01",
                "title": "Conflicting Modified Content",
                "body": "Different body completely.",
            }
        ]
        conflict_report = engine.run_migration(
            conflict_rows,
            target_tenant_id="tenant-target",
            dry_run=False,
        )
        self.assertEqual(conflict_report.total_conflicts, 1)

    def test_fresh_process_restart_parity(self) -> None:
        # Create entry using first stores instance
        create_entry(
            self.stores,
            entry_id="dje-restart-01",
            title="Durable Persistence",
            body="Must survive process restart.",
            actor_id="operator-chloe",
            tenant_id="tenant-gamma",
            created_at="2026-09-08T06:00:00Z",
            tags=["restart", "proof"],
        )

        # Re-build stores pointing to the exact same path (fresh process simulation)
        fresh_stores = build_decision_journal_stores(self.tmp_dir.name)
        fresh_entry = get_entry(fresh_stores, "dje-restart-01", tenant_id="tenant-gamma", actor_id="operator-chloe")
        self.assertIsNotNone(fresh_entry)
        self.assertEqual(fresh_entry["title"], "Durable Persistence")
        self.assertEqual(fresh_entry["tags"], ["restart", "proof"])

    def test_outbox_failure_rejects_create_and_rolls_back(self) -> None:
        with unittest.mock.patch.object(self.stores.outbox, "put", side_effect=OSError("synthetic outbox failure")):
            with self.assertRaises(OSError):
                create_entry(
                    self.stores,
                    entry_id="dje-fail-create",
                    title="Will Fail",
                    body="Should not persist on outbox failure",
                    actor_id="alice",
                    tenant_id="tenant-alpha",
                    created_at="2026-09-08T00:00:00Z",
                )
        self.assertIsNone(self.stores.entries.get("dje-fail-create"))

    def test_audit_failure_does_not_commit_entry_and_rolls_back(self) -> None:
        create_entry(
            self.stores,
            entry_id="dje-fail-audit",
            title="Initial Title",
            body="Initial Body",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        with unittest.mock.patch.object(self.stores.audit, "put", side_effect=OSError("synthetic audit failure")):
            with self.assertRaises(OSError):
                patch_entry(
                    self.stores,
                    "dje-fail-audit",
                    patch={"title": "Changed Title"},
                    actor_id="alice",
                    tenant_id="tenant-alpha",
                    idempotency_key="key-fail-audit",
                    request_hash="hash-fail-audit",
                    patched_at="2026-09-08T00:01:00Z",
                )
        fresh = build_decision_journal_stores(self.tmp_dir.name)
        entry = get_entry(fresh, "dje-fail-audit", tenant_id="tenant-alpha", actor_id="alice")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["title"], "Initial Title")

    def test_outbox_failure_does_not_commit_patch_and_rolls_back(self) -> None:
        create_entry(
            self.stores,
            entry_id="dje-fail-patch-outbox",
            title="Initial Title",
            body="Initial Body",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        with unittest.mock.patch.object(self.stores.outbox, "put", side_effect=OSError("synthetic patch outbox failure")):
            with self.assertRaises(OSError):
                patch_entry(
                    self.stores,
                    "dje-fail-patch-outbox",
                    patch={"title": "Changed Title"},
                    actor_id="alice",
                    tenant_id="tenant-alpha",
                    idempotency_key="key-fail-patch-outbox",
                    request_hash="hash-fail-patch-outbox",
                    patched_at="2026-09-08T00:01:00Z",
                )
        fresh = build_decision_journal_stores(self.tmp_dir.name)
        entry = get_entry(fresh, "dje-fail-patch-outbox", tenant_id="tenant-alpha", actor_id="alice")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["title"], "Initial Title")
        self.assertEqual(fresh.audit.list_all(), [])

    def test_patch_cannot_claim_unscoped_legacy(self) -> None:
        create_entry(
            self.stores,
            entry_id="legacy-unscoped-team",
            title="Unscoped Team Entry",
            body="Should fail closed on tenant-b patch",
            actor_id="alice",
            tenant_id=None,
            visibility="team",
            created_at="2026-09-08T00:00:00Z",
        )
        res = patch_entry(
            self.stores,
            "legacy-unscoped-team",
            patch={"title": "Claimed by bob"},
            actor_id="bob",
            tenant_id="tenant-b",
            idempotency_key="key-claim-attempt",
            request_hash="hash-claim-attempt",
            patched_at="2026-09-08T00:01:00Z",
        )
        self.assertIsNone(res)

    def test_migration_preserves_agora_author(self) -> None:
        engine = JournalMigrationEngine(self.stores)
        report = engine.run_migration(
            [
                {
                    "id": "old-agora-entry",
                    "title": "Agora Note",
                    "decision": "Decision content",
                    "author": "alice-agora",
                    "visibility": "private",
                }
            ],
            target_tenant_id="tenant-alpha",
            dry_run=False,
            dispose_source=True,
        )
        entry = get_entry(self.stores, "old-agora-entry", tenant_id="tenant-alpha", actor_id="alice-agora")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["createdBy"], "alice-agora")
        self.assertEqual(report.total_migrated, 1)
        self.assertGreater(report.audit_events_recorded, 0)

    def test_migration_rejects_cross_tenant_identical_collision(self) -> None:
        create_entry(
            self.stores,
            entry_id="dje-cross-coll",
            title="Alpha Entry",
            body="Body content",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        engine = JournalMigrationEngine(self.stores)
        report = engine.run_migration(
            [
                {
                    "id": "dje-cross-coll",
                    "title": "Alpha Entry",
                    "body": "Body content",
                    "author": "alice",
                    "visibility": "private",
                }
            ],
            target_tenant_id="tenant-beta",
            dry_run=False,
            dispose_source=True,
        )
        self.assertEqual(report.total_conflicts, 1)

    def test_migration_disposes_source_store_and_produces_evidence(self) -> None:
        class FakeSourceStore:
            def __init__(self):
                self._journal = {"leg-disp-01": {"title": "To Dispose"}}

        source = FakeSourceStore()
        engine = JournalMigrationEngine(self.stores)
        report = engine.run_migration(
            [
                {
                    "id": "leg-disp-01",
                    "title": "To Dispose",
                    "body": "Body",
                    "author": "alice",
                }
            ],
            target_tenant_id="tenant-alpha",
            dry_run=False,
            dispose_source=True,
            source_store=source,
        )
        self.assertEqual(report.total_migrated, 1)
        self.assertNotIn("leg-disp-01", source._journal)
        self.assertTrue(report.disposition_evidence["disposed"])
        self.assertEqual(report.disposition_evidence["total_disposed"], 1)
        self.assertEqual(len(report.inventory), 1)

    def test_concurrent_cas_conflict_handling(self) -> None:
        create_entry(
            self.stores,
            entry_id="dje-cas-conflict",
            title="Base Title",
            body="Base Body",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        with unittest.mock.patch.object(self.stores.entries, "compare_and_set", return_value=(False, None)):
            with self.assertRaises(DecisionJournalConcurrencyError):
                patch_entry(
                    self.stores,
                    "dje-cas-conflict",
                    patch={"title": "Concurrent update"},
                    actor_id="alice",
                    tenant_id="tenant-alpha",
                    idempotency_key="key-cas-conflict",
                    request_hash="hash-cas-conflict",
                    patched_at="2026-09-08T00:01:00Z",
                )
        fresh = build_decision_journal_stores(self.tmp_dir.name)
        entry = get_entry(fresh, "dje-cas-conflict", tenant_id="tenant-alpha", actor_id="alice")
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["title"], "Base Title")

    def test_migration_does_not_dispose_another_authors_identical_row(self) -> None:
        create_entry(
            self.stores,
            entry_id="dje-author-collision",
            title="Synthetic",
            body="Private synthetic body",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        source = {
            "id": "dje-author-collision",
            "title": "Synthetic",
            "body": "Private synthetic body",
            "createdBy": "bob",
            "actor_id": "bob",
            "userId": "bob",
            "user_id": "bob",
        }
        report = JournalMigrationEngine(self.stores).run_migration(
            [source],
            target_tenant_id="tenant-alpha",
            dry_run=False,
            dispose_source=True,
        )
        self.assertEqual(report.total_conflicts, 1)

    def test_migration_rejects_source_from_another_tenant(self) -> None:
        source = {
            "id": "source-foreign-tenant",
            "title": "Synthetic",
            "body": "Private foreign tenant body",
            "author": "bob",
            "tenant_id": "tenant-beta",
        }
        report = JournalMigrationEngine(self.stores).run_migration(
            [source],
            target_tenant_id="tenant-alpha",
            dry_run=False,
        )
        self.assertEqual(report.total_conflicts, 1)
        self.assertIsNone(get_entry(self.stores, "source-foreign-tenant", tenant_id="tenant-alpha"))

    def test_tenant_without_actor_must_not_list_private_record(self) -> None:
        create_entry(
            self.stores,
            entry_id="private-scoped-entry",
            title="Synthetic",
            body="original",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        self.assertEqual(list_entries(self.stores, tenant_id="tenant-alpha"), [])

    def test_legacy_without_scope_must_not_be_globally_visible(self) -> None:
        self.stores.entries.put(
            {"id": "legacy-unscoped", "title": "Synthetic legacy", "body": "private", "visibility": "private"}
        )
        self.assertEqual(list_entries(self.stores), [])

    def test_patch_without_tenant_must_not_change_tenant_record(self) -> None:
        create_entry(
            self.stores,
            entry_id="private-tenant-entry",
            title="Synthetic",
            body="original",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        result = patch_entry(
            self.stores,
            "private-tenant-entry",
            patch={"body": "changed without tenant"},
            actor_id="alice",
            tenant_id=None,
            idempotency_key="missing-tenant-key",
            request_hash="missing-tenant-hash",
            patched_at="2026-09-08T00:01:00Z",
        )
        self.assertIsNone(result)
        fresh = build_decision_journal_stores(self.tmp_dir.name)
        entry = get_entry(fresh, "private-tenant-entry", tenant_id="tenant-alpha", actor_id="alice")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["body"], "original")
        self.assertEqual(fresh.audit.list_all(), [])

    def test_independent_owner_instances_must_not_lose_committed_entries(self) -> None:
        other = build_decision_journal_stores(self.tmp_dir.name)
        create_entry(
            self.stores,
            entry_id="first-inst-entry",
            title="First",
            body="First body",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        create_entry(
            other,
            entry_id="second-inst-entry",
            title="Second",
            body="Second body",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        fresh = build_decision_journal_stores(self.tmp_dir.name)
        self.assertEqual(
            {r["id"] for r in list_entries(fresh, tenant_id="tenant-alpha", actor_id="alice")},
            {"first-inst-entry", "second-inst-entry"},
        )

    def test_failed_patch_must_not_survive_in_successful_interleaved_patch(self) -> None:
        create_entry(
            self.stores,
            entry_id="interleaved-target",
            title="Synthetic",
            body="original",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        original_put = self.stores.outbox.put
        interleaved = False

        def outbox_put(record):
            nonlocal interleaved
            if not interleaved:
                interleaved = True
                patch_entry(
                    self.stores,
                    "interleaved-target",
                    patch={"title": "Successful second patch"},
                    actor_id="alice",
                    tenant_id="tenant-alpha",
                    idempotency_key="successful-patch-b",
                    request_hash="successful-patch-b",
                    patched_at="2026-09-08T00:01:00Z",
                )
                raise OSError("synthetic first transaction outbox failure")
            return original_put(record)

        with unittest.mock.patch.object(self.stores.outbox, "put", side_effect=outbox_put):
            with self.assertRaises(OSError):
                patch_entry(
                    self.stores,
                    "interleaved-target",
                    patch={"body": "uncommitted first patch"},
                    actor_id="alice",
                    tenant_id="tenant-alpha",
                    idempotency_key="failed-patch-a",
                    request_hash="failed-patch-a",
                    patched_at="2026-09-08T00:01:00Z",
                )
        fresh = build_decision_journal_stores(self.tmp_dir.name)
        row = get_entry(fresh, "interleaved-target", tenant_id="tenant-alpha", actor_id="alice")
        self.assertEqual(row["title"], "Successful second patch")
        self.assertEqual(row["body"], "original", "failed mutation persisted through a concurrent successful patch")

    def test_migration_must_scope_existing_selected_owner_legacy_row(self) -> None:
        source = {
            "id": "legacy-destination-row",
            "title": "Synthetic",
            "body": "Legacy",
            "createdBy": "alice",
            "visibility": "private",
        }
        self.stores.entries.put(source)
        report = JournalMigrationEngine(self.stores).run_migration(
            [source],
            target_tenant_id="tenant-alpha",
            dry_run=False,
        )
        migrated = get_entry(self.stores, "legacy-destination-row", tenant_id="tenant-alpha", actor_id="alice")
        self.assertIsNotNone(migrated, report.to_dict())
        self.assertEqual(migrated["createdBy"], "alice")

    def test_migration_does_not_claim_disposal_without_source(self) -> None:
        report = JournalMigrationEngine(self.stores).run_migration(
            [{"id": "legacy-no-source", "title": "Synthetic", "author": "alice"}],
            target_tenant_id="tenant-alpha",
            dry_run=False,
            dispose_source=True,
        )
        self.assertEqual(len(report.items), 1)
        self.assertFalse(report.items[0]["disposed"])

    def test_legacy_author_without_tenant_not_globally_visible(self) -> None:
        self.stores.entries.put(
            {"id": "legacy", "title": "legacy", "body": "private", "createdBy": "alice", "visibility": "private"}
        )
        self.assertEqual(list_entries(self.stores, actor_id="bob"), [])

    def test_migration_unscoped_destination_collision_preserves_author_and_source(self) -> None:
        old = {"id": "legacy", "title": "bob data", "body": "private bob", "createdBy": "bob", "visibility": "private"}
        self.stores.entries.put(old)
        source = {"id": "legacy", "title": "alice data", "body": "private alice", "createdBy": "alice", "visibility": "private"}
        source_store = build_decision_journal_stores(self.tmp_dir.name + "/source").entries
        source_store.put(source)
        report = JournalMigrationEngine(self.stores).run_migration(
            [source],
            target_tenant_id="tenant-alpha",
            dry_run=False,
            dispose_source=True,
            source_store=source_store,
        )
        self.assertEqual(report.total_conflicts, 1, str(report.to_dict()))
        self.assertEqual(self.stores.entries.get("legacy"), old)
        self.assertIsNotNone(source_store.get("legacy"))

    def test_idempotency_failure_cannot_survive_concurrent_success(self) -> None:
        create_entry(
            self.stores,
            entry_id="e-idem-fail",
            title="original",
            body="original",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
        )
        second = build_decision_journal_stores(self.tmp_dir.name)
        original_put = self.stores.idempotency.put

        def fail_success(record):
            if record.get("status") == "succeeded":
                patch_entry(
                    second,
                    "e-idem-fail",
                    patch={"title": "successful B"},
                    actor_id="alice",
                    tenant_id="tenant-alpha",
                    idempotency_key="key-success-b",
                    request_hash="hash-success-b",
                    patched_at="2026-09-08T00:01:00Z",
                )
                raise OSError("synthetic idempotency commit failure")
            return original_put(record)

        with unittest.mock.patch.object(self.stores.idempotency, "put", side_effect=fail_success):
            with self.assertRaises(OSError):
                patch_entry(
                    self.stores,
                    "e-idem-fail",
                    patch={"body": "failed A"},
                    actor_id="alice",
                    tenant_id="tenant-alpha",
                    idempotency_key="key-fail-a",
                    request_hash="hash-fail-a",
                    patched_at="2026-09-08T00:01:00Z",
                )
        fresh = build_decision_journal_stores(self.tmp_dir.name)
        row = get_entry(fresh, "e-idem-fail", tenant_id="tenant-alpha", actor_id="alice")
        self.assertEqual(row["title"], "successful B")
        self.assertEqual(row["body"], "original", "failed A remains in B while its audit/outbox were deleted")


    def test_legacy_authored_private_row_is_hidden_from_unscoped_consumers(self) -> None:
        """P1 (2): Legacy authored private rows must be hidden from unscoped readers."""
        from services.control_plane.bff.governance.decision_journal_write_owner import DecisionJournalOwnerAdapter
        from services.control_plane.bff.ports.operations_consultation import DomainDecisionJournalReaderPort

        self.stores.entries.put({
            "id": "legacy-authored",
            "title": "Synthetic legacy",
            "body": "private",
            "createdBy": "alice",
            "visibility": "private",
        })
        adapter = DecisionJournalOwnerAdapter(stores=self.stores)
        reader = DomainDecisionJournalReaderPort(data_dir=self.tmp_dir.name)
        observations = {
            "get_entry": get_entry(self.stores, "legacy-authored"),
            "list_entries": list_entries(self.stores),
            "adapter_detail": adapter.get_decision_journal_entry("legacy-authored"),
            "adapter_list": adapter.list_decision_journal_entries(),
            "global_detail": reader.get_decision_journal_entry("legacy-authored"),
            "global_list": reader.list_decision_journal_entries(),
        }
        self.assertFalse(any(observations.values()), observations)

        # Governed legacy access with matching actor can retrieve it
        legacy_get = get_entry(self.stores, "legacy-authored", actor_id="alice", include_unscoped_legacy=True)
        self.assertIsNotNone(legacy_get)
        self.assertEqual(legacy_get["id"], "legacy-authored")

    def test_concurrent_retry_must_not_report_uncommitted_success(self) -> None:
        """P1 (1): Retries must not observe replayed success or audit before CAS commit."""
        create_entry(
            self.stores,
            entry_id="entry-retry-cas",
            title="Original",
            body="synthetic private",
            actor_id="alice",
            tenant_id="tenant-a",
            created_at="2026-09-08T00:00:00Z",
        )
        other = build_decision_journal_stores(self.tmp_dir.name)
        observed = {}

        def fail_cas(before, candidate):
            observed["replay"] = patch_entry(
                other,
                "entry-retry-cas",
                patch={"title": "Uncommitted"},
                actor_id="alice",
                tenant_id="tenant-a",
                idempotency_key="retry-cas-key",
                request_hash="same-hash",
                patched_at="2026-09-08T00:01:00Z",
            )
            observed["persisted"] = get_entry(other, "entry-retry-cas", tenant_id="tenant-a", actor_id="alice")
            observed["audit_before_commit"] = list_audit_events(other, tenant_id="tenant-a", actor_id="alice")
            raise OSError("synthetic failure before entry CAS")

        with unittest.mock.patch.object(self.stores.entries, "compare_and_set", side_effect=fail_cas):
            with self.assertRaises(OSError):
                patch_entry(
                    self.stores,
                    "entry-retry-cas",
                    patch={"title": "Uncommitted"},
                    actor_id="alice",
                    tenant_id="tenant-a",
                    idempotency_key="retry-cas-key",
                    request_hash="same-hash",
                    patched_at="2026-09-08T00:01:00Z",
                )
        self.assertNotEqual(observed["replay"]["status"], "replayed", observed)
        self.assertEqual(observed["audit_before_commit"], [])

    def test_audit_without_actor_cannot_disclose_private_diff(self) -> None:
        """P1 (3): list_audit_events without actor or with wrong tenant denies private diff."""
        create_entry(
            self.stores,
            entry_id="entry-audit-priv",
            title="Original",
            body="synthetic private",
            actor_id="alice",
            tenant_id="tenant-a",
            created_at="2026-09-08T00:00:00Z",
        )
        patch_entry(
            self.stores,
            "entry-audit-priv",
            patch={"title": "Updated"},
            actor_id="alice",
            tenant_id="tenant-a",
            idempotency_key="audit-priv-key",
            request_hash="hash-priv",
            patched_at="2026-09-08T00:01:00Z",
        )
        # Without actor
        self.assertEqual(list_audit_events(self.stores, tenant_id="tenant-a"), [])
        # Blank / missing tenant
        self.assertEqual(list_audit_events(self.stores, tenant_id=""), [])
        self.assertEqual(list_audit_events(self.stores, tenant_id=None), [])
        # Wrong tenant
        self.assertEqual(list_audit_events(self.stores, tenant_id="tenant-b", actor_id="alice"), [])
        # Wrong actor
        self.assertEqual(list_audit_events(self.stores, tenant_id="tenant-a", actor_id="bob"), [])
        # Matching tenant and actor succeeds
        events = list_audit_events(self.stores, tenant_id="tenant-a", actor_id="alice")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["diff"]["after"]["title"], "Updated")

    def test_crash_recovery_after_cas_commits_secondary_stores_and_replays(self) -> None:
        """P1 (1): Crash recovery when process crashes right after CAS commits."""
        create_entry(
            self.stores,
            entry_id="crash-after-cas",
            title="Initial",
            body="Original Body",
            actor_id="alice",
            tenant_id="tenant-a",
            created_at="2026-09-08T00:00:00Z",
        )
        # Simulate in-flight reservation left after entry committed to version 2
        entry_v2 = {
            **self.stores.entries.get("crash-after-cas"),
            "version": 2,
            "title": "Committed Before Crash",
        }
        entry_snapshot = dict(entry_v2)

        staged_audit = {
            "auditId": "aud-crash-recovery-1",
            "action": "governance.decision_journal.merge_patch",
            "actorId": "alice",
            "tenantId": "tenant-a",
            "diff": {"after": entry_snapshot},
        }
        staged_outbox = {
            "event_id": "evt-crash-recovery-1",
            "event_type": "decision_journal.entry.updated",
            "tenant_id": "tenant-a",
            "data": entry_snapshot,
        }
        scoped_key = "tenant-a:alice:idem-crash-1"
        tx_id = "tx-crash-recovery-1"
        tx_record = {
            "tx_id": tx_id,
            "idempotency_key": scoped_key,
            "entry_id": "crash-after-cas",
            "request_hash": "hash-crash-1",
            "version": 2,
            "audit_id": "aud-crash-recovery-1",
            "audit": staged_audit,
            "outbox_id": "evt-crash-recovery-1",
            "outbox": staged_outbox,
            "entry": entry_snapshot,
            "before_entry": dict(self.stores.entries.get("crash-after-cas")),
            "patched_at": "2026-09-08T00:01:00Z",
            "actor_id": "alice",
            "tenant_id": "tenant-a",
            "user_id": "alice",
        }
        entry_v2["_last_tx_id"] = tx_id
        entry_v2["_tx_history"] = [tx_record]
        self.stores.entries.put(entry_v2)

        self.stores.idempotency.put({
            "idempotency_key": scoped_key,
            "raw_idempotency_key": "idem-crash-1",
            "tenant_id": "tenant-a",
            "actor_id": "alice",
            "request_hash": "hash-crash-1",
            "entry_id": "crash-after-cas",
            "candidate_version": 2,
            "candidate_entry": entry_v2,
            "staged_audit": staged_audit,
            "staged_outbox": staged_outbox,
            "status": "pending",
            "created_pid": 99999999,  # different pid simulates crashed process
            "created_thread": 1,
            "created_at": 0,
        })

        second = build_decision_journal_stores(self.tmp_dir.name)
        replayed = patch_entry(
            second,
            "crash-after-cas",
            patch={"title": "Committed Before Crash"},
            actor_id="alice",
            tenant_id="tenant-a",
            idempotency_key="idem-crash-1",
            request_hash="hash-crash-1",
            patched_at="2026-09-08T00:01:00Z",
        )
        self.assertIsNotNone(replayed)
        self.assertEqual(replayed["status"], "replayed")
        self.assertEqual(replayed["entry"]["title"], "Committed Before Crash")
        self.assertIsNotNone(second.audit.get("aud-crash-recovery-1"))

    def test_crash_recovery_before_cas_aborts_pending_reservation(self) -> None:
        """P1 (1): Crash recovery when process crashed before CAS commits."""
        create_entry(
            self.stores,
            entry_id="crash-before-cas",
            title="Initial",
            body="Original Body",
            actor_id="alice",
            tenant_id="tenant-a",
            created_at="2026-09-08T00:00:00Z",
        )
        scoped_key = "tenant-a:alice:idem-crash-pre"
        self.stores.idempotency.put({
            "idempotency_key": scoped_key,
            "raw_idempotency_key": "idem-crash-pre",
            "tenant_id": "tenant-a",
            "actor_id": "alice",
            "request_hash": "hash-crash-pre",
            "entry_id": "crash-before-cas",
            "candidate_version": 2,
            "candidate_entry": {"title": "Never Committed"},
            "staged_audit": None,
            "staged_outbox": None,
            "status": "pending",
            "created_pid": 99999999,
            "created_thread": 1,
            "created_at": 0,
        })

        second = build_decision_journal_stores(self.tmp_dir.name)
        res = patch_entry(
            second,
            "crash-before-cas",
            patch={"title": "Never Committed"},
            actor_id="alice",
            tenant_id="tenant-a",
            idempotency_key="idem-crash-pre",
            request_hash="hash-crash-pre",
            patched_at="2026-09-08T00:01:00Z",
        )
        self.assertIsNotNone(res)
        self.assertEqual(res["status"], "failed")
        self.assertEqual(res["reason"], "uncommitted_mutation_aborted")
        # Durable entry remains version 1
        entry = get_entry(second, "crash-before-cas", tenant_id="tenant-a", actor_id="alice")
        self.assertEqual(entry["version"], 1)
        self.assertEqual(entry["title"], "Initial")

    def test_missing_scope_matrix_across_readers_and_adapters(self) -> None:
        """P1 (2): Missing scope matrix across readers, adapters, and ports."""
        from services.control_plane.bff.governance.decision_journal_write_owner import DecisionJournalOwnerAdapter
        from services.control_plane.bff.ports.operations_consultation import DomainDecisionJournalReaderPort

        # Create private scoped row
        create_entry(
            self.stores,
            entry_id="matrix-scoped",
            title="Scoped Title",
            body="Scoped Body",
            actor_id="alice",
            tenant_id="tenant-alpha",
            created_at="2026-09-08T00:00:00Z",
            visibility="private",
        )
        # Direct insert legacy row
        self.stores.entries.put({
            "id": "matrix-legacy",
            "title": "Legacy Title",
            "body": "Legacy Body",
            "createdBy": "alice",
            "visibility": "private",
        })

        adapter = DecisionJournalOwnerAdapter(stores=self.stores)
        reader = DomainDecisionJournalReaderPort(data_dir=self.tmp_dir.name)

        # 1. Unscoped queries (no tenant, no actor)
        self.assertIsNone(get_entry(self.stores, "matrix-scoped"))
        self.assertIsNone(get_entry(self.stores, "matrix-legacy"))
        self.assertEqual(list_entries(self.stores), [])
        self.assertIsNone(adapter.get_decision_journal_entry("matrix-scoped"))
        self.assertIsNone(adapter.get_decision_journal_entry("matrix-legacy"))
        self.assertEqual(adapter.list_decision_journal_entries(), [])
        self.assertIsNone(reader.get_decision_journal_entry("matrix-scoped"))
        self.assertIsNone(reader.get_decision_journal_entry("matrix-legacy"))
        self.assertEqual(reader.list_decision_journal_entries(), [])

        # 2. Blank tenant ("   ")
        self.assertIsNone(get_entry(self.stores, "matrix-scoped", tenant_id="   ", actor_id="alice"))
        self.assertEqual(list_entries(self.stores, tenant_id="   ", actor_id="alice"), [])
        self.assertIsNone(adapter.get_decision_journal_entry("matrix-scoped", tenant_id="   ", actor_id="alice"))
        self.assertEqual(adapter.list_decision_journal_entries(tenant_id="   ", actor_id="alice"), [])

        # 3. Wrong tenant with right actor
        self.assertIsNone(get_entry(self.stores, "matrix-scoped", tenant_id="tenant-beta", actor_id="alice"))
        self.assertEqual(list_entries(self.stores, tenant_id="tenant-beta", actor_id="alice"), [])
        self.assertIsNone(adapter.get_decision_journal_entry("matrix-scoped", tenant_id="tenant-beta", actor_id="alice"))
        self.assertEqual(adapter.list_decision_journal_entries(tenant_id="tenant-beta", actor_id="alice"), [])

        # 4. Right tenant with wrong actor
        self.assertIsNone(get_entry(self.stores, "matrix-scoped", tenant_id="tenant-alpha", actor_id="bob"))
        self.assertEqual(list_entries(self.stores, tenant_id="tenant-alpha", actor_id="bob"), [])
        self.assertIsNone(adapter.get_decision_journal_entry("matrix-scoped", tenant_id="tenant-alpha", actor_id="bob"))
        self.assertEqual(adapter.list_decision_journal_entries(tenant_id="tenant-alpha", actor_id="bob"), [])

        # 5. Right tenant + right actor succeeds for scoped entry
        scoped_res = get_entry(self.stores, "matrix-scoped", tenant_id="tenant-alpha", actor_id="alice")
        self.assertIsNotNone(scoped_res)
        self.assertEqual(scoped_res["id"], "matrix-scoped")
        self.assertEqual(len(list_entries(self.stores, tenant_id="tenant-alpha", actor_id="alice")), 1)
        self.assertEqual(len(adapter.list_decision_journal_entries(tenant_id="tenant-alpha", actor_id="alice")), 1)
        self.assertEqual(len(reader.list_decision_journal_entries(tenant_id="tenant-alpha", actor_id="alice")), 1)


_CRASH_HELPER_SCRIPT = """
import os, sys
os.environ['GOVERNANCE_STORE_BACKEND'] = 'json'
from services.governance.decision_journal import build_decision_journal_stores, create_entry, patch_entry

mode = sys.argv[1]
data_dir = sys.argv[2]
stores = build_decision_journal_stores(data_dir)

def create(s):
    return create_entry(s, entry_id='entry', title='initial', body='body',
                        actor_id='alice', tenant_id='tenant-a', created_at='2026-09-08')

def patch(s, key, title, tenant='tenant-a'):
    return patch_entry(s, 'entry', patch={'title': title}, actor_id='alice',
                       tenant_id=tenant, idempotency_key=key, request_hash=key,
                       patched_at='2026-09-08')

if mode == 'crash-create':
    stores.outbox.put = lambda *_: os._exit(71)
    create(stores)
elif mode == 'crash-before-cas':
    stores.entries.compare_and_set = lambda *_: os._exit(72)
    patch(stores, 'crashed', 'never committed')
elif mode == 'crash-after-cas':
    stores.outbox.put = lambda *_: os._exit(73)
    patch(stores, 'crashed', 'committed before crash')
elif mode == 'crash-unauthorized':
    stores.entries.get = lambda *_: os._exit(74)
    patch_entry(stores, 'private-entry', patch={'title': 'bob attempt'}, actor_id='bob', tenant_id='tenant-b', idempotency_key='shared', request_hash='bobbob attempt', patched_at='2026-09-08')
    sys.exit(99)
elif mode == 'read-and-replay':
    import json
    from services.governance.decision_journal import get_entry
    get_entry(stores, 'private-entry', actor_id='alice', tenant_id='tenant-a')
    res = patch_entry(stores, 'private-entry', patch={'title': 'alice updated'}, actor_id='alice', tenant_id='tenant-a', idempotency_key='shared', request_hash='alicealice updated', patched_at='2026-09-08')
    print(json.dumps(res))
    sys.exit(0)
sys.exit(99)
"""


class TestDecisionJournalRecoveryAndIsolationRegressions(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="journal-regression-")
        self.addCleanup(self.tmp.cleanup)
        self.stores = build_decision_journal_stores(self.tmp.name)

    def _crash(self, mode: str, expected_exit: int) -> None:
        env = dict(os.environ, PYTHONPATH=".")
        result = subprocess.run(
            [sys.executable, "-c", _CRASH_HELPER_SCRIPT, mode, self.tmp.name],
            env=env,
            timeout=10,
        )
        self.assertEqual(result.returncode, expected_exit)
        self.stores = build_decision_journal_stores(self.tmp.name)

    def _create(self) -> dict:
        return create_entry(
            self.stores,
            entry_id="entry",
            title="initial",
            body="body",
            actor_id="alice",
            tenant_id="tenant-a",
            created_at="2026-09-08",
        )

    def _patch(self, key: str, title: str, tenant: Optional[str] = "tenant-a") -> Optional[dict]:
        return patch_entry(
            self.stores,
            "entry",
            patch={"title": title},
            actor_id="alice",
            tenant_id=tenant,
            idempotency_key=key,
            request_hash=key,
            patched_at="2026-09-08",
        )

    def test_create_crash_retry_preserves_outbox(self) -> None:
        self._crash("crash-create", 71)
        self._create()
        self.assertEqual(
            len(list_outbox_events(self.stores, tenant_id="tenant-a")),
            1,
            "create retry returns success but committed entry has no creation event",
        )

    def test_aborted_patch_not_replayed_after_other_patch_reuses_version(self) -> None:
        self._create()
        self._crash("crash-before-cas", 72)
        self._patch("other", "actually committed")
        result = self._patch("crashed", "never committed")
        self.assertNotEqual(
            result["status"],
            "replayed",
            "aborted patch falsely replayed and phantom audit/outbox appended: " + str(result),
        )

    def test_committed_patch_recovered_after_later_version(self) -> None:
        """Interleaving 1: crashed patch retries after later patch commits."""
        self._create()
        self._crash("crash-after-cas", 73)
        self._patch("other", "later committed")
        result = self._patch("crashed", "committed before crash")
        self.assertEqual(
            result["status"],
            "replayed",
            "committed mutation marked failed after later version; first audit/outbox absent",
        )
        self.assertEqual(len(list_audit_events(self.stores, tenant_id="tenant-a", actor_id="alice")), 2)

    def test_committed_patch_recovered_before_later_version(self) -> None:
        """Interleaving 2: crashed patch retries before later patch commits."""
        self._create()
        self._crash("crash-after-cas", 73)
        result = self._patch("crashed", "committed before crash")
        self.assertEqual(result["status"], "replayed")
        self._patch("other", "later committed")
        self.assertEqual(len(list_audit_events(self.stores, tenant_id="tenant-a", actor_id="alice")), 2)

    def test_authored_legacy_patch_denied_without_tenant(self) -> None:
        self.stores.entries.put({
            "id": "entry",
            "title": "legacy",
            "body": "body",
            "createdBy": "alice",
            "visibility": "private",
            "version": 1,
        })
        self.assertIsNone(get_entry(self.stores, "entry", actor_id="alice"))
        result = self._patch("legacy", "ordinary mutation", tenant=None)
        self.assertIsNone(result, "ordinary patch modifies legacy record that ordinary read denies")

    def test_migration_audit_failure_preserves_source(self) -> None:
        source = build_decision_journal_stores(Path(self.tmp.name) / "source").entries
        row = {"id": "legacy", "title": "legacy", "body": "body", "createdBy": "alice"}
        source.put(row)

        def fail(_: Any) -> None:
            raise OSError("synthetic audit outage")

        self.stores.audit.put = fail
        try:
            JournalMigrationEngine(self.stores).run_migration(
                [row],
                target_tenant_id="tenant-a",
                dry_run=False,
                dispose_source=True,
                source_store=source,
            )
        except OSError:
            pass
        self.assertIsNotNone(
            source.get("legacy"),
            "source deleted despite missing durable migration audit",
        )

    def test_crashed_unauthorized_reservation_never_replays_other_tenant(self) -> None:
        create_entry(self.stores, entry_id="private-entry", title="alice private", body="private body", actor_id="alice", tenant_id="tenant-a", created_at="2026-09-08")
        patch_entry(self.stores, "private-entry", patch={"title": "alice updated"}, actor_id="alice", tenant_id="tenant-a", idempotency_key="shared", request_hash="alicealice updated", patched_at="2026-09-08")
        crashed = subprocess.run(
            [sys.executable, "-c", _CRASH_HELPER_SCRIPT, "crash-unauthorized", self.tmp.name],
            env=dict(os.environ, PYTHONPATH="."),
            timeout=10,
        )
        self.assertEqual(crashed.returncode, 74)
        fresh = build_decision_journal_stores(self.tmp.name)
        self.assertIsNone(get_entry(fresh, "private-entry", actor_id="bob", tenant_id="tenant-b"))
        result = patch_entry(fresh, "private-entry", patch={"title": "bob attempt"}, actor_id="bob", tenant_id="tenant-b", idempotency_key="shared", request_hash="bobbob attempt", patched_at="2026-09-08")
        self.assertFalse(result and result.get("entry"), "Cross-tenant private entry returned: " + str(result))

    def test_migration_retry_requires_durable_audit_before_disposal(self) -> None:
        source = build_decision_journal_stores(Path(self.tmp.name) / "source").entries
        row = {"id": "legacy", "title": "legacy", "body": "body", "createdBy": "alice"}
        source.put(row)

        def fail(_: Any) -> None:
            raise OSError("synthetic audit outage")

        self.stores.audit.put = fail
        with self.assertRaises(OSError):
            JournalMigrationEngine(self.stores).run_migration(
                [row],
                target_tenant_id="tenant-a",
                dry_run=False,
                dispose_source=True,
                source_store=source,
            )
        self.assertIsNotNone(source.get("legacy"))
        fresh = build_decision_journal_stores(self.tmp.name)
        report = JournalMigrationEngine(fresh).run_migration(
            [row],
            target_tenant_id="tenant-a",
            dry_run=False,
            dispose_source=True,
            source_store=source,
        )
        self.assertFalse(
            source.get("legacy") is None and not fresh.audit.list_all(),
            "Retry deleted source without migration audit: " + str(report.to_dict()),
        )

    def test_reader_cannot_replay_transaction_that_rolls_back(self) -> None:
        create_entry(self.stores, entry_id="private-entry", title="alice private", body="private body", actor_id="alice", tenant_id="tenant-a", created_at="2026-09-08")
        fresh = build_decision_journal_stores(self.tmp.name)
        observed = []

        def read_then_fail(_: Any) -> None:
            child = subprocess.run(
                [sys.executable, "-c", _CRASH_HELPER_SCRIPT, "read-and-replay", self.tmp.name],
                env=dict(os.environ, PYTHONPATH="."),
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(child.returncode, 0, child.stderr)
            observed.append(json.loads(child.stdout))
            raise OSError("original writer outbox failure")

        self.stores.outbox.put = read_then_fail
        with self.assertRaises(OSError):
            patch_entry(self.stores, "private-entry", patch={"title": "alice updated"}, actor_id="alice", tenant_id="tenant-a", idempotency_key="shared", request_hash="alicealice updated", patched_at="2026-09-08")
        final = get_entry(fresh, "private-entry", actor_id="alice", tenant_id="tenant-a")
        self.assertFalse(
            observed[0].get("status") == "replayed" and final["title"] == "alice private",
            "Reader replayed rolled-back transaction: " + str(observed[0]) + "; final=" + str(final),
        )

    def test_create_reader_must_not_publish_rolled_back_entry(self) -> None:
        writer = build_decision_journal_stores(self.tmp.name)
        reader = build_decision_journal_stores(self.tmp.name)
        observed = []

        def fail_outbox(event: Any) -> None:
            observed.append(get_entry(reader, "entry", tenant_id="tenant-a", actor_id="alice"))
            raise OSError("injected outbox failure")

        writer.outbox.put = fail_outbox  # type: ignore[assignment]
        with self.assertRaises(OSError):
            create_entry(
                writer,
                entry_id="entry",
                title="Initial",
                body="body",
                actor_id="alice",
                tenant_id="tenant-a",
                created_at="2026-09-08T00:00:00Z",
            )
        self.assertIsNone(observed[0], "Reader observed entry that subsequently rolled back")
        self.assertEqual(reader.outbox.list_all(), [])

    def test_recreate_failure_preserves_committed_entry(self) -> None:
        self._create()
        def fail_outbox(_: Any) -> None:
            raise OSError("injected outbox failure on recreate")
        self.stores.outbox.put = fail_outbox  # type: ignore[assignment]
        # Recreate should not delete the already committed row
        result = self._create()
        self.assertEqual(result["title"], "initial")
        entry = get_entry(self.stores, "entry", tenant_id="tenant-a", actor_id="alice")
        self.assertIsNotNone(entry, "Committed entry was deleted on recreate outbox failure")
        self.assertEqual(entry["title"], "initial")

    def test_recreate_does_not_emit_duplicate_or_mutated_event(self) -> None:
        self._create()
        events_before = self.stores.outbox.list_all()
        self.assertEqual(len(events_before), 1)
        self.assertEqual(events_before[0]["data"]["title"], "initial")

        # Second create with different title / payload
        create_entry(
            self.stores,
            entry_id="entry",
            title="different retry payload",
            body="different body",
            actor_id="alice",
            tenant_id="tenant-a",
            created_at="2026-09-08",
        )
        events_after = self.stores.outbox.list_all()
        self.assertEqual(len(events_after), 1, "Recreate emitted a second outbox event")
        self.assertEqual(events_after[0]["data"]["title"], "initial", "Original event title was altered")
        entry = get_entry(self.stores, "entry", tenant_id="tenant-a", actor_id="alice")
        self.assertIsNotNone(entry)
        self.assertEqual(entry["title"], "initial", "Original entry title was overwritten")

    def test_before_snapshot_must_not_recursively_duplicate_history(self) -> None:
        self._create()
        for index in range(1, 10):
            self._patch(str(index), f"title-{index}")
        tx = self.stores.entries.get("entry")["_tx_history"][-1]
        self.assertNotIn(
            "_tx_history",
            tx.get("before_entry", {}),
            "Every old full history is recursively copied into each transaction",
        )

    def test_patch_audit_cannot_substitute_for_migration_proof(self) -> None:
        with tempfile.TemporaryDirectory() as source_tmp:
            self._create()
            self._patch("1", "patched")
            source = CoordinatingJsonGovernanceRecordStore(
                Path(source_tmp) / "legacy.json", id_fields=("id",)
            )
            row = get_entry(self.stores, "entry", tenant_id="tenant-a", actor_id="alice")
            assert row is not None
            source.put(row)

            def reject_audit(record: Any) -> None:
                raise OSError("migration audit unavailable")

            self.stores.audit.put = reject_audit  # type: ignore[assignment]
            report = JournalMigrationEngine(self.stores).run_migration(
                [row],
                target_tenant_id="tenant-a",
                dry_run=False,
                dispose_source=True,
                source_store=source,
            )
            self.assertIsNotNone(
                source.get("entry"),
                "Source disposed without checksum-bound migration audit",
            )

    def test_domain_creation_idempotency_key_format(self) -> None:
        key = domain_creation_idempotency_key(
            tenant_id="tenant-alpha",
            actor_id="alice",
            entry_id="entry-100",
        )
        self.assertEqual(key, "domain:create:tenant-alpha:alice:entry-100")
        self.assertTrue(key.startswith("domain:create:"))

    def test_domain_creation_idempotency_key_quoted_scope_isolation(self) -> None:
        key1 = domain_creation_idempotency_key(
            tenant_id="tenant:alice",
            actor_id="bob",
            entry_id="entry-1",
        )
        key2 = domain_creation_idempotency_key(
            tenant_id="tenant",
            actor_id="alice:bob",
            entry_id="entry-1",
        )
        self.assertNotEqual(key1, key2)
        self.assertEqual(key1, "domain:create:tenant%3Aalice:bob:entry-1")
        self.assertEqual(key2, "domain:create:tenant:alice%3Abob:entry-1")

    def test_recovery_must_not_overwrite_successful_concurrent_patch(self) -> None:
        with tempfile.TemporaryDirectory() as path:
            stores = build_decision_journal_stores(path)
            create_args = dict(
                entry_id="entry",
                title="initial",
                body="body",
                actor_id="alice",
                tenant_id="tenant-a",
                created_at="2026-09-08",
            )

            class Crash(BaseException):
                pass

            put = stores.outbox.put

            def crash(_: Any) -> None:
                raise Crash()

            stores.outbox.put = crash  # type: ignore[assignment]
            with self.assertRaises(Crash):
                create_entry(stores, **create_args)

            entered, release = threading.Event(), threading.Event()

            def paused_put(event: Any) -> None:
                entered.set()
                if not release.wait(5):
                    raise RuntimeError("probe barrier timeout")
                put(event)

            stores.outbox.put = paused_put  # type: ignore[assignment]
            with ThreadPoolExecutor(max_workers=1) as pool:
                read = pool.submit(get_entry, stores, "entry", tenant_id="tenant-a", actor_id="alice")
                try:
                    self.assertTrue(entered.wait(5))
                    writer = build_decision_journal_stores(path)
                    create_entry(writer, **create_args)
                    result = patch_entry(
                        writer,
                        "entry",
                        patch={"title": "committed update"},
                        actor_id="alice",
                        tenant_id="tenant-a",
                        idempotency_key="patch",
                        request_hash="patch",
                        patched_at="2026-09-08",
                    )
                    self.assertEqual(result["status"], "updated")
                finally:
                    release.set()
                read.result(timeout=5)
            fresh = get_entry(build_decision_journal_stores(path), "entry", tenant_id="tenant-a", actor_id="alice")
            self.assertIsNotNone(fresh)
            self.assertEqual(fresh["title"], "committed update", "recovery overwrote a successful concurrent patch")

    def test_fresh_process_persistent_outbox_failure_and_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_path:
            script = (
                "import os, sys\n"
                "from services.governance.decision_journal import build_decision_journal_stores, create_entry\n"
                "stores = build_decision_journal_stores(sys.argv[1])\n"
                "stores.outbox.put = lambda event: os._exit(74)\n"
                "create_entry(stores, entry_id='crash-entry', title='t', body='b', actor_id='alice', tenant_id='tenant-a', created_at='2026-09-08')\n"
            )
            child = subprocess.run([sys.executable, "-c", script, tmp_path], timeout=10)
            self.assertEqual(child.returncode, 74)

            domain_key = domain_creation_idempotency_key(
                tenant_id="tenant-a",
                actor_id="alice",
                entry_id="crash-entry",
            )

            # Fresh process store with persistent outbox failure
            fresh = build_decision_journal_stores(tmp_path)
            def fail_outbox(event: Any) -> None:
                raise OSError("injected persistent outbox failure")
            fresh.outbox.put = fail_outbox  # type: ignore[assignment]

            # 1. Scoped get_entry must fail closed while creation is incomplete
            entry = get_entry(fresh, "crash-entry", tenant_id="tenant-a", actor_id="alice")
            self.assertIsNone(entry, "Uncommitted entry was exposed during persistent outbox failure")

            # 2. list_entries must exclude incomplete entry
            entries = list_entries(fresh, tenant_id="tenant-a", actor_id="alice")
            self.assertEqual(len(entries), 0, "Uncommitted entry was listed during persistent outbox failure")

            # 3. Domain idempotency status must remain pending, outbox must remain zero
            idem_rec = fresh.idempotency.get(domain_key)
            self.assertIsNotNone(idem_rec)
            self.assertEqual(idem_rec.get("status"), "pending", "Idempotency falsely marked succeeded without durable outbox")
            self.assertEqual(len(fresh.outbox.list_all()), 0)

            # 4. Replay while outbox is unavailable must raise and fail closed
            with self.assertRaises(OSError):
                create_entry(
                    fresh,
                    entry_id="crash-entry",
                    title="t",
                    body="b",
                    actor_id="alice",
                    tenant_id="tenant-a",
                    created_at="2026-09-08",
                )

            # 5. Outbox recovers: fresh store successfully publishes and commits entry
            recovered = build_decision_journal_stores(tmp_path)
            entry_recovered = get_entry(recovered, "crash-entry", tenant_id="tenant-a", actor_id="alice")
            self.assertIsNotNone(entry_recovered)
            self.assertEqual(entry_recovered["id"], "crash-entry")
            self.assertEqual(len(recovered.outbox.list_all()), 1)
            self.assertEqual(recovered.idempotency.get(domain_key).get("status"), "succeeded")

    def test_failed_recovery_list_must_not_disclose_private_body(self) -> None:
        """P1: list_entries must preserve committed snapshot and never disclose uncommitted patch."""
        with tempfile.TemporaryDirectory() as path:
            stores = build_decision_journal_stores(path)
            scope = {"tenant_id": "tenant-a", "actor_id": "alice"}
            create_entry(stores, entry_id="entry", title="private title", body="private body", created_at="2026-09-08", **scope)
            child_code = (
                "import os, sys\n"
                "from services.governance.decision_journal import build_decision_journal_stores, patch_entry\n"
                "s = build_decision_journal_stores(sys.argv[1])\n"
                "s.outbox.put = lambda event: os._exit(74)\n"
                "patch_entry(s, 'entry', patch={'visibility': 'public'}, tenant_id='tenant-a', actor_id='alice', idempotency_key='publish', request_hash='publish', patched_at='2026-09-08')\n"
            )
            child = subprocess.run([sys.executable, "-c", child_code, path], timeout=10)
            self.assertEqual(child.returncode, 74)
            fresh = build_decision_journal_stores(path)
            def fail(_: Any) -> None:
                raise OSError("outbox remains unavailable")
            fresh.outbox.put = fail  # type: ignore[assignment]
            self.assertEqual(get_entry(fresh, "entry", **scope)["visibility"], "private")
            self.assertIsNone(get_entry(fresh, "entry", tenant_id="tenant-a", actor_id="bob"))
            rows = list_entries(fresh, tenant_id="tenant-a", actor_id="bob")
            self.assertEqual(rows, [], "list exposed private body through an uncommitted visibility patch")

    def test_audit_and_outbox_must_not_publish_rolled_back_patch(self) -> None:
        """P1: audit/outbox list readers must gate on committed transaction outcome and not expose provisional events."""
        with tempfile.TemporaryDirectory() as path:
            scope = {"tenant_id": "tenant-a", "actor_id": "alice"}
            stores = build_decision_journal_stores(path)
            reader = build_decision_journal_stores(path)
            create_entry(stores, entry_id="entry", title="before", body="body", created_at="2026-09-08", **scope)
            original_put = stores.idempotency.put
            observed: Dict[str, Any] = {}

            def fail_commit(record: Dict[str, Any]) -> None:
                if record.get("status") == "succeeded":
                    observed["audit"] = list_audit_events(reader, entry_id="entry", **scope)
                    observed["outbox"] = [
                        e for e in list_outbox_events(reader, entry_id="entry", tenant_id="tenant-a")
                        if str(e.get("event_type") or "").endswith("updated")
                    ]
                    raise OSError("idempotency commit fails after audit and outbox")
                original_put(record)

            stores.idempotency.put = fail_commit  # type: ignore[assignment]
            with self.assertRaises(OSError):
                patch_entry(
                    stores,
                    "entry",
                    patch={"title": "aborted"},
                    idempotency_key="patch",
                    request_hash="patch",
                    patched_at="2026-09-08",
                    **scope,
                )
            self.assertEqual(get_entry(reader, "entry", **scope)["title"], "before")
            self.assertEqual(list_audit_events(reader, entry_id="entry", **scope), [])
            self.assertEqual(observed, {"audit": [], "outbox": []}, "uncommitted events were exposed before rollback")

    def test_public_rewrite_redacts_private_before_body_in_audit_for_unauthorized_reader(self) -> None:
        """P1: publishing a private entry must redact unauthorized before-image in audit diff for non-author reader."""
        create_entry(
            self.stores,
            entry_id="entry-pub-redact",
            title="private",
            body="PRIVATE ORIGINAL",
            actor_id="alice",
            user_id="alice",
            tenant_id="tenant-a",
            created_at="2026-09-08T00:00:00Z",
        )
        patch_entry(
            self.stores,
            "entry-pub-redact",
            patch={"visibility": "public", "body": "PUBLIC REPLACEMENT"},
            actor_id="alice",
            user_id="alice",
            tenant_id="tenant-a",
            idempotency_key="publish-key",
            request_hash="publish-hash",
            patched_at="2026-09-08T01:00:00Z",
        )
        # Bob in tenant-a reads entry
        bob_entry = get_entry(self.stores, "entry-pub-redact", tenant_id="tenant-a", actor_id="bob")
        self.assertIsNotNone(bob_entry)
        self.assertEqual(bob_entry["body"], "PUBLIC REPLACEMENT")

        # Bob lists audit events: must NOT see private before-body
        bob_audits = list_audit_events(self.stores, entry_id="entry-pub-redact", tenant_id="tenant-a", actor_id="bob")
        self.assertEqual(len(bob_audits), 1)
        self.assertNotIn("PRIVATE ORIGINAL", json.dumps(bob_audits), "Bob received private before-image in public rewrite audit")
        self.assertIsNone(bob_audits[0]["diff"]["before"])
        self.assertEqual(bob_audits[0]["diff"]["after"]["body"], "PUBLIC REPLACEMENT")
        for ch in bob_audits[0]["diff"].get("changes") or []:
            self.assertIsNone(ch.get("before"))

        # Alice (author) lists audit events: preserves full history
        alice_audits = list_audit_events(self.stores, entry_id="entry-pub-redact", tenant_id="tenant-a", actor_id="alice")
        self.assertEqual(len(alice_audits), 1)
        self.assertIn("PRIVATE ORIGINAL", json.dumps(alice_audits))
        self.assertEqual(alice_audits[0]["diff"]["before"]["body"], "PRIVATE ORIGINAL")
        self.assertEqual(alice_audits[0]["diff"]["after"]["body"], "PUBLIC REPLACEMENT")

        # Cross-tenant and unscoped readers receive empty list
        self.assertEqual(list_audit_events(self.stores, entry_id="entry-pub-redact", tenant_id="tenant-b", actor_id="alice"), [])
        self.assertEqual(list_audit_events(self.stores, entry_id="entry-pub-redact", tenant_id=""), [])
        self.assertEqual(list_audit_events(self.stores, entry_id="entry-pub-redact", tenant_id=None), [])

        # Tenant reader without actor sees public event with private before-image redacted
        anon_tenant_audits = list_audit_events(self.stores, entry_id="entry-pub-redact", tenant_id="tenant-a")
        self.assertEqual(len(anon_tenant_audits), 1)
        self.assertNotIn("PRIVATE ORIGINAL", json.dumps(anon_tenant_audits))
        self.assertIsNone(anon_tenant_audits[0]["diff"]["before"])

    def test_failed_replay_recovery_can_recover_after_outbox_returns(self) -> None:
        """P1: process crashes after CAS before outbox; same-key retry during outage fails,
        fresh-process recovery after outage coordinates transaction, permits successor, and ensures replay/audit/outbox parity.
        """
        with tempfile.TemporaryDirectory() as path:
            stores = build_decision_journal_stores(path)
            create_entry(
                stores,
                entry_id="entry-crash",
                title="private",
                body="PRIVATE ORIGINAL",
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                created_at="2026-09-08T00:00:00Z",
            )

            code = """
import os, sys
from services.governance.decision_journal import build_decision_journal_stores, patch_entry
s = build_decision_journal_stores(sys.argv[1])
s.outbox.put = lambda event: os._exit(74)
patch_entry(
    s,
    'entry-crash',
    patch={'title': 'crashed'},
    actor_id='alice',
    user_id='alice',
    tenant_id='tenant-a',
    idempotency_key='crashed',
    request_hash='crashed',
    patched_at='2026-09-08T01:00:00Z',
)
"""
            child = subprocess.run([sys.executable, "-c", code, path], timeout=10)
            self.assertEqual(child.returncode, 74)

            # 1. Same-key retry during continued outage fails with failed status
            fresh = build_decision_journal_stores(path)
            def unavailable(event: Any) -> None:
                raise OSError("outbox still down")
            fresh.outbox.put = unavailable  # type: ignore[assignment]
            failed = patch_entry(
                fresh,
                "entry-crash",
                patch={"title": "crashed"},
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                idempotency_key="crashed",
                request_hash="crashed",
                patched_at="2026-09-08T01:00:00Z",
            )
            self.assertIsNotNone(failed)
            self.assertEqual(failed["status"], "failed")

            # 2. Fresh-process recovery after outage returns
            recovered = build_decision_journal_stores(path)
            entry = get_entry(recovered, "entry-crash", tenant_id="tenant-a", actor_id="alice")
            self.assertIsNotNone(entry)
            self.assertEqual(entry["title"], "crashed")
            key = patch_idempotency_key(tenant_id="tenant-a", actor_id="alice", idempotency_key="crashed")
            self.assertEqual(recovered.idempotency.get(key)["status"], "succeeded")

            # 3. Successful successor patch succeeds without ConcurrencyError
            succ = patch_entry(
                recovered,
                "entry-crash",
                patch={"title": "next"},
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                idempotency_key="next",
                request_hash="next",
                patched_at="2026-09-08T02:00:00Z",
            )
            self.assertIsNotNone(succ)
            self.assertEqual(succ["status"], "updated")
            self.assertEqual(succ["entry"]["title"], "next")

            # 4. Replay parity: retrying crashed key returns replayed
            replayed = patch_entry(
                recovered,
                "entry-crash",
                patch={"title": "crashed"},
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                idempotency_key="crashed",
                request_hash="crashed",
                patched_at="2026-09-08T01:00:00Z",
            )
            self.assertIsNotNone(replayed)
            self.assertEqual(replayed["status"], "replayed")

            # 5. Audit and outbox parity: both contain durable event
            audits = list_audit_events(recovered, entry_id="entry-crash", tenant_id="tenant-a", actor_id="alice")
            self.assertEqual(len(audits), 2)
            outbox = [
                e for e in list_outbox_events(recovered, entry_id="entry-crash", tenant_id="tenant-a")
                if str(e.get("event_type") or "").endswith("updated")
            ]
            self.assertEqual(len(outbox), 2)


if __name__ == "__main__":
    unittest.main()



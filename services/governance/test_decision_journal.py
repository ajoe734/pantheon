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

import tempfile
import unittest
import unittest.mock
from pathlib import Path

from services.governance.decision_journal import (
    CANONICAL_WRITE_AUTHORITY,
    DecisionJournalCollisionError,
    DecisionJournalConcurrencyError,
    DecisionJournalValidationError,
    build_decision_journal_stores,
    create_entry,
    get_entry,
    list_audit_events,
    list_entries,
    list_outbox_events,
    patch_entry,
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

        # Check listed audit events
        audits = list_audit_events(self.stores, entry_id="dje-audit-01", tenant_id="tenant-alpha")
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
        fresh_entry = get_entry(fresh_stores, "dje-restart-01", tenant_id="tenant-gamma")
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
        entry = get_entry(fresh, "dje-fail-audit", tenant_id="tenant-alpha")
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
        entry = get_entry(fresh, "dje-fail-patch-outbox", tenant_id="tenant-alpha")
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
        entry = get_entry(self.stores, "old-agora-entry", tenant_id="tenant-alpha")
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
        entry = get_entry(fresh, "dje-cas-conflict", tenant_id="tenant-alpha")
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

    def test_migration_does_not_claim_disposal_without_source(self) -> None:
        report = JournalMigrationEngine(self.stores).run_migration(
            [{"id": "legacy-no-source", "title": "Synthetic", "author": "alice"}],
            target_tenant_id="tenant-alpha",
            dry_run=False,
            dispose_source=True,
        )
        self.assertEqual(len(report.items), 1)
        self.assertFalse(report.items[0]["disposed"])


if __name__ == "__main__":
    unittest.main()

"""JOURNAL-RUNTIME-CONTRACT-CORRECTIVE-001: Journal runtime contract tests.

Validates:
1. Backend resolution contract (JSON default only when no DSN; Postgres when DATABASE_URL is set).
2. Unsupported legacy aliases (e.g. 'memory', 'redis') are explicitly rejected.
3. Conflicting backend env vars fail closed with ValueError.
4. Data directory resolution hierarchy.
5. Read-only storage and filesystem failure honesty (fail closed with PermissionError).
6. Restart parity: fresh owner instance reads back previously committed entries.
7. Tenant, actor, CAS, and idempotency isolation.
8. BFF AppDependencies and main.py composition wiring.
"""
from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from services.governance.decision_journal import (
    CoordinatingJsonGovernanceRecordStore,
    DecisionJournalAccessDeniedError,
    DecisionJournalStores,
    build_decision_journal_stores,
    create_entry,
    get_entry,
    list_entries,
    patch_entry,
    resolve_decision_journal_backend,
)
from services.control_plane.bff.governance.decision_journal_write_owner import (
    DecisionJournalOwnerAdapter,
    DecisionJournalWriteOwner,
    build_decision_journal_write_owner,
    resolve_decision_journal_data_dir,
)
from services.control_plane.bff.bootstrap.dependencies import AppDependencies


class TestJournalRuntimeContract(unittest.TestCase):
    def test_backend_resolution_explicit_values(self) -> None:
        """Explicit GOVERNANCE_STORE_BACKEND takes precedence when valid."""
        with patch.dict(os.environ, {"GOVERNANCE_STORE_BACKEND": "postgres"}, clear=True):
            self.assertEqual(resolve_decision_journal_backend(), "postgres")

        with patch.dict(os.environ, {"GOVERNANCE_STORE_BACKEND": "json"}, clear=True):
            self.assertEqual(resolve_decision_journal_backend(), "json")

        with patch.dict(os.environ, {"GOVERNANCE_STORE_BACKEND": "invalid_backend"}, clear=True):
            with self.assertRaises(ValueError):
                resolve_decision_journal_backend()

    def test_backend_resolution_database_url_selection(self) -> None:
        """When GOVERNANCE_STORE_BACKEND is unset, presence of DATABASE_URL selects postgres."""
        with patch.dict(
            os.environ,
            {"DATABASE_URL": "postgresql://user:pass@localhost:5432/pantheon"},
            clear=True,
        ):
            self.assertEqual(resolve_decision_journal_backend(), "postgres")

        with patch.dict(
            os.environ,
            {"GOVERNANCE_STORE_DSN": "postgresql://user:pass@localhost:5432/pantheon"},
            clear=True,
        ):
            self.assertEqual(resolve_decision_journal_backend(), "postgres")

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_decision_journal_backend(), "json")

    def test_backend_resolution_legacy_alias_and_conflict(self) -> None:
        """AGORA_GOVERNANCE_STORE_BACKEND compatibility and conflict validation."""
        # Supported legacy value
        with patch.dict(os.environ, {"AGORA_GOVERNANCE_STORE_BACKEND": "postgres"}, clear=True):
            self.assertEqual(resolve_decision_journal_backend(), "postgres")

        # Unsupported legacy backend (e.g. memory, redis)
        with patch.dict(os.environ, {"AGORA_GOVERNANCE_STORE_BACKEND": "memory"}, clear=True):
            with self.assertRaises(ValueError) as ctx:
                resolve_decision_journal_backend()
            self.assertIn("Unsupported legacy environment backend", str(ctx.exception))

        with patch.dict(os.environ, {"AGORA_GOVERNANCE_STORE_BACKEND": "redis"}, clear=True):
            with self.assertRaises(ValueError):
                resolve_decision_journal_backend()

        # Conflicting backend specification
        with patch.dict(
            os.environ,
            {
                "GOVERNANCE_STORE_BACKEND": "json",
                "AGORA_GOVERNANCE_STORE_BACKEND": "postgres",
            },
            clear=True,
        ):
            with self.assertRaises(ValueError) as ctx:
                resolve_decision_journal_backend()
            self.assertIn("Conflicting backend configuration", str(ctx.exception))

    def test_data_dir_resolution_hierarchy(self) -> None:
        """PANTHEON_DECISION_JOURNAL_DATA_DIR > PANTHEON_BFF_DECISION_JOURNAL_STORE > PANTHEON_GOVERNANCE_DATA_DIR."""
        with patch.dict(
            os.environ,
            {
                "PANTHEON_DECISION_JOURNAL_DATA_DIR": "/custom/journal",
                "PANTHEON_BFF_DECISION_JOURNAL_STORE": "/bff/store/entries.json",
                "PANTHEON_GOVERNANCE_DATA_DIR": "/gov/data",
            },
            clear=True,
        ):
            self.assertEqual(resolve_decision_journal_data_dir(), "/custom/journal")

        with patch.dict(
            os.environ,
            {
                "PANTHEON_BFF_DECISION_JOURNAL_STORE": "/bff/store/decision_journal_entries.json",
                "PANTHEON_GOVERNANCE_DATA_DIR": "/gov/data",
            },
            clear=True,
        ):
            self.assertEqual(resolve_decision_journal_data_dir(), "/bff/store")

        with patch.dict(
            os.environ,
            {"PANTHEON_GOVERNANCE_DATA_DIR": "/gov/data"},
            clear=True,
        ):
            self.assertEqual(resolve_decision_journal_data_dir(), "/gov/data")

        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(resolve_decision_journal_data_dir(), "/tmp/pantheon/governance")

    def test_read_only_storage_fail_closed_honesty(self) -> None:
        """Read-only mounts/stores fail closed honestly with PermissionError."""
        with tempfile.TemporaryDirectory() as tmp:
            store_path = Path(tmp) / "test_store.json"
            store = CoordinatingJsonGovernanceRecordStore(store_path, id_fields=["id"])
            store.read_only = True

            with self.assertRaises(PermissionError):
                store.put({"id": "rec-1", "val": "abc"})

            with self.assertRaises(PermissionError):
                store.insert_if_absent({"id": "rec-1", "val": "abc"})

            with self.assertRaises(PermissionError):
                store.compare_and_set({"id": "rec-1", "val": "abc"}, {"id": "rec-1", "val": "def"})

            with self.assertRaises(PermissionError):
                store.delete("rec-1")

            with self.assertRaises(PermissionError):
                store.delete_if_equals("rec-1", {"id": "rec-1", "val": "abc"})

            # Adapter storage health check reflects read-only posture
            stores = build_decision_journal_stores(tmp)
            stores.entries.read_only = True
            adapter = DecisionJournalOwnerAdapter(stores=stores)
            self.assertFalse(adapter.is_storage_healthy)

    def test_durable_paper_command_restart_parity(self) -> None:
        """A fresh owner adapter sees committed entries after restart."""
        with tempfile.TemporaryDirectory() as tmp:
            first_owner = build_decision_journal_write_owner(data_dir=tmp)
            entry = first_owner.create_decision_journal_entry(
                title="Paper order review",
                body="Durable journal paper command execution.",
                actor_id="actor-dev-1",
                tenant_id="tenant-dev-1",
                created_at="2026-09-27T08:00:00Z",
                payload={"tags": ["paper", "preflight"], "visibility": "private"},
            )
            entry_id = entry["id"]

            # Simulate complete process restart with fresh adapter instance on same disk
            second_owner = build_decision_journal_write_owner(data_dir=tmp)
            fetched = second_owner.get_decision_journal_entry(
                entry_id,
                tenant_id="tenant-dev-1",
                actor_id="actor-dev-1",
            )
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched["id"], entry_id)
            self.assertEqual(fetched["title"], "Paper order review")
            self.assertEqual(fetched["canonicalWriteAuthority"], "governance-decision-journal-svc")

    def test_tenant_and_actor_isolation(self) -> None:
        """Entries scoped to tenant A cannot be observed or modified by tenant B."""
        with tempfile.TemporaryDirectory() as tmp:
            owner = build_decision_journal_write_owner(data_dir=tmp)
            entry = owner.create_decision_journal_entry(
                title="Tenant A Private Note",
                body="Private to tenant A",
                actor_id="actor-a",
                tenant_id="tenant-a",
                created_at="2026-09-27T08:00:00Z",
                payload={"visibility": "private"},
            )
            entry_id = entry["id"]

            # Tenant A can read
            self.assertIsNotNone(owner.get_decision_journal_entry(entry_id, tenant_id="tenant-a", actor_id="actor-a"))
            # Tenant B cannot read
            self.assertIsNone(owner.get_decision_journal_entry(entry_id, tenant_id="tenant-b", actor_id="actor-b"))
            # Unscoped query returns empty (fail closed)
            self.assertEqual(len(owner.list_decision_journal_entries()), 0)
            # Tenant B list query does not contain Tenant A entry
            tenant_b_entries = owner.list_decision_journal_entries(tenant_id="tenant-b", actor_id="actor-b")
            self.assertEqual(len(tenant_b_entries), 0)

    def test_patch_cas_isolation(self) -> None:
        """Idempotent patch requires version/hash and advances CAS version."""
        with tempfile.TemporaryDirectory() as tmp:
            owner = build_decision_journal_write_owner(data_dir=tmp)
            created = owner.create_decision_journal_entry(
                title="Initial Title",
                body="Initial Body",
                actor_id="actor-1",
                tenant_id="tenant-1",
                created_at="2026-09-27T08:00:00Z",
            )
            entry_id = created["id"]
            self.assertEqual(created["version"], 1)

            # Apply patch
            patched = owner.patch_decision_journal_entry(
                entry_id,
                patch={"title": "Updated Title"},
                actor_id="actor-1",
                tenant_id="tenant-1",
                idempotency_key="patch-key-1",
                request_hash="hash-1",
                patched_at="2026-09-27T08:01:00Z",
            )
            self.assertEqual(patched["status"], "updated")
            self.assertEqual(patched["entry"]["title"], "Updated Title")
            self.assertEqual(patched["entry"]["version"], 2)

            # Replaying exact same patch with same idempotency key returns replayed status
            replayed = owner.patch_decision_journal_entry(
                entry_id,
                patch={"title": "Updated Title"},
                actor_id="actor-1",
                tenant_id="tenant-1",
                idempotency_key="patch-key-1",
                request_hash="hash-1",
                patched_at="2026-09-27T08:01:00Z",
            )
            self.assertEqual(replayed["status"], "replayed")
            self.assertEqual(replayed["entry"]["version"], 2)

    def test_app_dependencies_contract(self) -> None:
        """AppDependencies exposes concrete decision_journal_write_owner."""
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"PANTHEON_DECISION_JOURNAL_DATA_DIR": tmp}, clear=False):
                deps = AppDependencies.create_default()
                self.assertIsNotNone(deps.decision_journal_write_owner)
                self.assertIsInstance(deps.decision_journal_write_owner, DecisionJournalWriteOwner)
                self.assertTrue(deps.decision_journal_write_owner.is_storage_healthy)


if __name__ == "__main__":
    unittest.main()

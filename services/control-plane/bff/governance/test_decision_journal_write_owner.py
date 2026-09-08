"""JOURNAL-OWNER-001: convergence tests for the Decision Journal owner adapter.

Proves the acceptance criteria from SD §5.3 for the narrow slice this task
owns: one durable write owner backs both the adapter used directly and the
Agora BFF route handlers, a fresh adapter pointed at the same data directory
sees identical state after a simulated restart, and the Agora service fails
closed instead of fabricating an unpersisted "success" when the canonical
owner adapter is missing.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from services.control_plane.bff.agora.service import AgoraService
from services.control_plane.bff.governance.decision_journal_write_owner import (
    DecisionJournalOwnerAdapter,
    DecisionJournalWriteOwner,
    build_decision_journal_owner_adapter,
    build_decision_journal_write_owner,
    wrap_get_read_store_with_decision_journal_owner,
)
from services.control_plane.bff.models import OperatorIdentity
from services.control_plane.bff.ports.operations_consultation import (
    CompositeOperationsConsultationPort,
    DomainDecisionJournalReaderPort,
)
from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts
from services.governance.decision_journal import (
    DecisionJournalAccessDeniedError,
    DecisionJournalCollisionError,
    DecisionJournalValidationError,
    build_decision_journal_stores,
    create_entry,
)


class _BareInnerReadStore:
    """A read store double with no Decision Journal capability at all."""

    def some_unrelated_read(self) -> str:
        return "unrelated"


class TestDecisionJournalOwnerAdapter(unittest.TestCase):
    def test_create_list_patch_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            adapter = build_decision_journal_owner_adapter(_BareInnerReadStore(), data_dir=tmp)

            created = adapter.create_decision_journal_entry(
                title="Delay promotion",
                body="Hold the canary promotion pending review.",
                actor_id="op-1",
                payload={"tags": ["risk"], "visibility": "private"},
                created_at="2026-09-05T00:00:00Z",
            )
            self.assertEqual(created["title"], "Delay promotion")
            self.assertEqual(created["canonicalWriteAuthority"], "governance-decision-journal-svc")

            listed = adapter.list_decision_journal_entries()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["id"], created["id"])

            patched = adapter.patch_decision_journal_entry(
                created["id"],
                patch={"title": "Delay promotion (updated)"},
                actor_id="op-1",
                idempotency_key="idem-1",
                request_hash="hash-1",
                patched_at="2026-09-05T00:05:00Z",
            )
            self.assertEqual(patched["status"], "updated")
            self.assertEqual(patched["entry"]["title"], "Delay promotion (updated)")

    def test_does_not_proxy_unrelated_attributes_via_getattr(self) -> None:
        """SD §5.3: Adapter does not dynamically proxy unrelated attributes via __getattr__."""
        with tempfile.TemporaryDirectory() as tmp:
            inner = _BareInnerReadStore()
            adapter = build_decision_journal_owner_adapter(inner, data_dir=tmp)
            with self.assertRaises(AttributeError):
                adapter.some_unrelated_read()

    def test_tenant_and_user_isolation(self) -> None:
        """SD §5.3 scorecard item 4: Tenant and user isolation across create, list, and detail."""
        with tempfile.TemporaryDirectory() as tmp:
            owner = build_decision_journal_write_owner(data_dir=tmp)

            # Tenant alpha / Alice
            entry_alpha = owner.create_decision_journal_entry(
                title="Alpha Policy",
                body="Confidential Alpha strategy",
                actor_id="op-alice",
                tenant_id="tenant-alpha",
                user_id="user-alice",
                payload={"visibility": "private"},
                created_at="2026-09-08T00:00:00Z",
            )

            # Tenant beta / Bob
            entry_beta = owner.create_decision_journal_entry(
                title="Beta Policy",
                body="Confidential Beta strategy",
                actor_id="op-bob",
                tenant_id="tenant-beta",
                user_id="user-bob",
                payload={"visibility": "private"},
                created_at="2026-09-08T00:00:00Z",
            )

            # Alice on Tenant Alpha only sees Alpha
            alice_entries = owner.list_decision_journal_entries(tenant_id="tenant-alpha", user_id="user-alice")
            self.assertEqual(len(alice_entries), 1)
            self.assertEqual(alice_entries[0]["id"], entry_alpha["id"])

            # Bob on Tenant Beta only sees Beta
            bob_entries = owner.list_decision_journal_entries(tenant_id="tenant-beta", user_id="user-bob")
            self.assertEqual(len(bob_entries), 1)
            self.assertEqual(bob_entries[0]["id"], entry_beta["id"])

            # Alice cannot get Beta entry across tenant boundary
            get_across_tenant = owner.get_decision_journal_entry(entry_beta["id"], tenant_id="tenant-alpha")
            self.assertIsNone(get_across_tenant)

            # Detail get with proper tenant succeeds
            get_own = owner.get_decision_journal_entry(entry_alpha["id"], tenant_id="tenant-alpha", user_id="user-alice")
            self.assertIsNotNone(get_own)
            self.assertEqual(get_own["id"], entry_alpha["id"])

    def test_same_operator_id_across_different_tenants_isolation(self) -> None:
        """SD §5.3 scorecard item 4: Same operator ID across different tenants maintains strict separation."""
        with tempfile.TemporaryDirectory() as tmp:
            owner = build_decision_journal_write_owner(data_dir=tmp)

            # Operator 'op-global' operates in Tenant Alpha
            entry_alpha = owner.create_decision_journal_entry(
                title="Global Op in Alpha",
                body="Tenant Alpha specific notes",
                actor_id="op-global",
                tenant_id="tenant-alpha",
                created_at="2026-09-08T00:00:00Z",
            )

            # Operator 'op-global' operates in Tenant Beta
            entry_beta = owner.create_decision_journal_entry(
                title="Global Op in Beta",
                body="Tenant Beta specific notes",
                actor_id="op-global",
                tenant_id="tenant-beta",
                created_at="2026-09-08T00:00:00Z",
            )

            # Alpha query returns only Alpha entry
            alpha_results = owner.list_decision_journal_entries(tenant_id="tenant-alpha", actor_id="op-global")
            self.assertEqual([e["id"] for e in alpha_results], [entry_alpha["id"]])

            # Beta query returns only Beta entry
            beta_results = owner.list_decision_journal_entries(tenant_id="tenant-beta", actor_id="op-global")
            self.assertEqual([e["id"] for e in beta_results], [entry_beta["id"]])

    def test_supplied_id_collision_rejection_across_tenants_and_actors(self) -> None:
        """SD §5.3 scorecard item 5: Reject caller-supplied ID collisions across actors/tenants."""
        with tempfile.TemporaryDirectory() as tmp:
            owner = build_decision_journal_write_owner(data_dir=tmp)
            shared_id = "dje-fixed-uuid-1234"

            # Alice creates entry with specific ID in Tenant Alpha
            created = owner.create_decision_journal_entry(
                entry_id=shared_id,
                title="Alice original",
                body="Alice body",
                actor_id="alice",
                tenant_id="tenant-alpha",
                created_at="2026-09-08T00:00:00Z",
            )
            self.assertEqual(created["id"], shared_id)

            # Bob in Tenant Beta tries to supply the same ID -> Collision Error!
            with self.assertRaises(DecisionJournalCollisionError):
                owner.create_decision_journal_entry(
                    entry_id=shared_id,
                    title="Bob spoof",
                    body="Bob attempt",
                    actor_id="bob",
                    tenant_id="tenant-beta",
                    created_at="2026-09-08T00:00:00Z",
                )

            # Charlie in Tenant Alpha (different actor) tries to supply the same ID -> Collision Error!
            with self.assertRaises(DecisionJournalCollisionError):
                owner.create_decision_journal_entry(
                    entry_id=shared_id,
                    title="Charlie collision",
                    body="Charlie attempt",
                    actor_id="charlie",
                    tenant_id="tenant-alpha",
                    created_at="2026-09-08T00:00:00Z",
                )

            # Alice in Tenant Alpha supplies the same ID -> Idempotent re-create succeeds
            recreated = owner.create_decision_journal_entry(
                entry_id=shared_id,
                title="Alice original",
                body="Alice body",
                actor_id="alice",
                tenant_id="tenant-alpha",
                created_at="2026-09-08T00:00:00Z",
            )
            self.assertEqual(recreated["id"], shared_id)

    def test_read_parity_between_read_surface_ports_and_write_owner(self) -> None:
        """SD §5.3: Read parity between ReadSurfacePorts and DecisionJournalWriteOwner."""
        with tempfile.TemporaryDirectory() as tmp:
            owner = build_decision_journal_write_owner(data_dir=tmp)

            entry_1 = owner.create_decision_journal_entry(
                title="Entry 1",
                body="Body 1",
                actor_id="op-test",
                tenant_id="tenant-corp",
                created_at="2026-09-08T00:00:00Z",
            )
            entry_2 = owner.create_decision_journal_entry(
                title="Entry 2",
                body="Body 2",
                actor_id="op-test",
                tenant_id="tenant-corp",
                created_at="2026-09-08T01:00:00Z",
            )

            # Build ReadSurfacePorts backed by operations consultation port
            dj_reader = DomainDecisionJournalReaderPort(data_dir=tmp)
            ops_consultation = CompositeOperationsConsultationPort(decision_journal_port=dj_reader)
            read_surface = ReadSurfacePorts(operations_consultation=ops_consultation)

            # Both list calls return identical data
            owner_list = owner.list_decision_journal_entries(tenant_id="tenant-corp")
            read_list = read_surface.list_decision_journal_entries(tenant_id="tenant-corp")
            self.assertEqual(owner_list, read_list)

            # Both get calls return identical entry
            owner_get = owner.get_decision_journal_entry(entry_1["id"], tenant_id="tenant-corp")
            read_get = read_surface.get_decision_journal_entry(entry_1["id"], tenant_id="tenant-corp")
            self.assertEqual(owner_get, read_get)

            # Verify ReadSurfacePorts exposes no mutation methods
            self.assertFalse(hasattr(read_surface, "create_decision_journal_entry"))
            self.assertFalse(hasattr(read_surface, "patch_decision_journal_entry"))

    def test_legacy_unscoped_row_isolation(self) -> None:
        """SD §5.3 scorecard item 6: Legacy rows missing tenant scope are excluded from tenant queries."""
        with tempfile.TemporaryDirectory() as tmp:
            from services.governance.decision_journal import build_decision_journal_stores
            stores = build_decision_journal_stores(tmp)
            # Directly insert an unscoped legacy row
            stores.entries.put({
                "id": "legacy-entry-001",
                "title": "Legacy Unscoped Title",
                "body": "Legacy body",
                "createdBy": "op-legacy",
                "visibility": "team",
                "createdAt": "2025-01-01T00:00:00Z",
                "version": 1,
            })

            owner = build_decision_journal_write_owner(data_dir=tmp)

            # Scoped tenant query excludes unscoped legacy rows by default
            scoped_list = owner.list_decision_journal_entries(tenant_id="tenant-alpha")
            self.assertEqual(len(scoped_list), 0)

            # Explicit include_unscoped_legacy=True includes it
            legacy_inclusive = owner.list_decision_journal_entries(
                tenant_id="tenant-alpha",
                include_unscoped_legacy=True,
            )
            self.assertEqual(len(legacy_inclusive), 1)
            self.assertEqual(legacy_inclusive[0]["id"], "legacy-entry-001")

    def test_restart_fresh_reader_parity(self) -> None:
        """A second adapter over the same data dir sees identical state.

        Simulates a process restart: no in-memory state is shared between
        the two adapter instances, only the durable owner store on disk.
        """

        with tempfile.TemporaryDirectory() as tmp:
            first = build_decision_journal_owner_adapter(_BareInnerReadStore(), data_dir=tmp)
            created = first.create_decision_journal_entry(
                title="Freeze rollback candidate",
                body="Restart parity check.",
                actor_id="op-2",
                payload={},
                created_at="2026-09-05T01:00:00Z",
            )
            first.patch_decision_journal_entry(
                created["id"],
                patch={"body": "Restart parity check (patched)."},
                actor_id="op-2",
                idempotency_key="idem-2",
                request_hash="hash-2",
                patched_at="2026-09-05T01:05:00Z",
            )

            second = build_decision_journal_owner_adapter(_BareInnerReadStore(), data_dir=tmp)
            fresh = second.list_decision_journal_entries()
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh[0]["id"], created["id"])
            self.assertEqual(fresh[0]["body"], "Restart parity check (patched).")
            self.assertEqual(fresh[0]["version"], 2)

    def test_wrap_get_read_store_builds_adapter_each_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inner = _BareInnerReadStore()
            wrapped = wrap_get_read_store_with_decision_journal_owner(lambda: inner, data_dir=tmp)
            store_a = wrapped()
            store_b = wrapped()
            self.assertIsInstance(store_a, DecisionJournalOwnerAdapter)
            self.assertIsInstance(store_b, DecisionJournalOwnerAdapter)

            created = store_a.create_decision_journal_entry(
                title="Wrapped store parity",
                body="",
                actor_id="op-3",
                payload={},
                created_at="2026-09-05T02:00:00Z",
            )
            self.assertEqual(len(store_b.list_decision_journal_entries()), 1)
            self.assertEqual(store_b.list_decision_journal_entries()[0]["id"], created["id"])


def _operator_identity() -> OperatorIdentity:
    return OperatorIdentity(operator_id="op-agora", roles=["operator"], mfa_verified=True)


class TestAgoraServiceUsesCanonicalDecisionJournalOwner(unittest.TestCase):
    def _build_service(self, get_read_store) -> AgoraService:
        return AgoraService(get_read_store=get_read_store)

    def test_create_list_patch_through_agora_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            get_read_store = wrap_get_read_store_with_decision_journal_owner(
                lambda: _BareInnerReadStore(), data_dir=tmp
            )
            service = self._build_service(get_read_store)
            identity = _operator_identity()

            created = service.create_journal_entry(
                payload={"title": "Persona drift review", "body": "Escalate.", "visibility": "private"},
                identity=identity,
                idempotency_key="agora-journal-idem-1",
                x_idempotency_key=None,
            )
            entry = created["data"]
            self.assertEqual(entry["canonicalWriteAuthority"], "governance-decision-journal-svc")
            self.assertNotIn("bff_local_dev_store", str(entry.get("persistenceMode")))

            listed = service.list_journal_entries(identity=identity)
            self.assertEqual(listed["items"][0]["id"], entry["id"])

            patch_response = service.patch_journal_entry(
                entry_id=entry["id"],
                patch={"title": "Persona drift review (escalated)"},
                identity=identity,
                resolved_key="agora-journal-patch-1",
            )
            self.assertEqual(patch_response.data.title, "Persona drift review (escalated)")
            self.assertEqual(
                patch_response.meta["canonicalWriteAuthority"], "governance-decision-journal-svc"
            )
            self.assertNotIn("degraded", patch_response.meta)

    def test_create_journal_entry_fails_closed_without_owner_adapter(self) -> None:
        service = self._build_service(lambda: _BareInnerReadStore())
        identity = _operator_identity()

        with self.assertRaises(HTTPException) as ctx:
            service.create_journal_entry(
                payload={"title": "Should not persist", "body": ""},
                identity=identity,
                idempotency_key="agora-journal-idem-2",
                x_idempotency_key=None,
            )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_patch_journal_entry_fails_closed_without_owner_adapter(self) -> None:
        service = self._build_service(lambda: _BareInnerReadStore())
        identity = _operator_identity()

        with self.assertRaises(HTTPException) as ctx:
            service.patch_journal_entry(
                entry_id="dje-does-not-exist",
                patch={"title": "x"},
                identity=identity,
                resolved_key="agora-journal-patch-2",
            )
        self.assertEqual(ctx.exception.status_code, 503)

    def test_create_replay_does_not_return_other_principal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            owner = build_decision_journal_write_owner(data_dir=tmp)
            svc = AgoraService(journal_write_owner=owner)
            first = svc.create_journal_entry(
                payload={"title": "Synthetic", "body": "Synthetic private body", "visibility": "private"},
                identity=OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True),
                idempotency_key="same-key",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            second = svc.create_journal_entry(
                payload={"title": "Synthetic", "body": "Synthetic private body", "visibility": "private"},
                identity=OperatorIdentity(operator_id="bob", roles=["operator"], mfa_verified=True),
                idempotency_key="same-key",
                x_idempotency_key=None,
                tenant_id="tenant-b",
                user_id="bob",
            )
            self.assertNotEqual(first["data"]["id"], second["data"]["id"])

    def test_create_replay_survives_new_service(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first_svc = AgoraService(journal_write_owner=build_decision_journal_write_owner(data_dir=tmp))
            first = first_svc.create_journal_entry(
                payload={"title": "Synthetic", "body": "Synthetic private body", "visibility": "private"},
                identity=OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True),
                idempotency_key="same-key",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            second_svc = AgoraService(journal_write_owner=build_decision_journal_write_owner(data_dir=tmp))
            second = second_svc.create_journal_entry(
                payload={"title": "Synthetic", "body": "Synthetic private body", "visibility": "private"},
                identity=OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True),
                idempotency_key="same-key",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertEqual(first["data"]["id"], second["data"]["id"])
            self.assertTrue(second["meta"]["idempotency"]["replayed"])

    def test_create_replay_payload_mismatch_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = AgoraService(journal_write_owner=build_decision_journal_write_owner(data_dir=tmp))
            svc.create_journal_entry(
                payload={"title": "Original Title", "body": "Body", "visibility": "private"},
                identity=OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True),
                idempotency_key="conflict-key",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            with self.assertRaises(HTTPException) as ctx:
                svc.create_journal_entry(
                    payload={"title": "Different Title", "body": "Body", "visibility": "private"},
                    identity=OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True),
                    idempotency_key="conflict-key",
                    x_idempotency_key=None,
                    tenant_id="tenant-a",
                    user_id="alice",
                )
            self.assertEqual(ctx.exception.status_code, 409)

    def test_existing_consumer_observes_new_write(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            reader = DomainDecisionJournalReaderPort(data_dir=tmp)
            self.assertEqual(reader.list_decision_journal_entries(tenant_id="tenant-a", user_id="alice"), [])
            owner = build_decision_journal_write_owner(data_dir=tmp)
            owner.create_decision_journal_entry(
                title="Observed Title",
                body="Observed Body",
                actor_id="alice",
                tenant_id="tenant-a",
                user_id="alice",
                created_at="2026-09-08T00:00:00Z",
            )
            entries = reader.list_decision_journal_entries(tenant_id="tenant-a", user_id="alice")
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["title"], "Observed Title")

    def test_actual_subprocess_restart_parity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = os.environ.copy()
            env["PYTHONPATH"] = os.getcwd()
            # Subprocess 1: create entry
            code_1 = f"""
import sys
from services.control_plane.bff.governance.decision_journal_write_owner import build_decision_journal_write_owner
owner = build_decision_journal_write_owner(data_dir={tmp!r})
res = owner.create_decision_journal_entry(
    title="Subprocess Initial",
    body="Body 1",
    entry_id="dje-subproc-1",
    actor_id="operator-subproc",
    created_at="2026-09-08T01:00:00Z",
    tenant_id="tenant-subproc",
    user_id="operator-subproc",
)
assert res["id"] == "dje-subproc-1"
sys.exit(0)
"""
            p1 = subprocess.run([sys.executable, "-c", code_1], capture_output=True, text=True, env=env)
            self.assertEqual(p1.returncode, 0, f"stdout: {p1.stdout}\nstderr: {p1.stderr}")

            # Subprocess 2: patch entry
            code_2 = f"""
import sys
from services.control_plane.bff.governance.decision_journal_write_owner import build_decision_journal_write_owner
owner = build_decision_journal_write_owner(data_dir={tmp!r})
res = owner.patch_decision_journal_entry(
    "dje-subproc-1",
    patch={{"title": "Subprocess Patched"}},
    actor_id="operator-subproc",
    tenant_id="tenant-subproc",
    idempotency_key="subproc-idem-key",
    request_hash="subproc-hash",
    patched_at="2026-09-08T01:05:00Z",
)
assert res is not None and res["entry"]["version"] == 2
sys.exit(0)
"""
            p2 = subprocess.run([sys.executable, "-c", code_2], capture_output=True, text=True, env=env)
            self.assertEqual(p2.returncode, 0, f"stdout: {p2.stdout}\nstderr: {p2.stderr}")

            # Subprocess 3: read and verify
            code_3 = f"""
import sys
from services.control_plane.bff.ports.operations_consultation import DomainDecisionJournalReaderPort
reader = DomainDecisionJournalReaderPort(data_dir={tmp!r})
entries = reader.list_decision_journal_entries(tenant_id="tenant-subproc", user_id="operator-subproc")
assert len(entries) == 1
assert entries[0]["id"] == "dje-subproc-1"
assert entries[0]["title"] == "Subprocess Patched"
assert entries[0]["version"] == 2
sys.exit(0)
"""
            p3 = subprocess.run([sys.executable, "-c", code_3], capture_output=True, text=True, env=env)
            self.assertEqual(p3.returncode, 0, f"stdout: {p3.stdout}\nstderr: {p3.stderr}")

    def test_daily_brief_does_not_publish_unscoped_private_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stores = build_decision_journal_stores(tmp)
            create_entry(
                stores,
                entry_id="synthetic-entry",
                title="Synthetic",
                body="Private synthetic body",
                actor_id="alice",
                tenant_id="tenant-a",
                created_at="2026-09-08T00:00:00Z",
            )
            reader = DomainDecisionJournalReaderPort(data_dir=tmp)
            service = AgoraService(get_read_store=lambda: reader)
            result = service.get_daily_brief()
            self.assertEqual(
                result["data"]["sections"]["journal"],
                [],
                "daily brief published tenant-a/alice private journal without any authenticated principal",
            )

    def test_concurrent_create_retry_is_one_entry(self) -> None:
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            stores = build_decision_journal_stores(tmp)
            owner = DecisionJournalOwnerAdapter(stores=stores)
            barrier = threading.Barrier(2)
            original = owner.check_create_idempotency

            def synchronized_read(**kw):
                result = original(**kw)
                barrier.wait(timeout=10)
                return result

            def run():
                service = AgoraService(journal_write_owner=owner)
                return service.create_journal_entry(
                    payload={"title": "Synthetic", "body": "Private synthetic body"},
                    identity=OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True),
                    idempotency_key="same-key",
                    x_idempotency_key=None,
                    tenant_id="tenant-a",
                )["data"]["id"]

            with patch.object(owner, "check_create_idempotency", side_effect=synchronized_read):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    ids = list(executor.map(lambda _: run(), range(2)))
            self.assertEqual(ids[0], ids[1], "same principal/request/key created two durable entries concurrently")


if __name__ == "__main__":
    unittest.main()

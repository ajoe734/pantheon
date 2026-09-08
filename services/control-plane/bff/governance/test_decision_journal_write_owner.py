"""JOURNAL-OWNER-001: convergence tests for the Decision Journal owner adapter.

Proves the acceptance criteria from SD §5.3 for the narrow slice this task
owns: one durable write owner backs both the adapter used directly and the
Agora BFF route handlers, a fresh adapter pointed at the same data directory
sees identical state after a simulated restart, and the Agora service fails
closed instead of fabricating an unpersisted "success" when the canonical
owner adapter is missing.
"""
from __future__ import annotations

import json
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
    domain_creation_idempotency_key,
    get_entry,
    list_audit_events,
    list_outbox_events,
    patch_idempotency_key,
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
                tenant_id="tenant-dev",
                payload={"tags": ["risk"], "visibility": "private"},
                created_at="2026-09-05T00:00:00Z",
            )
            self.assertEqual(created["title"], "Delay promotion")
            self.assertEqual(created["canonicalWriteAuthority"], "governance-decision-journal-svc")

            # Unscoped query returns empty (fail closed)
            self.assertEqual(len(adapter.list_decision_journal_entries()), 0)

            listed = adapter.list_decision_journal_entries(tenant_id="tenant-dev", actor_id="op-1")
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["id"], created["id"])

            patched = adapter.patch_decision_journal_entry(
                created["id"],
                patch={"title": "Delay promotion (updated)"},
                actor_id="op-1",
                tenant_id="tenant-dev",
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
                tenant_id="tenant-parity",
                payload={},
                created_at="2026-09-05T01:00:00Z",
            )
            first.patch_decision_journal_entry(
                created["id"],
                patch={"body": "Restart parity check (patched)."},
                actor_id="op-2",
                tenant_id="tenant-parity",
                idempotency_key="idem-2",
                request_hash="hash-2",
                patched_at="2026-09-05T01:05:00Z",
            )

            second = build_decision_journal_owner_adapter(_BareInnerReadStore(), data_dir=tmp)
            # Unscoped query returns empty (fail closed)
            self.assertEqual(len(second.list_decision_journal_entries()), 0)

            fresh = second.list_decision_journal_entries(tenant_id="tenant-parity", actor_id="op-2")
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
                tenant_id="tenant-wrapped",
                payload={},
                created_at="2026-09-05T02:00:00Z",
            )
            # Unscoped query returns empty (fail closed)
            self.assertEqual(len(store_b.list_decision_journal_entries()), 0)

            fresh = store_b.list_decision_journal_entries(tenant_id="tenant-wrapped", actor_id="op-3")
            self.assertEqual(len(fresh), 1)
            self.assertEqual(fresh[0]["id"], created["id"])


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


    def test_unscoped_detail_must_not_disclose_private_record(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stores = build_decision_journal_stores(tmp)
            create_entry(
                stores,
                entry_id="private-a",
                title="Synthetic",
                body="original",
                actor_id="alice",
                tenant_id="tenant-a",
                created_at="2026-09-08T00:00:00Z",
            )
            adapter = DecisionJournalOwnerAdapter(stores=stores)
            self.assertIsNone(adapter.get_decision_journal_entry("private-a"))

    def test_adapter_tenant_only_detail_denies_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stores = build_decision_journal_stores(tmp)
            create_entry(
                stores,
                entry_id="e",
                title="original",
                body="original",
                actor_id="alice",
                tenant_id="tenant-a",
                created_at="2026-09-08T00:00:00Z",
            )
            self.assertIsNone(
                DecisionJournalOwnerAdapter(stores=stores).get_decision_journal_entry("e", tenant_id="tenant-a")
            )

    def test_global_reader_unscoped_detail_denies_private(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            stores = build_decision_journal_stores(tmp)
            create_entry(
                stores,
                entry_id="e",
                title="original",
                body="original",
                actor_id="alice",
                tenant_id="tenant-a",
                created_at="2026-09-08T00:00:00Z",
            )
            self.assertIsNone(DomainDecisionJournalReaderPort(stores=stores).get_decision_journal_entry("e"))

    def test_bff_create_crash_retries_committed_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            crash_code = (
                "import os, sys\n"
                "from services.control_plane.bff.agora.service import AgoraService\n"
                "from services.control_plane.bff.governance.decision_journal_write_owner import build_decision_journal_write_owner\n"
                "from services.control_plane.bff.models import OperatorIdentity\n"
                "owner = build_decision_journal_write_owner(data_dir=sys.argv[1])\n"
                "owner.record_create_idempotency = lambda **kwargs: os._exit(74)\n"
                "AgoraService(journal_write_owner=owner).create_journal_entry(\n"
                "    payload={'title': 'Initial', 'body': 'body'},\n"
                "    identity=OperatorIdentity(operator_id='alice', roles=['operator'], mfa_verified=True),\n"
                "    idempotency_key='request-1',\n"
                "    x_idempotency_key=None,\n"
                "    tenant_id='tenant',\n"
                "    user_id='alice',\n"
                ")\n"
            )
            child = subprocess.run(
                [sys.executable, "-c", crash_code, tmp],
                env=dict(os.environ, PYTHONPATH="."),
                timeout=20,
            )
            self.assertEqual(child.returncode, 74)
            owner = build_decision_journal_write_owner(data_dir=tmp)
            self.assertEqual(len(owner.stores.entries.list_all()), 1)
            service = AgoraService(journal_write_owner=owner)
            result = service.create_journal_entry(
                payload={"title": "Initial", "body": "body"},
                identity=OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True),
                idempotency_key="request-1",
                x_idempotency_key=None,
                tenant_id="tenant",
                user_id="alice",
            )
            self.assertTrue(result["meta"]["idempotency"]["replayed"])

    def test_bff_concurrent_retry_does_not_promote_provisional_entry(self) -> None:
        import threading
        from concurrent.futures import ThreadPoolExecutor

        with tempfile.TemporaryDirectory() as tmp:
            stores = build_decision_journal_stores(tmp)
            owner = DecisionJournalOwnerAdapter(stores=stores)
            service = AgoraService(journal_write_owner=owner)
            entered = threading.Event()
            release = threading.Event()

            def blocked_failure(event: Any) -> None:
                entered.set()
                release.wait(5)
                raise OSError("injected outbox failure")

            stores.outbox.put = blocked_failure  # type: ignore[assignment]
            args = dict(
                payload={"id": "entry-1", "title": "provisional", "body": "synthetic"},
                identity=OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True),
                idempotency_key="request-1",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            with ThreadPoolExecutor(max_workers=1) as pool:
                writer = pool.submit(service.create_journal_entry, **args)
                self.assertTrue(entered.wait(5), "writer did not reach outbox")

                # While create is pending:
                # 1. Scoped get_entry must return None
                self.assertIsNone(owner.get_decision_journal_entry("entry-1", tenant_id="tenant-a", user_id="alice"))

                # 2. check_create_idempotency must return pending without promoting provisional row
                check = owner.check_create_idempotency(
                    scoped_key="create:tenant-a:alice:request-1",
                    request_hash=service.stable_json_hash({"route": "POST /bff/agora/journal", "payload": dict(args["payload"], visibility="private")}),
                    entry_id="entry-1",
                    raw_key="request-1",
                    tenant_id="tenant-a",
                    user_id="alice",
                )
                self.assertIsNotNone(check)
                self.assertTrue(check.get("pending"))
                self.assertIsNone(check.get("result"))

                # 3. _recover_committed_entry_result must return None for live provisional row
                pending_rec = stores.idempotency.get("create:tenant-a:alice:request-1")
                self.assertIsNotNone(pending_rec)
                self.assertIsNone(owner._recover_committed_entry_result(pending_rec, entry_id="entry-1", raw_key="request-1"))

                # Release writer to fail
                release.set()
                with self.assertRaises(OSError):
                    writer.result(timeout=5)

            # After rollback: entry is None, idempotency is failed, no ghost success
            self.assertIsNone(owner.get_decision_journal_entry("entry-1", tenant_id="tenant-a", user_id="alice"))
            self.assertEqual(stores.idempotency.get("create:tenant-a:alice:request-1")["status"], "failed")

    def test_supplied_entry_id_overlap_with_bff_request_key_does_not_alias(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            stores = build_decision_journal_stores(tmp_dir)
            owner = DecisionJournalOwnerAdapter(stores=stores)
            service = AgoraService(journal_write_owner=owner)

            identity = OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True)

            # 1. Create entry-a with idempotency key request-a
            res1 = service.create_journal_entry(
                payload={"id": "entry-a", "title": "First", "body": "Body 1"},
                identity=identity,
                idempotency_key="request-a",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertFalse(res1["meta"]["idempotency"]["replayed"])
            self.assertEqual(res1["data"]["id"], "entry-a")

            # 2. Replay request-a: must return replayed result
            res1_replay = service.create_journal_entry(
                payload={"id": "entry-a", "title": "First", "body": "Body 1"},
                identity=identity,
                idempotency_key="request-a",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertTrue(res1_replay["meta"]["idempotency"]["replayed"])

            # 3. Create entry where supplied ID is "request-a" (overlapping previous request key!) and key is "request-b"
            res2 = service.create_journal_entry(
                payload={"id": "request-a", "title": "Second", "body": "Body 2"},
                identity=identity,
                idempotency_key="request-b",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertFalse(res2["meta"]["idempotency"]["replayed"])
            self.assertEqual(res2["data"]["id"], "request-a")

            # 4. Verify request-level idempotency record was NOT overwritten by domain write
            req_a_record = stores.idempotency.get("create:tenant-a:alice:request-a")
            self.assertIsNotNone(req_a_record)
            self.assertEqual(req_a_record.get("entry_id"), "entry-a")
            self.assertEqual(req_a_record.get("status"), "succeeded")
            self.assertIsNotNone(req_a_record.get("request_hash"))
            self.assertIsNotNone(req_a_record.get("result"))

            # Verify domain creation transaction record is isolated
            domain_key = domain_creation_idempotency_key(
                tenant_id="tenant-a",
                actor_id="alice",
                entry_id="request-a",
            )
            self.assertEqual(domain_key, "domain:create:tenant-a:alice:request-a")
            domain_rec = stores.idempotency.get(domain_key)
            self.assertIsNotNone(domain_rec)
            self.assertEqual(domain_rec.get("entry_id"), "request-a")
            self.assertEqual(domain_rec.get("status"), "succeeded")

            # 5. Replay request-a again: MUST NOT raise HTTP 409 IDEMPOTENCY_CONFLICT
            res1_replay2 = service.create_journal_entry(
                payload={"id": "entry-a", "title": "First", "body": "Body 1"},
                identity=identity,
                idempotency_key="request-a",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertTrue(res1_replay2["meta"]["idempotency"]["replayed"])
            self.assertEqual(res1_replay2["data"]["id"], "entry-a")

            # 6. Replay request-b: must return replayed result
            res2_replay = service.create_journal_entry(
                payload={"id": "request-a", "title": "Second", "body": "Body 2"},
                identity=identity,
                idempotency_key="request-b",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertTrue(res2_replay["meta"]["idempotency"]["replayed"])
            self.assertEqual(res2_replay["data"]["id"], "request-a")

    def test_crash_recovery_dead_process_with_supplied_id_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            stores = build_decision_journal_stores(tmp_dir)
            owner = DecisionJournalOwnerAdapter(stores=stores)
            service = AgoraService(journal_write_owner=owner)

            identity = OperatorIdentity(operator_id="alice", roles=["operator"], mfa_verified=True)

            # Create entry with supplied ID "key-x" and request key "key-y"
            res = service.create_journal_entry(
                payload={"id": "key-x", "title": "Title", "body": "Body"},
                identity=identity,
                idempotency_key="key-y",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertEqual(res["data"]["id"], "key-x")

            # Simulate dead creator process on a new request key "key-x" whose target ID is "entry-z"
            dead_pid = 99999999  # Guaranteed dead PID
            scoped_key = "create:tenant-a:alice:key-x"
            req_payload = {"id": "entry-z", "title": "Z", "body": "Z", "visibility": "private"}
            req_hash = service.stable_json_hash({"route": "POST /bff/agora/journal", "payload": req_payload})
            stores.idempotency.put({
                "idempotency_key": scoped_key,
                "raw_idempotency_key": "key-x",
                "tenant_id": "tenant-a",
                "user_id": "alice",
                "actor_id": "alice",
                "request_hash": req_hash,
                "entry_id": "entry-z",
                "status": "pending",
                "created_pid": dead_pid,
                "created_at": 100.0,
                "result": None,
            })

            # service.create_journal_entry detects dead creator without committed entry and reclaims reservation
            res_z = service.create_journal_entry(
                payload={"id": "entry-z", "title": "Z", "body": "Z"},
                identity=identity,
                idempotency_key="key-x",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertEqual(res_z["data"]["id"], "entry-z")
            self.assertFalse(res_z["meta"]["idempotency"]["replayed"])

            # Replay request key-x: must return replayed result
            res_z_replay = service.create_journal_entry(
                payload={"id": "entry-z", "title": "Z", "body": "Z"},
                identity=identity,
                idempotency_key="key-x",
                x_idempotency_key=None,
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertTrue(res_z_replay["meta"]["idempotency"]["replayed"])
            self.assertEqual(res_z_replay["data"]["id"], "entry-z")

            # Both entry-z and key-x exist without aliasing
            self.assertIsNotNone(owner.get_decision_journal_entry("entry-z", tenant_id="tenant-a", user_id="alice"))
            self.assertIsNotNone(owner.get_decision_journal_entry("key-x", tenant_id="tenant-a", user_id="alice"))

    def test_create_replay_must_not_cross_ambiguous_scope_keys(self) -> None:
        with tempfile.TemporaryDirectory() as path:
            owner = DecisionJournalOwnerAdapter(stores=build_decision_journal_stores(path))
            service = AgoraService(journal_write_owner=owner)
            payload = {"title": "private record", "body": "private content"}
            first = service.create_journal_entry(
                payload=payload,
                identity=OperatorIdentity(operator_id="bob", roles=["operator"], mfa_verified=True),
                tenant_id="tenant:alice",
                user_id="bob",
                idempotency_key="key",
                x_idempotency_key=None,
            )
            second = service.create_journal_entry(
                payload=payload,
                identity=OperatorIdentity(operator_id="alice:bob", roles=["operator"], mfa_verified=True),
                tenant_id="tenant",
                user_id="alice:bob",
                idempotency_key="key",
                x_idempotency_key=None,
            )
            self.assertNotEqual(first["data"]["id"], second["data"]["id"], "different tenant/user returned the first principal record")
            self.assertEqual(second["data"]["tenant_id"], "tenant")
            self.assertEqual(first["data"]["tenant_id"], "tenant:alice")

    def test_idempotency_scope_mismatch_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as path:
            stores = build_decision_journal_stores(path)
            owner = DecisionJournalOwnerAdapter(stores=stores)
            forged_key = "create:tenant-a:alice:key-mismatch"
            stores.idempotency.put({
                "idempotency_key": forged_key,
                "raw_idempotency_key": "key-mismatch",
                "tenant_id": "tenant-a",
                "user_id": "alice",
                "actor_id": "alice",
                "request_hash": "somehash",
                "entry_id": "entry-mismatch",
                "status": "succeeded",
                "created_pid": os.getpid(),
                "created_at": 100.0,
                "result": {
                    "data": {
                        "id": "entry-mismatch",
                        "tenant_id": "tenant-b",
                        "createdBy": "alice",
                    }
                },
            })
            checked = owner.check_create_idempotency(
                scoped_key=forged_key,
                request_hash="somehash",
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertIsNotNone(checked)
            self.assertTrue(checked.get("conflict"))
            self.assertEqual(checked.get("reason"), "cross_tenant_scope_mismatch")

    def test_patch_cannot_authorize_using_uncommitted_visibility_and_recovers_later(self) -> None:
        with tempfile.TemporaryDirectory() as path:
            stores = build_decision_journal_stores(path)
            create_entry(
                stores,
                entry_id="entry-sec",
                title="private title",
                body="COMMITTED PRIVATE BODY",
                created_at="2026-09-08T10:00:00Z",
                tenant_id="tenant-a",
                actor_id="alice",
                user_id="alice",
            )
            child_code = (
                "import os, sys\n"
                "from services.governance.decision_journal import build_decision_journal_stores, patch_entry\n"
                "s = build_decision_journal_stores(sys.argv[1])\n"
                "s.outbox.put = lambda event: os._exit(74)\n"
                "patch_entry(s, 'entry-sec', patch={'visibility': 'public', 'body': 'UNCOMMITTED BODY'}, "
                "tenant_id='tenant-a', actor_id='alice', user_id='alice', idempotency_key='publish', request_hash='publish', patched_at='2026-09-08T10:01:00Z')\n"
            )
            child = subprocess.run([sys.executable, "-c", child_code, path], timeout=10)
            self.assertEqual(child.returncode, 74)

            fresh = build_decision_journal_stores(path)
            real_put = fresh.outbox.put
            def reject_crashed_event(event: Any) -> None:
                if event.get("raw_idempotency_key") == "publish":
                    raise OSError("prior transaction outbox unavailable")
                return real_put(event)
            fresh.outbox.put = reject_crashed_event  # type: ignore[assignment]

            owner = DecisionJournalOwnerAdapter(stores=fresh)
            service = AgoraService(get_read_store=lambda: owner, journal_write_owner=owner)
            bob = OperatorIdentity(operator_id="bob", claims={"tenant_id": "tenant-a"}, roles=["operator"], mfa_verified=True)

            # 1. Bob cannot see or mutate using uncommitted public visibility (must 404 / fail closed)
            with self.assertRaises(Exception) as cm:
                service.patch_journal_entry(
                    entry_id="entry-sec",
                    patch={"title": "bob changed"},
                    identity=bob,
                    resolved_key="bob-patch",
                    tenant_id="tenant-a",
                    user_id="bob",
                )
            self.assertIn(getattr(cm.exception, "status_code", None), (403, 404, 409, 503))

            # 2. Alice attempting to patch while predecessor outbox is unavailable fails closed (409)
            alice = OperatorIdentity(operator_id="alice", claims={"tenant_id": "tenant-a"}, roles=["operator"], mfa_verified=True)
            with self.assertRaises(Exception) as cm2:
                service.patch_journal_entry(
                    entry_id="entry-sec",
                    patch={"title": "alice changed"},
                    identity=alice,
                    resolved_key="alice-patch-blocked",
                    tenant_id="tenant-a",
                    user_id="alice",
                )
            self.assertIn(getattr(cm2.exception, "status_code", None), (409, 503))

            # 3. Outbox recovers: later recovery succeeds and subsequent mutation succeeds
            fresh.outbox.put = real_put  # type: ignore[assignment]
            succ = service.patch_journal_entry(
                entry_id="entry-sec",
                patch={"title": "alice changed after recovery"},
                identity=alice,
                resolved_key="alice-patch-success",
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertEqual(succ.status, "completed")
            self.assertEqual(succ.data.title, "alice changed after recovery")
            self.assertEqual(succ.data.visibility, "public")
            self.assertEqual(succ.data.body, "UNCOMMITTED BODY")

    def test_supplied_id_recreate_resolves_committed_snapshot_and_recovers_later(self) -> None:
        with tempfile.TemporaryDirectory() as path:
            stores = build_decision_journal_stores(path)
            create_entry(
                stores,
                entry_id="entry-rec",
                title="private title",
                body="COMMITTED PRIVATE BODY",
                created_at="2026-09-08T10:00:00Z",
                tenant_id="tenant-a",
                actor_id="alice",
                user_id="alice",
            )
            child_code = (
                "import os, sys\n"
                "from services.governance.decision_journal import build_decision_journal_stores, patch_entry\n"
                "s = build_decision_journal_stores(sys.argv[1])\n"
                "s.outbox.put = lambda event: os._exit(74)\n"
                "patch_entry(s, 'entry-rec', patch={'visibility': 'public', 'body': 'UNCOMMITTED BODY'}, "
                "tenant_id='tenant-a', actor_id='alice', user_id='alice', idempotency_key='publish', request_hash='publish', patched_at='2026-09-08T10:01:00Z')\n"
            )
            child = subprocess.run([sys.executable, "-c", child_code, path], timeout=10)
            self.assertEqual(child.returncode, 74)

            fresh = build_decision_journal_stores(path)
            real_put = fresh.outbox.put
            def reject_crashed_event(event: Any) -> None:
                if event.get("raw_idempotency_key") == "publish":
                    raise OSError("prior transaction outbox unavailable")
                return real_put(event)
            fresh.outbox.put = reject_crashed_event  # type: ignore[assignment]

            owner = DecisionJournalOwnerAdapter(stores=fresh)
            service = AgoraService(get_read_store=lambda: owner, journal_write_owner=owner)
            alice = OperatorIdentity(operator_id="alice", claims={"tenant_id": "tenant-a"}, roles=["operator"], mfa_verified=True)

            # 1. Supplied-ID recreate during outbox outage returns committed private body, not uncommitted patch
            res = service.create_journal_entry(
                payload={"id": "entry-rec", "title": "private title", "body": "COMMITTED PRIVATE BODY"},
                identity=alice,
                tenant_id="tenant-a",
                user_id="alice",
                idempotency_key="recreate-1",
                x_idempotency_key=None,
            )
            self.assertEqual(res["data"]["body"], "COMMITTED PRIVATE BODY")
            self.assertEqual(res["data"]["visibility"], "private")

            # 2. Later recovery when outbox recovers
            fresh.outbox.put = real_put  # type: ignore[assignment]
            get_res = owner.get_decision_journal_entry(
                "entry-rec",
                tenant_id="tenant-a",
                user_id="alice",
            )
            self.assertIsNotNone(get_res)
            self.assertEqual(get_res["id"], "entry-rec")

    def test_owner_adapter_failed_replay_recovery_can_recover_after_outbox_returns(self) -> None:
        """P1 adapter: crash recovery after outbox failure reconciles failed idempotency intent and allows successor."""
        with tempfile.TemporaryDirectory() as path:
            stores = build_decision_journal_stores(path)
            create_entry(
                stores,
                entry_id="entry-adapt-crash",
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
    'entry-adapt-crash',
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

            # 1. Retry during outbox failure
            fresh = build_decision_journal_stores(path)
            def unavailable(event: Any) -> None:
                raise OSError("outbox still down")
            fresh.outbox.put = unavailable  # type: ignore[assignment]
            owner_fresh = DecisionJournalOwnerAdapter(stores=fresh)
            failed = owner_fresh.patch_decision_journal_entry(
                "entry-adapt-crash",
                patch={"title": "crashed"},
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                idempotency_key="crashed",
                request_hash="crashed",
                patched_at="2026-09-08T01:00:00Z",
            )
            self.assertEqual(failed["status"], "failed")

            # 2. Fresh recovery after outage
            recovered = build_decision_journal_stores(path)
            owner_rec = DecisionJournalOwnerAdapter(stores=recovered)
            get_entry(recovered, "entry-adapt-crash", tenant_id="tenant-a", actor_id="alice")
            key = patch_idempotency_key(tenant_id="tenant-a", actor_id="alice", idempotency_key="crashed")
            self.assertEqual(recovered.idempotency.get(key)["status"], "succeeded")

            # 3. Successor patch succeeds
            succ = owner_rec.patch_decision_journal_entry(
                "entry-adapt-crash",
                patch={"title": "next"},
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                idempotency_key="next",
                request_hash="next",
                patched_at="2026-09-08T02:00:00Z",
            )
            self.assertEqual(succ["status"], "updated")

            # 4. Replay parity
            replayed = owner_rec.patch_decision_journal_entry(
                "entry-adapt-crash",
                patch={"title": "crashed"},
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                idempotency_key="crashed",
                request_hash="crashed",
                patched_at="2026-09-08T01:00:00Z",
            )
            self.assertEqual(replayed["status"], "replayed")

    def test_owner_adapter_public_rewrite_redacts_private_before_body_in_audit(self) -> None:
        """P1 adapter: public rewrite redacts private before-image in audit for non-author reader."""
        with tempfile.TemporaryDirectory() as path:
            stores = build_decision_journal_stores(path)
            owner = DecisionJournalOwnerAdapter(stores=stores)
            owner.create_decision_journal_entry(
                entry_id="entry-pub-redact-adapter",
                title="private",
                body="PRIVATE ORIGINAL",
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                created_at="2026-09-08T00:00:00Z",
                visibility="private",
            )
            owner.patch_decision_journal_entry(
                "entry-pub-redact-adapter",
                patch={"visibility": "public", "body": "PUBLIC REPLACEMENT"},
                actor_id="alice",
                user_id="alice",
                tenant_id="tenant-a",
                idempotency_key="publish",
                request_hash="publish",
                patched_at="2026-09-08T01:00:00Z",
            )
            bob_entry = owner.get_decision_journal_entry("entry-pub-redact-adapter", tenant_id="tenant-a", actor_id="bob")
            self.assertIsNotNone(bob_entry)
            self.assertEqual(bob_entry["body"], "PUBLIC REPLACEMENT")

            events = list_audit_events(stores, entry_id="entry-pub-redact-adapter", tenant_id="tenant-a", actor_id="bob")
            self.assertEqual(len(events), 1)
            self.assertNotIn("PRIVATE ORIGINAL", json.dumps(events), "Bob received private before-image in public rewrite audit")
            self.assertIsNone(events[0]["diff"]["before"])
            self.assertEqual(events[0]["diff"]["after"]["body"], "PUBLIC REPLACEMENT")

    def test_supported_tenant_claim_aliases_positive_roundtrip_and_parity(self) -> None:
        from unittest.mock import patch
        from services.control_plane.bff.agora.identity.scope import resolve_agora_user_scope

        aliases = [
            ("tenant_id", {"tenant_id": "tenant-alias-1"}),
            ("tenantId", {"tenantId": "tenant-alias-2"}),
            ("tenant.id", {"tenant": {"id": "tenant-alias-3"}}),
            ("tid", {"tid": "tenant-alias-4"}),
            ("org_id", {"org_id": "tenant-alias-5"}),
            ("organization.id", {"organization": {"id": "tenant-alias-6"}}),
        ]
        for name, claims in aliases:
            with self.subTest(alias=name):
                with tempfile.TemporaryDirectory() as tmp:
                    with patch.dict(os.environ, {}, clear=True):
                        owner = DecisionJournalOwnerAdapter(stores=build_decision_journal_stores(tmp))
                        reader = DomainDecisionJournalReaderPort(data_dir=tmp)
                        service = AgoraService(get_read_store=lambda: reader, journal_write_owner=owner)
                        identity = OperatorIdentity(
                            operator_id="operator-alice",
                            roles=["operator"],
                            mfa_verified=True,
                            claims={**claims, "sub": "operator-alice"},
                        )
                        scope = resolve_agora_user_scope(identity, utc_now=lambda: "2026-09-08T00:00:00Z")
                        val = list(claims.values())[0]
                        expected_tenant = val["id"] if isinstance(val, dict) else val
                        self.assertEqual(scope.tenant_id, expected_tenant)

                        created = service.create_journal_entry(
                            payload={"id": f"entry-{name}", "title": f"Title {name}", "body": "Body"},
                            identity=identity,
                            idempotency_key=f"create-{name}",
                            x_idempotency_key=None,
                            tenant_id=scope.tenant_id,
                            user_id=scope.user_id,
                        )
                        self.assertIsNotNone(created)

                        # Parity check: DomainDecisionJournalReaderPort vs AgoraService.list_journal_entries
                        canonical = reader.list_decision_journal_entries(tenant_id=scope.tenant_id, user_id=scope.user_id)
                        self.assertEqual(len(canonical), 1)
                        self.assertEqual(canonical[0]["id"], f"entry-{name}")

                        listed = service.list_journal_entries(
                            identity=identity,
                            tenant_id=scope.tenant_id,
                            user_id=scope.user_id,
                        )
                        self.assertEqual(len(listed["items"]), 1)
                        self.assertEqual(listed["items"][0]["id"], f"entry-{name}")
                        self.assertEqual(listed["data"], canonical)

                        # Patch check: owner can patch own created entry
                        patched = service.patch_journal_entry(
                            entry_id=f"entry-{name}",
                            patch={"title": f"Patched {name}"},
                            identity=identity,
                            resolved_key=f"patch-{name}",
                            tenant_id=scope.tenant_id,
                            user_id=scope.user_id,
                        )
                        self.assertIsNotNone(patched)
                        self.assertEqual(patched.data.title, f"Patched {name}")

    def test_env_tenant_precedence_over_claims(self) -> None:
        from unittest.mock import patch
        from fastapi import FastAPI, HTTPException
        from fastapi.testclient import TestClient
        from services.control_plane.bff.agora.identity.scope import (
            AgoraScopeResolutionError,
            resolve_agora_user_scope,
            resolve_canonical_agora_scope,
        )
        from services.control_plane.bff.agora.router import create_agora_router

        env_vars = [
            "PANTHEON_BFF_TENANT_ID",
            "PANTHEON_BFF_DEFAULT_TENANT_ID",
            "PANTHEON_TENANT_ID",
        ]
        for env_var in env_vars:
            with self.subTest(env_var=env_var):
                with tempfile.TemporaryDirectory() as tmp:
                    # 1. Authorized env positive test: env override takes precedence when within authorized scope
                    with patch.dict(os.environ, {env_var: "tenant-env-override"}, clear=True):
                        owner = DecisionJournalOwnerAdapter(stores=build_decision_journal_stores(tmp))
                        reader = DomainDecisionJournalReaderPort(data_dir=tmp)
                        service = AgoraService(get_read_store=lambda: reader, journal_write_owner=owner)
                        identity = OperatorIdentity(
                            operator_id="alice",
                            roles=["operator"],
                            mfa_verified=True,
                            claims={
                                "tid": "tenant-claim-fallback",
                                "sub": "alice",
                                "allowed_tenants": ["tenant-claim-fallback", "tenant-env-override"],
                            },
                        )
                        scope = resolve_agora_user_scope(identity, utc_now=lambda: "2026-09-08T00:00:00Z")
                        self.assertEqual(scope.tenant_id, "tenant-env-override")

                        created = service.create_journal_entry(
                            payload={"id": "entry-env", "title": "Env Title", "body": "Body"},
                            identity=identity,
                            idempotency_key="create-env",
                            x_idempotency_key=None,
                            tenant_id=scope.tenant_id,
                            user_id=scope.user_id,
                        )
                        self.assertEqual(created["data"]["tenant_id"], "tenant-env-override")

                        listed = service.list_journal_entries(
                            identity=identity,
                            tenant_id=scope.tenant_id,
                            user_id=scope.user_id,
                        )
                        self.assertEqual(len(listed["items"]), 1)
                        self.assertEqual(listed["items"][0]["id"], "entry-env")

                        patched = service.patch_journal_entry(
                            entry_id="entry-env",
                            patch={"title": "Patched Env"},
                            identity=identity,
                            resolved_key="patch-env",
                            tenant_id=scope.tenant_id,
                            user_id=scope.user_id,
                        )
                        self.assertEqual(patched.data.title, "Patched Env")

                    # 2. Denied env negative test: env default must not grant tenant membership when unauthorized
                    with patch.dict(os.environ, {env_var: "tenant-env-denied"}, clear=True):
                        denied_identity = OperatorIdentity(
                            operator_id="alice",
                            roles=["operator"],
                            mfa_verified=True,
                            claims={"tid": "tenant-a", "sub": "alice", "allowed_tenants": ["tenant-a"]},
                        )
                        with self.assertRaises(AgoraScopeResolutionError) as ctx:
                            resolve_agora_user_scope(denied_identity, utc_now=lambda: "2026-09-08T00:00:00Z")
                        self.assertEqual(ctx.exception.reason, "AGORA_SCOPE_TENANT_DENIED")

                        with self.assertRaises(AgoraScopeResolutionError):
                            resolve_canonical_agora_scope(denied_identity, tenant_id="tenant-env-denied")

                        # Denied through router path
                        app = FastAPI()
                        app_router = create_agora_router(
                            extract_identity=lambda *a, **k: denied_identity,
                            require_read_role=lambda *a, **k: None,
                            require_write_role=lambda *a, **k: None,
                            bff_error=lambda status, code, msg, details=None, **k: HTTPException(status_code=status, detail=msg),
                            utc_now=lambda: "2026-09-08T00:00:00Z",
                            sync_servant_agent=lambda payload: payload,
                            get_read_store=lambda: reader,
                            service=service,
                        )
                        app.include_router(app_router)
                        client = TestClient(app)

                        # GET journal fails with 403 when env default tenant is unauthorized
                        res_get = client.get("/bff/agora/journal")
                        self.assertEqual(res_get.status_code, 403)

                        # POST journal fails with 403
                        res_post = client.post("/bff/agora/journal", json={"title": "T", "body": "B"})
                        self.assertEqual(res_post.status_code, 403)

                    # 3. Denied requested-tenant negative test: caller requested tenant cannot elevate access
                    with patch.dict(os.environ, {}, clear=True):
                        restricted_identity = OperatorIdentity(
                            operator_id="alice",
                            roles=["operator"],
                            mfa_verified=True,
                            claims={"tid": "tenant-a", "sub": "alice", "allowed_tenants": ["tenant-a"]},
                        )
                        with self.assertRaises(AgoraScopeResolutionError) as ctx:
                            resolve_agora_user_scope(
                                restricted_identity,
                                utc_now=lambda: "2026-09-08T00:00:00Z",
                                requested_tenant_id="tenant-b",
                            )
                        self.assertEqual(ctx.exception.reason, "AGORA_SCOPE_TENANT_DENIED")

                        with self.assertRaises(AgoraScopeResolutionError):
                            resolve_canonical_agora_scope(restricted_identity, tenant_id="tenant-b")

                        # Denied requested tenant through router headers
                        app = FastAPI()
                        app_router = create_agora_router(
                            extract_identity=lambda *a, **k: restricted_identity,
                            require_read_role=lambda *a, **k: None,
                            require_write_role=lambda *a, **k: None,
                            bff_error=lambda status, code, msg, details=None, **k: HTTPException(status_code=status, detail=msg),
                            utc_now=lambda: "2026-09-08T00:00:00Z",
                            sync_servant_agent=lambda payload: payload,
                            get_read_store=lambda: reader,
                            service=service,
                        )
                        app.include_router(app_router)
                        client = TestClient(app)

                        res_denied_hdr = client.get("/bff/agora/journal", headers={"X-Tenant-Id": "tenant-b"})
                        self.assertEqual(res_denied_hdr.status_code, 403)

                        res_denied_post = client.post(
                            "/bff/agora/journal",
                            json={"title": "T", "body": "B", "tenant_id": "tenant-b"},
                        )
                        self.assertEqual(res_denied_post.status_code, 403)

                        # Authorized requested tenant succeeds through router
                        res_auth_hdr = client.get("/bff/agora/journal", headers={"X-Tenant-Id": "tenant-a"})
                        self.assertEqual(res_auth_hdr.status_code, 200)

    def test_fail_closed_cross_tenant_and_cross_user_isolation(self) -> None:
        from unittest.mock import patch
        from services.control_plane.bff.agora.identity.scope import resolve_agora_user_scope

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                owner = DecisionJournalOwnerAdapter(stores=build_decision_journal_stores(tmp))
                reader = DomainDecisionJournalReaderPort(data_dir=tmp)
                service = AgoraService(get_read_store=lambda: reader, journal_write_owner=owner)

                alice_identity = OperatorIdentity(
                    operator_id="alice",
                    roles=["operator"],
                    mfa_verified=True,
                    claims={"tid": "tenant-a", "sub": "alice"},
                )
                bob_identity = OperatorIdentity(
                    operator_id="bob",
                    roles=["operator"],
                    mfa_verified=True,
                    claims={"tid": "tenant-b", "sub": "bob"},
                )
                mallory_identity = OperatorIdentity(
                    operator_id="mallory",
                    roles=["operator"],
                    mfa_verified=True,
                    claims={"tid": "tenant-a", "sub": "mallory"},
                )

                alice_scope = resolve_agora_user_scope(alice_identity, utc_now=lambda: "2026-09-08T00:00:00Z")
                bob_scope = resolve_agora_user_scope(bob_identity, utc_now=lambda: "2026-09-08T00:00:00Z")
                mallory_scope = resolve_agora_user_scope(mallory_identity, utc_now=lambda: "2026-09-08T00:00:00Z")

                service.create_journal_entry(
                    payload={"id": "entry-alice", "title": "Alice Private", "body": "Secret", "visibility": "private"},
                    identity=alice_identity,
                    idempotency_key="create-alice",
                    x_idempotency_key=None,
                    tenant_id=alice_scope.tenant_id,
                    user_id=alice_scope.user_id,
                )

                # Cross-tenant (Bob in tenant-b cannot see alice's entry)
                bob_list = service.list_journal_entries(
                    identity=bob_identity,
                    tenant_id=bob_scope.tenant_id,
                    user_id=bob_scope.user_id,
                )
                self.assertEqual(bob_list["data"], [])

                with self.assertRaises(HTTPException) as ctx:
                    service.patch_journal_entry(
                        entry_id="entry-alice",
                        patch={"title": "Hacked by Bob"},
                        identity=bob_identity,
                        resolved_key="patch-bob",
                        tenant_id=bob_scope.tenant_id,
                        user_id=bob_scope.user_id,
                    )
                self.assertIn(ctx.exception.status_code, (403, 404))

                # Same tenant, different user (Mallory in tenant-a cannot see or patch Alice's private entry)
                mallory_list = service.list_journal_entries(
                    identity=mallory_identity,
                    tenant_id=mallory_scope.tenant_id,
                    user_id=mallory_scope.user_id,
                )
                self.assertEqual(mallory_list["data"], [])

                with self.assertRaises(HTTPException) as ctx:
                    service.patch_journal_entry(
                        entry_id="entry-alice",
                        patch={"title": "Hacked by Mallory"},
                        identity=mallory_identity,
                        resolved_key="patch-mallory",
                        tenant_id=mallory_scope.tenant_id,
                        user_id=mallory_scope.user_id,
                    )
                self.assertIn(ctx.exception.status_code, (403, 404))

    def test_main_bff_journal_context_ref_resolution_parity(self) -> None:
        from unittest.mock import patch
        from services.control_plane.bff.agora.identity.scope import resolve_agora_user_scope
        from services.control_plane.bff.main import _resolve_agora_interaction_context_ref

        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {}, clear=True):
                stores = build_decision_journal_stores(tmp)
                reader = DomainDecisionJournalReaderPort(data_dir=tmp)

                identity = OperatorIdentity(
                    operator_id="alice",
                    roles=["operator"],
                    mfa_verified=True,
                    claims={"tid": "tenant-a", "sub": "alice"},
                )
                scope = resolve_agora_user_scope(identity, utc_now=lambda: "2026-09-08T00:00:00Z")

                create_entry(
                    stores,
                    entry_id="ctx-ref-1",
                    title="Context Ref Entry",
                    body="Context body",
                    actor_id="alice",
                    tenant_id="tenant-a",
                    user_id="alice",
                    created_at="2026-09-08T00:00:00Z",
                )

                with patch("services.control_plane.bff.main.read_store", reader):
                    with patch("services.control_plane.bff.main._extract_identity", return_value=identity):
                        ref_res = _resolve_agora_interaction_context_ref(
                            kind="journal_entry",
                            ref_id="ctx-ref-1",
                            ref_version=None,
                            resolved=scope,
                            session={"workshop_id": "ws-1"},
                            context_refs=[],
                            authorization="Bearer token",
                            source_route="/agora/workshop",
                            focused_object={"kind": "other", "id": "other-1"},
                        )
                        self.assertIsNotNone(ref_res["row"])
                        self.assertEqual(ref_res["row"]["id"], "ctx-ref-1")


if __name__ == "__main__":
    unittest.main()



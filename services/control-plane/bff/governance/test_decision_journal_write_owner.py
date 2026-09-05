"""JOURNAL-OWNER-001: convergence tests for the Decision Journal owner adapter.

Proves the acceptance criteria from SD §5.3 for the narrow slice this task
owns: one durable write owner backs both the adapter used directly and the
Agora BFF route handlers, a fresh adapter pointed at the same data directory
sees identical state after a simulated restart, and the Agora service fails
closed instead of fabricating an unpersisted "success" when the canonical
owner adapter is missing.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import HTTPException

from services.control_plane.bff.agora.service import AgoraService
from services.control_plane.bff.governance.decision_journal_write_owner import (
    DecisionJournalOwnerAdapter,
    build_decision_journal_owner_adapter,
    wrap_get_read_store_with_decision_journal_owner,
)
from services.control_plane.bff.models import OperatorIdentity


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

    def test_proxies_unrelated_reads_to_inner_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inner = _BareInnerReadStore()
            adapter = build_decision_journal_owner_adapter(inner, data_dir=tmp)
            self.assertEqual(adapter.some_unrelated_read(), "unrelated")

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


if __name__ == "__main__":
    unittest.main()

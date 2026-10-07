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
from services.control_plane.bff.tests.bff_compose_stand_ins import resolve_with_stand_ins


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
            with self.assertRaisesRegex(ValueError, "retired"):
                resolve_decision_journal_data_dir()

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

    def test_postgres_backend_propagation_builds_postgres_store(self) -> None:
        """P1 AC4/AC6: When DATABASE_URL is set, build_decision_journal_stores must construct
        PostgresGovernanceRecordStore (not JsonGovernanceRecordStore) for every store.

        Reproduces the reviewer defect: resolver said 'postgres' but actual entries store
        was JsonGovernanceRecordStore because build_governance_record_store independently
        re-read GOVERNANCE_STORE_BACKEND and defaulted to json.
        """
        from services.governance.record_store import PostgresGovernanceRecordStore
        import unittest.mock as mock

        fake_dsn = "postgresql://user:pass@localhost:1/pantheon"
        # Patch PostgresGovernanceRecordStore.__init__ to avoid real DB connection.
        with patch(
            "services.governance.decision_journal.PostgresGovernanceRecordStore",
            autospec=True,
        ) as MockPGStore:
            MockPGStore.return_value = mock.MagicMock()
            with patch.dict(
                os.environ,
                {
                    "GOVERNANCE_STORE_BACKEND": "postgres",
                    "GOVERNANCE_STORE_DSN": fake_dsn,
                    "GOVERNANCE_STORE_BOOTSTRAP": "0",
                },
                clear=True,
            ):
                with tempfile.TemporaryDirectory() as tmp:
                    build_decision_journal_stores(tmp)
            # Every store (entries, idempotency, audit) must have used the Postgres path.
            # With outbox suppressed by GOVERNANCE_STORE_BACKEND=postgres and no
            # PANTHEON_DECISION_JOURNAL_OUTBOX, we expect exactly 3 calls.
            self.assertGreaterEqual(MockPGStore.call_count, 3, msg=(
                "Expected PostgresGovernanceRecordStore to be constructed for entries, "
                "idempotency, and audit; got %d call(s). "
                "This proves the backend was propagated through _build_journal_record_store "
                "rather than silently re-defaulting to json." % MockPGStore.call_count
            ))
            # Verify the DSN was passed, not read again independently.
            for call in MockPGStore.call_args_list:
                self.assertEqual(call.kwargs.get("dsn"), fake_dsn)

    def test_bff_factory_wires_injected_owner_into_agora_service(self) -> None:
        """P1 AC4/AC5: compose_bff_app with injected decision_journal_write_owner must wire
        that exact owner into the agora_service, not fall back to a new default.

        Reproduces the reviewer defect: compose_bff_app(app_deps=AppDependencies.create_default(
        decision_journal_write_owner=injected)) returned selected owner injected=False and
        selected path default-journal instead of injected-journal.
        """
        from services.control_plane.bff.core.app_factory import compose_bff_app

        with tempfile.TemporaryDirectory() as sentinel_dir:
            # Build a concrete adapter on a sentinel directory so we can verify
            # exact identity of the injected owner in agora_service.
            sentinel = build_decision_journal_write_owner(data_dir=sentinel_dir)

            with tempfile.TemporaryDirectory() as tmp:
                with patch.dict(
                    os.environ,
                    {"PANTHEON_DECISION_JOURNAL_DATA_DIR": tmp},
                    clear=False,
                ):
                    deps = AppDependencies.create_default(
                        decision_journal_write_owner=sentinel
                    )
                    app = compose_bff_app(dependency_resolver=resolve_with_stand_ins, app_deps=deps)

        self.assertIs(app.state.decision_journal_write_owner, sentinel)
        # The agora_service must expose the sentinel owner, not a freshly built default.
        agora_router = getattr(app.state, "agora_router", None)
        self.assertIsNotNone(agora_router, msg="agora_router not attached to app.state")
        agora_service = getattr(agora_router, "agora_service", None)
        self.assertIsNotNone(agora_service, msg="agora_service not attached to agora_router")
        get_jwo = getattr(agora_service, "_get_journal_write_owner", None)
        selected_owner = get_jwo() if callable(get_jwo) else None
        self.assertIs(
            selected_owner,
            sentinel,
            msg=(
                "Selected owner is NOT the injected sentinel. "
                "This means decision_journal_write_owner was not propagated from "
                "app_deps through create_agora_router into agora_service. "
                "has agora_service=%s, selected owner identity=%r"
                % (agora_service is not None, selected_owner)
            ),
        )

    def test_postgres_storage_health_false_for_unreachable_db(self) -> None:
        """P2 AC5: is_storage_healthy must return False for an unreachable Postgres store.

        Reproduces the reviewer defect: is_storage_healthy returned True even when
        GOVERNANCE_STORE_BOOTSTRAP=0 and list_all() raised OperationalError on
        localhost:1 (connect_timeout=1).
        """
        from unittest.mock import MagicMock

        # Build a mock entries store that has no storage_path (postgres posture)
        # and raises on list_all() to simulate unreachable DB.
        mock_entries = MagicMock()
        del mock_entries.storage_path  # no storage_path attribute = postgres-like
        mock_entries.read_only = False
        mock_entries.list_all.side_effect = Exception("connection refused: localhost:1")

        mock_stores = MagicMock(spec=DecisionJournalStores)
        mock_stores.entries = mock_entries

        adapter = DecisionJournalOwnerAdapter(stores=mock_stores)
        self.assertFalse(
            adapter.is_storage_healthy,
            msg=(
                "is_storage_healthy must return False when list_all() raises "
                "(simulating unreachable Postgres with GOVERNANCE_STORE_BOOTSTRAP=0)."
            ),
        )

    def test_factory_unavailable_owner_returns_503_without_replacement(self):
        from fastapi.testclient import TestClient
        from services.control_plane.bff.core.app_factory import compose_bff_app
        from services.control_plane.bff.models import OperatorIdentity
        with tempfile.TemporaryDirectory() as tmp:
            owner = build_decision_journal_write_owner(data_dir=tmp)
            owner.stores.entries.read_only = True
            deps = AppDependencies.create_default(decision_journal_write_owner=owner)
            with patch("services.control_plane.bff.agora.router.build_decision_journal_write_owner",
                       side_effect=AssertionError("must not replace selected owner")):
                app = compose_bff_app(dependency_resolver=resolve_with_stand_ins, app_deps=deps, _extract_identity=lambda *a, **k: OperatorIdentity(
                    operator_id="paper-reviewer", roles=["operator"], claims={"tenant_id": "tenant-a"}))
            with TestClient(app) as client:
                response = client.post("/bff/agora/journal", json={"title": "Paper"},
                                       headers={"Idempotency-Key": "paper"})
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["error"]["code"], "DEPENDENCY_UNAVAILABLE")
                self.assertIs(app.state.decision_journal_write_owner, owner)
                self.assertEqual(owner.stores.idempotency.list_all(), [])

    def test_packaged_json_override_cannot_create_second_authority(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {
            "GOVERNANCE_STORE_BACKEND": "json", "AGORA_GOVERNANCE_STORE_BACKEND": "json",
            "PANTHEON_DECISION_JOURNAL_REQUIRED_BACKEND": "postgres",
        }, clear=True):
            with self.assertRaisesRegex(ValueError, "shared Postgres authority"):
                build_decision_journal_stores(tmp)
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_legacy_dsn_alone_is_rejected_without_local_fallback(self):
        with patch.dict(os.environ, {"AGORA_GOVERNANCE_STORE_DSN": "postgresql://unused"}, clear=True):
            with self.assertRaisesRegex(ValueError, "configure GOVERNANCE_STORE_DSN"):
                build_decision_journal_stores("/unused")

    def test_inferred_postgres_constructs_real_store_objects(self):
        from services.governance.record_store import PostgresGovernanceRecordStore
        for key in ("DATABASE_URL", "GOVERNANCE_STORE_DSN"):
            with self.subTest(key=key), patch.dict(os.environ, {
                key: "postgresql://unused", "GOVERNANCE_STORE_BOOTSTRAP": "0",
            }, clear=True), tempfile.TemporaryDirectory() as tmp:
                stores = build_decision_journal_stores(tmp)
                for store in (stores.entries, stores.audit, stores.idempotency):
                    self.assertIsInstance(store, PostgresGovernanceRecordStore)
                    self.assertEqual(store._records.dsn, "postgresql://unused")
                self.assertEqual(list(Path(tmp).iterdir()), [])


def paper_factory_probe(phase: str) -> None:
    """Run in separate processes/containers against only disposable local storage.

    Identity is injected at the documented composition seam, not by changing
    production authentication. All journal owners, read ports and handlers are
    the real default factory objects; no test journal owner is injected.
    """
    import errno
    import json
    from fastapi.testclient import TestClient
    # Production resolves composition dependencies from the loaded main module;
    # importing it (not a stand-in resolver) makes a missing dependency fail the probe.
    import services.control_plane.bff.main  # noqa: F401
    from services.control_plane.bff.core.app_factory import compose_bff_app
    from services.control_plane.bff.models import OperatorIdentity
    from services.governance.record_store import PostgresGovernanceRecordStore

    def identity(authorization=None, **kwargs):
        tenant = "tenant-b" if authorization == "Bearer tenant-b" else "tenant-a"
        return OperatorIdentity(
            operator_id="paper-reviewer", roles=["operator"],
            claims={"tenant_id": tenant, "allowed_tenants": [tenant]},
        )

    deps = AppDependencies.create_default()
    owner = deps.decision_journal_write_owner
    app = compose_bff_app(app_deps=deps, _extract_identity=identity)
    assert app.state.decision_journal_write_owner is owner
    if phase != "unavailable":
        assert app.state.agora_router.agora_service.journal_write_owner is owner
    assert isinstance(owner.stores.entries, PostgresGovernanceRecordStore)
    # The actual read-only consumer mount remains unwritable, even while the
    # distinct Postgres authority accepts writes. Do not substitute chmod.
    if os.getenv("JOURNAL_REQUIRE_RO_MOUNT") == "1":
        try:
            Path("/data/governance/forbidden-write").write_text("must not write")
        except OSError as exc:
            assert exc.errno == errno.EROFS, exc
        else:
            raise AssertionError("governance consumer mount is not read-only")

    payload = {"title": "Paper runtime contract", "body": "No live execution", "visibility": "private"}
    headers = {"Idempotency-Key": "paper-runtime-create"}
    with TestClient(app, raise_server_exceptions=False) as client:
        if phase == "unavailable":
            assert not owner.is_storage_healthy
            response = client.post("/bff/agora/journal", json=payload, headers=headers)
            assert response.status_code == 503, response.text
            assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE", response.text
            assert not list(Path(os.environ["PANTHEON_DECISION_JOURNAL_DATA_DIR"]).glob("*.json"))
            print(json.dumps({"phase": phase, "status": response.status_code, "no_json_fallback": True}))
            return

        assert owner.is_storage_healthy
        response = client.post("/bff/agora/journal", json=payload, headers=headers)
        assert response.status_code == 201, response.text
        created = response.json()["data"]
        entry_id = created["id"]
        listed = client.get("/bff/agora/journal")
        assert listed.status_code == 200, listed.text
        assert any(row["id"] == entry_id for row in listed.json()["data"]), listed.text
        if phase == "restart":
            assert response.json()["meta"]["idempotency"]["replayed"] is True
            conflict = client.post("/bff/agora/journal", json={**payload, "body": "changed"}, headers=headers)
            assert conflict.status_code == 409, conflict.text
            other = client.get("/bff/agora/journal", headers={"Authorization": "Bearer tenant-b"})
            assert other.status_code == 200 and other.json()["data"] == [], other.text
            denied = client.patch(f"/bff/agora/journal/{entry_id}", json={"title": "cross-tenant"},
                                  headers={"Authorization": "Bearer tenant-b", "Idempotency-Key": "paper-patch", "Content-Type": "application/merge-patch+json"})
            assert denied.status_code in (403, 404), denied.text
            patched = client.patch(f"/bff/agora/journal/{entry_id}", json={"title": "Paper reviewed"},
                                   headers={"Idempotency-Key": "paper-patch", "Content-Type": "application/merge-patch+json"})
            assert patched.status_code == 200, patched.text
            assert patched.json()["data"]["version"] == 2, patched.text
            replay = client.patch(f"/bff/agora/journal/{entry_id}", json={"title": "Paper reviewed"},
                                  headers={"Idempotency-Key": "paper-patch", "Content-Type": "application/merge-patch+json"})
            assert replay.status_code == 200 and replay.json()["meta"]["idempotency"]["replayed"], replay.text
            # The selected durable authority must reject a stale CAS from an
            # independent store instance, without replacing the committed row.
            second = build_decision_journal_write_owner()
            current = second.stores.entries.get(entry_id)
            stale = {**current, "version": 1}
            accepted, canonical = second.stores.entries.compare_and_set(stale, {**current, "title": "stale"})
            assert not accepted and canonical == current
            # Same raw key in another tenant has independent durable identity.
            other_create = client.post("/bff/agora/journal", json=payload,
                                       headers={**headers, "Authorization": "Bearer tenant-b"})
            assert other_create.status_code == 201, other_create.text
            assert other_create.json()["data"]["id"] != entry_id
        elif phase == "reread":
            row = next(row for row in listed.json()["data"] if row["id"] == entry_id)
            assert row["title"] == "Paper reviewed" and row["version"] == 2
        else:
            assert phase == "create"
            assert created["version"] == 1
        print(json.dumps({"phase": phase, "entry_id": entry_id, "backend": "postgres", "passed": True}))


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--paper-probe":
        paper_factory_probe(sys.argv[2])
    else:
        unittest.main()



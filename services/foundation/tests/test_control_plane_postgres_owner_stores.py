from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return self._rows[0] if self._rows else None


class _FakeConnection:
    rows: dict[str, dict[str, dict]] = {}
    statements: list[str] = []
    rollback_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        table = self._table_name(sql)
        if normalized.startswith("CREATE"):
            return _FakeCursor([])
        if normalized.startswith("INSERT INTO"):
            payload = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            table_rows = self.rows.setdefault(table, {})
            record_id = str(params[0])
            if "DO NOTHING" in normalized and record_id in table_rows:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)] if "RETURNING PAYLOAD" in normalized else [])
        if normalized.startswith("UPDATE"):
            payload = json.loads(params[0]) if isinstance(params[0], str) else params[0]
            record_id = str(params[1])
            expected = json.loads(params[2]) if isinstance(params[2], str) else params[2]
            table_rows = self.rows.setdefault(table, {})
            if table_rows.get(record_id) != expected:
                return _FakeCursor([])
            table_rows[record_id] = payload
            return _FakeCursor([(payload,)])
        if normalized.startswith("DELETE FROM"):
            record_id = str(params[0])
            expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
            table_rows = self.rows.setdefault(table, {})
            if table_rows.get(record_id) != expected:
                return _FakeCursor([])
            removed = table_rows.pop(record_id)
            return _FakeCursor([(removed,)])
        if normalized.startswith("SELECT PAYLOAD") and "PAYLOAD ->> 'INCIDENT_ID'" in normalized:
            incident_id = str(params[0])
            matching = [
                payload
                for payload in self.rows.get(table, {}).values()
                if str(payload.get("incident_id") or "") == incident_id
            ]
            return _FakeCursor([(matching[0],)] if matching else [])
        if normalized.startswith("SELECT PAYLOAD") and "WHERE RECORD_ID" in normalized:
            record = self.rows.get(table, {}).get(str(params[0]))
            if "AND PAYLOAD" in normalized and record is not None:
                expected = json.loads(params[1]) if isinstance(params[1], str) else params[1]
                if record != expected:
                    record = None
            return _FakeCursor([(record,)] if record is not None else [])
        if normalized.startswith("SELECT PAYLOAD"):
            return _FakeCursor([(payload,) for payload in self.rows.get(table, {}).values()])
        return _FakeCursor([])

    def rollback(self):
        type(self).rollback_count += 1

    @staticmethod
    def _table_name(sql: str) -> str:
        match = re.search(
            r"(?:FROM|INTO|UPDATE)\s+((?:\"[^\"]+\"\.)?\"[^\"]+\")",
            sql,
            re.IGNORECASE,
        )
        return match.group(1) if match else "<unknown>"


def _fake_psycopg():
    conn = _FakeConnection()
    return SimpleNamespace(connect=lambda dsn: conn)


@pytest.fixture(autouse=True)
def _reset_fake_connection():
    _FakeConnection.rows = {}
    _FakeConnection.statements = []
    _FakeConnection.rollback_count = 0


class _PermissionDenied(Exception):
    sqlstate = "42501"


class _SchemaPermissionConnection:
    def __init__(self, *, schema_exists: bool) -> None:
        self.schema_exists = schema_exists
        self.rollback_count = 0
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        normalized = " ".join(sql.split()).upper()
        if normalized.startswith("CREATE SCHEMA"):
            raise _PermissionDenied("permission denied for database")
        if "INFORMATION_SCHEMA.SCHEMATA" in normalized:
            return _FakeCursor([(1,)] if self.schema_exists else [])
        return _FakeCursor([])

    def rollback(self):
        self.rollback_count += 1


def _load_reconciliation_store_module():
    path = Path(__file__).resolve().parents[2] / "reconciliation-drift" / "store.py"
    spec = importlib.util.spec_from_file_location("reconciliation_drift_store_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_postgres_json_owner_store_read_only_boundary():
    from services.foundation.postgres_json_store import PostgresJsonOwnerStore

    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        owner = PostgresJsonOwnerStore(
            dsn="postgresql://owner@example/db",
            table="governance.approval_decisions",
            owner_service="governance-svc",
        )
        owner.put("apv-001", {"decision_id": "apv-001"})

        reader = PostgresJsonOwnerStore(
            dsn="postgresql://reader@example/db",
            table="governance.approval_decisions",
            owner_service="governance-svc",
            bootstrap=False,
            read_only=True,
        )

        assert reader.get("apv-001") == {"decision_id": "apv-001"}
        with pytest.raises(PermissionError, match="writes must go through governance-svc"):
            reader.put("apv-002", {"decision_id": "apv-002"})
        with pytest.raises(PermissionError, match="writes must go through governance-svc"):
            reader.insert_if_absent("apv-002", {"decision_id": "apv-002"})
        with pytest.raises(PermissionError, match="writes must go through governance-svc"):
            reader.compare_and_set("apv-001", None, {"decision_id": "apv-001"})
        with pytest.raises(PermissionError, match="writes must go through governance-svc"):
            reader.delete_if_matches("apv-001", {"decision_id": "apv-001"})


def test_postgres_json_owner_store_put_upsert():
    from services.foundation.postgres_json_store import PostgresJsonOwnerStore

    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        owner = PostgresJsonOwnerStore(
            dsn="postgresql://owner@example/db",
            table="governance.approval_decisions",
            owner_service="governance-svc",
        )
        owner.put("apv-001", {"decision_id": "apv-001", "status": "draft"})
        assert owner.get("apv-001") == {"decision_id": "apv-001", "status": "draft"}

        owner.put("apv-001", {"decision_id": "apv-001", "status": "approved"})
        assert owner.get("apv-001") == {"decision_id": "apv-001", "status": "approved"}

        upsert_stmt = [s for s in _FakeConnection.statements if "ON CONFLICT" in s]
        assert len(upsert_stmt) > 0


def test_postgres_json_owner_store_compare_and_set_is_row_scoped():
    from services.foundation.postgres_json_store import PostgresJsonOwnerStore

    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        owner = PostgresJsonOwnerStore(
            dsn="postgresql://owner@example/db",
            table="incident.incident_cases",
            owner_service="incident-svc",
        )
        created, canonical = owner.compare_and_set(
            "inc-1",
            None,
            {"incident_id": "inc-1", "status": "open"},
        )
        stale_create, observed = owner.compare_and_set(
            "inc-1",
            None,
            {"incident_id": "inc-1", "status": "investigating"},
        )
        advanced, canonical = owner.compare_and_set(
            "inc-1",
            {"incident_id": "inc-1", "status": "open"},
            {"incident_id": "inc-1", "status": "resolved"},
        )
        stale_update, observed_after = owner.compare_and_set(
            "inc-1",
            {"incident_id": "inc-1", "status": "open"},
            {"incident_id": "inc-1", "status": "investigating"},
        )

        assert created is True
        assert stale_create is False
        assert advanced is True
        assert stale_update is False
        assert observed == {"incident_id": "inc-1", "status": "open"}
        assert canonical == observed_after == {
            "incident_id": "inc-1",
            "status": "resolved",
        }
        assert owner.delete_if_matches(
            "inc-1",
            {"incident_id": "inc-1", "status": "open"},
        ) is False
        assert owner.delete_if_matches(
            "inc-1",
            {"incident_id": "inc-1", "status": "resolved"},
        ) is True

    statements = " ".join(_FakeConnection.statements).upper()
    assert "WHERE RECORD_ID = %S AND PAYLOAD = %S::JSONB" in statements
    assert "RETURNING PAYLOAD" in statements


def test_postgres_json_owner_store_atomically_reserves_composite_identity():
    from services.foundation.postgres_json_store import PostgresJsonOwnerStore

    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        owner = PostgresJsonOwnerStore(
            dsn="postgresql://owner@example/db",
            table="incident.delivery_inbox",
            owner_service="postmortem-svc",
        )

        inserted, canonical = owner.insert_if_absent(
            "evt-1",
            {"idempotency_key": "shared", "value": "first"},
            unique_fields=("idempotency_key",),
        )
        duplicate_inserted, duplicate = owner.insert_if_absent(
            "evt-1",
            {"idempotency_key": "shared", "value": "second"},
            unique_fields=("idempotency_key",),
        )
        collision_inserted, collision = owner.insert_if_absent(
            "evt-2",
            {"idempotency_key": "shared", "value": "third"},
            unique_fields=("idempotency_key",),
        )

    assert inserted is True
    assert duplicate_inserted is False
    assert collision_inserted is False
    assert canonical == duplicate == collision == {
        "idempotency_key": "shared",
        "value": "first",
    }
    assert len(_FakeConnection.rows['"incident"."delivery_inbox"']) == 1
    assert any("LOCK TABLE" in statement for statement in _FakeConnection.statements)


def test_postgres_incident_store_persists_one_row_and_rejects_stale_snapshot():
    from services.incident.incident import IncidentCase, IncidentConcurrencyError
    from services.incident.pg_store import PostgresIncidentStore

    def incident(incident_id: str) -> IncidentCase:
        return IncidentCase(
            incident_id=incident_id,
            title=f"Incident {incident_id}",
            status="open",
            severity="high",
            created_at="2026-07-15T00:00:00Z",
            binding_id=f"binding-{incident_id}",
            deployment_stage="paper",
            deployment_plan_id=f"plan-{incident_id}",
            capital_pool_id="pool-1",
            persona_capital_binding_id=f"pcb-{incident_id}",
            artifact_id=f"artifact-{incident_id}",
            artifact_version="1.0.0",
            runtime_id=f"runtime-{incident_id}",
            trace_id=f"trace-{incident_id}",
        )

    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store = PostgresIncidentStore(dsn="postgresql://owner@example/db")
        store.create_incident(incident("inc-1"))
        store.create_incident(incident("inc-2"))
        _FakeConnection.statements.clear()

        previous = store.require_incident("inc-1")
        updated = store.update_incident_status(
            "inc-1",
            "investigating",
            expected_snapshot=previous.to_dict(),
        )
        assert updated.status == "investigating"
        assert store.require_incident("inc-2").status == "open"
        writes = [
            " ".join(statement.split()).upper()
            for statement in _FakeConnection.statements
            if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        assert len(writes) == 1
        assert 'UPDATE "INCIDENT"."INCIDENT_CASES"' in writes[0]

        expected = updated.to_dict()
        real_save = store._save
        table = '"incident"."incident_cases"'

        def concurrent_close(**kwargs):
            record_id = kwargs["record_id"]
            current = dict(_FakeConnection.rows[table][record_id])
            _FakeConnection.rows[table][record_id] = {
                **current,
                "status": "closed",
                "resolved_at": "2026-07-15T00:01:00Z",
            }
            return real_save(**kwargs)

        with mock.patch.object(store, "_save", side_effect=concurrent_close):
            with pytest.raises(IncidentConcurrencyError, match="changed concurrently"):
                store.update_incident_status(
                    "inc-1",
                    "resolved",
                    expected_snapshot=expected,
                )

        assert store.require_incident("inc-1").status == "closed"
        assert store.require_incident("inc-1").resolved_at == "2026-07-15T00:01:00Z"


def test_postgres_incident_store_explicit_target_prevents_cross_row_aba_corruption():
    from services.incident.incident import IncidentCase
    from services.incident.pg_store import PostgresIncidentStore

    def incident(incident_id: str) -> IncidentCase:
        return IncidentCase(
            incident_id=incident_id,
            title=f"Incident {incident_id}",
            status="open",
            severity="high",
            created_at="2026-07-15T00:00:00Z",
            binding_id=f"binding-{incident_id}",
            deployment_stage="paper",
            deployment_plan_id=f"plan-{incident_id}",
            capital_pool_id="pool-1",
            persona_capital_binding_id=f"pcb-{incident_id}",
            artifact_id=f"artifact-{incident_id}",
            artifact_version="1.0.0",
            runtime_id=f"runtime-{incident_id}",
            trace_id=f"trace-{incident_id}",
        )

    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store = PostgresIncidentStore(dsn="postgresql://owner@example/db")
        store.create_incident(incident("inc-1"))
        store.create_incident(incident("inc-2"))
        table = '"incident"."incident_cases"'
        real_require = store.require_incident

        def cross_row_aba(incident_id: str):
            second = dict(_FakeConnection.rows[table]["inc-2"])
            _FakeConnection.rows[table]["inc-2"] = {
                **second,
                "status": "investigating",
            }
            target = real_require(incident_id)
            _FakeConnection.rows[table]["inc-2"] = second
            return target

        with mock.patch.object(store, "require_incident", side_effect=cross_row_aba):
            updated = store.update_incident_status("inc-1", "investigating")

        assert updated.status == "investigating"
        assert _FakeConnection.rows[table]["inc-1"]["status"] == "investigating"
        assert _FakeConnection.rows[table]["inc-2"]["status"] == "open"


def test_postgres_incident_store_reads_share_the_guarded_write_lock():
    from services.incident.incident import IncidentCase
    from services.incident.pg_store import PostgresIncidentStore

    incident = IncidentCase(
        incident_id="inc-1",
        title="Incident inc-1",
        status="open",
        severity="high",
        created_at="2026-07-15T00:00:00Z",
        binding_id="binding-1",
        deployment_stage="paper",
        deployment_plan_id="plan-1",
        capital_pool_id="pool-1",
        persona_capital_binding_id="pcb-1",
        artifact_id="artifact-1",
        artifact_version="1.0.0",
        runtime_id="runtime-1",
        trace_id="trace-1",
    )
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store = PostgresIncidentStore(dsn="postgresql://owner@example/db")
        store.create_incident(incident)
        real_save = store._save
        reader_started = threading.Event()
        reader_finished = threading.Event()
        reader_threads: list[threading.Thread] = []
        reader_results = []

        def read_during_save():
            reader_started.set()
            reader_results.append(store.require_incident("inc-1"))
            reader_finished.set()

        def save_with_reader(**kwargs):
            reader = threading.Thread(target=read_during_save)
            reader_threads.append(reader)
            reader.start()
            assert reader_started.wait(timeout=2)
            assert reader_finished.wait(timeout=0.05) is False
            return real_save(**kwargs)

        with mock.patch.object(store, "_save", side_effect=save_with_reader):
            updated = store.update_incident_status("inc-1", "investigating")

        for reader in reader_threads:
            reader.join(timeout=2)
        assert reader_finished.is_set()
        assert updated.status == "investigating"
        assert reader_results[0].status == "investigating"


def test_postgres_postmortem_cas_locks_and_checks_parent_snapshot():
    from services.incident.incident import (
        IncidentCase,
        IncidentConcurrencyError,
        Postmortem,
    )
    from services.incident.pg_store import PostgresIncidentStore

    incident = IncidentCase(
        incident_id="inc-1",
        title="Incident",
        status="resolved",
        severity="high",
        created_at="2026-07-15T00:00:00Z",
        binding_id="binding-1",
        deployment_stage="paper",
        deployment_plan_id="plan-1",
        capital_pool_id="pool-1",
        persona_capital_binding_id="pcb-1",
        artifact_id="artifact-1",
        artifact_version="1.0.0",
        runtime_id="runtime-1",
        trace_id="trace-1",
        resolved_at="2026-07-15T00:01:00Z",
    )
    postmortem = Postmortem(
        postmortem_id="pm-1",
        title="Postmortem",
        status="draft",
        created_at="2026-07-15T00:02:00Z",
        incident_id="inc-1",
        binding_id="binding-1",
        deployment_stage="paper",
        deployment_plan_id="plan-1",
        capital_pool_id="pool-1",
        persona_capital_binding_id="pcb-1",
        artifact_id="artifact-1",
        artifact_version="1.0.0",
        runtime_id="runtime-1",
        trace_id="trace-1",
        root_cause="pending",
    )
    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store = PostgresIncidentStore(dsn="postgresql://owner@example/db")
        store.create_incident(incident)
        store.create_postmortem(postmortem)
        incident_table = '"incident"."incident_cases"'
        real_save = store._save

        def parent_changes_before_transaction(**kwargs):
            current = dict(_FakeConnection.rows[incident_table]["inc-1"])
            _FakeConnection.rows[incident_table]["inc-1"] = {
                **current,
                "evidence_summary": "concurrent evidence",
            }
            return real_save(**kwargs)

        with mock.patch.object(
            store,
            "_save",
            side_effect=parent_changes_before_transaction,
        ):
            with pytest.raises(
                IncidentConcurrencyError,
                match="IncidentCase changed concurrently",
            ):
                store.update_postmortem_status(
                    "pm-1",
                    "published",
                    published_event_id="evt-1",
                    expected_snapshot=postmortem.to_dict(),
                    expected_incident_snapshot=incident.to_dict(),
                )

        assert store.require_postmortem("pm-1").status == "draft"
        assert store.require_incident("inc-1").evidence_summary == "concurrent evidence"
        assert any("FOR SHARE" in statement.upper() for statement in _FakeConnection.statements)


def test_postgres_postmortem_create_race_is_retryable_and_one_per_incident():
    from services.incident.incident import (
        IncidentCase,
        IncidentConcurrencyError,
        Postmortem,
    )
    from services.incident.pg_store import PostgresIncidentStore

    incident = IncidentCase(
        incident_id="inc-1",
        title="Incident",
        status="resolved",
        severity="high",
        created_at="2026-07-15T00:00:00Z",
        binding_id="binding-1",
        deployment_stage="paper",
        deployment_plan_id="plan-1",
        capital_pool_id="pool-1",
        persona_capital_binding_id="pcb-1",
        artifact_id="artifact-1",
        artifact_version="1.0.0",
        runtime_id="runtime-1",
        trace_id="trace-1",
        resolved_at="2026-07-15T00:01:00Z",
    )

    def postmortem(postmortem_id: str) -> Postmortem:
        return Postmortem(
            postmortem_id=postmortem_id,
            title="Postmortem",
            status="draft",
            created_at="2026-07-15T00:02:00Z",
            incident_id="inc-1",
            binding_id="binding-1",
            deployment_stage="paper",
            deployment_plan_id="plan-1",
            capital_pool_id="pool-1",
            persona_capital_binding_id="pcb-1",
            artifact_id="artifact-1",
            artifact_version="1.0.0",
            runtime_id="runtime-1",
            trace_id="trace-1",
            root_cause="pending",
        )

    fake_psycopg = _fake_psycopg()
    with mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}):
        store = PostgresIncidentStore(dsn="postgresql://owner@example/db")
        store.create_incident(incident)
        postmortem_table = '"incident"."postmortems"'
        real_save = store._save

        def concurrent_manual_create(**kwargs):
            _FakeConnection.rows.setdefault(postmortem_table, {})["pm-manual"] = postmortem(
                "pm-manual"
            ).to_dict()
            return real_save(**kwargs)

        with mock.patch.object(store, "_save", side_effect=concurrent_manual_create):
            with pytest.raises(
                IncidentConcurrencyError,
                match="already exists for IncidentCase",
            ):
                store.create_postmortem(postmortem("pm-generated"))

        assert list(_FakeConnection.rows[postmortem_table]) == ["pm-manual"]


def test_ensure_postgres_schema_accepts_precreated_schema_for_restricted_role():
    from services.foundation.postgres_json_store import ensure_postgres_schema

    conn = _SchemaPermissionConnection(schema_exists=True)

    ensure_postgres_schema(conn, "management_ai")

    assert conn.rollback_count == 1
    assert any("information_schema.schemata" in statement for statement in conn.statements)


def test_ensure_postgres_schema_reraises_when_schema_is_missing_for_restricted_role():
    from services.foundation.postgres_json_store import ensure_postgres_schema

    conn = _SchemaPermissionConnection(schema_exists=False)

    with pytest.raises(_PermissionDenied):
        ensure_postgres_schema(conn, "management_ai")

    assert conn.rollback_count == 1


def test_wave3_postgres_builders_are_env_gated():
    from services.capital.pg_store import (
        PostgresCapitalAuditStore,
        PostgresCapitalPoolStore,
        PostgresPersonaCapitalBindingStore,
        build_capital_audit_store,
        build_capital_binding_store,
        build_capital_pool_store,
    )
    from services.governance.pg_store import (
        PostgresApprovalDecisionStore,
        PostgresGovernanceAuditStore,
        build_approval_decision_store,
        build_governance_audit_store,
    )
    from services.incident.pg_store import PostgresIncidentStore, build_incident_store
    from services.memory.institutional_memory_store import (
        PostgresInstitutionalMemoryStore,
        build_institutional_memory_store,
    )
    from services.memory.persona_memory_store import (
        PostgresPersonaMemoryStore,
        build_persona_memory_store,
    )
    from services.promotion.pg_store import (
        PostgresDeploymentPlanStore,
        PostgresPromotionApprovalStore,
        PostgresPromotionExtensionStore,
        build_promotion_approval_store,
        build_promotion_deployment_store,
        build_promotion_extension_store,
    )

    reconciliation_store = _load_reconciliation_store_module()
    fake_psycopg = _fake_psycopg()
    data_dir = Path(tempfile.mkdtemp(prefix="wave3_pg_builders_"))

    env = {
        "DATABASE_URL": "postgresql://writer@example/db",
        "GOVERNANCE_STORE_BACKEND": "postgres",
        "GOVERNANCE_AUDIT_BACKEND": "postgres",
        "CAPITAL_STORE_BACKEND": "postgres",
        "CAPITAL_AUDIT_BACKEND": "postgres",
        "INCIDENT_STORE_BACKEND": "postgres",
        "POSTMORTEM_STORE_BACKEND": "postgres",
        "PROMOTION_STORE_BACKEND": "postgres",
        "PANTHEON_MEMORY_STORE_BACKEND": "postgres",
        "RECONCILIATION_DRIFT_STORE_BACKEND": "postgres",
    }

    with (
        mock.patch.dict(sys.modules, {"psycopg": fake_psycopg}),
        mock.patch.dict("os.environ", env, clear=True),
    ):
        assert isinstance(
            build_approval_decision_store(str(data_dir / "approval_decisions.json")),
            PostgresApprovalDecisionStore,
        )
        assert isinstance(
            build_governance_audit_store(str(data_dir / "audit.jsonl")),
            PostgresGovernanceAuditStore,
        )
        assert isinstance(build_capital_pool_store(data_dir / "capital_pools.json"), PostgresCapitalPoolStore)
        assert isinstance(
            build_capital_binding_store(data_dir / "persona_capital_bindings.json"),
            PostgresPersonaCapitalBindingStore,
        )
        assert isinstance(build_capital_audit_store(data_dir / "capital_audit.jsonl"), PostgresCapitalAuditStore)
        assert isinstance(build_incident_store(data_dir / "incidents.json"), PostgresIncidentStore)
        assert isinstance(
            build_promotion_approval_store(data_dir / "approval_decisions.json"),
            PostgresPromotionApprovalStore,
        )
        assert isinstance(
            build_promotion_deployment_store(data_dir / "deployment_plans.json"),
            PostgresDeploymentPlanStore,
        )
        assert isinstance(
            build_promotion_extension_store(data_dir / "deployment_plan_extensions.json"),
            PostgresPromotionExtensionStore,
        )
        assert isinstance(
            build_institutional_memory_store(data_dir / "institutional_memory_entries.json"),
            PostgresInstitutionalMemoryStore,
        )
        assert isinstance(
            build_persona_memory_store(data_dir / "persona_memory_entries.json"),
            PostgresPersonaMemoryStore,
        )
        assert isinstance(
            reconciliation_store.build_reconciliation_drift_store(data_dir / "reconciliation-drift"),
            reconciliation_store.PostgresReconciliationDriftStore,
        )

    ddl = " ".join(_FakeConnection.statements).lower()
    for table in (
        "governance",
        "capital",
        "incident",
        "promotion",
        "memory",
        "reconciliation_drift",
    ):
        assert table in ddl

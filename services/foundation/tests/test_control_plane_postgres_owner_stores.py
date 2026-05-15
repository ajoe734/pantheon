from __future__ import annotations

import importlib.util
import json
import re
import sys
import tempfile
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
            self.rows.setdefault(table, {})[str(params[0])] = payload
            return _FakeCursor([])
        if normalized.startswith("SELECT PAYLOAD") and "WHERE RECORD_ID" in normalized:
            record = self.rows.get(table, {}).get(str(params[0]))
            return _FakeCursor([(record,)] if record is not None else [])
        if normalized.startswith("SELECT PAYLOAD"):
            return _FakeCursor([(payload,) for payload in self.rows.get(table, {}).values()])
        return _FakeCursor([])

    @staticmethod
    def _table_name(sql: str) -> str:
        match = re.search(r"(?:FROM|INTO)\s+((?:\"[^\"]+\"\.)?\"[^\"]+\")", sql, re.IGNORECASE)
        return match.group(1) if match else "<unknown>"


def _fake_psycopg():
    conn = _FakeConnection()
    return SimpleNamespace(connect=lambda dsn: conn)


@pytest.fixture(autouse=True)
def _reset_fake_connection():
    _FakeConnection.rows = {}
    _FakeConnection.statements = []


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

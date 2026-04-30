from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


class ReconciliationDriftStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.evaluations_path = self.data_dir / "drift_evaluations.json"
        self.alerts_path = self.data_dir / "alert_handoffs.json"

    def _read_map(self, path: Path) -> Dict[str, Dict[str, Any]]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _write_map(self, path: Path, payload: Dict[str, Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def _put_record(self, path: Path, record_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        if not record_id:
            raise ValueError("record_id is required")
        records = self._read_map(path)
        records[record_id] = json.loads(json.dumps(record))
        self._write_map(path, records)
        return records[record_id]

    def _list_records(self, path: Path) -> List[Dict[str, Any]]:
        return list(self._read_map(path).values())

    def _get_record(self, path: Path, record_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(path).get(record_id)

    def list_evaluations(self) -> List[Dict[str, Any]]:
        return self._list_records(self.evaluations_path)

    def get_evaluation(self, evaluation_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.evaluations_path, evaluation_id)

    def put_evaluation(self, evaluation: Dict[str, Any]) -> Dict[str, Any]:
        evaluation_id = str(evaluation.get("evaluation_id") or evaluation.get("id") or "").strip()
        evaluation["evaluation_id"] = evaluation_id
        evaluation["id"] = evaluation_id
        return self._put_record(self.evaluations_path, evaluation_id, evaluation)

    def list_alert_handoffs(self) -> List[Dict[str, Any]]:
        return self._list_records(self.alerts_path)

    def get_alert_handoff(self, alert_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.alerts_path, alert_id)

    def put_alert_handoff(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        alert_id = str(alert.get("alert_id") or alert.get("id") or "").strip()
        alert["alert_id"] = alert_id
        alert["id"] = alert_id
        return self._put_record(self.alerts_path, alert_id, alert)


class PostgresReconciliationDriftStore(ReconciliationDriftStore):
    """Postgres owner store for reconciliation drift evaluations and alert handoffs."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        dsn: str,
        evaluations_table: str = "reconciliation_drift.drift_evaluations",
        alerts_table: str = "reconciliation_drift.alert_handoffs",
        bootstrap: bool = True,
    ) -> None:
        super().__init__(data_dir)
        self._evaluation_records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=evaluations_table,
            owner_service="reconciliation-drift-svc",
            bootstrap=bootstrap,
        )
        self._alert_records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=alerts_table,
            owner_service="reconciliation-drift-svc",
            bootstrap=bootstrap,
        )

    def list_evaluations(self) -> List[Dict[str, Any]]:
        return self._evaluation_records.list_all()

    def get_evaluation(self, evaluation_id: str) -> Optional[Dict[str, Any]]:
        return self._evaluation_records.get(evaluation_id)

    def put_evaluation(self, evaluation: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(evaluation))
        evaluation_id = str(record.get("evaluation_id") or record.get("id") or "").strip()
        if not evaluation_id:
            raise ValueError("evaluation_id is required")
        record["evaluation_id"] = evaluation_id
        record["id"] = evaluation_id
        self._evaluation_records.put(evaluation_id, record)
        return record

    def list_alert_handoffs(self) -> List[Dict[str, Any]]:
        return self._alert_records.list_all()

    def get_alert_handoff(self, alert_id: str) -> Optional[Dict[str, Any]]:
        return self._alert_records.get(alert_id)

    def put_alert_handoff(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(alert))
        alert_id = str(record.get("alert_id") or record.get("id") or "").strip()
        if not alert_id:
            raise ValueError("alert_id is required")
        record["alert_id"] = alert_id
        record["id"] = alert_id
        self._alert_records.put(alert_id, record)
        return record


def build_reconciliation_drift_store(data_dir: str | Path) -> ReconciliationDriftStore:
    backend = os.getenv("RECONCILIATION_DRIFT_STORE_BACKEND", "json").strip().lower()
    if backend in ("", "json"):
        return ReconciliationDriftStore(data_dir)
    if backend != "postgres":
        raise ValueError("RECONCILIATION_DRIFT_STORE_BACKEND must be json or postgres")
    dsn = os.getenv("RECONCILIATION_DRIFT_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError(
            "RECONCILIATION_DRIFT_STORE_DSN or DATABASE_URL is required for Postgres reconciliation store"
        )
    bootstrap = os.getenv("RECONCILIATION_DRIFT_STORE_BOOTSTRAP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    return PostgresReconciliationDriftStore(
        data_dir,
        dsn=dsn,
        evaluations_table=os.getenv(
            "RECONCILIATION_DRIFT_EVALUATION_STORE_TABLE",
            "reconciliation_drift.drift_evaluations",
        ),
        alerts_table=os.getenv(
            "RECONCILIATION_DRIFT_ALERT_STORE_TABLE",
            "reconciliation_drift.alert_handoffs",
        ),
        bootstrap=bootstrap,
    )

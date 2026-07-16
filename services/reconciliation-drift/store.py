from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.foundation.postgres_json_store import PostgresJsonOwnerStore


_JSON_WHITESPACE = " \t\r\n"


class ReconciliationStoreError(RuntimeError):
    """Raised when a JSON map file cannot be safely read or written.

    Reads and writes fail closed on this error instead of silently
    treating malformed, truncated, or otherwise unrecoverable source
    bytes as an empty or partial map.
    """


class ReconciliationDriftStore:
    def __init__(self, data_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.evaluations_path = self.data_dir / "drift_evaluations.json"
        self.alerts_path = self.data_dir / "alert_handoffs.json"
        self.reconciliation_records_path = self.data_dir / "reconciliation_records.json"
        self.drift_reports_path = self.data_dir / "drift_reports.json"

    @contextlib.contextmanager
    def _locked(self, path: Path):
        """Hold a cross-process exclusive lock for the full transaction on `path`.

        A fresh file descriptor is opened per call so the lock is scoped to
        this transaction only (flock is per open-file-description), and it
        serializes readers and writers across every process attached to the
        same lock file, not just threads inside one process.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.parent / f".{path.name}.lock"
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def _validate_map(self, path: Path, payload: Any) -> Dict[str, Dict[str, Any]]:
        if not isinstance(payload, dict):
            raise ReconciliationStoreError(
                f"{path}: expected a JSON object at the top level, got {type(payload).__name__}"
            )
        records: Dict[str, Dict[str, Any]] = {}
        for key, value in payload.items():
            if not isinstance(value, dict):
                raise ReconciliationStoreError(
                    f"{path}: record {key!r} must be a JSON object, got {type(value).__name__}"
                )
            records[str(key)] = value
        return records

    @staticmethod
    def _json_decoder(path: Path) -> json.JSONDecoder:
        """Return a strict decoder for durable store documents.

        Python's JSON defaults accept non-standard ``NaN``/``Infinity``
        constants and silently keep the last occurrence of a duplicate
        object key. Both can hide source corruption or discard a record, so
        store documents reject them explicitly. Later *complete* documents
        in a historical concatenated source are still merged in order by
        ``_read_concatenated_maps``.
        """

        def reject_constant(value: str) -> None:
            raise ReconciliationStoreError(
                f"{path}: non-standard JSON constant {value!r} is not allowed"
            )

        def reject_duplicate_keys(pairs):
            payload: Dict[str, Any] = {}
            for key, value in pairs:
                if key in payload:
                    raise ReconciliationStoreError(
                        f"{path}: duplicate JSON object key {key!r} is not allowed"
                    )
                payload[key] = value
            return payload

        return json.JSONDecoder(
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )

    def _read_concatenated_maps(self, path: Path, text: str) -> Dict[str, Dict[str, Any]]:
        """Recover a historical concatenated-map source file.

        Only succeeds when the entire non-whitespace input parses as one or
        more consecutive JSON documents whose values all satisfy the store
        map contract. Any malformed suffix, truncation, or invalid value
        fails closed instead of returning whatever was parsed so far. When
        the same id appears in more than one document, the later document
        wins.
        """
        decoder = self._json_decoder(path)
        documents: List[Any] = []
        offset = 0
        length = len(text)
        while offset < length:
            while offset < length and text[offset] in _JSON_WHITESPACE:
                offset += 1
            if offset >= length:
                break
            try:
                payload, offset = decoder.raw_decode(text, offset)
            except json.JSONDecodeError as exc:
                raise ReconciliationStoreError(
                    f"{path}: malformed JSON at offset {offset}: {exc}"
                ) from exc
            documents.append(payload)
        if not documents:
            raise ReconciliationStoreError(f"{path}: no JSON documents found")
        records: Dict[str, Dict[str, Any]] = {}
        for document in documents:
            records.update(self._validate_map(path, document))
        return records

    def _read_map_locked(self, path: Path) -> Dict[str, Dict[str, Any]]:
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise ReconciliationStoreError(f"{path}: failed to read: {exc}") from exc
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ReconciliationStoreError(f"{path}: not valid UTF-8: {exc}") from exc
        if not text.strip(_JSON_WHITESPACE):
            raise ReconciliationStoreError(f"{path}: no complete JSON object found")
        decoder = self._json_decoder(path)
        try:
            payload = decoder.decode(text)
        except json.JSONDecodeError:
            return self._read_concatenated_maps(path, text)
        return self._validate_map(path, payload)

    def _read_map(self, path: Path) -> Dict[str, Dict[str, Any]]:
        with self._locked(path):
            return self._read_map_locked(path)

    @staticmethod
    def _fsync_dir(dir_path: Path) -> None:
        try:
            fd = os.open(str(dir_path), os.O_RDONLY)
        except OSError:
            return
        try:
            os.fsync(fd)
        except OSError:
            pass
        finally:
            os.close(fd)

    def _write_map_locked(self, path: Path, payload: Dict[str, Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                tmp_name = handle.name
                json.dump(payload, handle, indent=2, ensure_ascii=True, allow_nan=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, path)
            tmp_name = ""
            self._fsync_dir(path.parent)
        finally:
            if tmp_name:
                with contextlib.suppress(FileNotFoundError):
                    os.unlink(tmp_name)

    def _put_record(self, path: Path, record_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        if not record_id:
            raise ValueError("record_id is required")
        serializable = json.loads(json.dumps(record, allow_nan=False))
        with self._locked(path):
            records = self._read_map_locked(path)
            records[record_id] = serializable
            self._write_map_locked(path, records)
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

    def list_reconciliation_records(self) -> List[Dict[str, Any]]:
        return self._list_records(self.reconciliation_records_path)

    def get_reconciliation_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.reconciliation_records_path, record_id)

    def put_reconciliation_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record_id = str(record.get("record_id") or record.get("id") or "").strip()
        record["record_id"] = record_id
        record["id"] = record_id
        return self._put_record(self.reconciliation_records_path, record_id, record)

    def list_drift_reports(self) -> List[Dict[str, Any]]:
        return self._list_records(self.drift_reports_path)

    def get_drift_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.drift_reports_path, report_id)

    def put_drift_report(self, report: Dict[str, Any]) -> Dict[str, Any]:
        report_id = str(report.get("drift_report_id") or report.get("id") or "").strip()
        report["drift_report_id"] = report_id
        report["id"] = report_id
        return self._put_record(self.drift_reports_path, report_id, report)


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

    def list_reconciliation_records(self) -> List[Dict[str, Any]]:
        return self._list_records(self.reconciliation_records_path)

    def get_reconciliation_record(self, record_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.reconciliation_records_path, record_id)

    def put_reconciliation_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record_id = str(record.get("record_id") or record.get("id") or "").strip()
        if not record_id:
            raise ValueError("record_id is required")
        return super().put_reconciliation_record(record)

    def list_drift_reports(self) -> List[Dict[str, Any]]:
        return self._list_records(self.drift_reports_path)

    def get_drift_report(self, report_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.drift_reports_path, report_id)

    def put_drift_report(self, report: Dict[str, Any]) -> Dict[str, Any]:
        report_id = str(report.get("drift_report_id") or report.get("id") or "").strip()
        if not report_id:
            raise ValueError("drift_report_id is required")
        return super().put_drift_report(report)


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

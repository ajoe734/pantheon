from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
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
        self.work_claims_path = self.data_dir / "work_claims.json"
        self.worker_states_path = self.data_dir / "worker_states.json"

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

        def parse_finite_float(value: str) -> float:
            parsed = float(value)
            if not math.isfinite(parsed):
                raise ReconciliationStoreError(
                    f"{path}: JSON number {value!r} is outside the finite float range"
                )
            return parsed

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
            parse_float=parse_finite_float,
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

    @staticmethod
    def _storage_record_id(record_id: str, record: Dict[str, Any]) -> str:
        tenant_id = str(record.get("tenant_id") or "").strip()
        if not tenant_id:
            return record_id
        digest = hashlib.sha256(
            f"{tenant_id}\0{record_id}".encode("utf-8")
        ).hexdigest()
        return f"tenant-record-{digest}"

    def _put_record(self, path: Path, record_id: str, record: Dict[str, Any]) -> Dict[str, Any]:
        if not record_id:
            raise ValueError("record_id is required")
        serializable = json.loads(json.dumps(record, allow_nan=False))
        storage_id = self._storage_record_id(record_id, serializable)
        with self._locked(path):
            records = self._read_map_locked(path)
            records[storage_id] = serializable
            self._write_map_locked(path, records)
        return records[storage_id]

    def _list_records(self, path: Path) -> List[Dict[str, Any]]:
        return list(self._read_map(path).values())

    @staticmethod
    def _record_matches_tenant(
        record: Optional[Dict[str, Any]],
        tenant_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        if record is None:
            return None
        expected_tenant = str(tenant_id or "").strip()
        if (
            expected_tenant
            and str(record.get("tenant_id") or "").strip() != expected_tenant
        ):
            return None
        return record

    def _get_record(
        self,
        path: Path,
        record_id: str,
        *,
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        records = self._read_map(path)
        if tenant_id:
            storage_id = self._storage_record_id(
                record_id,
                {"tenant_id": tenant_id},
            )
            tenant_record = records.get(storage_id)
            tenant_record = self._record_matches_tenant(
                tenant_record,
                tenant_id,
            )
            if tenant_record is not None:
                return tenant_record
        direct = self._record_matches_tenant(
            records.get(record_id),
            tenant_id,
        )
        if direct is not None:
            return direct
        candidates = [
            record
            for record in records.values()
            if record_id
            in {
                str(record.get("id") or ""),
                str(record.get("evaluation_id") or ""),
                str(record.get("alert_id") or ""),
                str(record.get("record_id") or ""),
                str(record.get("drift_report_id") or ""),
                str(record.get("state_id") or ""),
            }
            and (not tenant_id or str(record.get("tenant_id") or "") == tenant_id)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def list_evaluations(self) -> List[Dict[str, Any]]:
        return self._list_records(self.evaluations_path)

    def get_evaluation(
        self, evaluation_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_record(
            self.evaluations_path,
            evaluation_id,
            tenant_id=tenant_id,
        )

    def put_evaluation(self, evaluation: Dict[str, Any]) -> Dict[str, Any]:
        evaluation_id = str(evaluation.get("evaluation_id") or evaluation.get("id") or "").strip()
        evaluation["evaluation_id"] = evaluation_id
        evaluation["id"] = evaluation_id
        return self._put_record(self.evaluations_path, evaluation_id, evaluation)

    def list_alert_handoffs(self) -> List[Dict[str, Any]]:
        return self._list_records(self.alerts_path)

    def get_alert_handoff(
        self, alert_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_record(self.alerts_path, alert_id, tenant_id=tenant_id)

    def put_alert_handoff(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        alert_id = str(alert.get("alert_id") or alert.get("id") or "").strip()
        alert["alert_id"] = alert_id
        alert["id"] = alert_id
        return self._put_record(self.alerts_path, alert_id, alert)

    def list_reconciliation_records(self) -> List[Dict[str, Any]]:
        return self._list_records(self.reconciliation_records_path)

    def get_reconciliation_record(
        self, record_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_record(
            self.reconciliation_records_path,
            record_id,
            tenant_id=tenant_id,
        )

    def put_reconciliation_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record_id = str(record.get("record_id") or record.get("id") or "").strip()
        record["record_id"] = record_id
        record["id"] = record_id
        return self._put_record(self.reconciliation_records_path, record_id, record)

    def list_drift_reports(self) -> List[Dict[str, Any]]:
        return self._list_records(self.drift_reports_path)

    def get_drift_report(
        self, report_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_record(
            self.drift_reports_path,
            report_id,
            tenant_id=tenant_id,
        )

    def put_drift_report(self, report: Dict[str, Any]) -> Dict[str, Any]:
        report_id = str(report.get("drift_report_id") or report.get("id") or "").strip()
        report["drift_report_id"] = report_id
        report["id"] = report_id
        return self._put_record(self.drift_reports_path, report_id, report)

    def list_worker_states(self) -> List[Dict[str, Any]]:
        return self._list_records(self.worker_states_path)

    def get_worker_state(
        self, state_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_record(
            self.worker_states_path,
            state_id,
            tenant_id=tenant_id,
        )

    def put_worker_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state_id = str(state.get("state_id") or state.get("id") or "").strip()
        state["state_id"] = state_id
        state["id"] = state_id
        return self._put_record(self.worker_states_path, state_id, state)

    @staticmethod
    def _work_claim_id(*, tenant_id: str, work_type: str, window_id: str) -> str:
        identity = f"{tenant_id}\0{work_type}\0{window_id}"
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        safe_type = "".join(
            character if character.isalnum() or character in "._-" else "-"
            for character in work_type
        ).strip("-._") or "work"
        return f"work-{safe_type[:32]}-{digest}"

    @staticmethod
    def _as_utc(value: datetime | str | None) -> datetime:
        if value is None:
            return datetime.now(timezone.utc)
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError as exc:
                raise ReconciliationStoreError(
                    f"invalid UTC timestamp in work claim: {value!r}"
                ) from exc
        else:
            raise ReconciliationStoreError(
                f"invalid UTC timestamp type in work claim: {type(value).__name__}"
            )
        if parsed.tzinfo is None:
            raise ReconciliationStoreError("work claim timestamp must include a timezone")
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _utc_iso(value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _validate_work_identity(
        *, tenant_id: str, work_type: str, window_id: str, owner_id: str
    ) -> None:
        missing = [
            name
            for name, value in (
                ("tenant_id", tenant_id),
                ("work_type", work_type),
                ("window_id", window_id),
                ("owner_id", owner_id),
            )
            if not str(value).strip()
        ]
        if missing:
            raise ValueError(f"work claim missing required identity: {', '.join(missing)}")

    def _compare_and_set_work_claim(
        self,
        claim_id: str,
        expected: Optional[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        serializable = json.loads(json.dumps(payload, allow_nan=False))
        with self._locked(self.work_claims_path):
            records = self._read_map_locked(self.work_claims_path)
            current = records.get(claim_id)
            if current != expected:
                return False, current
            records[claim_id] = serializable
            self._write_map_locked(self.work_claims_path, records)
            return True, serializable

    def _get_work_claim(self, claim_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.work_claims_path, claim_id)

    @staticmethod
    def _assert_claim_identity(
        claim: Dict[str, Any],
        *,
        claim_id: str,
        tenant_id: str,
        work_type: str,
        window_id: str,
    ) -> None:
        expected = {
            "claim_id": claim_id,
            "tenant_id": tenant_id,
            "work_type": work_type,
            "window_id": window_id,
        }
        mismatched = {
            field: {"expected": value, "actual": claim.get(field)}
            for field, value in expected.items()
            if claim.get(field) != value
        }
        if mismatched:
            raise ReconciliationStoreError(
                f"durable work claim identity mismatch for {claim_id}: {mismatched}"
            )

    def claim_work(
        self,
        *,
        tenant_id: str,
        work_type: str,
        window_id: str,
        owner_id: str,
        lease_seconds: float,
        now: datetime | str | None = None,
    ) -> Dict[str, Any]:
        """Atomically claim one tenant-scoped logical window.

        Completed windows are immutable idempotency receipts. Active leases
        defer competing workers, while failed or expired work may be recovered
        by a new owner. JSON uses the same transaction lock as record writes;
        Postgres overrides the CAS primitive below.
        """

        self._validate_work_identity(
            tenant_id=tenant_id,
            work_type=work_type,
            window_id=window_id,
            owner_id=owner_id,
        )
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValueError("lease_seconds must be a finite number > 0")
        observed_at = self._as_utc(now)
        claim_id = self._work_claim_id(
            tenant_id=tenant_id,
            work_type=work_type,
            window_id=window_id,
        )
        for _attempt in range(8):
            current = self._get_work_claim(claim_id)
            if current is not None:
                self._assert_claim_identity(
                    current,
                    claim_id=claim_id,
                    tenant_id=tenant_id,
                    work_type=work_type,
                    window_id=window_id,
                )
                current_status = current.get("status")
                if current_status not in {"in_progress", "completed", "failed"}:
                    raise ReconciliationStoreError(
                        f"work claim has invalid status for {claim_id}: {current_status!r}"
                    )
                if current_status == "completed":
                    if not isinstance(current.get("result"), dict):
                        raise ReconciliationStoreError(
                            f"completed work claim has no result receipt: {claim_id}"
                        )
                    return {
                        "acquired": False,
                        "reason": "completed",
                        "claim": current,
                    }
                raw_lease_expires_at = current.get("lease_expires_at")
                if (
                    current_status == "in_progress"
                    and not raw_lease_expires_at
                ):
                    raise ReconciliationStoreError(
                        f"active work claim has no lease expiry: {claim_id}"
                    )
                lease_expires_at = self._as_utc(raw_lease_expires_at)
                if (
                    current_status == "in_progress"
                    and lease_expires_at > observed_at
                ):
                    return {
                        "acquired": False,
                        "reason": "lease_active",
                        "claim": current,
                    }

            lease_token = uuid.uuid4().hex
            attempt_count = int((current or {}).get("attempt_count") or 0) + 1
            replacement = {
                "id": claim_id,
                "claim_id": claim_id,
                "tenant_id": tenant_id,
                "work_type": work_type,
                "window_id": window_id,
                "status": "in_progress",
                "owner_id": owner_id,
                "lease_token": lease_token,
                "lease_acquired_at": self._utc_iso(observed_at),
                "lease_expires_at": self._utc_iso(
                    observed_at + timedelta(seconds=lease_seconds)
                ),
                "attempt_count": attempt_count,
                "created_at": (current or {}).get("created_at")
                or self._utc_iso(observed_at),
                "updated_at": self._utc_iso(observed_at),
                "completed_at": None,
                "failed_at": None,
                "last_error": None,
                "result": None,
            }
            changed, canonical = self._compare_and_set_work_claim(
                claim_id,
                current,
                replacement,
            )
            if changed:
                return {
                    "acquired": True,
                    "reason": "acquired" if current is None else "recovered",
                    "claim": canonical,
                }
        return {
            "acquired": False,
            "reason": "contention",
            "claim": self._get_work_claim(claim_id),
        }

    def _finish_work(
        self,
        *,
        claim_id: str,
        lease_token: str,
        status: str,
        now: datetime | str | None,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None,
    ) -> Dict[str, Any]:
        if status not in {"completed", "failed"}:
            raise ValueError("work completion status must be completed or failed")
        if not claim_id or not lease_token:
            raise ValueError("claim_id and lease_token are required")
        observed_at = self._as_utc(now)
        current = self._get_work_claim(claim_id)
        if current is None:
            raise ReconciliationStoreError(f"work claim not found: {claim_id}")
        if (
            current.get("status") != "in_progress"
            or current.get("lease_token") != lease_token
        ):
            raise ReconciliationStoreError(
                f"work claim lease lost before {status}: {claim_id}"
            )
        replacement = dict(current)
        replacement.update(
            {
                "status": status,
                "lease_token": None,
                "lease_expires_at": None,
                "updated_at": self._utc_iso(observed_at),
                "completed_at": self._utc_iso(observed_at)
                if status == "completed"
                else None,
                "failed_at": self._utc_iso(observed_at)
                if status == "failed"
                else None,
                "last_error": error if status == "failed" else None,
                "result": json.loads(json.dumps(result, allow_nan=False))
                if result is not None
                else None,
            }
        )
        changed, canonical = self._compare_and_set_work_claim(
            claim_id,
            current,
            replacement,
        )
        if not changed or canonical is None:
            raise ReconciliationStoreError(
                f"work claim changed before {status}: {claim_id}"
            )
        return canonical

    def complete_work(
        self,
        *,
        claim_id: str,
        lease_token: str,
        result: Dict[str, Any],
        now: datetime | str | None = None,
    ) -> Dict[str, Any]:
        return self._finish_work(
            claim_id=claim_id,
            lease_token=lease_token,
            status="completed",
            now=now,
            result=result,
        )

    def fail_work(
        self,
        *,
        claim_id: str,
        lease_token: str,
        error: str,
        result: Optional[Dict[str, Any]] = None,
        now: datetime | str | None = None,
    ) -> Dict[str, Any]:
        return self._finish_work(
            claim_id=claim_id,
            lease_token=lease_token,
            status="failed",
            now=now,
            result=result,
            error=error,
        )


class PostgresReconciliationDriftStore(ReconciliationDriftStore):
    """Postgres owner store for every reconciliation authority record."""

    def __init__(
        self,
        data_dir: str | Path,
        *,
        dsn: str,
        evaluations_table: str = "reconciliation_drift.drift_evaluations",
        alerts_table: str = "reconciliation_drift.alert_handoffs",
        reconciliation_records_table: str = "reconciliation_drift.reconciliation_records",
        drift_reports_table: str = "reconciliation_drift.drift_reports",
        work_claims_table: str = "reconciliation_drift.work_claims",
        worker_states_table: str = "reconciliation_drift.worker_states",
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
        self._reconciliation_records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=reconciliation_records_table,
            owner_service="reconciliation-drift-svc",
            bootstrap=bootstrap,
        )
        self._drift_reports = PostgresJsonOwnerStore(
            dsn=dsn,
            table=drift_reports_table,
            owner_service="reconciliation-drift-svc",
            bootstrap=bootstrap,
        )
        self._work_claim_records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=work_claims_table,
            owner_service="reconciliation-drift-svc",
            bootstrap=bootstrap,
        )
        self._worker_state_records = PostgresJsonOwnerStore(
            dsn=dsn,
            table=worker_states_table,
            owner_service="reconciliation-drift-svc",
            bootstrap=bootstrap,
        )

    def _get_owner_record(
        self,
        owner_store: Any,
        record_id: str,
        tenant_id: Optional[str],
    ) -> Optional[Dict[str, Any]]:
        storage_id = self._storage_record_id(
            record_id,
            {"tenant_id": tenant_id} if tenant_id else {},
        )
        record = self._record_matches_tenant(
            owner_store.get(storage_id),
            tenant_id,
        )
        if record is not None:
            return record
        if storage_id != record_id:
            return self._record_matches_tenant(
                owner_store.get(record_id),
                tenant_id,
            )
        return None

    def list_evaluations(self) -> List[Dict[str, Any]]:
        return self._evaluation_records.list_all()

    def get_evaluation(
        self, evaluation_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_owner_record(
            self._evaluation_records,
            evaluation_id,
            tenant_id,
        )

    def put_evaluation(self, evaluation: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(evaluation))
        evaluation_id = str(record.get("evaluation_id") or record.get("id") or "").strip()
        if not evaluation_id:
            raise ValueError("evaluation_id is required")
        record["evaluation_id"] = evaluation_id
        record["id"] = evaluation_id
        self._evaluation_records.put(
            self._storage_record_id(evaluation_id, record),
            record,
        )
        return record

    def list_alert_handoffs(self) -> List[Dict[str, Any]]:
        return self._alert_records.list_all()

    def get_alert_handoff(
        self, alert_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_owner_record(
            self._alert_records,
            alert_id,
            tenant_id,
        )

    def put_alert_handoff(self, alert: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(alert))
        alert_id = str(record.get("alert_id") or record.get("id") or "").strip()
        if not alert_id:
            raise ValueError("alert_id is required")
        record["alert_id"] = alert_id
        record["id"] = alert_id
        self._alert_records.put(self._storage_record_id(alert_id, record), record)
        return record

    def list_reconciliation_records(self) -> List[Dict[str, Any]]:
        return self._reconciliation_records.list_all()

    def get_reconciliation_record(
        self, record_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_owner_record(
            self._reconciliation_records,
            record_id,
            tenant_id,
        )

    def put_reconciliation_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        record_id = str(record.get("record_id") or record.get("id") or "").strip()
        if not record_id:
            raise ValueError("record_id is required")
        record["record_id"] = record_id
        record["id"] = record_id
        self._reconciliation_records.put(
            self._storage_record_id(record_id, record),
            record,
        )
        return record

    def list_drift_reports(self) -> List[Dict[str, Any]]:
        return self._drift_reports.list_all()

    def get_drift_report(
        self, report_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_owner_record(
            self._drift_reports,
            report_id,
            tenant_id,
        )

    def put_drift_report(self, report: Dict[str, Any]) -> Dict[str, Any]:
        report_id = str(report.get("drift_report_id") or report.get("id") or "").strip()
        if not report_id:
            raise ValueError("drift_report_id is required")
        report["drift_report_id"] = report_id
        report["id"] = report_id
        self._drift_reports.put(
            self._storage_record_id(report_id, report),
            report,
        )
        return report

    def list_worker_states(self) -> List[Dict[str, Any]]:
        return self._worker_state_records.list_all()

    def get_worker_state(
        self, state_id: str, tenant_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        return self._get_owner_record(
            self._worker_state_records,
            state_id,
            tenant_id,
        )

    def put_worker_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        state_id = str(state.get("state_id") or state.get("id") or "").strip()
        if not state_id:
            raise ValueError("state_id is required")
        state["state_id"] = state_id
        state["id"] = state_id
        self._worker_state_records.put(
            self._storage_record_id(state_id, state),
            state,
        )
        return state

    def _compare_and_set_work_claim(
        self,
        claim_id: str,
        expected: Optional[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> tuple[bool, Optional[Dict[str, Any]]]:
        return self._work_claim_records.compare_and_set(claim_id, expected, payload)

    def _get_work_claim(self, claim_id: str) -> Optional[Dict[str, Any]]:
        return self._work_claim_records.get(claim_id)


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
        reconciliation_records_table=os.getenv(
            "RECONCILIATION_DRIFT_RECORD_STORE_TABLE",
            "reconciliation_drift.reconciliation_records",
        ),
        drift_reports_table=os.getenv(
            "RECONCILIATION_DRIFT_REPORT_STORE_TABLE",
            "reconciliation_drift.drift_reports",
        ),
        work_claims_table=os.getenv(
            "RECONCILIATION_DRIFT_WORK_CLAIM_STORE_TABLE",
            "reconciliation_drift.work_claims",
        ),
        worker_states_table=os.getenv(
            "RECONCILIATION_DRIFT_WORKER_STATE_STORE_TABLE",
            "reconciliation_drift.worker_states",
        ),
        bootstrap=bootstrap,
    )

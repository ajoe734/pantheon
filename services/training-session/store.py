from __future__ import annotations

import json
import os
import re
import tempfile
from contextlib import contextmanager
from fcntl import LOCK_EX, LOCK_SH, LOCK_UN, flock
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional


_PG_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _quote_pg_identifier(identifier: str) -> str:
    parts = identifier.split(".")
    if not parts or any(_PG_IDENTIFIER_RE.fullmatch(part) is None for part in parts):
        raise ValueError(f"Invalid Postgres identifier: {identifier}")
    return ".".join(f'"{part}"' for part in parts)


def _copy_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return json.loads(json.dumps(record))


def _append_event_to_session(
    session: Dict[str, Any],
    event: Dict[str, Any],
) -> Dict[str, Any]:
    record = _copy_record(session)
    durable_event = _copy_record(event)
    session_id = str(record.get("session_id") or record.get("id") or "").strip()
    event_session_id = str(durable_event.get("session_id") or "").strip()
    if not session_id or event_session_id != session_id:
        raise ValueError("event.session_id must match session.session_id")
    tenant_id = str(record.get("tenant_id") or "").strip()
    if not tenant_id or str(durable_event.get("tenant_id") or "").strip() != tenant_id:
        raise ValueError("event.tenant_id must match session.tenant_id")
    event_id = str(durable_event.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("event_id is required")

    events = record.setdefault("events", [])
    for prior in events:
        if not isinstance(prior, dict) or prior.get("event_id") != event_id:
            continue
        if prior != durable_event:
            raise ValueError(f"event_id conflict in session: {event_id}")
        return record
    events.append(durable_event)
    outcome_signal = durable_event.get("outcome_signal")
    if outcome_signal:
        outcomes = record.setdefault("outcomes", [])
        if outcome_signal not in outcomes:
            outcomes.append(outcome_signal)
    return record


class PostgresTrainingSessionEventStore:
    def __init__(self, dsn: str, table: str = "training_session.teaching_events", bootstrap: bool = True) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        self.dsn = dsn
        self.table = _quote_pg_identifier(table)
        self.schema = table.split(".", 1)[0] if "." in table else ""
        if bootstrap:
            self.bootstrap()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required when TRAINING_SESSION_EVENT_STORE_BACKEND=postgres") from exc
        return psycopg.connect(self.dsn)

    def bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg_identifier(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    append_id BIGSERIAL PRIMARY KEY,
                    event_id TEXT NOT NULL UNIQUE,
                    session_id TEXT NOT NULL,
                    event_type TEXT,
                    sequence_number INTEGER,
                    emitted_at TEXT,
                    payload JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    @staticmethod
    def _fetch_one(cursor: Any) -> Any:
        if hasattr(cursor, "fetchone"):
            return cursor.fetchone()
        rows = cursor.fetchall()
        return rows[0] if rows else None

    @staticmethod
    def _row_payload(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        payload = row[0] if isinstance(row, tuple) else row.get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return dict(payload) if isinstance(payload, dict) else None

    def _append_event_with_connection(
        self,
        conn: Any,
        event: Dict[str, Any],
    ) -> Dict[str, Any]:
        record = _copy_record(event)
        session_id = str(record.get("session_id") or "").strip()
        event_id = str(record.get("event_id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        if not event_id:
            raise ValueError("event_id is required")
        cursor = conn.execute(
            f"""
            INSERT INTO {self.table}
                (event_id, session_id, event_type, sequence_number, emitted_at, payload)
            VALUES (%s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            RETURNING payload
            """,
            (
                event_id,
                session_id,
                record.get("event_type"),
                record.get("sequence_number"),
                record.get("emitted_at") or record.get("created_at"),
                json.dumps(record, ensure_ascii=True, sort_keys=True),
            ),
        )
        durable = self._row_payload(self._fetch_one(cursor))
        if durable is None:
            # A separate statement gets a fresh READ COMMITTED snapshot after
            # waiting on a concurrent unique-key insert.
            cursor = conn.execute(
                f"SELECT payload FROM {self.table} WHERE event_id = %s",
                (event_id,),
            )
            durable = self._row_payload(self._fetch_one(cursor))
        if durable is None:
            raise RuntimeError(f"durable event readback missing: {event_id}")
        if durable != record:
            raise ValueError(f"event_id conflict: {event_id}")
        return durable

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        with self._connect() as conn:
            return self._append_event_with_connection(conn, event)

    def list_event_log(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params: tuple[Any, ...] = ()
        where = ""
        if session_id is not None:
            where = "WHERE session_id = %s"
            params = (session_id,)
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} {where} ORDER BY append_id ASC", params)
            rows = cursor.fetchall()
        records: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                records.append(payload)
        return records


class PostgresTrainingSessionStore:
    """Authoritative HA owner store for every mutable teaching record.

    The earlier Postgres pilot moved only ``TeachingEvent`` rows off the local
    volume.  Sessions, controls, previews, jobs, and replay decisions therefore
    still diverged when more than one API instance was running.  This store
    keeps those records in one service-owned JSONB table and uses a
    transaction-scoped advisory lock for read/decide/write mutations.  Holding
    that lock across the replay mutator is intentional: the persona-target
    write is idempotent, and a process restart can repeat its terminal readback
    without allowing a second service instance to pass the same admission
    decision concurrently.
    """

    _KINDS = {
        "session",
        "controls",
        "preview",
        "preview_job",
        "replay",
        "functional_health",
    }

    def __init__(
        self,
        data_dir: str | Path,
        *,
        dsn: str,
        records_table: str = "training_session.authority_records",
        events_table: str = "training_session.teaching_events",
        bootstrap: bool = True,
    ) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.dsn = dsn
        self.records_table_name = records_table
        self.records_table = _quote_pg_identifier(records_table)
        self.schema = records_table.split(".", 1)[0] if "." in records_table else ""
        self.event_store = PostgresTrainingSessionEventStore(
            dsn=dsn,
            table=events_table,
            bootstrap=False,
        )
        if bootstrap:
            self.bootstrap()

    def _connect(self):
        return self.event_store._connect()

    def bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg_identifier(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.records_table} (
                    record_kind TEXT NOT NULL,
                    record_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (record_kind, record_id)
                )
                """
            )
            conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS training_session_authority_records_tenant_idx
                ON {self.records_table} (tenant_id, record_kind, updated_at)
                """
            )
        self.event_store.bootstrap()

    @staticmethod
    def _decode_payload(value: Any) -> Optional[Dict[str, Any]]:
        if value is None:
            return None
        if isinstance(value, str):
            value = json.loads(value)
        return dict(value) if isinstance(value, dict) else None

    @classmethod
    def _row_payload(cls, row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        value = row[0] if isinstance(row, tuple) else row.get("payload")
        return cls._decode_payload(value)

    @staticmethod
    def _fetch_one(cursor: Any) -> Any:
        if hasattr(cursor, "fetchone"):
            return cursor.fetchone()
        rows = cursor.fetchall()
        return rows[0] if rows else None

    @classmethod
    def _record(cls, kind: str, record_id: str, payload: Dict[str, Any]) -> tuple[str, str]:
        if kind not in cls._KINDS:
            raise ValueError(f"unsupported training-session record kind: {kind}")
        clean_id = str(record_id or "").strip()
        if not clean_id:
            raise ValueError("record_id is required")
        tenant_id = str(payload.get("tenant_id") or "").strip()
        if not tenant_id:
            raise ValueError(f"tenant_id is required for {kind} record {clean_id}")
        return clean_id, tenant_id

    def _list_records(self, kind: str) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT payload
                FROM {self.records_table}
                WHERE record_kind = %s
                ORDER BY updated_at ASC, record_id ASC
                """,
                (kind,),
            )
            rows = cursor.fetchall()
        return [
            payload
            for payload in (self._row_payload(row) for row in rows)
            if payload is not None
        ]

    def _get_record(self, kind: str, record_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                SELECT payload
                FROM {self.records_table}
                WHERE record_kind = %s AND record_id = %s
                """,
                (kind, record_id),
            )
            return self._row_payload(self._fetch_one(cursor))

    def _put_record(self, kind: str, record_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(payload))
        clean_id, tenant_id = self._record(kind, record_id, record)
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.records_table}
                    (record_kind, record_id, tenant_id, payload, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, now())
                ON CONFLICT (record_kind, record_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """,
                (
                    kind,
                    clean_id,
                    tenant_id,
                    json.dumps(record, ensure_ascii=True, sort_keys=True),
                ),
            )
        return record

    def _mutate_record(
        self,
        kind: str,
        record_id: str,
        mutator: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        clean_id = str(record_id or "").strip()
        if not clean_id:
            raise ValueError("record_id is required")
        lock_ref = f"training-session:{kind}:{clean_id}"
        with self._connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_ref,))
            cursor = conn.execute(
                f"""
                SELECT payload
                FROM {self.records_table}
                WHERE record_kind = %s AND record_id = %s
                FOR UPDATE
                """,
                (kind, clean_id),
            )
            existing = self._row_payload(self._fetch_one(cursor))
            candidate = mutator(json.loads(json.dumps(existing)) if existing is not None else None)
            if not isinstance(candidate, dict):
                raise TypeError(f"{kind} mutator must return a dict")
            record = json.loads(json.dumps(candidate))
            _, tenant_id = self._record(kind, clean_id, record)
            conn.execute(
                f"""
                INSERT INTO {self.records_table}
                    (record_kind, record_id, tenant_id, payload, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, now())
                ON CONFLICT (record_kind, record_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """,
                (
                    kind,
                    clean_id,
                    tenant_id,
                    json.dumps(record, ensure_ascii=True, sort_keys=True),
                ),
            )
        return record

    def list_sessions(self) -> List[Dict[str, Any]]:
        return self._list_records("session")

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record("session", session_id)

    def put_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "").strip()
        return self._put_record("session", session_id, session)

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        return self.event_store.append_event(event)

    def append_session_event(
        self,
        session_id: str,
        event_factory: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
        *,
        session_transform: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        clean_id = str(session_id or "").strip()
        if not clean_id:
            raise ValueError("session_id is required")
        lock_ref = f"training-session:session:{clean_id}"
        with self._connect() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock_ref,))
            cursor = conn.execute(
                f"""
                SELECT payload
                FROM {self.records_table}
                WHERE record_kind = %s AND record_id = %s
                FOR UPDATE
                """,
                ("session", clean_id),
            )
            existing = self._row_payload(self._fetch_one(cursor))
            candidate_event = event_factory(
                _copy_record(existing) if existing is not None else None
            )
            if existing is None:
                raise ValueError(f"training session not found: {clean_id}")
            durable_event = self.event_store._append_event_with_connection(
                conn,
                candidate_event,
            )
            record = _append_event_to_session(existing, durable_event)
            if session_transform is not None:
                record = session_transform(_copy_record(record))
                if not isinstance(record, dict):
                    raise TypeError("session_transform must return a dict")
            _, tenant_id = self._record("session", clean_id, record)
            conn.execute(
                f"""
                INSERT INTO {self.records_table}
                    (record_kind, record_id, tenant_id, payload, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, now())
                ON CONFLICT (record_kind, record_id) DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """,
                (
                    "session",
                    clean_id,
                    tenant_id,
                    json.dumps(record, ensure_ascii=True, sort_keys=True),
                ),
            )
        return record, durable_event

    def list_event_log(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        return self.event_store.list_event_log(session_id)

    def list_controls(self) -> List[Dict[str, Any]]:
        return self._list_records("controls")

    def get_controls(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record("controls", session_id)

    def put_controls(self, session_id: str, controls: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(controls))
        record["session_id"] = session_id
        return self._put_record("controls", session_id, record)

    def list_previews(self) -> List[Dict[str, Any]]:
        return self._list_records("preview")

    def get_preview_bundle(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record("preview", session_id)

    def put_preview_bundle(self, session_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(bundle))
        record["session_id"] = session_id
        return self._put_record("preview", session_id, record)

    def list_preview_jobs(self) -> List[Dict[str, Any]]:
        return self._list_records("preview_job")

    def get_preview_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record("preview_job", job_id)

    def put_preview_job(self, job_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(job))
        record["job_id"] = job_id
        return self._put_record("preview_job", job_id, record)

    def mutate_preview_job(
        self,
        job_id: str,
        mutator: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        return self._mutate_record("preview_job", job_id, mutator)

    def list_replays(self) -> List[Dict[str, Any]]:
        return self._list_records("replay")

    def get_replay(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record("replay", session_id)

    def put_replay(self, session_id: str, replay: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(replay))
        record["session_id"] = session_id
        return self._put_record("replay", session_id, record)

    def mutate_replay(
        self,
        session_id: str,
        mutator: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        return self._mutate_record("replay", session_id, mutator)

    def list_functional_results(self) -> List[Dict[str, Any]]:
        return self._list_records("functional_health")

    def put_functional_result(
        self,
        operation: str,
        tenant_id: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        record = json.loads(json.dumps(result))
        record["operation"] = operation
        record["tenant_id"] = tenant_id
        return self._put_record("functional_health", f"{tenant_id}:{operation}", record)

    def storage_health(self) -> Dict[str, Any]:
        try:
            with self._connect() as conn:
                self._fetch_one(conn.execute("SELECT 1"))
        except Exception as exc:  # noqa: BLE001 - health must report dependency failure.
            return {
                "status": "error",
                "backend": "postgres",
                "authoritative": True,
                "error": type(exc).__name__,
            }
        return {"status": "ok", "backend": "postgres", "authoritative": True}


class TrainingSessionStore:
    def __init__(self, data_dir: str | Path, event_store: Optional[PostgresTrainingSessionEventStore] = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_path = self.data_dir / "teaching_sessions.json"
        self.events_path = self.data_dir / "teaching_events.jsonl"
        self.controls_path = self.data_dir / "trainer_controls.json"
        self.previews_path = self.data_dir / "trainer_previews.json"
        self.preview_jobs_path = self.data_dir / "trainer_preview_jobs.json"
        self.replays_path = self.data_dir / "trainer_replays.json"
        self.functional_health_path = self.data_dir / "functional_health.json"
        self.event_store = event_store

    @contextmanager
    def _file_lock(self, path: Path, *, exclusive: bool) -> Iterator[None]:
        """Serialize API and worker access to a shared JSON/JSONL artifact."""

        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_name(f".{path.name}.lock")
        with lock_path.open("a+", encoding="utf-8") as handle:
            flock(handle.fileno(), LOCK_EX if exclusive else LOCK_SH)
            try:
                yield
            finally:
                flock(handle.fileno(), LOCK_UN)

    def _read_map_unlocked(self, path: Path) -> Dict[str, Dict[str, Any]]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {}
        return {str(k): v for k, v in payload.items() if isinstance(v, dict)}

    def _read_map(self, path: Path) -> Dict[str, Dict[str, Any]]:
        with self._file_lock(path, exclusive=False):
            return self._read_map_unlocked(path)

    def _write_map_unlocked(self, path: Path, payload: Dict[str, Dict[str, Any]]) -> None:
        """Durably replace a map without exposing a partial JSON document."""

        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)

    def _put_map_record(self, path: Path, key: str, record: Dict[str, Any]) -> Dict[str, Any]:
        stored = json.loads(json.dumps(record))
        with self._file_lock(path, exclusive=True):
            records = self._read_map_unlocked(path)
            records[key] = stored
            self._write_map_unlocked(path, records)
        return stored

    def _read_jsonl_unlocked(self, path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        records: List[Dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
        with self._file_lock(path, exclusive=False):
            return self._read_jsonl_unlocked(path)

    def list_sessions(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.sessions_path).values())

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.sessions_path).get(session_id)

    def put_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        return self._put_map_record(self.sessions_path, session_id, session)

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if self.event_store is not None:
            return self.event_store.append_event(event)
        record = _copy_record(event)
        if not str(record.get("session_id") or "").strip():
            raise ValueError("session_id is required")
        if not str(record.get("event_id") or "").strip():
            raise ValueError("event_id is required")
        with self._file_lock(self.events_path, exclusive=True):
            return self._append_jsonl_event_unlocked(record)

    def _append_jsonl_event_unlocked(self, record: Dict[str, Any]) -> Dict[str, Any]:
        existing = self._read_jsonl_unlocked(self.events_path)
        for prior in existing:
            if prior.get("event_id") == record.get("event_id"):
                if prior != record:
                    raise ValueError(f"event_id conflict: {record['event_id']}")
                return prior
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        return record

    def append_session_event(
        self,
        session_id: str,
        event_factory: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
        *,
        session_transform: Optional[Callable[[Dict[str, Any]], Dict[str, Any]]] = None,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        clean_id = str(session_id or "").strip()
        if not clean_id:
            raise ValueError("session_id is required")
        with self._file_lock(self.sessions_path, exclusive=True):
            records = self._read_map_unlocked(self.sessions_path)
            existing = records.get(clean_id)
            candidate_event = event_factory(
                _copy_record(existing) if existing is not None else None
            )
            if existing is None:
                raise ValueError(f"training session not found: {clean_id}")
            if self.event_store is not None:
                durable_event = self.event_store.append_event(candidate_event)
            else:
                with self._file_lock(self.events_path, exclusive=True):
                    durable_event = self._append_jsonl_event_unlocked(
                        _copy_record(candidate_event)
                    )
            record = _append_event_to_session(existing, durable_event)
            if session_transform is not None:
                record = session_transform(_copy_record(record))
                if not isinstance(record, dict):
                    raise TypeError("session_transform must return a dict")
            records[clean_id] = record
            self._write_map_unlocked(self.sessions_path, records)
        return record, durable_event

    def list_event_log(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if self.event_store is not None:
            return self.event_store.list_event_log(session_id)
        records = self._read_jsonl(self.events_path)
        if session_id is not None:
            records = [record for record in records if record.get("session_id") == session_id]
        return records

    def list_controls(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.controls_path).values())

    def get_controls(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.controls_path).get(session_id)

    def put_controls(self, session_id: str, controls: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(controls))
        record["session_id"] = session_id
        return self._put_map_record(self.controls_path, session_id, record)

    def list_previews(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.previews_path).values())

    def get_preview_bundle(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.previews_path).get(session_id)

    def put_preview_bundle(self, session_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(bundle))
        record["session_id"] = session_id
        return self._put_map_record(self.previews_path, session_id, record)

    def list_preview_jobs(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.preview_jobs_path).values())

    def get_preview_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.preview_jobs_path).get(job_id)

    def put_preview_job(self, job_id: str, job: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(job))
        record["job_id"] = job_id
        return self._put_map_record(self.preview_jobs_path, job_id, record)

    def mutate_preview_job(
        self,
        job_id: str,
        mutator: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Atomically read, validate, and replace one preview job.

        The callback runs while the cross-process file lock is held.  It must
        not call another method on this store or perform network I/O.
        Raising from the callback leaves the durable map unchanged.
        """

        with self._file_lock(self.preview_jobs_path, exclusive=True):
            records = self._read_map_unlocked(self.preview_jobs_path)
            existing = records.get(job_id)
            candidate = mutator(json.loads(json.dumps(existing)) if existing is not None else None)
            if not isinstance(candidate, dict):
                raise TypeError("preview job mutator must return a dict")
            record = json.loads(json.dumps(candidate))
            record["job_id"] = job_id
            records[job_id] = record
            self._write_map_unlocked(self.preview_jobs_path, records)
            return record

    def mutate_replay(
        self,
        session_id: str,
        mutator: Callable[[Optional[Dict[str, Any]]], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Atomically read, decide, and replace one replay record.

        Replay decisions can include bounded authority I/O while the replay
        lock is held.  This serializes the local pending-decision check with
        the external commit side effect for a session so concurrent requests
        cannot both pass admission before the durable replay state advances.
        Raising from the callback leaves the durable replay map unchanged.
        """

        with self._file_lock(self.replays_path, exclusive=True):
            records = self._read_map_unlocked(self.replays_path)
            existing = records.get(session_id)
            candidate = mutator(json.loads(json.dumps(existing)) if existing is not None else None)
            if not isinstance(candidate, dict):
                raise TypeError("replay mutator must return a dict")
            record = json.loads(json.dumps(candidate))
            record["session_id"] = session_id
            records[session_id] = record
            self._write_map_unlocked(self.replays_path, records)
            return record

    def list_replays(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.replays_path).values())

    def get_replay(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.replays_path).get(session_id)

    def put_replay(self, session_id: str, replay: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(replay))
        record["session_id"] = session_id
        return self._put_map_record(self.replays_path, session_id, record)

    def list_functional_results(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.functional_health_path).values())

    def put_functional_result(
        self,
        operation: str,
        tenant_id: str,
        result: Dict[str, Any],
    ) -> Dict[str, Any]:
        record = json.loads(json.dumps(result))
        record["operation"] = operation
        record["tenant_id"] = tenant_id
        return self._put_map_record(
            self.functional_health_path,
            f"{tenant_id}:{operation}",
            record,
        )

    def storage_health(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "backend": "postgres-events" if self.event_store is not None else "json",
            "authoritative": False,
        }


def build_training_session_store(data_dir: str | Path) -> TrainingSessionStore | PostgresTrainingSessionStore:
    backend = os.getenv("TRAINING_SESSION_EVENT_STORE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return TrainingSessionStore(data_dir)
    if backend != "postgres":
        raise ValueError("TRAINING_SESSION_EVENT_STORE_BACKEND must be jsonl or postgres")

    dsn = os.getenv("TRAINING_SESSION_EVENT_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("TRAINING_SESSION_EVENT_STORE_DSN or DATABASE_URL is required for Postgres event store")
    events_table = os.getenv("TRAINING_SESSION_EVENT_STORE_TABLE", "training_session.teaching_events")
    records_table = os.getenv(
        "TRAINING_SESSION_AUTHORITY_STORE_TABLE",
        "training_session.authority_records",
    )
    bootstrap = os.getenv("TRAINING_SESSION_EVENT_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return PostgresTrainingSessionStore(
        data_dir,
        dsn=dsn,
        records_table=records_table,
        events_table=events_table,
        bootstrap=bootstrap,
    )

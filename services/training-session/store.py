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

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(event))
        session_id = str(record.get("session_id") or "").strip()
        event_id = str(record.get("event_id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        if not event_id:
            raise ValueError("event_id is required")
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table}
                    (event_id, session_id, event_type, sequence_number, emitted_at, payload)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (event_id) DO NOTHING
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
        return record

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
        record = json.loads(json.dumps(event))
        if not str(record.get("session_id") or "").strip():
            raise ValueError("session_id is required")
        if not str(record.get("event_id") or "").strip():
            raise ValueError("event_id is required")
        with self._file_lock(self.events_path, exclusive=True):
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


def build_training_session_store(data_dir: str | Path) -> TrainingSessionStore:
    backend = os.getenv("TRAINING_SESSION_EVENT_STORE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return TrainingSessionStore(data_dir)
    if backend != "postgres":
        raise ValueError("TRAINING_SESSION_EVENT_STORE_BACKEND must be jsonl or postgres")

    dsn = os.getenv("TRAINING_SESSION_EVENT_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("TRAINING_SESSION_EVENT_STORE_DSN or DATABASE_URL is required for Postgres event store")
    table = os.getenv("TRAINING_SESSION_EVENT_STORE_TABLE", "training_session.teaching_events")
    bootstrap = os.getenv("TRAINING_SESSION_EVENT_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return TrainingSessionStore(
        data_dir,
        event_store=PostgresTrainingSessionEventStore(dsn=dsn, table=table, bootstrap=bootstrap),
    )

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


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
        self.replays_path = self.data_dir / "trainer_replays.json"
        self.event_store = event_store

    def _read_map(self, path: Path) -> Dict[str, Dict[str, Any]]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {}
        return {str(k): v for k, v in payload.items() if isinstance(v, dict)}

    def _write_map(self, path: Path, payload: Dict[str, Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def _read_jsonl(self, path: Path) -> List[Dict[str, Any]]:
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

    def list_sessions(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.sessions_path).values())

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.sessions_path).get(session_id)

    def put_session(self, session: Dict[str, Any]) -> Dict[str, Any]:
        session_id = str(session.get("session_id") or session.get("id") or "").strip()
        if not session_id:
            raise ValueError("session_id is required")
        records = self._read_map(self.sessions_path)
        records[session_id] = json.loads(json.dumps(session))
        self._write_map(self.sessions_path, records)
        return records[session_id]

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if self.event_store is not None:
            return self.event_store.append_event(event)
        record = json.loads(json.dumps(event))
        if not str(record.get("session_id") or "").strip():
            raise ValueError("session_id is required")
        if not str(record.get("event_id") or "").strip():
            raise ValueError("event_id is required")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            handle.write("\n")
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
        records = self._read_map(self.controls_path)
        record = json.loads(json.dumps(controls))
        record["session_id"] = session_id
        records[session_id] = record
        self._write_map(self.controls_path, records)
        return record

    def list_previews(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.previews_path).values())

    def get_preview_bundle(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.previews_path).get(session_id)

    def put_preview_bundle(self, session_id: str, bundle: Dict[str, Any]) -> Dict[str, Any]:
        records = self._read_map(self.previews_path)
        record = json.loads(json.dumps(bundle))
        record["session_id"] = session_id
        records[session_id] = record
        self._write_map(self.previews_path, records)
        return record

    def list_replays(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.replays_path).values())

    def get_replay(self, session_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.replays_path).get(session_id)

    def put_replay(self, session_id: str, replay: Dict[str, Any]) -> Dict[str, Any]:
        records = self._read_map(self.replays_path)
        record = json.loads(json.dumps(replay))
        record["session_id"] = session_id
        records[session_id] = record
        self._write_map(self.replays_path, records)
        return record


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

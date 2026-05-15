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


class PostgresWorkerEventStore:
    def __init__(self, dsn: str, table: str = "research_worker_gateway.worker_events", bootstrap: bool = True) -> None:
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
            raise RuntimeError("psycopg is required when RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND=postgres") from exc
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
                    job_id TEXT,
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
        event_id = str(record.get("event_id") or "").strip()
        if not event_id:
            raise ValueError("event_id is required")
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table}
                    (event_id, job_id, event_type, sequence_number, emitted_at, payload)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event_id,
                    record.get("job_id"),
                    record.get("event_type"),
                    record.get("sequence_number"),
                    record.get("emitted_at") or record.get("created_at"),
                    json.dumps(record, ensure_ascii=True, sort_keys=True),
                ),
            )
        return record

    def list_events(self, job_id: str | None = None) -> List[Dict[str, Any]]:
        params: tuple[Any, ...] = ()
        where = ""
        if job_id is not None:
            where = "WHERE job_id = %s"
            params = (job_id,)
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


class ResearchWorkerGatewayStore:
    def __init__(self, data_dir: str | Path, event_store: PostgresWorkerEventStore | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_path = self.data_dir / "worker_jobs.json"
        self.events_path = self.data_dir / "worker_events.jsonl"
        self.outputs_path = self.data_dir / "worker_outputs.json"
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
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _write_map(self, path: Path, payload: Dict[str, Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")

    def list_jobs(self) -> List[Dict[str, Any]]:
        return list(self._read_map(self.jobs_path).values())

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        return self._read_map(self.jobs_path).get(job_id)

    def put_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        job["id"] = job_id
        job["job_id"] = job_id
        jobs = self._read_map(self.jobs_path)
        jobs[job_id] = json.loads(json.dumps(job))
        self._write_map(self.jobs_path, jobs)
        return jobs[job_id]

    def append_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        if self.event_store is not None:
            return self.event_store.append_event(event)
        record = json.loads(json.dumps(event))
        if not str(record.get("event_id") or "").strip():
            raise ValueError("event_id is required")
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
            handle.write("\n")
        return record

    def list_events(self, job_id: str | None = None) -> List[Dict[str, Any]]:
        if self.event_store is not None:
            return self.event_store.list_events(job_id)
        if not self.events_path.exists():
            return []
        records: List[Dict[str, Any]] = []
        for line in self.events_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(payload)
        if job_id is not None:
            records = [record for record in records if record.get("job_id") == job_id]
        return records

    def put_output(self, output: Dict[str, Any]) -> Dict[str, Any]:
        output_id = str(output.get("output_id") or output.get("id") or "").strip()
        if not output_id:
            raise ValueError("output_id is required")
        output["id"] = output_id
        output["output_id"] = output_id
        outputs = self._read_map(self.outputs_path)
        outputs[output_id] = json.loads(json.dumps(output))
        self._write_map(self.outputs_path, outputs)
        return outputs[output_id]

    def list_outputs(self, job_id: str | None = None) -> List[Dict[str, Any]]:
        outputs = list(self._read_map(self.outputs_path).values())
        if job_id is not None:
            outputs = [output for output in outputs if output.get("job_id") == job_id]
        return outputs


def build_research_worker_gateway_store(data_dir: str | Path) -> ResearchWorkerGatewayStore:
    backend = os.getenv("RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return ResearchWorkerGatewayStore(data_dir)
    if backend != "postgres":
        raise ValueError("RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND must be jsonl or postgres")

    dsn = os.getenv("RESEARCH_WORKER_GATEWAY_EVENT_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("RESEARCH_WORKER_GATEWAY_EVENT_STORE_DSN or DATABASE_URL is required for Postgres event store")
    table = os.getenv("RESEARCH_WORKER_GATEWAY_EVENT_STORE_TABLE", "research_worker_gateway.worker_events")
    bootstrap = os.getenv("RESEARCH_WORKER_GATEWAY_EVENT_STORE_BOOTSTRAP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    return ResearchWorkerGatewayStore(
        data_dir,
        event_store=PostgresWorkerEventStore(dsn=dsn, table=table, bootstrap=bootstrap),
    )

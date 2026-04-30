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


class PostgresResearchEventStore:
    def __init__(self, dsn: str, table: str = "research_orchestrator.research_events", bootstrap: bool = True) -> None:
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
            raise RuntimeError("psycopg is required when RESEARCH_ORCHESTRATOR_EVENT_STORE_BACKEND=postgres") from exc
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
                    run_id TEXT,
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
                    (event_id, run_id, event_type, sequence_number, emitted_at, payload)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                """,
                (
                    event_id,
                    record.get("run_id"),
                    record.get("event_type"),
                    record.get("sequence_number"),
                    record.get("emitted_at") or record.get("created_at"),
                    json.dumps(record, ensure_ascii=True, sort_keys=True),
                ),
            )
        return record

    def list_events(self, run_id: str | None = None) -> List[Dict[str, Any]]:
        params: tuple[Any, ...] = ()
        where = ""
        if run_id is not None:
            where = "WHERE run_id = %s"
            params = (run_id,)
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


class ResearchOrchestratorStore:
    def __init__(self, data_dir: str | Path, event_store: PostgresResearchEventStore | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_path = self.data_dir / "research_tasks.json"
        self.runs_path = self.data_dir / "research_runs.json"
        self.artifacts_path = self.data_dir / "research_artifacts.json"
        self.proposals_path = self.data_dir / "research_proposals.json"
        self.events_path = self.data_dir / "research_events.jsonl"
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

    def list_tasks(self) -> List[Dict[str, Any]]:
        return self._list_records(self.tasks_path)

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.tasks_path, task_id)

    def put_task(self, task: Dict[str, Any]) -> Dict[str, Any]:
        task_id = str(task.get("task_id") or task.get("id") or "").strip()
        task["task_id"] = task_id
        task["id"] = task_id
        return self._put_record(self.tasks_path, task_id, task)

    def list_runs(self) -> List[Dict[str, Any]]:
        return self._list_records(self.runs_path)

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.runs_path, run_id)

    def put_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        run_id = str(run.get("run_id") or run.get("id") or "").strip()
        run["run_id"] = run_id
        run["id"] = run_id
        return self._put_record(self.runs_path, run_id, run)

    def list_artifacts(self) -> List[Dict[str, Any]]:
        return self._list_records(self.artifacts_path)

    def get_artifact(self, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.artifacts_path, artifact_id)

    def put_artifact(self, artifact: Dict[str, Any]) -> Dict[str, Any]:
        artifact_id = str(artifact.get("artifact_id") or artifact.get("id") or "").strip()
        artifact["artifact_id"] = artifact_id
        artifact["id"] = artifact_id
        return self._put_record(self.artifacts_path, artifact_id, artifact)

    def list_proposals(self) -> List[Dict[str, Any]]:
        return self._list_records(self.proposals_path)

    def get_proposal(self, proposal_id: str) -> Optional[Dict[str, Any]]:
        return self._get_record(self.proposals_path, proposal_id)

    def put_proposal(self, proposal: Dict[str, Any]) -> Dict[str, Any]:
        proposal_id = str(proposal.get("proposal_id") or proposal.get("id") or "").strip()
        proposal["proposal_id"] = proposal_id
        proposal["id"] = proposal_id
        return self._put_record(self.proposals_path, proposal_id, proposal)

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

    def list_events(self, run_id: str | None = None) -> List[Dict[str, Any]]:
        if self.event_store is not None:
            return self.event_store.list_events(run_id)
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
        if run_id is not None:
            records = [record for record in records if record.get("run_id") == run_id]
        return records


def build_research_orchestrator_store(data_dir: str | Path) -> ResearchOrchestratorStore:
    backend = os.getenv("RESEARCH_ORCHESTRATOR_EVENT_STORE_BACKEND", "jsonl").strip().lower()
    if backend in ("", "jsonl"):
        return ResearchOrchestratorStore(data_dir)
    if backend != "postgres":
        raise ValueError("RESEARCH_ORCHESTRATOR_EVENT_STORE_BACKEND must be jsonl or postgres")

    dsn = os.getenv("RESEARCH_ORCHESTRATOR_EVENT_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("RESEARCH_ORCHESTRATOR_EVENT_STORE_DSN or DATABASE_URL is required for Postgres event store")
    table = os.getenv("RESEARCH_ORCHESTRATOR_EVENT_STORE_TABLE", "research_orchestrator.research_events")
    bootstrap = os.getenv("RESEARCH_ORCHESTRATOR_EVENT_STORE_BOOTSTRAP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )
    return ResearchOrchestratorStore(
        data_dir,
        event_store=PostgresResearchEventStore(dsn=dsn, table=table, bootstrap=bootstrap),
    )

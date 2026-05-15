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


class PostgresPolicyLearningJobStore:
    def __init__(self, dsn: str, table: str = "policy_learning.jobs", bootstrap: bool = True) -> None:
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
            raise RuntimeError("psycopg is required when POLICY_LEARNING_STORE_BACKEND=postgres") from exc
        return psycopg.connect(self.dsn)

    def bootstrap(self) -> None:
        with self._connect() as conn:
            if self.schema:
                conn.execute(f"CREATE SCHEMA IF NOT EXISTS {_quote_pg_identifier(self.schema)}")
            conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self.table} (
                    job_id TEXT PRIMARY KEY,
                    policy_id TEXT,
                    status TEXT,
                    proposed_at TEXT,
                    payload JSONB NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )

    def list_jobs(self) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} ORDER BY proposed_at NULLS LAST, job_id")
            rows = cursor.fetchall()
        records: List[Dict[str, Any]] = []
        for row in rows:
            payload = row[0] if isinstance(row, tuple) else row.get("payload")
            if isinstance(payload, str):
                payload = json.loads(payload)
            if isinstance(payload, dict):
                records.append(payload)
        return records

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            cursor = conn.execute(f"SELECT payload FROM {self.table} WHERE job_id = %s", (job_id,))
            rows = cursor.fetchall()
        if not rows:
            return None
        payload = rows[0][0] if isinstance(rows[0], tuple) else rows[0].get("payload")
        if isinstance(payload, str):
            payload = json.loads(payload)
        return payload if isinstance(payload, dict) else None

    def put_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        record = json.loads(json.dumps(job))
        job_id = str(record.get("job_id") or record.get("id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        record["job_id"] = job_id
        record["id"] = job_id
        with self._connect() as conn:
            conn.execute(
                f"""
                INSERT INTO {self.table} (job_id, policy_id, status, proposed_at, payload)
                VALUES (%s, %s, %s, %s, %s::jsonb)
                ON CONFLICT (job_id) DO UPDATE SET
                    policy_id = EXCLUDED.policy_id,
                    status = EXCLUDED.status,
                    proposed_at = EXCLUDED.proposed_at,
                    payload = EXCLUDED.payload,
                    updated_at = now()
                """,
                (
                    job_id,
                    record.get("policy_id"),
                    record.get("status"),
                    record.get("proposed_at"),
                    json.dumps(record, ensure_ascii=True, sort_keys=True),
                ),
            )
        return record


class PolicyLearningStore:
    def __init__(self, data_dir: str | Path, job_store: PostgresPolicyLearningJobStore | None = None) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.jobs_path = self.data_dir / "policy_learning_jobs.json"
        self.job_store = job_store

    def _read_jobs(self) -> Dict[str, Dict[str, Any]]:
        if not self.jobs_path.exists():
            return {}
        text = self.jobs_path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        payload = json.loads(text)
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items() if isinstance(value, dict)}

    def _write_jobs(self, jobs: Dict[str, Dict[str, Any]]) -> None:
        self.jobs_path.parent.mkdir(parents=True, exist_ok=True)
        self.jobs_path.write_text(json.dumps(jobs, indent=2, ensure_ascii=True), encoding="utf-8")

    def list_jobs(self) -> List[Dict[str, Any]]:
        if self.job_store is not None:
            return self.job_store.list_jobs()
        return list(self._read_jobs().values())

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        if self.job_store is not None:
            return self.job_store.get_job(job_id)
        return self._read_jobs().get(job_id)

    def put_job(self, job: Dict[str, Any]) -> Dict[str, Any]:
        if self.job_store is not None:
            return self.job_store.put_job(job)
        job_id = str(job.get("job_id") or job.get("id") or "").strip()
        if not job_id:
            raise ValueError("job_id is required")
        records = self._read_jobs()
        record = json.loads(json.dumps(job))
        record["job_id"] = job_id
        record["id"] = job_id
        records[job_id] = record
        self._write_jobs(records)
        return record


def build_policy_learning_store(data_dir: str | Path) -> PolicyLearningStore:
    backend = os.getenv("POLICY_LEARNING_STORE_BACKEND", "json").strip().lower()
    if backend in ("", "json", "jsonl"):
        return PolicyLearningStore(data_dir)
    if backend != "postgres":
        raise ValueError("POLICY_LEARNING_STORE_BACKEND must be json or postgres")

    dsn = os.getenv("POLICY_LEARNING_STORE_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("POLICY_LEARNING_STORE_DSN or DATABASE_URL is required for Postgres store")
    table = os.getenv("POLICY_LEARNING_STORE_TABLE", "policy_learning.jobs")
    bootstrap = os.getenv("POLICY_LEARNING_STORE_BOOTSTRAP", "1").strip().lower() not in ("0", "false", "no")
    return PolicyLearningStore(
        data_dir,
        job_store=PostgresPolicyLearningJobStore(dsn=dsn, table=table, bootstrap=bootstrap),
    )

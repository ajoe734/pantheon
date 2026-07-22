"""Durable storage for Agora dashboard recipes."""
from __future__ import annotations

import copy
import json
import logging
import os
import threading
from typing import Any, Dict, List, Optional

BACKEND_ENV = "AGORA_DASHBOARD_STORE_BACKEND"
DSN_ENV = "AGORA_DASHBOARD_STORE_DSN"
SCHEMA_ENV = "AGORA_DASHBOARD_STORE_SCHEMA"
DEFAULT_SCHEMA = "agora"
_logger = logging.getLogger(__name__)


class MemoryDashboardRecipeStore:
    def __init__(self) -> None:
        self.identities: Dict[str, dict] = {}
        self.versions: Dict[tuple[str, int], dict] = {}
        self.feedback: List[dict] = []
        self.idempotency_keys: Dict[str, str] = {}
        self._lock = threading.RLock()

    def list_identities(self) -> List[dict]:
        with self._lock:
            return copy.deepcopy(list(self.identities.values()))

    def get_identity(self, recipe_id: str) -> Optional[dict]:
        with self._lock:
            value = self.identities.get(recipe_id)
            return copy.deepcopy(value) if value else None

    def get_version(self, recipe_id: str, version: int) -> Optional[dict]:
        with self._lock:
            value = self.versions.get((recipe_id, version))
            return copy.deepcopy(value) if value else None

    def list_versions(self, recipe_id: str) -> List[dict]:
        with self._lock:
            return copy.deepcopy([v for (rid, _), v in self.versions.items() if rid == recipe_id])

    def create_recipe(self, identity: dict, version: dict, idempotency_key: Optional[str] = None) -> str:
        with self._lock:
            if idempotency_key and idempotency_key in self.idempotency_keys:
                return self.idempotency_keys[idempotency_key]
            self.identities[identity["recipe_id"]] = copy.deepcopy(identity)
            self.versions[(version["recipe_id"], version["version"])] = copy.deepcopy(version)
            if idempotency_key:
                self.idempotency_keys[idempotency_key] = identity["recipe_id"]
            return identity["recipe_id"]

    def append_version(self, recipe_id: str, expected_version: int, version: dict,
                       idempotency_key: Optional[str] = None) -> bool:
        with self._lock:
            identity = self.identities.get(recipe_id)
            if not identity or identity["active_version"] != expected_version:
                return False
            self.versions[(recipe_id, version["version"])] = copy.deepcopy(version)
            identity["active_version"] = version["version"]
            if idempotency_key:
                self.idempotency_keys[idempotency_key] = recipe_id
            return True

    def has_idempotency_key(self, key: Optional[str]) -> bool:
        with self._lock:
            return bool(key and key in self.idempotency_keys)

    def add_feedback(self, feedback: dict) -> None:
        with self._lock:
            self.feedback.append(copy.deepcopy(feedback))


class PostgresDashboardRecipeStore:
    def __init__(self, dsn: str, schema: str = DEFAULT_SCHEMA) -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required")
        if not schema.replace("_", "").isalnum():
            raise ValueError("Invalid Postgres schema")
        try:
            import psycopg
        except ImportError as exc:
            raise RuntimeError("psycopg is required for the Postgres dashboard store") from exc
        self._psycopg = psycopg
        self._dsn = dsn
        self._schema = schema
        self._bootstrap()

    def _connect(self):
        return self._psycopg.connect(self._dsn)

    def _bootstrap(self) -> None:
        s = self._schema
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'CREATE SCHEMA IF NOT EXISTS "{s}"')
            cur.execute(f'''CREATE TABLE IF NOT EXISTS "{s}".dashboard_recipe_identity (
                recipe_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, user_id TEXT NOT NULL,
                strategy_id TEXT NOT NULL, active_version INTEGER NOT NULL, created_at TEXT NOT NULL)''')
            cur.execute(f'''CREATE TABLE IF NOT EXISTS "{s}".dashboard_recipe_version (
                recipe_id TEXT NOT NULL, version INTEGER NOT NULL, record_json JSONB NOT NULL,
                PRIMARY KEY (recipe_id, version), FOREIGN KEY (recipe_id)
                REFERENCES "{s}".dashboard_recipe_identity(recipe_id) ON DELETE CASCADE)''')
            cur.execute(f'''CREATE TABLE IF NOT EXISTS "{s}".dashboard_recipe_idempotency (
                idempotency_key TEXT PRIMARY KEY, recipe_id TEXT NOT NULL)''')
            cur.execute(f'''CREATE TABLE IF NOT EXISTS "{s}".dashboard_recipe_feedback (
                feedback_id BIGSERIAL PRIMARY KEY, recipe_id TEXT NOT NULL, record_json JSONB NOT NULL)''')

    @staticmethod
    def _decode(value: Any) -> dict:
        return value if isinstance(value, dict) else json.loads(value)

    def list_identities(self) -> List[dict]:
        s = self._schema
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'SELECT recipe_id, tenant_id, user_id, strategy_id, active_version, created_at FROM "{s}".dashboard_recipe_identity')
            return [dict(zip(("recipe_id", "tenant_id", "user_id", "strategy_id", "active_version", "created_at"), row)) for row in cur.fetchall()]

    def get_identity(self, recipe_id: str) -> Optional[dict]:
        return next((row for row in self.list_identities() if row["recipe_id"] == recipe_id), None)

    def get_version(self, recipe_id: str, version: int) -> Optional[dict]:
        s = self._schema
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'SELECT record_json FROM "{s}".dashboard_recipe_version WHERE recipe_id=%s AND version=%s', (recipe_id, version))
            row = cur.fetchone()
            return self._decode(row[0]) if row else None

    def list_versions(self, recipe_id: str) -> List[dict]:
        s = self._schema
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'SELECT record_json FROM "{s}".dashboard_recipe_version WHERE recipe_id=%s ORDER BY version', (recipe_id,))
            return [self._decode(row[0]) for row in cur.fetchall()]

    def create_recipe(self, identity: dict, version: dict, idempotency_key: Optional[str] = None) -> str:
        s = self._schema
        with self._connect() as conn, conn.cursor() as cur:
            if idempotency_key:
                cur.execute(f'''INSERT INTO "{s}".dashboard_recipe_idempotency
                    (idempotency_key,recipe_id) VALUES (%s,%s)
                    ON CONFLICT (idempotency_key) DO NOTHING RETURNING recipe_id''',
                    (idempotency_key, identity["recipe_id"]))
                reserved = cur.fetchone()
                if reserved is None:
                    cur.execute(f'SELECT recipe_id FROM "{s}".dashboard_recipe_idempotency WHERE idempotency_key=%s',
                                (idempotency_key,))
                    return cur.fetchone()[0]
            cur.execute(f'''INSERT INTO "{s}".dashboard_recipe_identity
                (recipe_id,tenant_id,user_id,strategy_id,active_version,created_at) VALUES (%s,%s,%s,%s,%s,%s)''',
                tuple(identity[k] for k in ("recipe_id", "tenant_id", "user_id", "strategy_id", "active_version", "created_at")))
            cur.execute(f'INSERT INTO "{s}".dashboard_recipe_version (recipe_id,version,record_json) VALUES (%s,%s,%s::jsonb)',
                        (version["recipe_id"], version["version"], json.dumps(version)))
            return identity["recipe_id"]

    def append_version(self, recipe_id: str, expected_version: int, version: dict,
                       idempotency_key: Optional[str] = None) -> bool:
        s = self._schema
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'UPDATE "{s}".dashboard_recipe_identity SET active_version=%s WHERE recipe_id=%s AND active_version=%s',
                        (version["version"], recipe_id, expected_version))
            if cur.rowcount != 1:
                return False
            cur.execute(f'INSERT INTO "{s}".dashboard_recipe_version (recipe_id,version,record_json) VALUES (%s,%s,%s::jsonb)',
                        (recipe_id, version["version"], json.dumps(version)))
            if idempotency_key:
                cur.execute(f'INSERT INTO "{s}".dashboard_recipe_idempotency (idempotency_key,recipe_id) VALUES (%s,%s) ON CONFLICT DO NOTHING', (idempotency_key, recipe_id))
            return True

    def has_idempotency_key(self, key: Optional[str]) -> bool:
        if not key:
            return False
        s = self._schema
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'SELECT 1 FROM "{s}".dashboard_recipe_idempotency WHERE idempotency_key=%s', (key,))
            return cur.fetchone() is not None

    def add_feedback(self, feedback: dict) -> None:
        s = self._schema
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f'INSERT INTO "{s}".dashboard_recipe_feedback (recipe_id,record_json) VALUES (%s,%s::jsonb)', (feedback["recipe_id"], json.dumps(feedback)))


def make_dashboard_recipe_store(backend: Optional[str] = None, dsn: Optional[str] = None,
                                schema: Optional[str] = None) -> Any:
    resolved = (backend or os.getenv(BACKEND_ENV, "off")).strip().lower()
    if resolved in {"", "off", "false", "disabled", "none", ":memory:"}:
        store = MemoryDashboardRecipeStore()
    elif resolved == "postgres":
        resolved_dsn = dsn or os.getenv(DSN_ENV, "")
        if not resolved_dsn:
            raise RuntimeError(f"{DSN_ENV} must be set when {BACKEND_ENV}=postgres")
        store = PostgresDashboardRecipeStore(resolved_dsn, schema or os.getenv(SCHEMA_ENV, DEFAULT_SCHEMA))
    else:
        raise RuntimeError(f"Unknown {BACKEND_ENV}={resolved!r}; expected 'off' or 'postgres'")
    _logger.info("Agora dashboard recipe store initialized backend=%s store=%s", resolved, type(store).__name__)
    return store

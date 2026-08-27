"""Durable authority for daily Persona interaction lifecycle records.

The deployed backend mirrors the Agora governance store: when governance is
Postgres, interactions are Postgres too.  The memory implementation exists
only for isolated tests.  Workshop events/cards and SSE are projections from
the durable outbox and are never the authoritative interaction record.
"""
from __future__ import annotations

import copy
import base64
import json
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional


_SCHEMA_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class InteractionConflict(RuntimeError):
    """The caller reused an immutable identity with different content."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _decode(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return copy.deepcopy(value)
    return json.loads(value)


def _timestamp(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    return str(value)


class InteractionLifecycleStore:
    """Interaction aggregate with atomic invocation claims and durable outbox."""

    def __init__(
        self,
        *,
        backend: str = "memory",
        dsn: str = "",
        schema: str = "agora",
        storage_filepath: Optional[str] = None,
    ) -> None:
        self.backend = "memory" if backend in {"", "off", "memory", "none"} else backend
        self.storage_filepath = storage_filepath
        if self.backend not in {"memory", "postgres"}:
            raise RuntimeError("interaction lifecycle backend must be memory or postgres")
        self._lock = threading.RLock()
        self._requests: Dict[str, Dict[str, Any]] = {}
        self._idempotency: Dict[str, tuple[str, str]] = {}
        self._invocations: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._syntheses: Dict[str, Optional[Dict[str, Any]]] = {}
        self._outbox: Dict[str, Dict[str, Any]] = {}
        self._candidate_links: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._audits: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._retry_commands: Dict[str, Dict[str, Any]] = {}
        self._context_bindings: Dict[str, Dict[str, Any]] = {}
        self._context_binding_latest: Dict[str, str] = {}
        if self.backend == "memory":
            if self.storage_filepath and os.path.exists(self.storage_filepath):
                self._load_from_disk()
            return
        if not dsn:
            raise RuntimeError("Postgres DSN is required for interaction lifecycle storage")
        if not _SCHEMA_RE.fullmatch(schema):
            raise ValueError("unsafe interaction lifecycle Postgres schema")
        self.dsn = dsn
        self.schema = schema
        q = f'"{schema}"'
        self._request_table = f'{q}."persona_interaction_request"'
        self._invocation_table = f'{q}."persona_interaction_invocation"'
        self._synthesis_table = f'{q}."persona_interaction_synthesis"'
        self._outbox_table = f'{q}."persona_interaction_outbox"'
        self._candidate_table = f'{q}."persona_interaction_candidate_link"'
        self._audit_table = f'{q}."persona_interaction_audit"'
        self._retry_table = f'{q}."persona_interaction_retry_command"'
        self._context_table = f'{q}."persona_interaction_context_binding"'
        self._bootstrap()

    def _persist_locked(self) -> None:
        if not self.storage_filepath or self.backend != "memory":
            return
        data = {
            "requests": self._requests,
            "idempotency": {k: list(v) for k, v in self._idempotency.items()},
            "invocations": self._invocations,
            "syntheses": self._syntheses,
            "outbox": self._outbox,
            "candidate_links": self._candidate_links,
            "audits": self._audits,
            "retry_commands": self._retry_commands,
            "context_bindings": self._context_bindings,
            "context_binding_latest": self._context_binding_latest,
        }
        parent = os.path.dirname(self.storage_filepath)
        if parent:
            os.makedirs(parent, exist_ok=True)
        import uuid as _uuid
        tmp_file = f"{self.storage_filepath}.tmp.{_uuid.uuid4().hex}"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, default=str, indent=2)
        os.replace(tmp_file, self.storage_filepath)

    def _load_from_disk(self) -> None:
        try:
            with open(self.storage_filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._requests = data.get("requests", {})
            self._idempotency = {k: tuple(v) for k, v in data.get("idempotency", {}).items()}
            self._invocations = data.get("invocations", {})
            self._syntheses = data.get("syntheses", {})
            self._outbox = data.get("outbox", {})
            self._candidate_links = data.get("candidate_links", {})
            self._audits = data.get("audits", {})
            self._retry_commands = data.get("retry_commands", {})
            self._context_bindings = data.get("context_bindings", {})
            self._context_binding_latest = data.get("context_binding_latest", {})
        except Exception:
            pass

    @classmethod
    def from_governance_store(cls, governance_store: Any) -> "InteractionLifecycleStore":
        if getattr(governance_store, "backend", "memory") == "postgres":
            return cls(
                backend="postgres",
                dsn=str(getattr(governance_store, "dsn", "")),
                schema=str(getattr(governance_store, "schema", "agora")),
            )
        environment = os.getenv("PANTHEON_ENV", "").strip().lower()
        if environment in {"staging", "staging-live", "prod", "production"}:
            raise RuntimeError(
                "Postgres Agora governance storage is required for durable Persona interactions "
                f"when PANTHEON_ENV={environment}"
            )
        return cls(backend="memory")

    def _connect(self) -> Any:
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:  # pragma: no cover - deployment dependency
            raise RuntimeError("psycopg is required for interaction lifecycle storage") from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._request_table} (
                    interaction_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    workshop_id TEXT NOT NULL,
                    idempotency_scope TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    request_json JSONB NOT NULL,
                    status TEXT NOT NULL,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    trace_id TEXT NOT NULL,
                    lease_owner TEXT,
                    lease_until TIMESTAMPTZ,
                    demo_run_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    UNIQUE (idempotency_scope, idempotency_key),
                    CHECK (status IN ('queued','running','completed','degraded','failed'))
                )
            """)
            conn.execute(f'ALTER TABLE {self._request_table} ADD COLUMN IF NOT EXISTS lease_owner TEXT')
            conn.execute(f'ALTER TABLE {self._request_table} ADD COLUMN IF NOT EXISTS lease_until TIMESTAMPTZ')
            conn.execute(f'ALTER TABLE {self._request_table} ADD COLUMN IF NOT EXISTS demo_run_id TEXT')
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS ix_persona_interaction_scope_created
                ON {self._request_table} (tenant_id, owner_user_id, created_at DESC, interaction_id)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS ix_persona_interaction_recovery
                ON {self._request_table}
                    (tenant_id, owner_user_id, status, created_at, interaction_id)
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS ix_persona_interaction_claim
                ON {self._request_table} (status, lease_until, created_at)
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._invocation_table} (
                    invocation_id TEXT PRIMARY KEY,
                    interaction_id TEXT NOT NULL REFERENCES {self._request_table}(interaction_id),
                    persona_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    lease_owner TEXT,
                    lease_until TIMESTAMPTZ,
                    invocation_json JSONB NOT NULL,
                    opinion_json JSONB,
                    error_json JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CHECK (status IN ('queued','running','succeeded','failed'))
                )
            """)
            # Each retry is a distinct, immutable provider invocation.  Older
            # development builds briefly installed a one-Persona-per-request
            # constraint; remove it before accepting retry attempts.
            conn.execute(
                f'ALTER TABLE {self._invocation_table} DROP CONSTRAINT IF EXISTS '
                'persona_interaction_invocation_interaction_id_persona_id_key'
            )
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._synthesis_table} (
                    interaction_id TEXT PRIMARY KEY REFERENCES {self._request_table}(interaction_id),
                    synthesis_json JSONB,
                    missing_participant_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    degraded_participant_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._outbox_table} (
                    outbox_id TEXT PRIMARY KEY,
                    interaction_id TEXT NOT NULL REFERENCES {self._request_table}(interaction_id),
                    projection_kind TEXT NOT NULL,
                    payload_json JSONB NOT NULL,
                    state TEXT NOT NULL DEFAULT 'pending',
                    attempt INTEGER NOT NULL DEFAULT 0,
                    lease_until TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    CHECK (state IN ('pending','processing','completed'))
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS ix_persona_interaction_outbox_pending
                ON {self._outbox_table} (state, created_at, outbox_id)
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._candidate_table} (
                    interaction_id TEXT NOT NULL REFERENCES {self._request_table}(interaction_id),
                    proposal_id TEXT NOT NULL,
                    link_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (interaction_id, proposal_id)
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._audit_table} (
                    audit_id TEXT PRIMARY KEY,
                    interaction_id TEXT NOT NULL REFERENCES {self._request_table}(interaction_id),
                    audit_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._retry_table} (
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    interaction_id TEXT NOT NULL REFERENCES {self._request_table}(interaction_id),
                    request_fingerprint TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    PRIMARY KEY (tenant_id, owner_user_id, idempotency_key)
                )
            """)
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {self._context_table} (
                    binding_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    owner_user_id TEXT NOT NULL,
                    workshop_id TEXT NOT NULL,
                    context_digest TEXT NOT NULL,
                    binding_json JSONB NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                    UNIQUE (tenant_id, owner_user_id, workshop_id, context_digest)
                )
            """)
            conn.execute(f"""
                CREATE INDEX IF NOT EXISTS ix_persona_interaction_context_latest
                ON {self._context_table} (tenant_id, owner_user_id, workshop_id, created_at DESC)
            """)

    def save_context_binding(self, binding: Dict[str, Any], *, owner_user_id: str) -> Dict[str, Any]:
        binding_id = str(binding["binding_id"])
        scope = f"{binding['tenant_id']}:{owner_user_id}:{binding['workshop_id']}"
        if self.backend == "memory":
            with self._lock:
                existing = self._context_bindings.get(binding_id)
                if existing and existing != binding:
                    raise InteractionConflict("context binding identity reused with different content")
                if existing is None:
                    self._context_bindings[binding_id] = copy.deepcopy(binding)
                    self._context_binding_latest[scope] = binding_id
                    self._persist_locked()
                return copy.deepcopy(binding)
        with self._connect() as conn:
            inserted = conn.execute(
                f"INSERT INTO {self._context_table} "
                "(binding_id,tenant_id,owner_user_id,workshop_id,context_digest,binding_json) "
                "VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT (binding_id) DO NOTHING RETURNING binding_id",
                (binding_id, binding["tenant_id"], owner_user_id, binding["workshop_id"],
                 binding["context_digest"], json.dumps(binding, default=str)),
            ).fetchone()
            if inserted is None:
                existing = conn.execute(
                    f"SELECT binding_json FROM {self._context_table} WHERE binding_id=%s", (binding_id,),
                ).fetchone()
                if not existing or _decode(existing[0]) != binding:
                    raise InteractionConflict("context binding identity reused with different content")
        return copy.deepcopy(binding)

    def latest_context_binding(self, tenant_id: str, user_id: str, workshop_id: str) -> Optional[Dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                binding_id = self._context_binding_latest.get(f"{tenant_id}:{user_id}:{workshop_id}")
                return copy.deepcopy(self._context_bindings.get(binding_id)) if binding_id else None
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT binding_json FROM {self._context_table} WHERE tenant_id=%s AND owner_user_id=%s "
                "AND workshop_id=%s ORDER BY created_at DESC,binding_id DESC LIMIT 1",
                (tenant_id, user_id, workshop_id),
            ).fetchone()
        return _decode(row[0]) if row else None

    def matching_context_binding(
        self,
        tenant_id: str,
        user_id: str,
        workshop_id: str,
        submitted_snapshot: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Resolve the exact receipt, never the most recent browser tab."""
        exact_keys = (
            "tenant_id", "source_route", "focused_object", "context_refs", "strategy_ref",
            "decision_ref", "journal_ref", "position_risk_snapshot_refs", "evidence_cutoff",
            "selected_persona_ids", "initial_mode", "return_route",
        )
        if self.backend == "memory":
            with self._lock:
                candidates = list(self._context_bindings.values())
        else:
            with self._connect() as conn:
                rows = conn.execute(
                    f"SELECT binding_json FROM {self._context_table} WHERE tenant_id=%s "
                    "AND owner_user_id=%s AND workshop_id=%s ORDER BY created_at DESC,binding_id DESC",
                    (tenant_id, user_id, workshop_id),
                ).fetchall()
            candidates = [_decode(row[0]) for row in rows]
        for binding in candidates:
            if all(submitted_snapshot.get(key) == binding.get(key) for key in exact_keys):
                return copy.deepcopy(binding)
        return None

    def create_request(
        self,
        record: Dict[str, Any],
        *,
        idempotency_scope: str,
        idempotency_key: str,
        fingerprint: str,
        trace_id: str,
    ) -> tuple[Dict[str, Any], bool]:
        interaction_id = str(record["interaction_id"])
        compound = f"{idempotency_scope}:{idempotency_key}"
        demo_run_id = record.get("demo_run_id")
        if self.backend == "memory":
            with self._lock:
                replay = self._idempotency.get(compound)
                if replay:
                    if replay[0] != fingerprint:
                        raise InteractionConflict("idempotency key reused with a different interaction payload")
                    return self.get(replay[1], record["tenant_id"], record["owner_user_id"]), False  # type: ignore[return-value]
                existing = self._requests.get(interaction_id)
                if existing:
                    if existing["request_fingerprint"] != fingerprint:
                        raise InteractionConflict("interaction_id reused with different immutable content")
                    return self.get(interaction_id, record["tenant_id"], record["owner_user_id"]), False  # type: ignore[return-value]
                saved = copy.deepcopy(record)
                saved.update({"request_fingerprint": fingerprint, "trace_id": trace_id, "demo_run_id": demo_run_id, "lease_owner": None, "lease_until": None})
                self._requests[interaction_id] = saved
                self._idempotency[compound] = (fingerprint, interaction_id)
                self._audit_locked(interaction_id, f"audit:{interaction_id}:submitted", {
                    "audit_id": f"audit:{interaction_id}:submitted", "action": "interaction_submitted",
                    "actor_id": saved["human_request"]["operator_id"], "occurred_at": saved["created_at"],
                })
                self._enqueue_locked(interaction_id, {
                    "outbox_id": f"iob:interaction_queued:{interaction_id}",
                    "projection_kind": "interaction_queued",
                    "payload": {
                        "interaction_id": interaction_id,
                        "workshop_id": record["workshop_id"],
                        "tenant_id": record["tenant_id"],
                        "owner_user_id": record["owner_user_id"],
                        "status": "queued",
                        "created_at": record["created_at"],
                        "trace_id": trace_id,
                        "demo_run_id": demo_run_id,
                    },
                })
                self._persist_locked()
                return self._materialize_locked(interaction_id), True
        with self._connect() as conn:
            inserted = conn.execute(
                f"INSERT INTO {self._request_table} "
                "(interaction_id,tenant_id,owner_user_id,workshop_id,idempotency_scope,idempotency_key,"
                "request_fingerprint,request_json,status,trace_id,demo_run_id,created_at,updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'queued',%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING interaction_id",
                (interaction_id, record["tenant_id"], record["owner_user_id"], record["workshop_id"],
                 idempotency_scope, idempotency_key, fingerprint, json.dumps(record, default=str),
                 trace_id, demo_run_id, record["created_at"], record["updated_at"]),
            ).fetchone()
            created = inserted is not None
            existing = conn.execute(
                f"SELECT interaction_id,request_fingerprint FROM {self._request_table} "
                "WHERE idempotency_scope=%s AND idempotency_key=%s FOR UPDATE",
                (idempotency_scope, idempotency_key),
            ).fetchone()
            if existing is None:
                existing = conn.execute(
                    f"SELECT interaction_id,request_fingerprint FROM {self._request_table} "
                    "WHERE interaction_id=%s FOR UPDATE", (interaction_id,),
                ).fetchone()
            if existing is None:  # pragma: no cover - transaction corruption guard
                raise RuntimeError("interaction insert conflict has no winner")
            if existing[1] != fingerprint:
                raise InteractionConflict("idempotency or interaction identity reused with different content")
            existing_id = existing[0]
            if created:
                self._insert_audit_pg(conn, interaction_id, f"audit:{interaction_id}:submitted", {
                    "audit_id": f"audit:{interaction_id}:submitted", "action": "interaction_submitted",
                    "actor_id": record["human_request"]["operator_id"], "occurred_at": record["created_at"],
                })
                self._enqueue_pg(conn, interaction_id, {
                    "outbox_id": f"iob:interaction_queued:{interaction_id}",
                    "projection_kind": "interaction_queued",
                    "payload": {
                        "interaction_id": interaction_id,
                        "workshop_id": record["workshop_id"],
                        "tenant_id": record["tenant_id"],
                        "owner_user_id": record["owner_user_id"],
                        "status": "queued",
                        "created_at": record["created_at"],
                        "trace_id": trace_id,
                        "demo_run_id": demo_run_id,
                    },
                })
        loaded = self.get(existing_id, record["tenant_id"], record["owner_user_id"])
        if loaded is None:  # pragma: no cover - defensive corruption guard
            raise RuntimeError("interaction request disappeared after commit")
        return loaded, created

    def mark_running(self, interaction_id: str) -> None:
        if self.backend == "memory":
            with self._lock:
                if self._requests[interaction_id]["status"] == "queued":
                    self._requests[interaction_id]["status"] = "running"
                    self._requests[interaction_id]["updated_at"] = _now()
                    self._persist_locked()
            return
        with self._connect() as conn:
            conn.execute(
                f"UPDATE {self._request_table} SET status='running',updated_at=now() "
                "WHERE interaction_id=%s AND status='queued'", (interaction_id,),
            )

    def claim_invocation(
        self,
        interaction_id: str,
        invocation: Dict[str, Any],
        *,
        lease_owner: str,
        lease_duration_seconds: int = 300,
    ) -> tuple[Dict[str, Any], bool]:
        invocation_id = str(invocation["invocation_id"])
        persona_id = str(invocation["participant"]["persona_id"])
        if self.backend == "memory":
            with self._lock:
                now_dt = datetime.now(timezone.utc)
                lease_until_str = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat().replace("+00:00", "Z")
                bucket = self._invocations.setdefault(interaction_id, {})
                row = bucket.get(invocation_id)
                if row is None:
                    row = {
                        "invocation": copy.deepcopy(invocation),
                        "opinion": None,
                        "error": None,
                        "status": "running",
                        "attempt": 1,
                        "lease_owner": lease_owner,
                        "lease_until": lease_until_str,
                    }
                    row["invocation"]["status"] = "running"
                    bucket[invocation_id] = row
                    self._persist_locked()
                    return copy.deepcopy(row), True

                if row["invocation"]["participant"]["persona_id"] != persona_id:
                    raise InteractionConflict("invocation identity reused for a different Persona")

                l_until = row.get("lease_until")
                expired = False
                if l_until:
                    try:
                        expired = datetime.fromisoformat(str(l_until).replace("Z", "+00:00")) < now_dt
                    except Exception:
                        expired = True
                else:
                    expired = True

                # ONLY claim if queued or running with an expired lease
                if row["status"] == "queued" or (row["status"] == "running" and expired):
                    row.update({
                        "status": "running",
                        "attempt": row["attempt"] + 1,
                        "lease_owner": lease_owner,
                        "lease_until": lease_until_str,
                        "invocation": copy.deepcopy(invocation),
                    })
                    row["invocation"]["status"] = "running"
                    self._persist_locked()
                    return copy.deepcopy(row), True

                return copy.deepcopy(row), False

        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO {self._invocation_table} "
                "(invocation_id,interaction_id,persona_id,status,invocation_json) "
                "VALUES (%s,%s,%s,'queued',%s::jsonb) ON CONFLICT (invocation_id) DO NOTHING",
                (invocation_id, interaction_id, persona_id, json.dumps(invocation, default=str)),
            )
            claimed = conn.execute(
                f"UPDATE {self._invocation_table} SET status='running',attempt=attempt+1,lease_owner=%s,"
                f"lease_until=now()+interval '{int(lease_duration_seconds)} seconds',invocation_json=%s::jsonb,updated_at=now() "
                "WHERE invocation_id=%s AND interaction_id=%s AND "
                "(status='queued' OR (status='running' AND (lease_until IS NULL OR lease_until < now()))) RETURNING invocation_id",
                (lease_owner, json.dumps(invocation, default=str), invocation_id, interaction_id),
            ).fetchone()
            row = conn.execute(
                f"SELECT status,attempt,lease_owner,invocation_json,opinion_json,error_json "
                f"FROM {self._invocation_table} WHERE invocation_id=%s AND interaction_id=%s",
                (invocation_id, interaction_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("invocation claim disappeared")
            inv = _decode(row[3])
            inv["status"] = row[0]
            if row[5] is not None:
                inv["error"] = _decode(row[5])
            return {
                "status": row[0],
                "attempt": row[1],
                "lease_owner": row[2],
                "invocation": inv,
                "opinion": _decode(row[4]) if row[4] is not None else None,
                "error": _decode(row[5]) if row[5] is not None else None,
            }, bool(claimed)

    def finish_invocation(
        self,
        interaction_id: str,
        *,
        invocation: Dict[str, Any],
        opinion: Optional[Dict[str, Any]],
        error: Optional[Dict[str, Any]],
        outbox: List[Dict[str, Any]],
        lease_owner: Optional[str] = None,
    ) -> bool:
        invocation_id = str(invocation["invocation_id"])
        status = str(invocation["status"])
        if status not in {"succeeded", "failed"}:
            raise ValueError("terminal invocation status is required")
        if self.backend == "memory":
            with self._lock:
                bucket = self._invocations.get(interaction_id, {})
                row = bucket.get(invocation_id)
                if row is None:
                    raise RuntimeError("invocation completion has no claim")

                req = self._requests.get(interaction_id)

                if lease_owner is not None:
                    if row.get("lease_owner") is not None and row.get("lease_owner") != lease_owner:
                        return False
                    if req and req.get("lease_owner") is not None and req.get("lease_owner") != lease_owner:
                        return False
                    if row["status"] in {"succeeded", "failed"}:
                        if row["invocation"] != invocation or row["opinion"] != opinion:
                            return False
                        return True
                else:
                    if row["status"] in {"succeeded", "failed"}:
                        if row["invocation"] != invocation or row["opinion"] != opinion:
                            raise InteractionConflict("terminal invocation cannot be overwritten")
                        return True

                row.update({
                    "status": status,
                    "invocation": copy.deepcopy(invocation),
                    "opinion": copy.deepcopy(opinion),
                    "error": copy.deepcopy(error),
                    "lease_owner": None,
                    "lease_until": None,
                })
                for item in outbox:
                    self._enqueue_locked(interaction_id, item)
                self._audit_locked(interaction_id, f"audit:{invocation_id}:{status}", {
                    "audit_id": f"audit:{invocation_id}:{status}",
                    "action": f"provider_invocation_{status}",
                    "provider_invocation_id": invocation_id,
                    "persona_id": invocation["participant"]["persona_id"],
                    "occurred_at": invocation.get("completed_at") or _now(),
                    "error": error,
                })
                self._persist_locked()
                return True

        with self._connect() as conn:
            current = conn.execute(
                f"SELECT status,invocation_json,opinion_json,lease_owner FROM {self._invocation_table} "
                "WHERE interaction_id=%s AND invocation_id=%s FOR UPDATE", (interaction_id, invocation_id),
            ).fetchone()
            if current is None:
                raise RuntimeError("invocation completion has no claim")

            current_status = current[0]
            current_lease_owner = current[3]

            if lease_owner is not None:
                if current_lease_owner is not None and current_lease_owner != lease_owner:
                    return False
                req_row = conn.execute(
                    f"SELECT lease_owner FROM {self._request_table} WHERE interaction_id=%s FOR UPDATE",
                    (interaction_id,),
                ).fetchone()
                if req_row and req_row[0] is not None and req_row[0] != lease_owner:
                    return False
                if current_status in {"succeeded", "failed"}:
                    if _decode(current[1]) != invocation or (
                        _decode(current[2]) if current[2] is not None else None
                    ) != opinion:
                        return False
                    return True
            else:
                if current_status in {"succeeded", "failed"}:
                    if _decode(current[1]) != invocation or (
                        _decode(current[2]) if current[2] is not None else None
                    ) != opinion:
                        raise InteractionConflict("terminal invocation cannot be overwritten")
                    return True

            conn.execute(
                f"UPDATE {self._invocation_table} SET status=%s,lease_owner=NULL,lease_until=NULL,"
                "invocation_json=%s::jsonb,opinion_json=%s::jsonb,error_json=%s::jsonb,updated_at=now() "
                "WHERE interaction_id=%s AND invocation_id=%s",
                (status, json.dumps(invocation, default=str), json.dumps(opinion, default=str),
                 json.dumps(error, default=str), interaction_id, invocation_id),
            )
            for item in outbox:
                self._enqueue_pg(conn, interaction_id, item)
            self._insert_audit_pg(conn, interaction_id, f"audit:{invocation_id}:{status}", {
                "audit_id": f"audit:{invocation_id}:{status}", "action": f"provider_invocation_{status}",
                "provider_invocation_id": invocation_id, "persona_id": invocation["participant"]["persona_id"],
                "occurred_at": invocation.get("completed_at") or _now(), "error": error,
            })
            return True

    def finalize(
        self,
        interaction_id: str,
        *,
        status: str,
        synthesis: Optional[Dict[str, Any]],
        missing_participant_ids: List[str],
        degraded_participant_ids: List[str],
        outbox: List[Dict[str, Any]],
        lease_owner: Optional[str] = None,
    ) -> bool:
        if status not in {"completed", "degraded", "failed"}:
            raise ValueError("invalid terminal interaction status")
        if self.backend == "memory":
            with self._lock:
                request = self._requests.get(interaction_id)
                if request is None:
                    raise KeyError(interaction_id)

                if lease_owner is not None:
                    if request.get("lease_owner") is not None and request.get("lease_owner") != lease_owner:
                        return False
                    if request.get("status") in {"completed", "degraded", "failed"}:
                        return False

                self._syntheses[interaction_id] = copy.deepcopy(synthesis)
                request.update({
                    "status": status,
                    "lease_owner": None,
                    "lease_until": None,
                    "missing_participant_ids": list(missing_participant_ids),
                    "degraded_participant_ids": list(degraded_participant_ids),
                    "updated_at": _now(),
                })
                for item in outbox:
                    self._enqueue_locked(interaction_id, item)
                attempt = int(request.get("retry_count", 0))
                audit_id = f"audit:{interaction_id}:attempt:{attempt}:final:{status}"
                self._audit_locked(interaction_id, audit_id, {
                    "audit_id": audit_id,
                    "action": "interaction_finalized",
                    "attempt": attempt,
                    "status": status,
                    "occurred_at": request["updated_at"],
                })
                self._persist_locked()
                return True

        with self._connect() as conn:
            request_row = conn.execute(
                f"SELECT retry_count, lease_owner, status FROM {self._request_table} WHERE interaction_id=%s FOR UPDATE",
                (interaction_id,),
            ).fetchone()
            if request_row is None:
                raise KeyError(interaction_id)
            attempt = int(request_row[0])
            current_lease_owner = request_row[1]
            current_status = request_row[2]

            if lease_owner is not None:
                if current_lease_owner is not None and current_lease_owner != lease_owner:
                    return False
                if current_status in {"completed", "degraded", "failed"}:
                    return False

            conn.execute(
                f"INSERT INTO {self._synthesis_table} "
                "(interaction_id,synthesis_json,missing_participant_ids,degraded_participant_ids) "
                "VALUES (%s,%s::jsonb,%s::jsonb,%s::jsonb) ON CONFLICT (interaction_id) DO UPDATE SET "
                "synthesis_json=EXCLUDED.synthesis_json,missing_participant_ids=EXCLUDED.missing_participant_ids,"
                "degraded_participant_ids=EXCLUDED.degraded_participant_ids,updated_at=now()",
                (interaction_id, json.dumps(synthesis, default=str), json.dumps(missing_participant_ids),
                 json.dumps(degraded_participant_ids)),
            )
            conn.execute(
                f"UPDATE {self._request_table} SET status=%s,lease_owner=NULL,lease_until=NULL,updated_at=now() WHERE interaction_id=%s",
                (status, interaction_id),
            )
            for item in outbox:
                self._enqueue_pg(conn, interaction_id, item)
            audit_id = f"audit:{interaction_id}:attempt:{attempt}:final:{status}"
            self._insert_audit_pg(conn, interaction_id, audit_id, {
                "audit_id": audit_id,
                "action": "interaction_finalized",
                "attempt": attempt,
                "status": status,
                "occurred_at": _now(),
            })
            return True

    def prepare_retry(
        self,
        interaction_id: str,
        tenant_id: str,
        user_id: str,
        *,
        idempotency_key: str,
        fingerprint: str,
        actor_id: str,
        reason: str,
    ) -> tuple[Dict[str, Any], bool]:
        current = self.get(interaction_id, tenant_id, user_id)
        if current is None:
            raise KeyError(interaction_id)
        command_key = f"{tenant_id}:{user_id}:{idempotency_key}"
        if self.backend == "memory":
            with self._lock:
                replay = self._retry_commands.get(command_key)
                if replay:
                    if replay["fingerprint"] != fingerprint or replay["interaction_id"] != interaction_id:
                        raise InteractionConflict("retry idempotency key reused with different content")
                    return self._materialize_locked(interaction_id), True
        else:
            with self._connect() as conn:
                replay = conn.execute(
                    f"SELECT interaction_id,request_fingerprint FROM {self._retry_table} "
                    "WHERE tenant_id=%s AND owner_user_id=%s AND idempotency_key=%s",
                    (tenant_id, user_id, idempotency_key),
                ).fetchone()
            if replay:
                if replay[0] != interaction_id or replay[1] != fingerprint:
                    raise InteractionConflict("retry idempotency key reused with different content")
                return current, True
        if current.get("status") not in {"failed", "degraded"}:
            raise InteractionConflict("only failed or degraded interactions may be retried")
        latest_by_persona: Dict[str, Dict[str, Any]] = {}
        for item in current["provider_invocations"]:
            persona_id = str((item.get("participant") or {}).get("persona_id") or "")
            if persona_id:
                latest_by_persona[persona_id] = item
        retryable = {
            item["invocation_id"] for item in latest_by_persona.values()
            if item.get("status") == "failed" and (item.get("error") or {}).get("retryable") is True
        }
        if not retryable:
            raise InteractionConflict("interaction has no retryable failed provider invocation")
        if self.backend == "memory":
            with self._lock:
                if self._requests[interaction_id].get("status") not in {"failed", "degraded"}:
                    raise InteractionConflict("another retry already owns this interaction")
                self._retry_commands[command_key] = {
                    "fingerprint": fingerprint, "interaction_id": interaction_id,
                    "actor_id": actor_id, "reason": reason,
                }
                request = self._requests[interaction_id]
                request.update({"status": "queued", "retry_count": int(request.get("retry_count", 0)) + 1,
                                "updated_at": _now()})
                self._syntheses.pop(interaction_id, None)
                self._audit_locked(interaction_id, f"audit:retry:{interaction_id}:{idempotency_key}", {
                    "audit_id": f"audit:retry:{interaction_id}:{idempotency_key}",
                    "action": "interaction_retry_requested", "actor_id": actor_id,
                    "reason": reason, "occurred_at": request["updated_at"],
                })
                self._persist_locked()
            return self.get(interaction_id, tenant_id, user_id), False  # type: ignore[return-value]
        with self._connect() as conn:
            locked_request = conn.execute(
                f"SELECT status FROM {self._request_table} WHERE interaction_id=%s FOR UPDATE",
                (interaction_id,),
            ).fetchone()
            # A same-key contender may have committed while this transaction
            # waited on the aggregate lock.  It is a replay, not a competing
            # second retry command.
            replay = conn.execute(
                f"SELECT interaction_id,request_fingerprint FROM {self._retry_table} "
                "WHERE tenant_id=%s AND owner_user_id=%s AND idempotency_key=%s",
                (tenant_id, user_id, idempotency_key),
            ).fetchone()
            if replay:
                if replay[0] != interaction_id or replay[1] != fingerprint:
                    raise InteractionConflict("retry idempotency key reused with different content")
                return self.get(interaction_id, tenant_id, user_id), True  # type: ignore[return-value]
            if not locked_request or locked_request[0] not in {"failed", "degraded"}:
                raise InteractionConflict("another retry already owns this interaction")
            inserted = conn.execute(
                f"INSERT INTO {self._retry_table} "
                "(tenant_id,owner_user_id,idempotency_key,interaction_id,request_fingerprint,actor_id,reason) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING interaction_id",
                (tenant_id, user_id, idempotency_key, interaction_id, fingerprint, actor_id, reason),
            ).fetchone()
            if inserted is None:
                replay = conn.execute(
                    f"SELECT interaction_id,request_fingerprint FROM {self._retry_table} "
                    "WHERE tenant_id=%s AND owner_user_id=%s AND idempotency_key=%s FOR UPDATE",
                    (tenant_id, user_id, idempotency_key),
                ).fetchone()
                if not replay or replay[0] != interaction_id or replay[1] != fingerprint:
                    raise InteractionConflict("retry idempotency key reused with different content")
                return self.get(interaction_id, tenant_id, user_id), True  # type: ignore[return-value]
            conn.execute(f"DELETE FROM {self._synthesis_table} WHERE interaction_id=%s", (interaction_id,))
            conn.execute(
                f"UPDATE {self._request_table} SET status='queued',retry_count=retry_count+1,updated_at=now() "
                "WHERE interaction_id=%s", (interaction_id,),
            )
            self._insert_audit_pg(conn, interaction_id, f"audit:retry:{interaction_id}:{idempotency_key}", {
                "audit_id": f"audit:retry:{interaction_id}:{idempotency_key}",
                "action": "interaction_retry_requested", "actor_id": actor_id,
                "reason": reason, "occurred_at": _now(),
            })
        return self.get(interaction_id, tenant_id, user_id), False  # type: ignore[return-value]

    def enqueue(self, interaction_id: str, item: Dict[str, Any]) -> None:
        if self.backend == "memory":
            with self._lock:
                self._enqueue_locked(interaction_id, item)
            return
        with self._connect() as conn:
            self._enqueue_pg(conn, interaction_id, item)

    def _enqueue_locked(self, interaction_id: str, item: Dict[str, Any]) -> None:
        outbox_id = str(item["outbox_id"])
        existing = self._outbox.get(outbox_id)
        if existing and existing["payload"] != item["payload"]:
            raise InteractionConflict("outbox identity reused with different projection content")
        if not existing:
            self._outbox[outbox_id] = {
                "outbox_id": outbox_id, "interaction_id": interaction_id,
                "projection_kind": item["projection_kind"], "payload": copy.deepcopy(item["payload"]),
                "state": "pending", "attempt": 0, "created_at": _now(),
            }
            self._persist_locked()

    def _enqueue_pg(self, conn: Any, interaction_id: str, item: Dict[str, Any]) -> None:
        row = conn.execute(
            f"INSERT INTO {self._outbox_table} (outbox_id,interaction_id,projection_kind,payload_json) "
            "VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT (outbox_id) DO NOTHING RETURNING outbox_id",
            (item["outbox_id"], interaction_id, item["projection_kind"], json.dumps(item["payload"], default=str)),
        ).fetchone()
        if row is None:
            existing = conn.execute(
                f"SELECT interaction_id,projection_kind,payload_json FROM {self._outbox_table} WHERE outbox_id=%s",
                (item["outbox_id"],),
            ).fetchone()
            if not existing or existing[0] != interaction_id or existing[1] != item["projection_kind"] or _decode(existing[2]) != item["payload"]:
                raise InteractionConflict("outbox identity reused with different projection content")

    def drain_outbox(self, dispatch: Callable[[str, Dict[str, Any]], None], *, limit: int = 100) -> int:
        completed = 0
        if self.backend == "memory":
            while completed < limit:
                with self._lock:
                    row = next((item for item in self._outbox.values() if item["state"] == "pending"), None)
                    if row is None:
                        break
                    row["state"] = "processing"
                    row["attempt"] += 1
                    claimed = copy.deepcopy(row)
                    self._persist_locked()
                try:
                    dispatch(claimed["projection_kind"], claimed["payload"])
                except Exception:
                    with self._lock:
                        self._outbox[claimed["outbox_id"]]["state"] = "pending"
                        self._persist_locked()
                    raise
                with self._lock:
                    self._outbox[claimed["outbox_id"]]["state"] = "completed"
                    self._persist_locked()
                completed += 1
            return completed
        while completed < limit:
            with self._connect() as conn:
                row = conn.execute(
                    f"UPDATE {self._outbox_table} SET state='processing',attempt=attempt+1,"
                    "lease_until=now()+interval '5 minutes',updated_at=now() WHERE outbox_id=(SELECT outbox_id "
                    f"FROM {self._outbox_table} WHERE state='pending' OR (state='processing' AND lease_until < now()) "
                    "ORDER BY created_at,outbox_id FOR UPDATE SKIP LOCKED LIMIT 1) "
                    "RETURNING outbox_id,projection_kind,payload_json",
                ).fetchone()
            if row is None:
                break
            try:
                dispatch(row[1], _decode(row[2]))
            except Exception:
                with self._connect() as conn:
                    conn.execute(
                        f"UPDATE {self._outbox_table} SET state='pending',lease_until=NULL,updated_at=now() WHERE outbox_id=%s",
                        (row[0],),
                    )
                raise
            with self._connect() as conn:
                conn.execute(
                    f"UPDATE {self._outbox_table} SET state='completed',completed_at=now(),lease_until=NULL,updated_at=now() "
                    "WHERE outbox_id=%s", (row[0],),
                )
            completed += 1
        return completed

    def link_candidate(self, interaction_id: str, link: Dict[str, Any]) -> None:
        proposal_id = str(link["proposal_id"])
        if self.backend == "memory":
            with self._lock:
                bucket = self._candidate_links.setdefault(interaction_id, {})
                existing = bucket.get(proposal_id)
                if existing is not None and existing != link:
                    raise InteractionConflict("candidate proposal link is immutable")
                bucket[proposal_id] = copy.deepcopy(link)
                self._persist_locked()
            return
        with self._connect() as conn:
            inserted = conn.execute(
                f"INSERT INTO {self._candidate_table} (interaction_id,proposal_id,link_json) VALUES (%s,%s,%s::jsonb) "
                "ON CONFLICT (interaction_id,proposal_id) DO NOTHING RETURNING proposal_id",
                (interaction_id, proposal_id, json.dumps(link, default=str)),
            ).fetchone()
            if inserted is None:
                existing = conn.execute(
                    f"SELECT link_json FROM {self._candidate_table} WHERE interaction_id=%s AND proposal_id=%s",
                    (interaction_id, proposal_id),
                ).fetchone()
                if not existing or _decode(existing[0]) != link:
                    raise InteractionConflict("candidate proposal link is immutable")

    @staticmethod
    def _page_token(created_at: str, interaction_id: str) -> str:
        return base64.urlsafe_b64encode(
            json.dumps([created_at, interaction_id], separators=(",", ":")).encode()
        ).decode().rstrip("=")

    @staticmethod
    def _decode_page_token(token: str) -> tuple[str, str]:
        try:
            raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
            value = json.loads(raw)
            if not isinstance(value, list) or len(value) != 2 or not all(isinstance(x, str) for x in value):
                raise ValueError
            return value[0], value[1]
        except Exception as exc:
            raise ValueError("invalid interaction page token") from exc

    def list_page(self, tenant_id: str, user_id: str, *, workshop_id: Optional[str] = None,
                  page_size: int = 20, page_token: Optional[str] = None) -> tuple[List[Dict[str, Any]], Optional[str]]:
        cursor = self._decode_page_token(page_token) if page_token else None
        if self.backend == "memory":
            with self._lock:
                ids = [interaction_id for interaction_id, row in self._requests.items()
                       if row["tenant_id"] == tenant_id and row["owner_user_id"] == user_id
                       and (workshop_id is None or row["workshop_id"] == workshop_id)]
                ids.sort(key=lambda value: (self._requests[value]["created_at"], value), reverse=True)
                if cursor:
                    ids = [value for value in ids if (self._requests[value]["created_at"], value) < cursor]
                selected = ids[:page_size + 1]
                has_more = len(selected) > page_size
                selected = selected[:page_size]
                token = (self._page_token(self._requests[selected[-1]]["created_at"], selected[-1])
                         if has_more and selected else None)
                return [self._materialize_locked(value) for value in selected], token
        params: List[Any] = [tenant_id, user_id]
        where = "tenant_id=%s AND owner_user_id=%s"
        if workshop_id:
            where += " AND workshop_id=%s"
            params.append(workshop_id)
        if cursor:
            where += " AND (created_at,interaction_id) < (%s::timestamptz,%s)"
            params.extend(cursor)
        params.append(page_size + 1)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT interaction_id,created_at FROM {self._request_table} WHERE {where} "
                "ORDER BY created_at DESC,interaction_id DESC LIMIT %s", params,
            ).fetchall()
        has_more = len(rows) > page_size
        rows = rows[:page_size]
        token = self._page_token(_timestamp(rows[-1][1]), rows[-1][0]) if has_more and rows else None
        return [item for row in rows if (item := self.get(row[0], tenant_id, user_id)) is not None], token

    def list(self, tenant_id: str, user_id: str, *, workshop_id: Optional[str] = None,
             page_size: int = 100) -> List[Dict[str, Any]]:
        return self.list_page(tenant_id, user_id, workshop_id=workshop_id, page_size=page_size)[0]

    def get(self, interaction_id: str, tenant_id: str, user_id: str) -> Optional[Dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                request = self._requests.get(interaction_id)
                if not request or request["tenant_id"] != tenant_id or request["owner_user_id"] != user_id:
                    return None
                return self._materialize_locked(interaction_id)
        with self._connect() as conn:
            request_row = conn.execute(
                f"SELECT request_json,status,retry_count,trace_id,created_at,updated_at,lease_owner,lease_until,demo_run_id FROM {self._request_table} "
                "WHERE interaction_id=%s AND tenant_id=%s AND owner_user_id=%s",
                (interaction_id, tenant_id, user_id),
            ).fetchone()
            if request_row is None:
                return None
            invocations = conn.execute(
                f"SELECT invocation_json,opinion_json,error_json,status FROM {self._invocation_table} "
                "WHERE interaction_id=%s ORDER BY created_at,invocation_id", (interaction_id,),
            ).fetchall()
            synthesis_row = conn.execute(
                f"SELECT synthesis_json,missing_participant_ids,degraded_participant_ids FROM {self._synthesis_table} "
                "WHERE interaction_id=%s", (interaction_id,),
            ).fetchone()
            candidate_rows = conn.execute(
                f"SELECT link_json FROM {self._candidate_table} WHERE interaction_id=%s ORDER BY created_at,proposal_id",
                (interaction_id,),
            ).fetchall()
            audit_rows = conn.execute(
                f"SELECT audit_json FROM {self._audit_table} WHERE interaction_id=%s ORDER BY created_at,audit_id",
                (interaction_id,),
            ).fetchall()
        resource = _decode(request_row[0])
        resource.update({"status": request_row[1], "retry_count": request_row[2], "trace_id": request_row[3],
                         "created_at": _timestamp(request_row[4]), "updated_at": _timestamp(request_row[5])})
        if request_row[6] is not None:
            resource["lease_owner"] = request_row[6]
        if request_row[7] is not None:
            resource["lease_until"] = _timestamp(request_row[7])
        if request_row[8] is not None:
            resource["demo_run_id"] = request_row[8]
        resource["provider_invocations"] = []
        resource["opinions"] = []
        for invocation_json, opinion_json, error_json, status in invocations:
            invocation = _decode(invocation_json)
            invocation["status"] = status
            if error_json is not None:
                invocation["error"] = _decode(error_json)
            resource["provider_invocations"].append(invocation)
            if opinion_json is not None:
                resource["opinions"].append(_decode(opinion_json))
        resource["synthesis"] = _decode(synthesis_row[0]) if synthesis_row and synthesis_row[0] is not None else None
        resource["missing_participant_ids"] = _decode(synthesis_row[1]) if synthesis_row else []
        resource["degraded_participant_ids"] = _decode(synthesis_row[2]) if synthesis_row else []
        resource["candidate_proposal_links"] = [_decode(row[0]) for row in candidate_rows]
        resource["audit_refs"] = [_decode(row[0])["audit_id"] for row in audit_rows]
        return resource

    def timeline(self, interaction_id: str, tenant_id: str, user_id: str) -> Optional[List[Dict[str, Any]]]:
        if self.get(interaction_id, tenant_id, user_id) is None:
            return None
        if self.backend == "memory":
            with self._lock:
                return [copy.deepcopy(item) for item in self._outbox.values()
                        if item["interaction_id"] == interaction_id]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT outbox_id,projection_kind,payload_json,state,attempt,created_at,completed_at "
                f"FROM {self._outbox_table} WHERE interaction_id=%s ORDER BY created_at,outbox_id",
                (interaction_id,),
            ).fetchall()
        return [{"outbox_id": row[0], "projection_kind": row[1], "payload": _decode(row[2]),
                 "state": row[3], "attempt": row[4], "created_at": _timestamp(row[5]),
                 "completed_at": _timestamp(row[6]) if row[6] else None} for row in rows]

    def recoverable(self, tenant_id: str, user_id: str, *, limit: int = 25) -> List[Dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                ids = [
                    interaction_id
                    for interaction_id, request in self._requests.items()
                    if request["tenant_id"] == tenant_id
                    and request["owner_user_id"] == user_id
                    and request["status"] in {"queued", "running"}
                ]
                ids.sort(
                    key=lambda value: (self._requests[value]["created_at"], value),
                )
                return [self._materialize_locked(value) for value in ids[:limit]]
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT interaction_id FROM {self._request_table} "
                "WHERE tenant_id=%s AND owner_user_id=%s "
                "AND status IN ('queued','running') "
                "ORDER BY created_at ASC,interaction_id ASC LIMIT %s",
                (tenant_id, user_id, limit),
            ).fetchall()
        return [
            item
            for row in rows
            if (item := self.get(row[0], tenant_id, user_id)) is not None
        ]

    def claim_interaction(
        self,
        *,
        lease_owner: str,
        lease_duration_seconds: int = 300,
        interaction_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self.backend == "memory":
            with self._lock:
                now_dt = datetime.now(timezone.utc)
                candidates = []
                for iid, req in self._requests.items():
                    if interaction_id and iid != interaction_id:
                        continue
                    if tenant_id and req.get("tenant_id") != tenant_id:
                        continue
                    if user_id and req.get("owner_user_id") != user_id:
                        continue
                    st = req.get("status")
                    l_until = req.get("lease_until")
                    expired = False
                    if l_until:
                        try:
                            expired = datetime.fromisoformat(str(l_until).replace("Z", "+00:00")) < now_dt
                        except Exception:
                            expired = True
                    else:
                        expired = True
                    if st == "queued" or (st == "running" and expired):
                        candidates.append(req)
                if not candidates:
                    return None
                candidates.sort(key=lambda r: (r.get("created_at", ""), r.get("interaction_id", "")))
                chosen = candidates[0]
                chosen_id = chosen["interaction_id"]
                lease_until_str = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat().replace("+00:00", "Z")
                chosen["status"] = "running"
                chosen["lease_owner"] = lease_owner
                chosen["lease_until"] = lease_until_str
                chosen["updated_at"] = _now()
                self._persist_locked()
                return self.get(chosen_id, chosen["tenant_id"], chosen["owner_user_id"])

        where_clauses = ["(status = 'queued' OR (status = 'running' AND (lease_until IS NULL OR lease_until < now())))"]
        params: List[Any] = [lease_owner, lease_duration_seconds]
        sub_params: List[Any] = []
        if interaction_id:
            where_clauses.append("interaction_id = %s")
            sub_params.append(interaction_id)
        if tenant_id:
            where_clauses.append("tenant_id = %s")
            sub_params.append(tenant_id)
        if user_id:
            where_clauses.append("owner_user_id = %s")
            sub_params.append(user_id)
        where_sql = " AND ".join(where_clauses)
        query = f"""
            UPDATE {self._request_table}
            SET status = 'running',
                lease_owner = %s,
                lease_until = now() + (%s || ' seconds')::interval,
                updated_at = now()
            WHERE interaction_id = (
                SELECT interaction_id
                FROM {self._request_table}
                WHERE {where_sql}
                ORDER BY created_at ASC, interaction_id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            RETURNING interaction_id, tenant_id, owner_user_id
        """
        all_params = params + sub_params
        with self._connect() as conn:
            row = conn.execute(query, all_params).fetchone()
        if row is None:
            return None
        return self.get(row[0], row[1], row[2])

    def heartbeat_interaction(
        self,
        interaction_id: str,
        *,
        lease_owner: str,
        lease_duration_seconds: int = 300,
    ) -> bool:
        if self.backend == "memory":
            with self._lock:
                req = self._requests.get(interaction_id)
                if req and req.get("status") == "running" and req.get("lease_owner") == lease_owner:
                    now_dt = datetime.now(timezone.utc)
                    req["lease_until"] = (now_dt + timedelta(seconds=lease_duration_seconds)).isoformat().replace("+00:00", "Z")
                    req["updated_at"] = _now()
                    self._persist_locked()
                    return True
                return False
        with self._connect() as conn:
            row = conn.execute(
                f"UPDATE {self._request_table} "
                "SET lease_until = now() + (%s || ' seconds')::interval, updated_at = now() "
                "WHERE interaction_id = %s AND status = 'running' AND lease_owner = %s "
                "RETURNING interaction_id",
                (lease_duration_seconds, interaction_id, lease_owner),
            ).fetchone()
            return row is not None

    def release_interaction_lease(
        self,
        interaction_id: str,
        *,
        lease_owner: str,
        reset_to_queued: bool = True,
    ) -> bool:
        new_status = "queued" if reset_to_queued else "running"
        if self.backend == "memory":
            with self._lock:
                req = self._requests.get(interaction_id)
                if req and req.get("status") == "running" and req.get("lease_owner") == lease_owner:
                    req["status"] = new_status
                    req["lease_owner"] = None
                    req["lease_until"] = None
                    req["updated_at"] = _now()
                    if reset_to_queued:
                        bucket = self._invocations.get(interaction_id, {})
                        for inv_id, inv_row in bucket.items():
                            if inv_row.get("status") == "running":
                                inv_row["status"] = "queued"
                                inv_row["lease_owner"] = None
                                inv_row["lease_until"] = None
                                inv_row["invocation"]["status"] = "queued"
                    self._persist_locked()
                    return True
                return False
        with self._connect() as conn:
            row = conn.execute(
                f"UPDATE {self._request_table} "
                "SET status = %s, lease_owner = NULL, lease_until = NULL, updated_at = now() "
                "WHERE interaction_id = %s AND status = 'running' AND lease_owner = %s "
                "RETURNING interaction_id",
                (new_status, interaction_id, lease_owner),
            ).fetchone()
            if row is not None and reset_to_queued:
                conn.execute(
                    f"UPDATE {self._invocation_table} "
                    "SET status = 'queued', lease_owner = NULL, lease_until = NULL, updated_at = now() "
                    "WHERE interaction_id = %s AND status = 'running'",
                    (interaction_id,),
                )
            return row is not None

    def _materialize_locked(self, interaction_id: str) -> Dict[str, Any]:
        resource = copy.deepcopy(self._requests[interaction_id])
        resource.pop("request_fingerprint", None)
        rows = list(self._invocations.get(interaction_id, {}).values())
        resource["provider_invocations"] = [copy.deepcopy(row["invocation"]) for row in rows]
        resource["opinions"] = [copy.deepcopy(row["opinion"]) for row in rows if row.get("opinion")]
        resource["synthesis"] = copy.deepcopy(self._syntheses.get(interaction_id))
        resource.setdefault("missing_participant_ids", [])
        resource.setdefault("degraded_participant_ids", [])
        resource["candidate_proposal_links"] = list(copy.deepcopy(self._candidate_links.get(interaction_id, {})).values())
        resource["audit_refs"] = list(copy.deepcopy(self._audits.get(interaction_id, {})).keys())
        resource.setdefault("retry_count", 0)
        if "lease_owner" in self._requests[interaction_id]:
            resource["lease_owner"] = self._requests[interaction_id]["lease_owner"]
        if "lease_until" in self._requests[interaction_id]:
            resource["lease_until"] = self._requests[interaction_id]["lease_until"]
        if "demo_run_id" in self._requests[interaction_id]:
            resource["demo_run_id"] = self._requests[interaction_id]["demo_run_id"]
        return resource

    def _audit_locked(self, interaction_id: str, audit_id: str, audit: Dict[str, Any]) -> None:
        bucket = self._audits.setdefault(interaction_id, {})
        existing = bucket.get(audit_id)
        if existing and existing != audit:
            raise InteractionConflict("audit identity reused with different content")
        bucket[audit_id] = copy.deepcopy(audit)

    def _insert_audit_pg(self, conn: Any, interaction_id: str, audit_id: str, audit: Dict[str, Any]) -> None:
        inserted = conn.execute(
            f"INSERT INTO {self._audit_table} (audit_id,interaction_id,audit_json) VALUES (%s,%s,%s::jsonb) "
            "ON CONFLICT (audit_id) DO NOTHING RETURNING audit_id",
            (audit_id, interaction_id, json.dumps(audit, default=str)),
        ).fetchone()
        if inserted is None:
            existing = conn.execute(
                f"SELECT interaction_id,audit_json FROM {self._audit_table} WHERE audit_id=%s",
                (audit_id,),
            ).fetchone()
            if not existing or existing[0] != interaction_id or _decode(existing[1]) != audit:
                raise InteractionConflict("audit identity reused with different content")

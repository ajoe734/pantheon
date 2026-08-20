"""Agora research plan store — memory and durable Postgres backends.

Backend env:
  AGORA_RESEARCH_STORE_BACKEND        off | postgres  (default: off)
  AGORA_RESEARCH_STORE_DSN            Postgres DSN (falls back to DATABASE_URL)
  AGORA_RESEARCH_STORE_SCHEMA         Postgres schema name (default: agora_research)
"""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MemoryResearchPlanStore:
    """Thread-safe in-memory store for ResearchPlanExecution and ResearchRunProjection.

    Used when AGORA_RESEARCH_STORE_BACKEND=off (default).
    """

    def __init__(self) -> None:
        self._plans: Dict[str, Dict[str, Any]] = {}
        self._runs: Dict[str, Dict[str, Any]] = {}
        self._candidate_pools: Dict[str, Dict[str, Any]] = {}
        self._candidate_scores: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._candidate_reviews: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
        self._candidate_discussions: Dict[str, List[Dict[str, Any]]] = {}
        self._candidate_monitoring: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._candidate_metrics: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._outbox: Dict[str, Dict[str, Any]] = {}
        self._audit_actions: List[Dict[str, Any]] = []
        self._idempotency: Dict[str, bool] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Idempotency
    # ------------------------------------------------------------------

    def check_and_record_idempotency_key(self, scope: str, key: str) -> bool:
        """Return True if the scope:key pair was already seen (conflict)."""
        combined = f"{scope}:{key}"
        with self._lock:
            if combined in self._idempotency:
                return True
            self._idempotency[combined] = True
            return False

    # ------------------------------------------------------------------
    # Plans
    # ------------------------------------------------------------------

    def create_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._plans[plan["plan_id"]] = dict(plan)
            return dict(self._plans[plan["plan_id"]])

    def get_plan(
        self,
        plan_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._plans.get(plan_id)
            if entry is None:
                return None
            if tenant_id is not None and entry.get("tenant_id") and entry.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and entry.get("user_id") and entry.get("user_id") != user_id:
                return None
            return dict(entry)

    def update_plan(
        self,
        plan_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            if plan_id not in self._plans:
                return None
            entry = self._plans[plan_id]
            if tenant_id is not None and entry.get("tenant_id") and entry.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and entry.get("user_id") and entry.get("user_id") != user_id:
                return None
            entry.update(updates)
            return dict(entry)

    def list_plans_for_workshop(
        self,
        workshop_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            plans = []
            for p in self._plans.values():
                if p.get("workshop_id") != workshop_id:
                    continue
                if tenant_id is not None and p.get("tenant_id") and p.get("tenant_id") != tenant_id:
                    continue
                if user_id is not None and p.get("user_id") and p.get("user_id") != user_id:
                    continue
                plans.append(dict(p))
            return plans

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def create_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._runs[run["run_id"]] = dict(run)
            return dict(self._runs[run["run_id"]])

    def get_run(
        self,
        run_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry is None:
                return None
            if tenant_id is not None and entry.get("tenant_id") and entry.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and entry.get("user_id") and entry.get("user_id") != user_id:
                return None
            return dict(entry)

    def update_run(
        self,
        run_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            if run_id not in self._runs:
                return None
            entry = self._runs[run_id]
            if tenant_id is not None and entry.get("tenant_id") and entry.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and entry.get("user_id") and entry.get("user_id") != user_id:
                return None
            entry.update(updates)
            return dict(entry)

    def list_runs_for_plan(
        self,
        plan_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            runs = []
            for r in self._runs.values():
                if r.get("plan_id") != plan_id:
                    continue
                if tenant_id is not None and r.get("tenant_id") and r.get("tenant_id") != tenant_id:
                    continue
                if user_id is not None and r.get("user_id") and r.get("user_id") != user_id:
                    continue
                runs.append(dict(r))
            return runs

    # ------------------------------------------------------------------
    # Candidate pools
    # ------------------------------------------------------------------

    def create_candidate_pool(
        self,
        pool: Dict[str, Any],
        *,
        metrics_by_artifact: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            self._candidate_pools[pool["pool_id"]] = dict(pool)
            self._candidate_scores.setdefault(pool["pool_id"], {})
            self._candidate_reviews.setdefault(pool["pool_id"], {})
            self._candidate_monitoring.setdefault(pool["pool_id"], {})
            if metrics_by_artifact is not None:
                self._candidate_metrics[pool["pool_id"]] = {
                    artifact_id: dict(metrics)
                    for artifact_id, metrics in metrics_by_artifact.items()
                }
            else:
                self._candidate_metrics.setdefault(pool["pool_id"], {})
            return dict(self._candidate_pools[pool["pool_id"]])

    def get_candidate_pool(
        self,
        pool_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            pool = self._candidate_pools.get(pool_id)
            if pool is None:
                return None
            if tenant_id is not None and pool.get("tenant_id") and pool.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and pool.get("user_id") and pool.get("user_id") != user_id:
                return None
            return dict(pool)

    def list_candidate_pools(
        self,
        *,
        user_id: str,
        tenant_id: str,
        lifecycle_state: Optional[str] = None,
        strategy_family: Optional[str] = None,
        strategy_id: Optional[str] = None,
        strategy_version: Optional[str] = None,
        strategy_ref: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            pools = []
            for pool in self._candidate_pools.values():
                if pool.get("user_id") != user_id or pool.get("tenant_id") != tenant_id:
                    continue
                if strategy_family:
                    families = (pool.get("filter") or {}).get("strategy_families") or []
                    metadata_family = (pool.get("metadata") or {}).get("strategy_family")
                    if strategy_family not in families and strategy_family != metadata_family:
                        continue
                if strategy_id:
                    meta_sid = (pool.get("metadata") or {}).get("strategy_id")
                    candidate_sids = {
                        c.get("strategy_id") or (c.get("strategy_ref") or "").split("/")[-1]
                        for c in pool.get("candidates", [])
                    }
                    if meta_sid != strategy_id and strategy_id not in candidate_sids:
                        continue
                if strategy_version:
                    meta_ver = (pool.get("metadata") or {}).get("strategy_version")
                    candidate_vers = {c.get("strategy_version") for c in pool.get("candidates", [])}
                    if meta_ver != strategy_version and strategy_version not in candidate_vers:
                        continue
                if strategy_ref:
                    candidate_refs = {c.get("strategy_ref") for c in pool.get("candidates", [])}
                    meta_ref = (pool.get("metadata") or {}).get("strategy_ref")
                    if meta_ref != strategy_ref and strategy_ref not in candidate_refs:
                        continue
                if lifecycle_state:
                    candidates = [
                        dict(candidate)
                        for candidate in pool.get("candidates", [])
                        if candidate.get("lifecycle_state") == lifecycle_state
                    ]
                    if not candidates:
                        continue
                    filtered = dict(pool)
                    filtered["candidates"] = candidates
                    filtered["total"] = len(candidates)
                    pools.append(filtered)
                else:
                    pools.append(dict(pool))
            return sorted(pools, key=lambda p: p.get("snapshot_at", ""), reverse=True)

    def get_candidate_pool_for_strategy(
        self,
        *,
        user_id: str,
        tenant_id: str,
        strategy_id: str,
        strategy_version: Optional[str] = None,
        strategy_ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Look up the most recent candidate pool matching the strategy."""
        matching = self.list_candidate_pools(
            user_id=user_id,
            tenant_id=tenant_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_ref=strategy_ref,
        )
        return matching[0] if matching else None

    def update_candidate_pool(
        self,
        pool_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            if pool_id not in self._candidate_pools:
                return None
            pool = self._candidate_pools[pool_id]
            if tenant_id is not None and pool.get("tenant_id") and pool.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and pool.get("user_id") and pool.get("user_id") != user_id:
                return None
            pool.update(updates)
            return dict(pool)

    def get_candidate_member(
        self,
        pool_id: str,
        artifact_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            pool = self._candidate_pools.get(pool_id)
            if pool is None:
                return None
            if tenant_id is not None and pool.get("tenant_id") and pool.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and pool.get("user_id") and pool.get("user_id") != user_id:
                return None
            for candidate in pool.get("candidates", []):
                if candidate.get("artifact_id") == artifact_id:
                    return dict(candidate)
            return None

    def update_candidate_member(
        self,
        pool_id: str,
        artifact_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            pool = self._candidate_pools.get(pool_id)
            if pool is None:
                return None
            if tenant_id is not None and pool.get("tenant_id") and pool.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and pool.get("user_id") and pool.get("user_id") != user_id:
                return None
            for index, candidate in enumerate(pool.get("candidates", [])):
                if candidate.get("artifact_id") == artifact_id:
                    updated = {**candidate, **updates}
                    pool["candidates"][index] = updated
                    pool["total"] = len(pool.get("candidates", []))
                    return dict(updated)
            return None

    def get_candidate_metrics(
        self,
        pool_id: str,
        artifact_id: str,
    ) -> Dict[str, Any]:
        with self._lock:
            metrics = self._candidate_metrics.get(pool_id, {}).get(artifact_id)
            return dict(metrics) if metrics is not None else {}

    def replace_candidate_scores(
        self,
        pool_id: str,
        scores_by_artifact: Dict[str, Dict[str, Any]],
    ) -> None:
        with self._lock:
            self._candidate_scores[pool_id] = {
                artifact_id: dict(score)
                for artifact_id, score in scores_by_artifact.items()
            }

    def list_candidate_scores(self, pool_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                dict(score)
                for score in self._candidate_scores.get(pool_id, {}).values()
            ]

    def get_candidate_score(
        self,
        pool_id: str,
        artifact_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            score = self._candidate_scores.get(pool_id, {}).get(artifact_id)
            return dict(score) if score is not None else None

    def add_candidate_review(
        self,
        pool_id: str,
        artifact_id: str,
        review: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._lock:
            reviews_by_member = self._candidate_reviews.setdefault(pool_id, {})
            bucket = reviews_by_member.setdefault(artifact_id, [])
            bucket.append(dict(review))
            return dict(review)

    def list_candidate_reviews(
        self,
        pool_id: str,
        artifact_id: str,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                dict(review)
                for review in self._candidate_reviews.get(pool_id, {}).get(artifact_id, [])
            ]

    def add_candidate_discussion(self, discussion: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            pool_id = discussion["pool_id"]
            bucket = self._candidate_discussions.setdefault(pool_id, [])
            bucket.append(dict(discussion))
            return dict(discussion)

    def list_candidate_discussions(
        self,
        pool_id: str,
        *,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        kind: Optional[str] = None,
        resolved: Optional[bool] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            discussions = []
            for discussion in self._candidate_discussions.get(pool_id, []):
                if tenant_id is not None and discussion.get("tenant_id") and discussion.get("tenant_id") != tenant_id:
                    continue
                if user_id is not None and discussion.get("user_id") and discussion.get("user_id") != user_id:
                    continue
                if subject_type and discussion.get("subject_type") != subject_type:
                    continue
                if subject_id and discussion.get("subject_id") != subject_id:
                    continue
                if kind and discussion.get("kind") != kind:
                    continue
                if resolved is not None and bool(discussion.get("resolved")) != resolved:
                    continue
                discussions.append(dict(discussion))
            return sorted(discussions, key=lambda d: d.get("created_at", ""))

    def upsert_candidate_monitoring(
        self,
        pool_id: str,
        artifact_id: str,
        monitoring: Dict[str, Any],
    ) -> Dict[str, Any]:
        with self._lock:
            bucket = self._candidate_monitoring.setdefault(pool_id, {})
            bucket[artifact_id] = dict(monitoring)
            return dict(monitoring)

    def get_candidate_monitoring(
        self,
        pool_id: str,
        artifact_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            monitoring = self._candidate_monitoring.get(pool_id, {}).get(artifact_id)
            return dict(monitoring) if monitoring is not None else None

    def list_candidate_monitoring(
        self,
        pool_id: str,
        *,
        monitoring_state: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            entries = []
            for monitoring in self._candidate_monitoring.get(pool_id, {}).values():
                if monitoring_state and monitoring.get("monitoring_state") != monitoring_state:
                    continue
                entries.append(dict(monitoring))
            return sorted(entries, key=lambda entry: entry.get("added_at", ""))

    # ------------------------------------------------------------------
    # Outbox & Leases
    # ------------------------------------------------------------------

    def create_outbox_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._outbox[record["outbox_id"]] = dict(record)
            return dict(self._outbox[record["outbox_id"]])

    def get_outbox_record(
        self,
        outbox_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._outbox.get(outbox_id)
            if entry is None:
                return None
            if tenant_id is not None and entry.get("tenant_id") and entry.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and entry.get("user_id") and entry.get("user_id") != user_id:
                return None
            return dict(entry)

    def update_outbox_record(
        self,
        outbox_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            if outbox_id not in self._outbox:
                return None
            entry = self._outbox[outbox_id]
            if tenant_id is not None and entry.get("tenant_id") and entry.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and entry.get("user_id") and entry.get("user_id") != user_id:
                return None
            entry.update(updates)
            return dict(entry)

    def acquire_outbox_lease(
        self,
        outbox_id: str,
        lease_owner: str,
        lease_duration_seconds: float = 60.0,
        now_iso: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        now = now_iso or _utc_now_iso()
        with self._lock:
            entry = self._outbox.get(outbox_id)
            if entry is None:
                return None
            # Check existing lease
            current_owner = entry.get("lease_owner")
            expires_at = entry.get("lease_expires_at")
            if current_owner and expires_at and expires_at > now and current_owner != lease_owner:
                # Lease held by someone else and not expired
                return None
            entry["lease_owner"] = lease_owner
            # compute expires
            dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
            exp_dt = datetime.fromtimestamp(dt.timestamp() + lease_duration_seconds, tz=timezone.utc)
            entry["lease_expires_at"] = exp_dt.isoformat()
            entry["updated_at"] = now
            return dict(entry)

    def list_outbox_records(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        plan_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            items = []
            for item in self._outbox.values():
                if tenant_id is not None and item.get("tenant_id") and item.get("tenant_id") != tenant_id:
                    continue
                if user_id is not None and item.get("user_id") and item.get("user_id") != user_id:
                    continue
                if status is not None and item.get("status") != status:
                    continue
                if plan_id is not None and item.get("plan_id") != plan_id:
                    continue
                if run_id is not None and item.get("run_id") != run_id:
                    continue
                items.append(dict(item))
            return items

    # ------------------------------------------------------------------
    # Audit actions
    # ------------------------------------------------------------------

    def record_audit_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            doc = dict(action)
            doc.setdefault("recorded_at", _utc_now_iso())
            self._audit_actions.append(doc)
            return dict(doc)

    def list_audit_actions(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        with self._lock:
            actions = []
            for a in self._audit_actions:
                if tenant_id is not None and a.get("tenant_id") and a.get("tenant_id") != tenant_id:
                    continue
                if user_id is not None and a.get("user_id") and a.get("user_id") != user_id:
                    continue
                if subject_type and a.get("subject_type") != subject_type:
                    continue
                if subject_id and a.get("subject_id") != subject_id:
                    continue
                actions.append(dict(a))
            return sorted(actions, key=lambda a: str(a.get("recorded_at", "")))


class PostgresResearchPlanStore:
    """Durable JSONB aggregate store implementing contract-equivalent research store."""

    _KINDS = (
        "plan",
        "run",
        "candidate_pool",
        "candidate_score",
        "candidate_review",
        "candidate_discussion",
        "candidate_monitoring",
        "candidate_metrics",
        "research_outbox",
        "research_audit",
    )

    def __init__(self, *, dsn: str, schema: str = "agora_research") -> None:
        if not dsn:
            raise ValueError("Postgres DSN is required for PostgresResearchPlanStore")
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema) is None:
            raise ValueError("Invalid Postgres schema")
        self.dsn = dsn
        self.schema = schema
        self._records = f'"{schema}"."research_aggregate"'
        self._keys = f'"{schema}"."research_idempotency_key"'
        self._bootstrap()

    def _connect(self):
        try:
            import psycopg  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("psycopg is required for PostgresResearchPlanStore") from exc
        return psycopg.connect(self.dsn)

    def _bootstrap(self) -> None:
        with self._connect() as conn:
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self.schema}"')
            conn.execute(f"""CREATE TABLE IF NOT EXISTS {self._records} (
                aggregate_kind TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                parent_id TEXT,
                subject_id TEXT,
                payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (aggregate_kind, aggregate_id))""")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_research_parent ON {self._records} (aggregate_kind, parent_id)")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_research_subject ON {self._records} (aggregate_kind, subject_id)")
            conn.execute(f"""CREATE TABLE IF NOT EXISTS {self._keys} (
                scope TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (scope, idempotency_key))""")

    @staticmethod
    def _payload(row: Any) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        value = row[0] if not isinstance(row, dict) else row["payload"]
        return json.loads(value) if isinstance(value, str) else dict(value)

    def _put(self, kind: str, record_id: str, payload: Dict[str, Any], *, parent_id: Optional[str] = None, subject_id: Optional[str] = None) -> Dict[str, Any]:
        document = json.loads(json.dumps(payload))
        with self._connect() as conn:
            row = conn.execute(
                f"""INSERT INTO {self._records}
                (aggregate_kind, aggregate_id, parent_id, subject_id, payload)
                VALUES (%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (aggregate_kind, aggregate_id) DO UPDATE SET
                  parent_id=EXCLUDED.parent_id, subject_id=EXCLUDED.subject_id,
                  payload=EXCLUDED.payload, updated_at=now() RETURNING payload""",
                (kind, record_id, parent_id, subject_id, json.dumps(document)),
            ).fetchone()
        return self._payload(row)  # type: ignore[return-value]

    def _get(self, kind: str, record_id: str) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT payload FROM {self._records} WHERE aggregate_kind=%s AND aggregate_id=%s",
                (kind, record_id),
            ).fetchone()
        return self._payload(row)

    def _list(self, kind: str, *, parent_id: Optional[str] = None, subject_id: Optional[str] = None) -> List[Dict[str, Any]]:
        where, params = "aggregate_kind=%s", [kind]
        if parent_id is not None:
            where += " AND parent_id=%s"
            params.append(parent_id)
        if subject_id is not None:
            where += " AND subject_id=%s"
            params.append(subject_id)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT payload FROM {self._records} WHERE {where} ORDER BY created_at, aggregate_id",
                tuple(params),
            ).fetchall()
        return [self._payload(row) for row in rows]  # type: ignore[misc]

    def _patch(self, kind: str, record_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        document = json.loads(json.dumps(updates))
        with self._connect() as conn:
            row = conn.execute(
                f"""UPDATE {self._records}
                    SET payload = payload || %s::jsonb, updated_at = now()
                    WHERE aggregate_kind=%s AND aggregate_id=%s
                    RETURNING payload""",
                (json.dumps(document), kind, record_id),
            ).fetchone()
        return self._payload(row)

    def check_and_record_idempotency_key(self, scope: str, key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                f"INSERT INTO {self._keys} (scope,idempotency_key) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING scope",
                (scope, key),
            ).fetchone()
        return row is None

    # Plans
    def create_plan(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        return self._put("plan", plan["plan_id"], plan, parent_id=plan.get("workshop_id"))

    def get_plan(
        self,
        plan_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        doc = self._get("plan", plan_id)
        if doc is None:
            return None
        if tenant_id is not None and doc.get("tenant_id") and doc.get("tenant_id") != tenant_id:
            return None
        if user_id is not None and doc.get("user_id") and doc.get("user_id") != user_id:
            return None
        return doc

    def update_plan(
        self,
        plan_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        doc = self.get_plan(plan_id, tenant_id=tenant_id, user_id=user_id)
        if doc is None:
            return None
        return self._patch("plan", plan_id, updates)

    def list_plans_for_workshop(
        self,
        workshop_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        plans = self._list("plan", parent_id=workshop_id)
        return [
            p for p in plans
            if (tenant_id is None or not p.get("tenant_id") or p.get("tenant_id") == tenant_id)
            and (user_id is None or not p.get("user_id") or p.get("user_id") == user_id)
        ]

    # Runs
    def create_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        return self._put("run", run["run_id"], run, parent_id=run.get("plan_id"))

    def get_run(
        self,
        run_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        doc = self._get("run", run_id)
        if doc is None:
            return None
        if tenant_id is not None and doc.get("tenant_id") and doc.get("tenant_id") != tenant_id:
            return None
        if user_id is not None and doc.get("user_id") and doc.get("user_id") != user_id:
            return None
        return doc

    def update_run(
        self,
        run_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        doc = self.get_run(run_id, tenant_id=tenant_id, user_id=user_id)
        if doc is None:
            return None
        return self._patch("run", run_id, updates)

    def list_runs_for_plan(
        self,
        plan_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        runs = self._list("run", parent_id=plan_id)
        return [
            r for r in runs
            if (tenant_id is None or not r.get("tenant_id") or r.get("tenant_id") == tenant_id)
            and (user_id is None or not r.get("user_id") or r.get("user_id") == user_id)
        ]

    # Candidate Pools
    def create_candidate_pool(
        self,
        pool: Dict[str, Any],
        *,
        metrics_by_artifact: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        result = self._put("candidate_pool", pool["pool_id"], pool)
        for artifact_id, metrics in (metrics_by_artifact or {}).items():
            self._put(
                "candidate_metrics",
                f'{pool["pool_id"]}:{artifact_id}',
                metrics,
                parent_id=pool["pool_id"],
                subject_id=artifact_id,
            )
        return result

    def get_candidate_pool(
        self,
        pool_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        pool = self._get("candidate_pool", pool_id)
        if pool is None:
            return None
        if tenant_id is not None and pool.get("tenant_id") and pool.get("tenant_id") != tenant_id:
            return None
        if user_id is not None and pool.get("user_id") and pool.get("user_id") != user_id:
            return None
        return pool

    def list_candidate_pools(
        self,
        *,
        user_id: str,
        tenant_id: str,
        lifecycle_state: Optional[str] = None,
        strategy_family: Optional[str] = None,
        strategy_id: Optional[str] = None,
        strategy_version: Optional[str] = None,
        strategy_ref: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        result = []
        for pool in self._list("candidate_pool"):
            if pool.get("user_id") != user_id or pool.get("tenant_id") != tenant_id:
                continue
            families = (pool.get("filter") or {}).get("strategy_families") or []
            if strategy_family and strategy_family not in families and strategy_family != (pool.get("metadata") or {}).get("strategy_family"):
                continue
            if strategy_id:
                meta_sid = (pool.get("metadata") or {}).get("strategy_id")
                meta_sref = (pool.get("metadata") or {}).get("strategy_ref") or ""
                candidate_sids = set()
                for c in pool.get("candidates", []):
                    cid = c.get("strategy_id")
                    if cid:
                        candidate_sids.add(cid)
                    sref = c.get("strategy_ref") or ""
                    if sref:
                        candidate_sids.add(sref)
                        candidate_sids.add(sref.split("/")[-1])
                        parts = sref.split(":")
                        if len(parts) >= 2:
                            candidate_sids.add(parts[1])
                meta_parts = meta_sref.split(":") if meta_sref else []
                meta_extracted_id = meta_parts[1] if len(meta_parts) >= 2 else (meta_sref.split("/")[-1] if meta_sref else None)
                if meta_sid != strategy_id and meta_extracted_id != strategy_id and strategy_id not in candidate_sids:
                    continue
            if strategy_version:
                meta_ver = (pool.get("metadata") or {}).get("strategy_version")
                candidate_vers = set()
                for c in pool.get("candidates", []):
                    cver = c.get("strategy_version")
                    if cver:
                        candidate_vers.add(cver)
                    sref = c.get("strategy_ref") or ""
                    parts = sref.split(":")
                    if len(parts) >= 3:
                        candidate_vers.add(parts[2])
                if meta_ver != strategy_version and strategy_version not in candidate_vers:
                    continue
            if strategy_ref:
                candidate_refs = {c.get("strategy_ref") for c in pool.get("candidates", [])}
                meta_ref = (pool.get("metadata") or {}).get("strategy_ref")
                meta_sid = (pool.get("metadata") or {}).get("strategy_id")
                if meta_ref != strategy_ref and meta_sid != strategy_ref and strategy_ref not in candidate_refs:
                    continue
            candidates = pool.get("candidates", [])
            if lifecycle_state:
                candidates = [dict(c) for c in candidates if c.get("lifecycle_state") == lifecycle_state]
                if not candidates:
                    continue
                pool = {**pool, "candidates": candidates, "total": len(candidates)}
            result.append(pool)
        return sorted(result, key=lambda p: p.get("snapshot_at", ""), reverse=True)

    def get_candidate_pool_for_strategy(
        self,
        *,
        user_id: str,
        tenant_id: str,
        strategy_id: str,
        strategy_version: Optional[str] = None,
        strategy_ref: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        matching = self.list_candidate_pools(
            user_id=user_id,
            tenant_id=tenant_id,
            strategy_id=strategy_id,
            strategy_version=strategy_version,
            strategy_ref=strategy_ref,
        )
        return matching[0] if matching else None

    def update_candidate_pool(
        self,
        pool_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        doc = self.get_candidate_pool(pool_id, tenant_id=tenant_id, user_id=user_id)
        if doc is None:
            return None
        return self._patch("candidate_pool", pool_id, updates)

    def get_candidate_member(
        self,
        pool_id: str,
        artifact_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        pool = self.get_candidate_pool(pool_id, tenant_id=tenant_id, user_id=user_id)
        if pool is None:
            return None
        return next((dict(c) for c in pool.get("candidates", []) if c.get("artifact_id") == artifact_id), None)

    def update_candidate_member(
        self,
        pool_id: str,
        artifact_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT payload FROM {self._records} WHERE aggregate_kind='candidate_pool' AND aggregate_id=%s FOR UPDATE",
                (pool_id,),
            ).fetchone()
            pool = self._payload(row)
            if pool is None:
                return None
            if tenant_id is not None and pool.get("tenant_id") and pool.get("tenant_id") != tenant_id:
                return None
            if user_id is not None and pool.get("user_id") and pool.get("user_id") != user_id:
                return None
            for index, candidate in enumerate(pool.get("candidates", [])):
                if candidate.get("artifact_id") == artifact_id:
                    updated = {**candidate, **json.loads(json.dumps(updates))}
                    pool["candidates"][index] = updated
                    pool["total"] = len(pool["candidates"])
                    conn.execute(
                        f"UPDATE {self._records} SET payload=%s::jsonb, updated_at=now() WHERE aggregate_kind='candidate_pool' AND aggregate_id=%s",
                        (json.dumps(pool), pool_id),
                    )
                    return updated
        return None

    def get_candidate_metrics(self, pool_id: str, artifact_id: str) -> Dict[str, Any]:
        return self._get("candidate_metrics", f"{pool_id}:{artifact_id}") or {}

    def replace_candidate_scores(self, pool_id: str, scores_by_artifact: Dict[str, Dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute(f"DELETE FROM {self._records} WHERE aggregate_kind='candidate_score' AND parent_id=%s", (pool_id,))
            for artifact_id, score in scores_by_artifact.items():
                conn.execute(
                    f"""INSERT INTO {self._records}
                    (aggregate_kind,aggregate_id,parent_id,subject_id,payload)
                    VALUES ('candidate_score',%s,%s,%s,%s::jsonb)""",
                    (f"{pool_id}:{artifact_id}", pool_id, artifact_id, json.dumps(score)),
                )

    def list_candidate_scores(self, pool_id: str) -> List[Dict[str, Any]]:
        return self._list("candidate_score", parent_id=pool_id)

    def get_candidate_score(self, pool_id: str, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self._get("candidate_score", f"{pool_id}:{artifact_id}")

    def add_candidate_review(self, pool_id: str, artifact_id: str, review: Dict[str, Any]) -> Dict[str, Any]:
        return self._put(
            "candidate_review",
            f'{pool_id}:{artifact_id}:{review["review_id"]}',
            review,
            parent_id=pool_id,
            subject_id=artifact_id,
        )

    def list_candidate_reviews(self, pool_id: str, artifact_id: str) -> List[Dict[str, Any]]:
        return self._list("candidate_review", parent_id=pool_id, subject_id=artifact_id)

    def add_candidate_discussion(self, discussion: Dict[str, Any]) -> Dict[str, Any]:
        return self._put("candidate_discussion", discussion["discussion_id"], discussion, parent_id=discussion["pool_id"])

    def list_candidate_discussions(
        self,
        pool_id: str,
        *,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
        kind: Optional[str] = None,
        resolved: Optional[bool] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = self._list("candidate_discussion", parent_id=pool_id)
        filtered = []
        for d in rows:
            if tenant_id is not None and d.get("tenant_id") and d.get("tenant_id") != tenant_id:
                continue
            if user_id is not None and d.get("user_id") and d.get("user_id") != user_id:
                continue
            if subject_type and d.get("subject_type") != subject_type:
                continue
            if subject_id and d.get("subject_id") != subject_id:
                continue
            if kind and d.get("kind") != kind:
                continue
            if resolved is not None and bool(d.get("resolved")) != resolved:
                continue
            filtered.append(d)
        return sorted(filtered, key=lambda d: str(d.get("created_at", "")))

    def upsert_candidate_monitoring(self, pool_id: str, artifact_id: str, monitoring: Dict[str, Any]) -> Dict[str, Any]:
        return self._put("candidate_monitoring", f"{pool_id}:{artifact_id}", monitoring, parent_id=pool_id, subject_id=artifact_id)

    def get_candidate_monitoring(self, pool_id: str, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self._get("candidate_monitoring", f"{pool_id}:{artifact_id}")

    def list_candidate_monitoring(self, pool_id: str, *, monitoring_state: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = self._list("candidate_monitoring", parent_id=pool_id)
        if monitoring_state:
            rows = [r for r in rows if r.get("monitoring_state") == monitoring_state]
        return sorted(rows, key=lambda r: str(r.get("added_at", "")))

    # Outbox & Leases
    def create_outbox_record(self, record: Dict[str, Any]) -> Dict[str, Any]:
        return self._put("research_outbox", record["outbox_id"], record, parent_id=record.get("plan_id"), subject_id=record.get("run_id"))

    def get_outbox_record(
        self,
        outbox_id: str,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        doc = self._get("research_outbox", outbox_id)
        if doc is None:
            return None
        if tenant_id is not None and doc.get("tenant_id") and doc.get("tenant_id") != tenant_id:
            return None
        if user_id is not None and doc.get("user_id") and doc.get("user_id") != user_id:
            return None
        return doc

    def update_outbox_record(
        self,
        outbox_id: str,
        updates: Dict[str, Any],
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        doc = self.get_outbox_record(outbox_id, tenant_id=tenant_id, user_id=user_id)
        if doc is None:
            return None
        return self._patch("research_outbox", outbox_id, updates)

    def acquire_outbox_lease(
        self,
        outbox_id: str,
        lease_owner: str,
        lease_duration_seconds: float = 60.0,
        now_iso: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        now = now_iso or _utc_now_iso()
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT payload FROM {self._records} WHERE aggregate_kind='research_outbox' AND aggregate_id=%s FOR UPDATE",
                (outbox_id,),
            ).fetchone()
            record = self._payload(row)
            if record is None:
                return None
            current_owner = record.get("lease_owner")
            expires_at = record.get("lease_expires_at")
            if current_owner and expires_at and expires_at > now and current_owner != lease_owner:
                return None
            dt = datetime.fromisoformat(now.replace("Z", "+00:00"))
            exp_dt = datetime.fromtimestamp(dt.timestamp() + lease_duration_seconds, tz=timezone.utc)
            record["lease_owner"] = lease_owner
            record["lease_expires_at"] = exp_dt.isoformat()
            record["updated_at"] = now
            conn.execute(
                f"UPDATE {self._records} SET payload=%s::jsonb, updated_at=now() WHERE aggregate_kind='research_outbox' AND aggregate_id=%s",
                (json.dumps(record), outbox_id),
            )
            return record

    def list_outbox_records(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        plan_id: Optional[str] = None,
        run_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = self._list("research_outbox", parent_id=plan_id, subject_id=run_id)
        filtered = []
        for item in rows:
            if tenant_id is not None and item.get("tenant_id") and item.get("tenant_id") != tenant_id:
                continue
            if user_id is not None and item.get("user_id") and item.get("user_id") != user_id:
                continue
            if status is not None and item.get("status") != status:
                continue
            filtered.append(item)
        return filtered

    # Audit
    def record_audit_action(self, action: Dict[str, Any]) -> Dict[str, Any]:
        action_id = action.get("action_id") or f"audit-{_utc_now_iso()}-{os.urandom(4).hex()}"
        doc = dict(action)
        doc.setdefault("action_id", action_id)
        doc.setdefault("recorded_at", _utc_now_iso())
        return self._put("research_audit", action_id, doc, parent_id=action.get("subject_type"), subject_id=action.get("subject_id"))

    def list_audit_actions(
        self,
        *,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        subject_type: Optional[str] = None,
        subject_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        rows = self._list("research_audit", parent_id=subject_type, subject_id=subject_id)
        filtered = []
        for a in rows:
            if tenant_id is not None and a.get("tenant_id") and a.get("tenant_id") != tenant_id:
                continue
            if user_id is not None and a.get("user_id") and a.get("user_id") != user_id:
                continue
            filtered.append(a)
        return sorted(filtered, key=lambda a: str(a.get("recorded_at", "")))


def make_research_plan_store():
    backend = os.environ.get(
        "AGORA_RESEARCH_STORE_BACKEND",
        os.environ.get("AGORA_RESEARCH_PLAN_STORE_BACKEND", "off"),
    ).strip().lower()
    if backend in ("", "off", "memory"):
        return MemoryResearchPlanStore()
    if backend != "postgres":
        raise ValueError("AGORA_RESEARCH_STORE_BACKEND must be off or postgres")
    dsn = os.environ.get("AGORA_RESEARCH_STORE_DSN") or os.environ.get("DATABASE_URL")
    if not dsn:
        raise ValueError("AGORA_RESEARCH_STORE_DSN or DATABASE_URL is required")
    return PostgresResearchPlanStore(
        dsn=dsn,
        schema=os.environ.get("AGORA_RESEARCH_STORE_SCHEMA", "agora_research"),
    )

"""Agora research plan store — memory and durable Postgres backends.

Backend env:
  AGORA_RESEARCH_STORE_BACKEND        off | postgres  (default: off)
  AGORA_RESEARCH_STORE_DSN            Postgres DSN (falls back to DATABASE_URL)
"""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List, Optional


class MemoryResearchPlanStore:
    """Thread-safe in-memory store for ResearchPlanExecution and ResearchRunProjection.

    Used when AGORA_RESEARCH_PLAN_STORE_BACKEND=off (default).
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

    def get_plan(self, plan_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._plans.get(plan_id)
            return dict(entry) if entry is not None else None

    def update_plan(self, plan_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            if plan_id not in self._plans:
                return None
            self._plans[plan_id].update(updates)
            return dict(self._plans[plan_id])

    def list_plans_for_workshop(self, workshop_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                dict(p) for p in self._plans.values()
                if p.get("workshop_id") == workshop_id
            ]

    # ------------------------------------------------------------------
    # Runs
    # ------------------------------------------------------------------

    def create_run(self, run: Dict[str, Any]) -> Dict[str, Any]:
        with self._lock:
            self._runs[run["run_id"]] = dict(run)
            return dict(self._runs[run["run_id"]])

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            entry = self._runs.get(run_id)
            return dict(entry) if entry is not None else None

    def update_run(self, run_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self._lock:
            if run_id not in self._runs:
                return None
            self._runs[run_id].update(updates)
            return dict(self._runs[run_id])

    def list_runs_for_plan(self, plan_id: str) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                dict(r) for r in self._runs.values()
                if r.get("plan_id") == plan_id
            ]

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

    def get_candidate_pool(self, pool_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            pool = self._candidate_pools.get(pool_id)
            return dict(pool) if pool is not None else None

    def list_candidate_pools(
        self,
        *,
        user_id: str,
        tenant_id: str,
        lifecycle_state: Optional[str] = None,
        strategy_family: Optional[str] = None,
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

    def update_candidate_pool(
        self,
        pool_id: str,
        updates: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            if pool_id not in self._candidate_pools:
                return None
            self._candidate_pools[pool_id].update(updates)
            return dict(self._candidate_pools[pool_id])

    def get_candidate_member(
        self,
        pool_id: str,
        artifact_id: str,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            pool = self._candidate_pools.get(pool_id)
            if pool is None:
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
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            pool = self._candidate_pools.get(pool_id)
            if pool is None:
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
    ) -> List[Dict[str, Any]]:
        with self._lock:
            discussions = []
            for discussion in self._candidate_discussions.get(pool_id, []):
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


class PostgresResearchPlanStore:
    """Durable JSONB aggregate store implementing ``MemoryResearchPlanStore``.

    Keeping the route-shaped documents intact makes the memory and Postgres
    backends contract-equivalent while the primary key includes aggregate kind,
    preventing identifiers in different research aggregate families colliding.
    """

    _KINDS = ("plan", "run", "candidate_pool", "candidate_score", "candidate_review",
              "candidate_discussion", "candidate_monitoring", "candidate_metrics")

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
                aggregate_kind TEXT NOT NULL, aggregate_id TEXT NOT NULL,
                parent_id TEXT, subject_id TEXT, payload JSONB NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (aggregate_kind, aggregate_id))""")
            conn.execute(f"CREATE INDEX IF NOT EXISTS idx_research_parent ON {self._records} (aggregate_kind, parent_id)")
            conn.execute(f"""CREATE TABLE IF NOT EXISTS {self._keys} (
                scope TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
                PRIMARY KEY (scope, idempotency_key))""")

    @staticmethod
    def _payload(row):
        if row is None:
            return None
        value = row[0] if not isinstance(row, dict) else row["payload"]
        return json.loads(value) if isinstance(value, str) else dict(value)

    def _put(self, kind: str, record_id: str, payload: Dict[str, Any], *, parent_id=None, subject_id=None):
        document = json.loads(json.dumps(payload))
        with self._connect() as conn:
            row = conn.execute(f"""INSERT INTO {self._records}
                (aggregate_kind, aggregate_id, parent_id, subject_id, payload)
                VALUES (%s,%s,%s,%s,%s::jsonb)
                ON CONFLICT (aggregate_kind, aggregate_id) DO UPDATE SET
                  parent_id=EXCLUDED.parent_id, subject_id=EXCLUDED.subject_id,
                  payload=EXCLUDED.payload, updated_at=now() RETURNING payload""",
                (kind, record_id, parent_id, subject_id, json.dumps(document))).fetchone()
        return self._payload(row)

    def _get(self, kind: str, record_id: str):
        with self._connect() as conn:
            row = conn.execute(f"SELECT payload FROM {self._records} WHERE aggregate_kind=%s AND aggregate_id=%s",
                               (kind, record_id)).fetchone()
        return self._payload(row)

    def _list(self, kind: str, *, parent_id=None):
        where, params = "aggregate_kind=%s", [kind]
        if parent_id is not None:
            where += " AND parent_id=%s"; params.append(parent_id)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT payload FROM {self._records} WHERE {where} ORDER BY created_at, aggregate_id", tuple(params)).fetchall()
        return [self._payload(row) for row in rows]

    def check_and_record_idempotency_key(self, scope: str, key: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(f"INSERT INTO {self._keys} (scope,idempotency_key) VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING scope", (scope, key)).fetchone()
        return row is None

    def create_plan(self, plan): return self._put("plan", plan["plan_id"], plan, parent_id=plan.get("workshop_id"))
    def get_plan(self, plan_id): return self._get("plan", plan_id)
    def update_plan(self, plan_id, updates):
        current = self.get_plan(plan_id)
        if current is None: return None
        current.update(updates); return self.create_plan(current)
    def list_plans_for_workshop(self, workshop_id): return self._list("plan", parent_id=workshop_id)
    def create_run(self, run): return self._put("run", run["run_id"], run, parent_id=run.get("plan_id"))
    def get_run(self, run_id): return self._get("run", run_id)
    def update_run(self, run_id, updates):
        current = self.get_run(run_id)
        if current is None: return None
        current.update(updates); return self.create_run(current)
    def list_runs_for_plan(self, plan_id): return self._list("run", parent_id=plan_id)
    def create_candidate_pool(self, pool, *, metrics_by_artifact=None):
        result = self._put("candidate_pool", pool["pool_id"], pool)
        for artifact_id, metrics in (metrics_by_artifact or {}).items():
            self._put("candidate_metrics", f'{pool["pool_id"]}:{artifact_id}', metrics, parent_id=pool["pool_id"], subject_id=artifact_id)
        return result
    def get_candidate_pool(self, pool_id): return self._get("candidate_pool", pool_id)
    def list_candidate_pools(self, *, user_id, tenant_id, lifecycle_state=None, strategy_family=None):
        result = []
        for pool in self._list("candidate_pool"):
            if pool.get("user_id") != user_id or pool.get("tenant_id") != tenant_id: continue
            families = (pool.get("filter") or {}).get("strategy_families") or []
            if strategy_family and strategy_family not in families and strategy_family != (pool.get("metadata") or {}).get("strategy_family"): continue
            candidates = pool.get("candidates", [])
            if lifecycle_state:
                candidates = [dict(c) for c in candidates if c.get("lifecycle_state") == lifecycle_state]
                if not candidates: continue
                pool = {**pool, "candidates": candidates, "total": len(candidates)}
            result.append(pool)
        return sorted(result, key=lambda p: p.get("snapshot_at", ""), reverse=True)
    def update_candidate_pool(self, pool_id, updates):
        current = self.get_candidate_pool(pool_id)
        if current is None: return None
        current.update(updates); return self._put("candidate_pool", pool_id, current)
    def get_candidate_member(self, pool_id, artifact_id):
        pool = self.get_candidate_pool(pool_id)
        return next((dict(c) for c in (pool or {}).get("candidates", []) if c.get("artifact_id") == artifact_id), None)
    def update_candidate_member(self, pool_id, artifact_id, updates):
        pool = self.get_candidate_pool(pool_id)
        if pool is None: return None
        for index, candidate in enumerate(pool.get("candidates", [])):
            if candidate.get("artifact_id") == artifact_id:
                updated = {**candidate, **updates}; pool["candidates"][index] = updated
                pool["total"] = len(pool["candidates"]); self._put("candidate_pool", pool_id, pool); return updated
        return None
    def get_candidate_metrics(self, pool_id, artifact_id): return self._get("candidate_metrics", f"{pool_id}:{artifact_id}") or {}
    def replace_candidate_scores(self, pool_id, scores_by_artifact):
        with self._connect() as conn: conn.execute(f"DELETE FROM {self._records} WHERE aggregate_kind='candidate_score' AND parent_id=%s", (pool_id,))
        for artifact_id, score in scores_by_artifact.items(): self._put("candidate_score", f"{pool_id}:{artifact_id}", score, parent_id=pool_id, subject_id=artifact_id)
    def list_candidate_scores(self, pool_id): return self._list("candidate_score", parent_id=pool_id)
    def get_candidate_score(self, pool_id, artifact_id): return self._get("candidate_score", f"{pool_id}:{artifact_id}")
    def add_candidate_review(self, pool_id, artifact_id, review): return self._put("candidate_review", f'{pool_id}:{artifact_id}:{review["review_id"]}', review, parent_id=pool_id, subject_id=artifact_id)
    def list_candidate_reviews(self, pool_id, artifact_id): return [r for r in self._list("candidate_review", parent_id=pool_id) if r.get("artifact_id", artifact_id) == artifact_id]
    def add_candidate_discussion(self, discussion): return self._put("candidate_discussion", discussion["discussion_id"], discussion, parent_id=discussion["pool_id"])
    def list_candidate_discussions(self, pool_id, *, subject_type=None, subject_id=None, kind=None, resolved=None):
        rows = self._list("candidate_discussion", parent_id=pool_id)
        rows = [d for d in rows if (not subject_type or d.get("subject_type") == subject_type) and (not subject_id or d.get("subject_id") == subject_id) and (not kind or d.get("kind") == kind) and (resolved is None or bool(d.get("resolved")) == resolved)]
        return sorted(rows, key=lambda d: d.get("created_at", ""))
    def upsert_candidate_monitoring(self, pool_id, artifact_id, monitoring): return self._put("candidate_monitoring", f"{pool_id}:{artifact_id}", monitoring, parent_id=pool_id, subject_id=artifact_id)
    def get_candidate_monitoring(self, pool_id, artifact_id): return self._get("candidate_monitoring", f"{pool_id}:{artifact_id}")
    def list_candidate_monitoring(self, pool_id, *, monitoring_state=None):
        rows = self._list("candidate_monitoring", parent_id=pool_id)
        if monitoring_state: rows = [r for r in rows if r.get("monitoring_state") == monitoring_state]
        return sorted(rows, key=lambda r: r.get("added_at", ""))


def make_research_plan_store():
    backend = os.environ.get("AGORA_RESEARCH_STORE_BACKEND", os.environ.get("AGORA_RESEARCH_PLAN_STORE_BACKEND", "off")).strip().lower()
    if backend in ("", "off", "memory"): return MemoryResearchPlanStore()
    if backend != "postgres": raise ValueError("AGORA_RESEARCH_STORE_BACKEND must be off or postgres")
    dsn = os.environ.get("AGORA_RESEARCH_STORE_DSN") or os.environ.get("DATABASE_URL")
    if not dsn: raise ValueError("AGORA_RESEARCH_STORE_DSN or DATABASE_URL is required")
    return PostgresResearchPlanStore(dsn=dsn, schema=os.environ.get("AGORA_RESEARCH_STORE_SCHEMA", "agora_research"))

"""Agora research plan store — in-memory backend for dev / tests.

Backend env:
  AGORA_RESEARCH_PLAN_STORE_BACKEND   off | postgres  (default: off)

Only the in-memory backend is implemented here. A Postgres backend
can be added as a later task once the BFF facade contract is stable.
"""
from __future__ import annotations

import os
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


def make_research_plan_store() -> MemoryResearchPlanStore:
    """Factory: return the configured store backend (only memory for now)."""
    # AGORA_RESEARCH_PLAN_STORE_BACKEND is reserved for a future Postgres backend.
    _ = os.environ.get("AGORA_RESEARCH_PLAN_STORE_BACKEND", "off")
    return MemoryResearchPlanStore()

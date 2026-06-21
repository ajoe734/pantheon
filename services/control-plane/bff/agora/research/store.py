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


def make_research_plan_store() -> MemoryResearchPlanStore:
    """Factory: return the configured store backend (only memory for now)."""
    # AGORA_RESEARCH_PLAN_STORE_BACKEND is reserved for a future Postgres backend.
    _ = os.environ.get("AGORA_RESEARCH_PLAN_STORE_BACKEND", "off")
    return MemoryResearchPlanStore()

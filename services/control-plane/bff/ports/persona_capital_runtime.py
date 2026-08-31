"""Domain port for Persona, Capital, Deployment, Runtime, Ranking, and Evolution reads.

This module formalizes two disposition-matrix items from the BFF
architecture-cleanup program into typed, testable domain ports:

- ACG-02-011 (MERGE): "Persona, Capital, Deployment and Runtime methods" --
  route reads (and any command-shaped writes) through domain owners; extract
  pure DTO projectors. This module implements the read side: ``PersonaFleetPort``,
  ``CapitalPoolPort``, ``DeploymentPlanPort``, and ``RuntimePort`` are narrow
  wrappers around existing owner stores/services. They accept constructor-injected
  ``store`` objects or ``records_provider``/``*_reader`` callables -- never their
  own storage, and never a direct import of ``main.py`` or ``read_store.py``
  module globals.

- ACG-02-012 (MIGRATE): "ranking, rebalance, allocation, containment, league and
  Evolution methods" -- separate capital-owned behavior from BFF-owned
  composition and remove mixed storage. ``RankingProjectionPort`` and
  ``EvolutionProjectionPort`` own only pure DTO/projection logic (sorting,
  filtering, derivation) that composes reads supplied by injected readers.
  Neither port owns raw entity storage: every dataset they touch (rankings,
  rebalances, capital allocations, containments, persona league, evolution
  programs, evolution decisions) has exactly one named owner/reader injected
  from outside this module.

This module is intentionally READ-ONLY / projector-only. It performs no
writes and holds no module-level singleton state or file/db access of its
own. If a write-shaped operation is ever needed here, it must accept and
delegate to an injected ``command_api`` callable shaped like
``command_executor.py``'s entry points -- never implement local persistence.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_rfc3339(val: Any) -> Optional[datetime]:
    if not val or not isinstance(val, str):
        return None
    try:
        clean = val.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def _deep_copy(value: Any) -> Any:
    return json.loads(json.dumps(value))


# Operational persona lifecycle states, mirrored from the existing BFF
# persona-fleet compatibility surface. This port owns only the projection of
# this set over persona records supplied by an injected reader/store -- it
# does not own persona storage.
PERSONA_OPERATIONAL_LIFECYCLE_STATES = frozenset({
    "active",
    "deployed",
    "ready",
    "running",
    "paper",
    "paper_running",
    "canary",
    "canary_running",
    "live",
    "live_running",
})


# ---------------------------------------------------------------------------
# Persona mutation boundary
# ---------------------------------------------------------------------------

class PersonaMutationPort(Protocol):
    """Explicit write boundary used by Persona lifecycle command handlers.

    ``ReadSurfacePorts`` deliberately exposes none of these methods.  The
    concrete adapter must be backed by the authoritative Persona owner, rather
    than by a BFF projection or an in-process read store.
    """

    def create_persona(
        self,
        *,
        persona_id: str,
        name: str,
        actor_id: str,
        **kwargs: Any,
    ) -> Dict[str, Any]: ...

    def update_persona(
        self,
        persona_id: str,
        *,
        lifecycle_state: Optional[str] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        **kwargs: Any,
    ) -> Optional[Dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Persona Fleet Port (ACG-02-011)
# ---------------------------------------------------------------------------

class PersonaFleetPort:
    """Narrow read port over the Persona registry's fleet/intent surface.

    Wraps an injected ``store`` (following the same duck-typed contract as
    ``read_store.ReadSurfaceStore``: ``list_personas(...)`` / ``get_persona(id)``)
    or a plain ``records_provider`` callable. Never invents its own storage.
    """

    def __init__(
        self,
        *,
        store: Optional[Any] = None,
        records_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self._store = store
        self._records_provider = records_provider

    def _get_raw_records(self) -> Tuple[str, List[Dict[str, Any]]]:
        if self._store is not None:
            try:
                if hasattr(self._store, "list_personas") and callable(self._store.list_personas):
                    return "store", list(self._store.list_personas() or [])
            except Exception:
                return "unavailable", []
        if self._records_provider is not None:
            try:
                records = self._records_provider()
                return "service", [dict(r) for r in (records or [])]
            except Exception:
                return "unavailable", []
        return "missing", []

    def get_surface_status(self) -> Dict[str, Any]:
        source, records = self._get_raw_records()
        if source in ("missing", "unavailable"):
            return {
                "status": "unavailable",
                "source": source,
                "message": "Persona registry store is unavailable or unconfigured.",
            }
        return {
            "status": "ok" if records else "degraded",
            "source": source,
            "message": None if records else "Persona registry store is empty.",
        }

    @staticmethod
    def _persona_id(record: Mapping[str, Any]) -> str:
        return str(record.get("persona_id") or record.get("id") or "").strip()

    def list_personas(
        self,
        *,
        lifecycle_state: Optional[str] = None,
        mandate: Optional[str] = None,
        strategy_family: Optional[str] = None,
        operational_only: bool = False,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        """List personas with optional filtering.

        ``include_market_persona_defaults`` is accepted for compatibility with
        composite caller interfaces that layer market defaults separately.
        """
        _, raw_records = self._get_raw_records()
        items = [_deep_copy(record) for record in raw_records if self._persona_id(record)]
        if lifecycle_state:
            items = [p for p in items if str(p.get("lifecycle_state") or "") == lifecycle_state]
        if mandate:
            items = [p for p in items if str(p.get("mandate") or "") == mandate]
        if strategy_family:
            items = [p for p in items if str(p.get("strategy_family") or "") == strategy_family]
        if operational_only:
            items = [
                p
                for p in items
                if str(p.get("lifecycle_state") or "").strip().lower()
                in PERSONA_OPERATIONAL_LIFECYCLE_STATES
            ]
        return items

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(persona_id or "").strip()
        if not clean_id:
            return None
        if self._store is not None and hasattr(self._store, "get_persona"):
            try:
                found = self._store.get_persona(clean_id)
                return _deep_copy(found) if found is not None else None
            except Exception:
                pass
        for persona in self.list_personas():
            if self._persona_id(persona) == clean_id:
                return persona
        return None

    def list_operational_personas(self) -> List[Dict[str, Any]]:
        return self.list_personas(operational_only=True)


# ---------------------------------------------------------------------------
# Capital Pool Port (ACG-02-011)
# ---------------------------------------------------------------------------

class CapitalPoolPort:
    """Narrow read port over the Capital service's pool and binding surface."""

    def __init__(
        self,
        *,
        store: Optional[Any] = None,
        pools_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        bindings_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self._store = store
        self._pools_provider = pools_provider
        self._bindings_provider = bindings_provider

    def _get_raw_pools(self) -> Tuple[str, List[Dict[str, Any]]]:
        if self._store is not None:
            try:
                if hasattr(self._store, "list_capital_pools") and callable(self._store.list_capital_pools):
                    return "store", list(self._store.list_capital_pools() or [])
            except Exception:
                return "unavailable", []
        if self._pools_provider is not None:
            try:
                return "service", [dict(r) for r in (self._pools_provider() or [])]
            except Exception:
                return "unavailable", []
        return "missing", []

    def _get_raw_bindings(self) -> Tuple[str, List[Dict[str, Any]]]:
        if self._store is not None:
            try:
                if hasattr(self._store, "list_bindings") and callable(self._store.list_bindings):
                    return "store", list(self._store.list_bindings() or [])
            except Exception:
                return "unavailable", []
        if self._bindings_provider is not None:
            try:
                return "service", [dict(r) for r in (self._bindings_provider() or [])]
            except Exception:
                return "unavailable", []
        return "missing", []

    def get_surface_status(self) -> Dict[str, Any]:
        pool_source, pools = self._get_raw_pools()
        binding_source, bindings = self._get_raw_bindings()
        if pool_source in ("missing", "unavailable"):
            return {
                "status": "unavailable",
                "source": pool_source,
                "message": "Capital pool store is unavailable or unconfigured.",
            }
        status = "ok" if pools else "degraded"
        if binding_source in ("missing", "unavailable"):
            status = "degraded"
        return {
            "status": status,
            "source": pool_source,
            "bindings_source": binding_source,
            "message": None if pools else "Capital pool store is empty.",
        }

    @staticmethod
    def _pool_id(record: Mapping[str, Any]) -> str:
        return str(record.get("pool_id") or record.get("id") or "").strip()

    @staticmethod
    def _binding_id(record: Mapping[str, Any]) -> str:
        return str(record.get("binding_id") or record.get("id") or "").strip()

    def list_capital_pools(
        self,
        *,
        status: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        """List capital pools with optional status filtering.

        ``include_market_persona_defaults`` is accepted for compatibility with
        composite caller interfaces that layer market defaults separately.
        """
        _, raw = self._get_raw_pools()
        items = [_deep_copy(p) for p in raw if self._pool_id(p)]
        if status:
            items = [p for p in items if str(p.get("status") or "") == status]
        return items

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(pool_id or "").strip()
        if not clean_id:
            return None
        for pool in self.list_capital_pools():
            if self._pool_id(pool) == clean_id:
                return pool
        return None

    def list_bindings(
        self,
        *,
        persona_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        role: Optional[str] = None,
        validity: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        """List persona-capital bindings with optional filtering.

        ``include_market_persona_defaults`` is accepted for compatibility with
        composite caller interfaces that layer market defaults separately.
        """
        _, raw = self._get_raw_bindings()
        items = [_deep_copy(b) for b in raw if self._binding_id(b)]
        if persona_id:
            items = [b for b in items if str(b.get("persona_id") or "") == persona_id]
        if capital_pool_id:
            items = [b for b in items if str(b.get("capital_pool_id") or "") == capital_pool_id]
        if role:
            items = [b for b in items if str(b.get("role") or "") == role]
        if validity:
            items = [b for b in items if str(b.get("validity") or "") == validity]
        return items

    def get_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(binding_id or "").strip()
        if not clean_id:
            return None
        for binding in self.list_bindings():
            if self._binding_id(binding) == clean_id:
                return binding
        return None

    def get_bindings_for_pool(self, pool_id: Optional[str]) -> List[Dict[str, Any]]:
        if not pool_id:
            return []
        return self.list_bindings(capital_pool_id=str(pool_id))

    def get_bindings_for_persona(self, persona_id: Optional[str]) -> List[Dict[str, Any]]:
        if not persona_id:
            return []
        return self.list_bindings(persona_id=str(persona_id))


# ---------------------------------------------------------------------------
# Deployment Plan Port (ACG-02-011)
# ---------------------------------------------------------------------------

class DeploymentPlanPort:
    """Narrow read port over the Deployment service's plan surface."""

    def __init__(
        self,
        *,
        store: Optional[Any] = None,
        plans_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self._store = store
        self._plans_provider = plans_provider

    def _get_raw_plans(self) -> Tuple[str, List[Dict[str, Any]]]:
        if self._store is not None:
            try:
                if hasattr(self._store, "list_deployment_plans") and callable(self._store.list_deployment_plans):
                    return "store", list(self._store.list_deployment_plans() or [])
            except Exception:
                return "unavailable", []
        if self._plans_provider is not None:
            try:
                return "service", [dict(r) for r in (self._plans_provider() or [])]
            except Exception:
                return "unavailable", []
        return "missing", []

    def get_surface_status(self) -> Dict[str, Any]:
        source, records = self._get_raw_plans()
        if source in ("missing", "unavailable"):
            return {
                "status": "unavailable",
                "source": source,
                "message": "Deployment plan store is unavailable or unconfigured.",
            }
        return {
            "status": "ok" if records else "degraded",
            "source": source,
            "message": None if records else "Deployment plan store is empty.",
        }

    @staticmethod
    def _plan_id(record: Mapping[str, Any]) -> str:
        return str(record.get("plan_id") or record.get("id") or "").strip()

    def list_deployment_plans(
        self,
        *,
        status: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _, raw = self._get_raw_plans()
        items = [_deep_copy(p) for p in raw if self._plan_id(p)]
        if status:
            items = [p for p in items if str(p.get("status") or "").lower() == status.lower()]
        if capital_pool_id:
            items = [
                p
                for p in items
                if str(p.get("capital_pool_id") or p.get("target_pool_id") or "") == capital_pool_id
            ]
        return items

    def get_deployment_plan(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(plan_id or "").strip()
        if not clean_id:
            return None
        for plan in self.list_deployment_plans():
            if self._plan_id(plan) == clean_id:
                return plan
        return None


# ---------------------------------------------------------------------------
# Runtime Port (ACG-02-011)
# ---------------------------------------------------------------------------

class RuntimePort:
    """Narrow read port over the Runtime service's binding/roster surface."""

    def __init__(
        self,
        *,
        store: Optional[Any] = None,
        runtime_bindings_provider: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self._store = store
        self._runtime_bindings_provider = runtime_bindings_provider

    def _get_raw_bindings(self) -> Tuple[str, List[Dict[str, Any]]]:
        if self._store is not None:
            try:
                if hasattr(self._store, "list_runtime_bindings") and callable(self._store.list_runtime_bindings):
                    return "store", list(self._store.list_runtime_bindings() or [])
            except Exception:
                return "unavailable", []
        if self._runtime_bindings_provider is not None:
            try:
                return "service", [dict(r) for r in (self._runtime_bindings_provider() or [])]
            except Exception:
                return "unavailable", []
        return "missing", []

    def get_surface_status(self) -> Dict[str, Any]:
        source, records = self._get_raw_bindings()
        if source in ("missing", "unavailable"):
            return {
                "status": "unavailable",
                "source": source,
                "message": "Runtime binding store is unavailable or unconfigured.",
            }
        return {
            "status": "ok" if records else "degraded",
            "source": source,
            "message": None if records else "Runtime binding store is empty.",
        }

    @staticmethod
    def _runtime_id(record: Mapping[str, Any]) -> str:
        return str(
            record.get("runtime_id")
            or record.get("runtime_binding_id")
            or record.get("binding_id")
            or record.get("id")
            or ""
        ).strip()

    def list_runtime_bindings(
        self,
        *,
        deployment_mode: Optional[str] = None,
        version: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        """List runtime bindings with optional filtering.

        ``include_market_persona_defaults`` is accepted for compatibility with
        composite caller interfaces that layer market defaults separately.
        """
        _, raw = self._get_raw_bindings()
        items = [_deep_copy(r) for r in raw if self._runtime_id(r)]
        if deployment_mode:
            items = [r for r in items if str(r.get("deployment_mode") or "") == deployment_mode]
        if version:
            items = [r for r in items if str(r.get("version") or "") == version]
        return items

    def get_runtime_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(binding_id or "").strip()
        if not clean_id:
            return None
        for record in self.list_runtime_bindings():
            candidate_ids = {
                str(record.get("binding_id") or "").strip(),
                str(record.get("id") or "").strip(),
                str(record.get("runtime_binding_id") or "").strip(),
            }
            if clean_id in candidate_ids and clean_id:
                return record
        return None

    def get_runtime_binding_by_runtime_id(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(runtime_id or "").strip()
        if not clean_id:
            return None
        for record in self.list_runtime_bindings():
            if str(record.get("runtime_id") or "").strip() == clean_id:
                return record
        return None


# ---------------------------------------------------------------------------
# Ranking Projection Port (ACG-02-012) -- pure composition, no raw storage
# ---------------------------------------------------------------------------

class RankingProjectionPort:
    """Pure DTO/projection composition over ranking, rebalance, allocation,
    containment, and persona-league datasets.

    Each dataset injected here has exactly one named owner/reader; this port
    never persists any of them. It owns only sorting/filtering/derivation
    logic and cross-dataset composition (e.g. building one persona's capital
    ranking view out of separately-owned league, allocation, and containment
    readers).
    """

    def __init__(
        self,
        *,
        rankings_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        ranking_formulas_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        persona_league_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        rebalances_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        capital_allocations_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        containments_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self._rankings_reader = rankings_reader
        self._ranking_formulas_reader = ranking_formulas_reader
        self._persona_league_reader = persona_league_reader
        self._rebalances_reader = rebalances_reader
        self._capital_allocations_reader = capital_allocations_reader
        self._containments_reader = containments_reader

    @staticmethod
    def _read(reader: Optional[Callable[[], List[Dict[str, Any]]]]) -> Tuple[str, List[Dict[str, Any]]]:
        if reader is None:
            return "missing", []
        try:
            records = reader()
            return "service", [dict(r) for r in (records or [])]
        except Exception:
            return "unavailable", []

    def get_surface_status(self) -> Dict[str, Any]:
        surfaces = {}
        for name, reader in (
            ("rankings", self._rankings_reader),
            ("ranking_formulas", self._ranking_formulas_reader),
            ("persona_league", self._persona_league_reader),
            ("rebalances", self._rebalances_reader),
            ("capital_allocations", self._capital_allocations_reader),
            ("containments", self._containments_reader),
        ):
            source, records = self._read(reader)
            if source in ("missing", "unavailable"):
                surfaces[name] = {"status": "unavailable", "source": source, "message": f"{name} reader is unavailable or unconfigured."}
            else:
                surfaces[name] = {"status": "ok" if records else "degraded", "source": source, "message": None if records else f"{name} reader returned no records."}
        overall = "ok" if all(s["status"] == "ok" for s in surfaces.values()) else (
            "unavailable" if all(s["status"] == "unavailable" for s in surfaces.values()) else "degraded"
        )
        return {"status": overall, "surfaces": surfaces}

    # -- Rankings ---------------------------------------------------------

    @staticmethod
    def _ranking_id(record: Mapping[str, Any]) -> str:
        return str(record.get("ranking_id") or record.get("id") or "").strip()

    def list_rankings(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        _, raw = self._read(self._rankings_reader)
        items = [_deep_copy(r) for r in raw if self._ranking_id(r)]
        if status:
            items = [r for r in items if str(r.get("status") or "") == status]
        return sorted(items, key=lambda x: self._ranking_id(x))

    def get_ranking(self, ranking_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(ranking_id or "").strip()
        if not clean_id:
            return None
        for ranking in self.list_rankings():
            if self._ranking_id(ranking) == clean_id:
                return ranking
        return None

    def list_ranking_formulas(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        _, raw = self._read(self._ranking_formulas_reader)
        items = [_deep_copy(r) for r in raw]
        if status:
            items = [r for r in items if str(r.get("status") or "") == status]
        return sorted(items, key=lambda x: str(x.get("formula_id") or x.get("id") or ""))

    # -- Persona league -----------------------------------------------------

    @staticmethod
    def _league_persona_id(record: Mapping[str, Any]) -> str:
        return str(record.get("persona_id") or record.get("id") or "").strip()

    def list_persona_league(
        self,
        *,
        market_scope: Optional[str] = None,
        status: Optional[str] = None,
        include_market_persona_defaults: bool = False,
    ) -> List[Dict[str, Any]]:
        """List persona league standings with optional scope and status filtering.

        ``include_market_persona_defaults`` is accepted for compatibility with
        composite caller interfaces that layer market defaults separately.
        """
        _, raw = self._read(self._persona_league_reader)
        items = [_deep_copy(r) for r in raw]
        if market_scope:
            requested = {s.strip().upper() for s in market_scope.split(",") if s.strip()}
            items = [
                item
                for item in items
                if requested.intersection({str(scope).upper() for scope in (item.get("market_scope") or [])})
            ]
        if status:
            requested_statuses = {s.strip().lower() for s in status.split(",") if s.strip()}
            items = [item for item in items if str(item.get("status") or "").lower() in requested_statuses]
        return sorted(
            items,
            key=lambda item: (
                int(item.get("rank") or 9999),
                -float(item.get("league_score") or 0.0),
                self._league_persona_id(item),
            ),
        )

    def get_persona_league_entry(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(persona_id or "").strip()
        if not clean_id:
            return None
        for entry in self.list_persona_league():
            if self._league_persona_id(entry) == clean_id:
                return entry
        return None

    # -- Rebalances -----------------------------------------------------

    @staticmethod
    def _rebalance_id(record: Mapping[str, Any]) -> str:
        return str(record.get("rebalance_id") or record.get("id") or "").strip()

    def list_rebalances(
        self,
        *,
        status: Optional[str] = None,
        pool_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _, raw = self._read(self._rebalances_reader)
        items = [_deep_copy(r) for r in raw if self._rebalance_id(r)]
        if status:
            items = [r for r in items if str(r.get("status") or "") == status]
        if pool_id:
            items = [r for r in items if str(r.get("capital_pool_id") or "") == pool_id]
        return sorted(
            items,
            key=lambda x: (_parse_rfc3339(x.get("created_at")) or datetime.min).replace(tzinfo=None),
            reverse=True,
        )

    def get_rebalance(self, rebalance_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(rebalance_id or "").strip()
        if not clean_id:
            return None
        for item in self.list_rebalances():
            if self._rebalance_id(item) == clean_id:
                return item
        return None

    # -- Capital allocations -----------------------------------------------

    def list_capital_allocations(
        self,
        *,
        capital_pool_id: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        _, raw = self._read(self._capital_allocations_reader)
        items = [_deep_copy(r) for r in raw]
        if capital_pool_id:
            items = [r for r in items if str(r.get("capital_pool_id") or "") == capital_pool_id]
        if persona_id:
            items = [r for r in items if str(r.get("persona_id") or "") == persona_id]
        return sorted(
            items,
            key=lambda item: (
                str(item.get("capital_pool_id") or ""),
                str(item.get("persona_id") or ""),
                str(item.get("capital_sleeve_id") or item.get("sleeve_id") or ""),
            ),
        )

    # -- Containments -----------------------------------------------------

    def list_containments(self, *, persona_id: Optional[str] = None) -> List[Dict[str, Any]]:
        _, raw = self._read(self._containments_reader)
        items = [_deep_copy(r) for r in raw]
        if persona_id:
            items = [r for r in items if str(r.get("persona_id") or "") == persona_id]
        return sorted(
            items,
            key=lambda item: str(
                item.get("executed_at")
                or item.get("updated_at")
                or item.get("applied_at")
                or item.get("created_at")
                or ""
            ),
            reverse=True,
        )

    def get_persona_containment(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        if not persona_id:
            return None
        items = self.list_containments(persona_id=str(persona_id))
        return items[0] if items else None

    # -- Composition --------------------------------------------------------

    def build_persona_capital_ranking_view(self, persona_id: Optional[str]) -> Dict[str, Any]:
        """Compose a single persona's ranking/capital view from independently-
        owned readers. This is pure DTO composition -- no dataset here is
        written or cached by this port; every field comes straight from an
        injected reader's current snapshot.
        """
        clean_id = str(persona_id or "").strip()
        league_entry = self.get_persona_league_entry(clean_id) if clean_id else None
        allocations = self.list_capital_allocations(persona_id=clean_id) if clean_id else []
        containment = self.get_persona_containment(clean_id) if clean_id else None
        return {
            "persona_id": clean_id or None,
            "league_entry": league_entry,
            "capital_allocations": allocations,
            "active_containment": containment,
            "is_contained": containment is not None
            and str((containment or {}).get("status") or "").lower() not in ("released", "resolved", "cleared"),
        }


# ---------------------------------------------------------------------------
# Evolution Projection Port (ACG-02-012) -- pure composition, no raw storage
# ---------------------------------------------------------------------------

class EvolutionProjectionPort:
    """Pure DTO/projection composition over Evolution program/decision datasets.

    Mirrors the derived-run/derived-candidate projection already used by the
    read store, but as pure composition over injected readers: no state is
    owned or persisted here.
    """

    def __init__(
        self,
        *,
        evolution_programs_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        evolution_decisions_reader: Optional[Callable[[], List[Dict[str, Any]]]] = None,
    ) -> None:
        self._evolution_programs_reader = evolution_programs_reader
        self._evolution_decisions_reader = evolution_decisions_reader

    @staticmethod
    def _read(reader: Optional[Callable[[], List[Dict[str, Any]]]]) -> Tuple[str, List[Dict[str, Any]]]:
        if reader is None:
            return "missing", []
        try:
            records = reader()
            return "service", [dict(r) for r in (records or [])]
        except Exception:
            return "unavailable", []

    def get_surface_status(self) -> Dict[str, Any]:
        surfaces = {}
        for name, reader in (
            ("evolution_programs", self._evolution_programs_reader),
            ("evolution_decisions", self._evolution_decisions_reader),
        ):
            source, records = self._read(reader)
            if source in ("missing", "unavailable"):
                surfaces[name] = {"status": "unavailable", "source": source, "message": f"{name} reader is unavailable or unconfigured."}
            else:
                surfaces[name] = {"status": "ok" if records else "degraded", "source": source, "message": None if records else f"{name} reader returned no records."}
        overall = "ok" if all(s["status"] == "ok" for s in surfaces.values()) else (
            "unavailable" if all(s["status"] == "unavailable" for s in surfaces.values()) else "degraded"
        )
        return {"status": overall, "surfaces": surfaces}

    @staticmethod
    def _program_id(record: Mapping[str, Any]) -> str:
        return str(record.get("program_id") or record.get("id") or "").strip()

    def list_evolution_programs(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        _, raw = self._read(self._evolution_programs_reader)
        items = [_deep_copy(r) for r in raw if self._program_id(r)]
        if status:
            items = [r for r in items if str(r.get("status") or "") == status]
        return sorted(
            items,
            key=lambda x: (_parse_rfc3339(x.get("created_at")) or datetime.min).replace(tzinfo=None),
            reverse=True,
        )

    def get_evolution_program(self, program_id: Optional[str]) -> Optional[Dict[str, Any]]:
        clean_id = str(program_id or "").strip()
        if not clean_id:
            return None
        for program in self.list_evolution_programs():
            if self._program_id(program) == clean_id:
                return program
        return None

    def list_evolution_decisions(self, *, status: Optional[str] = None) -> List[Dict[str, Any]]:
        _, raw = self._read(self._evolution_decisions_reader)
        items = [_deep_copy(r) for r in raw]
        if status:
            items = [r for r in items if str(r.get("status") or "") == status]
        return items

    def list_evolution_program_runs(self, program_id: Optional[str]) -> List[Dict[str, Any]]:
        """Pure derived projection: an evolution program's decisions,
        reshaped as run summaries. Composes two independently-owned readers;
        stores nothing of its own.
        """
        clean_id = str(program_id or "").strip()
        if not clean_id or not self.get_evolution_program(clean_id):
            return []
        related = [d for d in self.list_evolution_decisions() if str(d.get("program_id") or "") == clean_id]
        return [
            {
                "run_id": d.get("decision_id", d.get("id", "")),
                "program_id": clean_id,
                "status": d.get("status", "unknown"),
                "started_at": d.get("created_at"),
                "completed_at": d.get("resolved_at"),
                "score": d.get("score"),
                "artifact_ref": d.get("artifact_ref"),
            }
            for d in related
        ]

    def list_evolution_program_candidates(self, program_id: Optional[str]) -> List[Dict[str, Any]]:
        """Pure derived projection: pending decisions for a program, reshaped
        as candidate summaries.
        """
        clean_id = str(program_id or "").strip()
        if not clean_id or not self.get_evolution_program(clean_id):
            return []
        related = [
            d
            for d in self.list_evolution_decisions(status="pending")
            if str(d.get("program_id") or "") == clean_id
        ]
        return [
            {
                "candidate_id": d.get("decision_id", d.get("id", "")),
                "program_id": clean_id,
                "status": "pending",
                "score": d.get("score"),
                "proposed_at": d.get("created_at"),
            }
            for d in related
        ]


# ---------------------------------------------------------------------------
# Combined Persona/Capital/Deployment/Runtime Domain Port
# ---------------------------------------------------------------------------

class PersonaCapitalRuntimeDomainPort:
    """Consolidated domain port for Persona, Capital, Deployment, Runtime
    reads, and Ranking/Evolution projections.

    All reads route through injected sub-ports, each of which is itself
    constructed from injected stores/readers. This facade owns no storage
    and performs no writes; any future write-shaped verb must be added as a
    call into an injected ``command_api`` callable, never local persistence.
    """

    def __init__(
        self,
        *,
        persona_port: Optional[PersonaFleetPort] = None,
        capital_port: Optional[CapitalPoolPort] = None,
        deployment_port: Optional[DeploymentPlanPort] = None,
        runtime_port: Optional[RuntimePort] = None,
        ranking_port: Optional[RankingProjectionPort] = None,
        evolution_port: Optional[EvolutionProjectionPort] = None,
    ) -> None:
        self.persona = persona_port or PersonaFleetPort()
        self.capital = capital_port or CapitalPoolPort()
        self.deployment = deployment_port or DeploymentPlanPort()
        self.runtime = runtime_port or RuntimePort()
        self.ranking = ranking_port or RankingProjectionPort()
        self.evolution = evolution_port or EvolutionProjectionPort()

    # Persona delegates
    def list_personas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.persona.list_personas(**kwargs)

    def get_persona(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.persona.get_persona(persona_id)

    def list_operational_personas(self) -> List[Dict[str, Any]]:
        return self.persona.list_operational_personas()

    # Capital delegates
    def list_capital_pools(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.capital.list_capital_pools(**kwargs)

    def get_capital_pool(self, pool_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.capital.get_capital_pool(pool_id)

    def list_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.capital.list_bindings(**kwargs)

    def get_bindings_for_pool(self, pool_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.capital.get_bindings_for_pool(pool_id)

    def get_bindings_for_persona(self, persona_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.capital.get_bindings_for_persona(persona_id)

    # Deployment delegates
    def list_deployment_plans(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.deployment.list_deployment_plans(**kwargs)

    def get_deployment_plan(self, plan_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.deployment.get_deployment_plan(plan_id)

    # Runtime delegates
    def list_runtime_bindings(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.runtime.list_runtime_bindings(**kwargs)

    def get_runtime_binding(self, binding_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.runtime.get_runtime_binding(binding_id)

    def get_runtime_binding_by_runtime_id(self, runtime_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.runtime.get_runtime_binding_by_runtime_id(runtime_id)

    # Ranking delegates
    def list_rankings(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ranking.list_rankings(**kwargs)

    def get_ranking(self, ranking_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ranking.get_ranking(ranking_id)

    def list_ranking_formulas(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ranking.list_ranking_formulas(**kwargs)

    def list_persona_league(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ranking.list_persona_league(**kwargs)

    def get_persona_league_entry(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ranking.get_persona_league_entry(persona_id)

    def list_rebalances(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ranking.list_rebalances(**kwargs)

    def get_rebalance(self, rebalance_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ranking.get_rebalance(rebalance_id)

    def list_capital_allocations(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ranking.list_capital_allocations(**kwargs)

    def list_containments(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.ranking.list_containments(**kwargs)

    def get_persona_containment(self, persona_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.ranking.get_persona_containment(persona_id)

    def build_persona_capital_ranking_view(self, persona_id: Optional[str]) -> Dict[str, Any]:
        return self.ranking.build_persona_capital_ranking_view(persona_id)

    # Evolution delegates
    def list_evolution_programs(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.evolution.list_evolution_programs(**kwargs)

    def get_evolution_program(self, program_id: Optional[str]) -> Optional[Dict[str, Any]]:
        return self.evolution.get_evolution_program(program_id)

    def list_evolution_decisions(self, **kwargs: Any) -> List[Dict[str, Any]]:
        return self.evolution.list_evolution_decisions(**kwargs)

    def list_evolution_program_runs(self, program_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.evolution.list_evolution_program_runs(program_id)

    def list_evolution_program_candidates(self, program_id: Optional[str]) -> List[Dict[str, Any]]:
        return self.evolution.list_evolution_program_candidates(program_id)

    def get_surface_status(self) -> Dict[str, Any]:
        return {
            "persona": self.persona.get_surface_status(),
            "capital": self.capital.get_surface_status(),
            "deployment": self.deployment.get_surface_status(),
            "runtime": self.runtime.get_surface_status(),
            "ranking": self.ranking.get_surface_status(),
            "evolution": self.evolution.get_surface_status(),
        }


CompositePersonaCapitalRuntimePort = PersonaCapitalRuntimeDomainPort
InMemoryPersonaCapitalRuntimePort = PersonaCapitalRuntimeDomainPort


def create_persona_capital_runtime_port(
    *,
    persona_port: Optional[PersonaFleetPort] = None,
    capital_port: Optional[CapitalPoolPort] = None,
    deployment_port: Optional[DeploymentPlanPort] = None,
    runtime_port: Optional[RuntimePort] = None,
    ranking_port: Optional[RankingProjectionPort] = None,
    evolution_port: Optional[EvolutionProjectionPort] = None,
) -> PersonaCapitalRuntimeDomainPort:
    """Create a consolidated PersonaCapitalRuntimeDomainPort."""
    return PersonaCapitalRuntimeDomainPort(
        persona_port=persona_port,
        capital_port=capital_port,
        deployment_port=deployment_port,
        runtime_port=runtime_port,
        ranking_port=ranking_port,
        evolution_port=evolution_port,
    )


def create_in_memory_persona_capital_runtime_port(
    *,
    personas: Optional[List[Dict[str, Any]]] = None,
    capital_pools: Optional[List[Dict[str, Any]]] = None,
    bindings: Optional[List[Dict[str, Any]]] = None,
    deployment_plans: Optional[List[Dict[str, Any]]] = None,
    runtime_bindings: Optional[List[Dict[str, Any]]] = None,
    rankings: Optional[List[Dict[str, Any]]] = None,
    ranking_formulas: Optional[List[Dict[str, Any]]] = None,
    persona_league: Optional[List[Dict[str, Any]]] = None,
    rebalances: Optional[List[Dict[str, Any]]] = None,
    capital_allocations: Optional[List[Dict[str, Any]]] = None,
    containments: Optional[List[Dict[str, Any]]] = None,
    evolution_programs: Optional[List[Dict[str, Any]]] = None,
    evolution_decisions: Optional[List[Dict[str, Any]]] = None,
    **kwargs: Any,
) -> PersonaCapitalRuntimeDomainPort:
    """Create an in-memory PersonaCapitalRuntimeDomainPort for testing."""
    persona_p = PersonaFleetPort(records_provider=lambda: list(personas or []))
    capital_p = CapitalPoolPort(
        pools_provider=lambda: list(capital_pools or []),
        bindings_provider=lambda: list(bindings or []),
    )
    deploy_p = DeploymentPlanPort(plans_provider=lambda: list(deployment_plans or []))
    runtime_p = RuntimePort(runtime_bindings_provider=lambda: list(runtime_bindings or []))
    ranking_p = RankingProjectionPort(
        rankings_reader=lambda: list(rankings or []),
        ranking_formulas_reader=lambda: list(ranking_formulas or []),
        persona_league_reader=lambda: list(persona_league or []),
        rebalances_reader=lambda: list(rebalances or []),
        capital_allocations_reader=lambda: list(capital_allocations or []),
        containments_reader=lambda: list(containments or []),
    )
    evolution_p = EvolutionProjectionPort(
        evolution_programs_reader=lambda: list(evolution_programs or []),
        evolution_decisions_reader=lambda: list(evolution_decisions or []),
    )
    return PersonaCapitalRuntimeDomainPort(
        persona_port=persona_p,
        capital_port=capital_p,
        deployment_port=deploy_p,
        runtime_port=runtime_p,
        ranking_port=ranking_p,
        evolution_port=evolution_p,
    )


__all__ = [
    "PersonaFleetPort",
    "CapitalPoolPort",
    "DeploymentPlanPort",
    "RuntimePort",
    "RankingProjectionPort",
    "EvolutionProjectionPort",
    "PersonaMutationPort",
    "PersonaCapitalRuntimeDomainPort",
    "CompositePersonaCapitalRuntimePort",
    "InMemoryPersonaCapitalRuntimePort",
    "create_persona_capital_runtime_port",
    "create_in_memory_persona_capital_runtime_port",
    "PERSONA_OPERATIONAL_LIFECYCLE_STATES",
]

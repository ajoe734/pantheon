"""Storage for the two regime tables, ``market_state`` and ``regime_states`` (design R2).

Reads are open to the routes. Writes exist only as the ``evaluator_*`` methods, which only
``regime/evaluator.py`` calls (a test scans the package for any other caller). Postgres uses
the registry's JSONB owner store, one table per name in the ``registry`` schema.
"""
from __future__ import annotations

import copy
import os
import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional, Protocol, Sequence

from ..models import utc_now_iso

SERVED_STATES = ("validated", "active", "superseded", "invalidated")  # never "proposed"
WRITE_METHODS = ("evaluator_commit", "evaluator_invalidate")


class _Table(Protocol):
    def get(self, record_id: str) -> Optional[dict]: ...
    def put(self, record_id: str, payload: dict) -> None: ...
    def list_all(self) -> list[dict]: ...


class _MemoryTable:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def get(self, record_id: str) -> Optional[dict]:
        row = self._rows.get(record_id)
        return copy.deepcopy(row) if row is not None else None

    def put(self, record_id: str, payload: dict) -> None:
        self._rows[record_id] = copy.deepcopy(payload)

    def list_all(self) -> list[dict]:
        return [copy.deepcopy(r) for r in self._rows.values()]


def market_state_id(market: str, session: str, model_set_version: str, revision: int) -> str:
    return f"{market}:{session}:{model_set_version}:r{revision}"


class RegimeStore:
    """Two tables over a pair of key/JSON tables; memory by default, Postgres via ``postgres``."""

    MARKET_TABLE = "registry.market_state"
    REGIME_TABLE = "registry.regime_states"

    def __init__(self, market_state: Optional[_Table] = None, regime_states: Optional[_Table] = None,
                 *, guard: Any = None) -> None:
        self._market = market_state or _MemoryTable()
        self._regimes = regime_states or _MemoryTable()
        self._guard = guard
        self._lock = threading.RLock()

    @classmethod
    def postgres(cls, dsn: str, *, bootstrap: bool = True) -> "RegimeStore":
        from services.foundation.postgres_json_store import PostgresJsonOwnerStore

        market = PostgresJsonOwnerStore(dsn=dsn, table=cls.MARKET_TABLE, owner_service="registry-svc", bootstrap=bootstrap)
        regimes = PostgresJsonOwnerStore(dsn=dsn, table=cls.REGIME_TABLE, owner_service="registry-svc", bootstrap=bootstrap)
        return cls(market, regimes, guard=market)

    @contextmanager
    def _serialized(self, key: str) -> Iterator[None]:
        """Process lock plus, on Postgres, a transaction advisory lock held for the whole commit."""
        with self._lock:
            if self._guard is None:
                yield
                return
            with self._guard.transaction() as conn:
                self._guard.advisory_xact_lock(key, conn=conn)
                yield

    # -- reads ---------------------------------------------------------------------------------

    def get_market_state(self, market: str, session: str, model_set_version: str,
                         revision: Optional[int] = None) -> Optional[dict]:
        rows = [r for r in self._market.list_all() if r["market"] == market and r["session"] == session
                and r["model_set_version"] == model_set_version]
        if revision is not None:
            rows = [r for r in rows if r["revision"] == revision]
        return max(rows, key=lambda r: r["revision"]) if rows else None

    def list_market_states(self, market: str, session: Optional[str] = None, *, served_only: bool = True) -> list[dict]:
        rows = [r for r in self._market.list_all() if r["market"] == market
                and (session is None or r["session"] == session)
                and (not served_only or r["model_status"] == "approved")]
        return sorted(rows, key=lambda r: (r["session"], r["model_set_version"], r["revision"]))

    def get_regime(self, regime_id: str) -> Optional[dict]:
        return self._regimes.get(regime_id)

    def list_regimes(self, *, strategy_id: Optional[str] = None, session: Optional[str] = None,
                     market: Optional[str] = None, states: Optional[Sequence[str]] = None,
                     served_only: bool = True) -> list[dict]:
        out = []
        for row in self._regimes.list_all():
            state = row["record"]["state"]
            if served_only and state not in SERVED_STATES:
                continue
            if states is not None and state not in states:
                continue
            if strategy_id is not None and row["record"]["strategy_id"] != strategy_id:
                continue
            if session is not None and row["session"] != session:
                continue
            if market is not None and row["market"] != market:
                continue
            out.append(row)
        return sorted(out, key=lambda r: (r["session"], r["record"]["strategy_id"], r["revision"]))

    # -- writes (evaluator only) ---------------------------------------------------------------

    def evaluator_commit(self, market_state: dict, regimes: Sequence[dict]) -> dict:
        """Store the measurement once and each strategy record once; returns what was newly stored."""
        key = f"regime:{market_state['market']}:{market_state['session']}:{market_state['model_set_version']}"
        with self._serialized(key):
            ms_id = market_state_id(market_state["market"], market_state["session"],
                                    market_state["model_set_version"], market_state["revision"])
            stored_ms = self._market.get(ms_id) is None
            if stored_ms:
                self._market.put(ms_id, market_state)
            new_ids: list[str] = []
            for row in regimes:
                rid = row["record"]["regime_id"]
                if self._regimes.get(rid) is not None:
                    continue
                self._regimes.put(rid, row)
                new_ids.append(rid)
                if row["record"]["state"] == "validated":
                    self._activate(rid)
            return {"market_state_id": ms_id, "market_state_stored": stored_ms, "regime_ids": new_ids}

    def _activate(self, regime_id: str) -> None:
        """validated -> active; an older active for the strategy becomes superseded (R10)."""
        row = self._regimes.get(regime_id)
        record = row["record"]
        mine = (row["session"], row["revision"])
        siblings = [r for r in self._regimes.list_all()
                    if r["record"]["strategy_id"] == record["strategy_id"] and r["market"] == row["market"]
                    and r["record"]["regime_id"] != regime_id and r["record"]["state"] == "active"]
        newer = [r for r in siblings if (r["session"], r["revision"]) > mine]
        now = utc_now_iso()
        if newer:  # a late backfill never displaces a later active record
            self._transition(row, "superseded", now, superseded_by=max(newer, key=lambda r: (r["session"], r["revision"]))["record"]["regime_id"])
            return
        for old in siblings:
            self._transition(old, "superseded", now, superseded_by=regime_id)
        self._transition(row, "active", now)

    def _transition(self, row: dict, state: str, at: str, *, superseded_by: Optional[str] = None,
                    reason: Optional[str] = None) -> None:
        record = row["record"]
        record["state"] = state
        if superseded_by:
            record["superseded_by"] = superseded_by
        meta = record.setdefault("metadata", {})
        meta.setdefault("lifecycle", []).append({"state": state, "at": at, **({"reason": reason} if reason else {})})
        self._regimes.put(record["regime_id"], row)

    def evaluator_invalidate(self, market: str, session: str, model_set_version: str, revision: int,
                             reason: str) -> dict:
        """Revised inputs: the stored measurement and its strategy records become ``invalidated``."""
        key = f"regime:{market}:{session}:{model_set_version}"
        with self._serialized(key):
            now = utc_now_iso()
            ms = self._market.get(market_state_id(market, session, model_set_version, revision))
            if ms is not None and ms.get("state") != "invalidated":
                ms["state"] = "invalidated"
                ms["invalidated_reason"] = reason
                self._market.put(market_state_id(market, session, model_set_version, revision), ms)
            changed = []
            for row in self._regimes.list_all():
                rec = row["record"]
                if (row["market"], row["session"], row["model_set_version"], row["revision"]) != (market, session, model_set_version, revision):
                    continue
                if rec["state"] not in ("invalidated",):
                    self._transition(row, "invalidated", now, reason=reason)
                    changed.append(rec["regime_id"])
            return {"invalidated": changed}


_default: Optional[RegimeStore] = None
_default_lock = threading.Lock()


def build_regime_store() -> RegimeStore:
    from .. import pg_store

    if pg_store._registry_backend() == "postgres":
        return RegimeStore.postgres(pg_store._registry_dsn(), bootstrap=pg_store._registry_bootstrap())
    return RegimeStore()


def get_regime_store() -> RegimeStore:
    global _default
    with _default_lock:
        if _default is None:
            _default = build_regime_store()
        return _default


def reset_regime_store() -> None:
    global _default
    with _default_lock:
        _default = None

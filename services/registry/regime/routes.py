"""HTTP surface of the regime evaluator (design R12, C2).

One mutating route, ``POST /api/registry/regime/evaluate``, uses the registry's existing write
authentication. Every ``GET`` is read-only and serves approved-model records only (candidate
records are stored ``proposed`` and never served). Tenant scoping follows the strategy spec's
owner tenant; the shared market-state measurement is a market fact and is not tenant-scoped.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from .evaluator import MARKET, EvaluationRefused, RegimeEvaluator
from .store import get_regime_store

router = APIRouter()
STORE_ROOT_ENV = "REGISTRY_REGIME_STORE_ROOT"
# The market data store source-ingest writes (services/source_ingestion/runtime.py); datasets live in
# ``<root>/normalized/<dataset>``.
DEFAULT_STORE_ROOT = "/data/source-ingest/market_data_store"


class EvaluateRequest(BaseModel):
    session: str
    market: str = MARKET
    verify_inputs: bool = False


# Least-privilege role accepted on POST /regime/evaluate only: absent from the registry write and read
# role sets, so every other registry route refuses it.
REGIME_EVALUATOR_ROLE = "regime-evaluator"
REGIME_EVALUATE_ROLES = ("operator", "registry-writer", "admin", REGIME_EVALUATOR_ROLE)


def _semver(version: str) -> tuple:
    try:
        return tuple(int(p) for p in str(version).split("."))
    except ValueError:
        return (-1,)


def _store_root() -> str:
    return os.environ.get(STORE_ROOT_ENV) or DEFAULT_STORE_ROOT


def _observations_loader():
    def load():
        from pathlib import Path

        from services.research.regime.features import load_governed_observations, missing_input_datasets

        root = _store_root()
        if not Path(root).is_dir():
            raise EvaluationRefused("governed_store_unavailable", "governed dataset root is not mounted")
        missing = missing_input_datasets(root)
        if missing:
            raise EvaluationRefused("session_data_incomplete", "missing_datasets:" + ",".join(missing))
        return load_governed_observations(root)[0]

    return load


def _preflight_observations(body: "EvaluateRequest") -> list:
    """Load and check the governed inputs before any registry entry is read.

    Refuses (nothing read from the registry) when the request is invalid, the store is absent, a declared
    input dataset has no normalized directory (each one is listed), or the session close is not visible.
    """
    from .evaluator import SESSION_RE, session_complete

    if body.market != MARKET:
        raise EvaluationRefused("unsupported_market", body.market)
    if not SESSION_RE.match(body.session):
        raise EvaluationRefused("invalid_session", body.session)
    observations = _observations_loader()()
    refusal = session_complete(observations, body.session)
    if refusal:
        raise EvaluationRefused("session_data_incomplete", refusal)
    return observations


def _scoped(ctx, row: dict) -> bool:
    from .. import service as svc
    from ..models import BUILTIN_TENANT

    if "admin" in ctx.roles:
        return True
    return row.get("owner_tenant") in (BUILTIN_TENANT, svc._caller_tenant(ctx))


@router.post("/api/registry/regime/evaluate")
def evaluate(body: EvaluateRequest, authorization: Optional[str] = Header(default=None),
             x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
    from .. import service as svc

    svc._authenticate_registry(authorization, required_roles=REGIME_EVALUATE_ROLES)
    try:
        observations = _preflight_observations(body)
        ctx, header_tenant = svc._strategy_spec_read_scope(authorization, x_tenant_id)
        strategies = svc.list_regime_evaluation_strategies(ctx, header_tenant)
        models = svc.list_regime_evaluation_models()
        return RegimeEvaluator(get_regime_store(), lambda: observations).evaluate(
            session=body.session, market=body.market, models=models, strategies=strategies,
            verify_inputs=body.verify_inputs)
    except EvaluationRefused as exc:
        status = 422 if exc.code in ("invalid_session", "unsupported_market") else 409
        raise HTTPException(status_code=status, detail={"code": exc.code, "detail": exc.detail}) from exc


@router.get("/api/registry/regime/market-state/{market}/{session}")
def read_market_state(market: str, session: str, authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    from .. import service as svc

    svc._authenticate_registry_read(authorization)
    rows = get_regime_store().list_market_states(market, session)
    if not rows:
        raise HTTPException(status_code=404, detail="no served market state for this market and session")
    return {"market": market, "session": session, "market_states": rows}


@router.get("/api/registry/regime/states")
def read_states(strategy_id: Optional[str] = Query(default=None), session: Optional[str] = Query(default=None),
                market: str = Query(default=MARKET), authorization: Optional[str] = Header(default=None),
                x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
    from .. import service as svc

    ctx, _ = svc._strategy_spec_read_scope(authorization, x_tenant_id)
    rows = [r for r in get_regime_store().list_regimes(strategy_id=strategy_id, session=session, market=market)
            if r["model_status"] == "approved" and _scoped(ctx, r)]
    return {"regime_states": [r["record"] for r in rows]}


@router.get("/api/registry/regime/strategies/{strategy_id}/active")
def read_active(strategy_id: str, authorization: Optional[str] = Header(default=None),
                x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id")) -> dict[str, Any]:
    from .. import service as svc

    ctx, _ = svc._strategy_spec_read_scope(authorization, x_tenant_id)
    rows = [r for r in get_regime_store().list_regimes(strategy_id=strategy_id, states=("active",))
            if r["model_status"] == "approved" and _scoped(ctx, r)]
    if not rows:
        raise HTTPException(status_code=404, detail="no active regime state for this strategy")
    return rows[-1]["record"]

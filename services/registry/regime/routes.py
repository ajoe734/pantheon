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
DEFAULT_STORE_ROOT = "/data/source-ingest"


class EvaluateRequest(BaseModel):
    session: str
    market: str = MARKET
    verify_inputs: bool = False


def _semver(version: str) -> tuple:
    try:
        return tuple(int(p) for p in str(version).split("."))
    except ValueError:
        return (-1,)


def _observations_loader():
    def load():
        from pathlib import Path

        from services.research.regime.features import load_governed_observations

        root = os.environ.get(STORE_ROOT_ENV, DEFAULT_STORE_ROOT)
        if not Path(root).is_dir():
            raise EvaluationRefused("governed_store_unavailable", "governed dataset root is not mounted")
        return load_governed_observations(root)[0]

    return load


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
    from ..models import ArtifactType

    svc._authenticate_registry_write(authorization)
    views = svc._filter_strategy_spec_views(None, authorization, x_tenant_id, None)
    latest: dict[str, Any] = {}
    for view in views:
        e = view.entry
        if e.artifact_state.value == "retired":
            continue
        if e.strategy_id not in latest or _semver(e.version) >= _semver(latest[e.strategy_id].version):
            latest[e.strategy_id] = e
    strategies = [{"strategy_id": e.strategy_id, "registry_id": e.registry_id, "metadata": e.metadata or {},
                   "owner_tenant": e.owner_tenant} for e in latest.values()]
    models = [{"registry_id": v.entry.registry_id, "artifact_state": v.entry.artifact_state.value,
               "metadata": v.entry.metadata or {}, "created_at": v.entry.created_at}
              for v in svc.get_registry_service().list_all_entries()
              if v.entry.artifact_type == ArtifactType.MODEL_ARTIFACT]
    try:
        return RegimeEvaluator(get_regime_store(), _observations_loader()).evaluate(
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

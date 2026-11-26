"""
services/evaluation — Evaluation and Performance Outlier Attribution Owner Service (C3, §12.5).

Exposes the evaluation library and the deterministic performance outlier
attribution owner as a deployable FastAPI service.

Routes
------
  GET  /__health__                          liveness probe
  POST /api/evaluation/results              submit a pre-computed EvaluatorResult
  GET  /api/evaluation/results/{result_id}  read an EvaluatorResult
  POST /api/attribution/runs                run weekly outlier detection & shadow settlement (idempotent per ISO week)
  GET  /api/attribution/records             list outlier & disagreement attribution records by subject/window/direction
  GET  /api/attribution/records/{record_id} read single attribution record (404 if unknown)
  GET  /api/attribution/shadow/{decision_id} read settled shadow arms for a decision (404 if unknown)
"""
from __future__ import annotations

import hmac
import logging
import os
import uuid
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from fastapi import APIRouter, FastAPI, Header, HTTPException, Query
from services.foundation.health import register_fastapi_health_routes

from services.evaluation.attribution import (
    AgoraPlatformSourceClient,
    AttributionStore,
    CostModelConfig,
    OutlierAttributionConfig,
    iso_week_for_date,
    run_weekly_outlier_attribution,
    settle_decision_lock,
)

log = logging.getLogger(__name__)

LOCAL_EVALUATION_SERVICE_TOKEN = "pantheon-local-evaluation-service-token"
EXAMPLE_EVALUATION_SERVICE_TOKEN = "replace-me-evaluation-service-token"
_TOKEN_ENV_VARS = (
    "PANTHEON_ATTRIBUTION_SERVICE_TOKEN",
    "EVALUATION_SERVICE_TOKEN",
    "PANTHEON_EVALUATION_SERVICE_TOKEN",
    "EVALUATION_API_TOKEN",
)


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _authenticate_attribution_caller(
    authorization: Optional[str],
    *,
    explicit_tokens: Optional[Sequence[str]] = None,
) -> None:
    """Authenticate service-token callers on C3 /api/attribution/* routes."""
    configured_tokens: List[str] = []
    if explicit_tokens:
        configured_tokens.extend(_clean(t) for t in explicit_tokens if _clean(t))
    for env_name in _TOKEN_ENV_VARS:
        if env_name in os.environ:
            val = _clean(os.environ.get(env_name))
            if val and val not in configured_tokens:
                configured_tokens.append(val)

    posture = (
        os.getenv("PANTHEON_PERSISTENCE_POSTURE")
        or os.getenv("PANTHEON_ENV")
        or "dev"
    ).strip().lower()
    enforced = posture in {
        "stage",
        "staging",
        "staging-live",
        "prod",
        "production",
    } or posture.startswith(("staging", "prod"))

    if not configured_tokens:
        if enforced:
            raise HTTPException(
                status_code=503,
                detail={
                    "status": "unavailable",
                    "reason": "attribution_service_boundary_unconfigured",
                },
            )
        configured_tokens = [LOCAL_EVALUATION_SERVICE_TOKEN]
    elif enforced and any(
        t in {LOCAL_EVALUATION_SERVICE_TOKEN, EXAMPLE_EVALUATION_SERVICE_TOKEN}
        for t in configured_tokens
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "status": "unavailable",
                "reason": "attribution_service_boundary_unconfigured",
            },
        )

    scheme, _, supplied = str(authorization or "").partition(" ")
    token = supplied.strip()
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=401,
            detail={
                "status": "unauthorized",
                "reason": "attribution_service_token_required",
            },
        )
    if not any(hmac.compare_digest(token, expected) for expected in configured_tokens):
        raise HTTPException(
            status_code=401,
            detail={
                "status": "unauthorized",
                "reason": "attribution_invalid_service_credential",
            },
        )


def _resolve_tenant_id(
    query_tenant: Optional[str] = None,
    x_tenant_id: Optional[str] = None,
    x_pantheon_tenant_id: Optional[str] = None,
    x_pantheon_tenant: Optional[str] = None,
) -> str:
    return (
        _clean(query_tenant)
        or _clean(x_tenant_id)
        or _clean(x_pantheon_tenant_id)
        or _clean(x_pantheon_tenant)
        or _clean(os.getenv("PANTHEON_TENANT_ID"))
        or "default"
    )


class AttributionOwnerRuntime:
    """Stateful owner runtime coordinating store, Agora platform source client, and recorded inputs."""

    def __init__(
        self,
        *,
        store: Optional[AttributionStore] = None,
        config: Optional[OutlierAttributionConfig] = None,
        cost_model: Optional[CostModelConfig] = None,
        agora_source_client: Optional[AgoraPlatformSourceClient] = None,
        service_tokens: Optional[Sequence[str]] = None,
        market_rows: Sequence[Mapping[str, Any]] = (),
        paper_fills: Sequence[Mapping[str, Any]] = (),
        decision_locks: Sequence[Mapping[str, Any]] = (),
        journal_entries: Sequence[Mapping[str, Any]] = (),
        member_virtual_books: Sequence[Mapping[str, Any]] = (),
        persona_subjects: Sequence[Mapping[str, Any]] = (),
        pool_member_subjects: Sequence[Mapping[str, Any]] = (),
        trader_subjects: Sequence[Mapping[str, Any]] = (),
        regime_state: Optional[Mapping[str, Any]] = None,
        regime_resolver: Optional[Callable[[str, str], Optional[Mapping[str, Any]]]] = None,
    ) -> None:
        self.store = store or AttributionStore()
        self.config = config
        self.cost_model = cost_model or CostModelConfig()
        self.agora_source_client = agora_source_client
        self.service_tokens = list(service_tokens) if service_tokens else None
        self.market_rows: List[Dict[str, Any]] = [dict(r) for r in market_rows if isinstance(r, Mapping)]
        self.paper_fills: List[Dict[str, Any]] = [dict(f) for f in paper_fills if isinstance(f, Mapping)]
        self.decision_locks: List[Dict[str, Any]] = [dict(l) for l in decision_locks if isinstance(l, Mapping)]
        self.journal_entries: List[Dict[str, Any]] = [dict(j) for j in journal_entries if isinstance(j, Mapping)]
        self.member_virtual_books: List[Dict[str, Any]] = [
            dict(b) for b in member_virtual_books if isinstance(b, Mapping)
        ]
        self.persona_subjects: List[Dict[str, Any]] = [dict(p) for p in persona_subjects if isinstance(p, Mapping)]
        self.pool_member_subjects: List[Dict[str, Any]] = [
            dict(m) for m in pool_member_subjects if isinstance(m, Mapping)
        ]
        self.trader_subjects: List[Dict[str, Any]] = [dict(t) for t in trader_subjects if isinstance(t, Mapping)]
        self.regime_state: Optional[Dict[str, Any]] = dict(regime_state) if isinstance(regime_state, Mapping) else None
        self.regime_resolver = regime_resolver

    def effective_config(self) -> OutlierAttributionConfig:
        return self.config if self.config is not None else OutlierAttributionConfig.from_env()

    def _pull_agora_inputs(self, tenant_id: str) -> Dict[str, List[Dict[str, Any]]]:
        client = self.agora_source_client
        if client is None and os.getenv("AGORA_PLATFORM_SOURCE_URL"):
            client = AgoraPlatformSourceClient()
        if client is None:
            return {"decision_locks": [], "journal_entries": []}
        try:
            return client.pull_records(tenant_id=tenant_id)
        except Exception:
            log.debug("Agora platform source pull skipped or unavailable", exc_info=True)
            return {"decision_locks": [], "journal_entries": []}

    def settle_and_store_decision(
        self,
        lock: Mapping[str, Any],
        *,
        market_rows: Optional[Sequence[Mapping[str, Any]]] = None,
        paper_fills: Optional[Sequence[Mapping[str, Any]]] = None,
        observation_window: Optional[str] = None,
    ) -> Dict[str, Any]:
        cfg = self.effective_config()
        did = _clean(lock.get("decision_id") or lock.get("human_decision_id") or lock.get("record_id"))
        prior = self.store.get_shadow_settlement(did) if did else None
        settled = settle_decision_lock(
            lock,
            market_rows=market_rows if market_rows is not None else self.market_rows,
            paper_fills=paper_fills if paper_fills is not None else self.paper_fills,
            cost_model=self.cost_model,
            observation_window=observation_window or cfg.observation_window,
            prior_settlement=prior,
        )
        return self.store.put_shadow_settlement(settled)

    def run_weekly(self, payload: Mapping[str, Any], *, tenant_id: str) -> Dict[str, Any]:
        kind = _clean(payload.get("kind") or "weekly").lower()
        if kind != "weekly":
            raise HTTPException(
                status_code=422,
                detail={"status": "invalid_request", "reason": f"unsupported_run_kind:{kind}"},
            )
        window_end = _clean(payload.get("window_end"))
        if not window_end:
            raise HTTPException(
                status_code=422,
                detail={"status": "invalid_request", "reason": "window_end_required"},
            )
        window_start = _clean(payload.get("window_start")) or None
        iso_week = iso_week_for_date(window_end)
        cfg = self.effective_config()

        def _compute() -> Dict[str, Any]:
            agora_pulled = self._pull_agora_inputs(tenant_id)
            locks = (
                list(self.decision_locks)
                + list(agora_pulled.get("decision_locks") or [])
                + [dict(x) for x in (payload.get("decision_locks") or []) if isinstance(x, Mapping)]
            )
            journals = (
                list(self.journal_entries)
                + list(agora_pulled.get("journal_entries") or [])
                + [dict(x) for x in (payload.get("journal_entries") or []) if isinstance(x, Mapping)]
            )
            m_rows = list(self.market_rows) + [
                dict(x) for x in (payload.get("market_rows") or []) if isinstance(x, Mapping)
            ]
            p_fills = list(self.paper_fills) + [
                dict(x) for x in (payload.get("paper_fills") or []) if isinstance(x, Mapping)
            ]
            v_books = list(self.member_virtual_books) + [
                dict(x) for x in (payload.get("member_virtual_books") or []) if isinstance(x, Mapping)
            ]
            p_subjs = list(self.persona_subjects) + [
                dict(x) for x in (payload.get("persona_subjects") or []) if isinstance(x, Mapping)
            ]
            pm_subjs = list(self.pool_member_subjects) + [
                dict(x) for x in (payload.get("pool_member_subjects") or []) if isinstance(x, Mapping)
            ]
            t_subjs = list(self.trader_subjects) + [
                dict(x) for x in (payload.get("trader_subjects") or []) if isinstance(x, Mapping)
            ]
            reg = payload.get("regime_state") if isinstance(payload.get("regime_state"), Mapping) else self.regime_state

            return run_weekly_outlier_attribution(
                window_end=window_end,
                window_start=window_start,
                tenant_id=tenant_id,
                config=cfg,
                cost_model=self.cost_model,
                decision_locks=locks,
                market_rows=m_rows,
                paper_fills=p_fills,
                journal_entries=journals,
                member_virtual_books=v_books,
                persona_subjects=p_subjs,
                pool_member_subjects=pm_subjs,
                trader_subjects=t_subjs,
                regime_state=reg,
                regime_resolver=self.regime_resolver,
            )

        run_record, _ = self.store.record_or_replay_weekly_run(
            tenant_id=tenant_id,
            iso_week=iso_week,
            window_end=window_end,
            compute_fn=_compute,
        )
        return run_record


def register_attribution_routes(
    target: FastAPI | APIRouter,
    runtime: Optional[AttributionOwnerRuntime] = None,
) -> AttributionOwnerRuntime:
    """Register the C3 attribution owner routes directly on a FastAPI app or APIRouter."""
    owner_rt = runtime or AttributionOwnerRuntime()

    @target.post("/api/attribution/runs")
    async def post_attribution_run(
        payload: Dict[str, Any],
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant_id: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        _authenticate_attribution_caller(authorization, explicit_tokens=owner_rt.service_tokens)
        tenant_id = _resolve_tenant_id(
            payload.get("tenant_id"),
            x_tenant_id,
            x_pantheon_tenant_id,
            x_pantheon_tenant,
        )
        return owner_rt.run_weekly(payload, tenant_id=tenant_id)

    @target.get("/api/attribution/records")
    async def get_attribution_records(
        since: Optional[str] = Query(default=None),
        subject_kind: Optional[str] = Query(default=None),
        subject_id: Optional[str] = Query(default=None),
        direction: Optional[str] = Query(default=None),
        window_start: Optional[str] = Query(default=None),
        window_end: Optional[str] = Query(default=None),
        cursor: Optional[str] = Query(default=None),
        limit: int = Query(default=50, ge=1, le=500),
        tenant_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant_id: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        _authenticate_attribution_caller(authorization, explicit_tokens=owner_rt.service_tokens)
        resolved_tenant = _resolve_tenant_id(
            tenant_id,
            x_tenant_id,
            x_pantheon_tenant_id,
            x_pantheon_tenant,
        )
        try:
            return owner_rt.store.list_records(
                tenant_id=resolved_tenant,
                since=since,
                subject_kind=subject_kind,
                subject_id=subject_id,
                direction=direction,
                window_start=window_start,
                window_end=window_end,
                cursor=cursor,
                limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"status": "invalid_request", "reason": str(exc)},
            ) from exc

    @target.get("/api/attribution/records/{record_id}")
    async def get_attribution_record_detail(
        record_id: str,
        tenant_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant_id: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        _authenticate_attribution_caller(authorization, explicit_tokens=owner_rt.service_tokens)
        resolved_tenant = _resolve_tenant_id(
            tenant_id,
            x_tenant_id,
            x_pantheon_tenant_id,
            x_pantheon_tenant,
        )
        record = owner_rt.store.get_record(record_id, tenant_id=resolved_tenant)
        if record is None:
            raise HTTPException(
                status_code=404,
                detail={"status": "not_found", "reason": "attribution_record_not_found", "record_id": record_id},
            )
        return record

    @target.get("/api/attribution/shadow/{decision_id}")
    async def get_attribution_shadow(
        decision_id: str,
        tenant_id: Optional[str] = Query(default=None),
        authorization: Optional[str] = Header(default=None),
        x_tenant_id: Optional[str] = Header(default=None, alias="X-Tenant-Id"),
        x_pantheon_tenant_id: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant-Id"),
        x_pantheon_tenant: Optional[str] = Header(default=None, alias="X-Pantheon-Tenant"),
    ) -> Dict[str, Any]:
        _authenticate_attribution_caller(authorization, explicit_tokens=owner_rt.service_tokens)
        resolved_tenant = _resolve_tenant_id(
            tenant_id,
            x_tenant_id,
            x_pantheon_tenant_id,
            x_pantheon_tenant,
        )
        settled = owner_rt.store.get_shadow_settlement(decision_id, tenant_id=resolved_tenant)
        if settled is None:
            candidates = list(owner_rt.decision_locks)
            matching_lock = next(
                (
                    l
                    for l in candidates
                    if _clean(l.get("decision_id") or l.get("human_decision_id") or l.get("record_id")) == decision_id
                ),
                None,
            )
            if matching_lock is None:
                pulled = owner_rt._pull_agora_inputs(resolved_tenant)
                matching_lock = next(
                    (
                        l
                        for l in (pulled.get("decision_locks") or [])
                        if _clean(l.get("decision_id") or l.get("human_decision_id") or l.get("record_id"))
                        == decision_id
                    ),
                    None,
                )
            if matching_lock is None:
                raise HTTPException(
                    status_code=404,
                    detail={"status": "not_found", "reason": "shadow_decision_not_found", "decision_id": decision_id},
                )
            settled = owner_rt.settle_and_store_decision(matching_lock)

        return {
            "decision_id": settled["decision_id"],
            "arms": settled.get("arms") or [],
            "data_cutoff": settled.get("data_cutoff"),
            "cost_model": settled.get("cost_model"),
            "observation_window": settled.get("observation_window"),
            "settled_at": settled.get("settled_at"),
        }

    return owner_rt


def create_attribution_router(runtime: Optional[AttributionOwnerRuntime] = None) -> APIRouter:
    """Build the C3 attribution owner APIRouter."""
    router = APIRouter(tags=["attribution-owner"])
    owner_rt = register_attribution_routes(router, runtime)
    router.attribution_runtime = owner_rt  # type: ignore[attr-defined]
    return router


_store: Dict[str, Any] = {}
attribution_runtime = AttributionOwnerRuntime()


def create_evaluation_app(runtime: Optional[AttributionOwnerRuntime] = None) -> FastAPI:
    """Create the FastAPI application for the evaluation & attribution owner service."""
    rt = runtime or attribution_runtime
    application = FastAPI(title="Pantheon Evaluation Service", version="0.2.0")
    register_fastapi_health_routes(
        application,
        "evaluation",
        metrics=lambda: {
            "stored_results": len(_store),
            "attribution_store_backend": rt.store.backend,
        },
    )

    @application.get("/__health__")
    async def health() -> Dict[str, str]:
        return {"status": "ok", "service": "evaluation"}

    @application.post("/api/evaluation/results", status_code=201)
    async def submit_result(payload: Dict[str, Any]) -> Dict[str, str]:
        result_id = payload.get("result_id") or f"eval-{uuid.uuid4().hex[:12]}"
        _store[result_id] = payload
        return {"result_id": result_id}

    @application.get("/api/evaluation/results/{result_id}")
    async def get_result(result_id: str) -> Dict[str, Any]:
        entry = _store.get(result_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="result not found")
        return entry

    register_attribution_routes(application, rt)
    application.state.attribution_runtime = rt
    return application


app = create_evaluation_app(attribution_runtime)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", "8084"))
    uvicorn.run("services.evaluation.main:app", host="0.0.0.0", port=port, reload=False)

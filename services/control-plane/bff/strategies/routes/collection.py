"""Strategy collection routes (list, create)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ..service import (
    is_true_draft_lifecycle_state,
    normalize_strategy_dto_state,
)
from ..service import STRATEGY_OPERATIONAL_COUNT_MAX_CANDIDATES
from .common import (
    STATUS_RETIRED,
    StrategyRouteContext,
    _build_ranking_strategy_index,
    operational_counts,
    summary_persona_ids,
)

try:
    from services.control_plane.bff.models import ErrorCode
    from services.control_plane.bff.core.owner_reads import (
        request_read_cache,
        submit_owner_read,
    )
except (ImportError, ValueError):
    from models import ErrorCode
    from core.owner_reads import request_read_cache, submit_owner_read


def build_collection_router(ctx: StrategyRouteContext) -> APIRouter:
    router = APIRouter()

    def _hide_retired_and_slice(
        summaries: List[Dict[str, Any]],
        persona_lookup: Any,
        page_token: Optional[str],
        page_size: int,
    ):
        kept = [
            summary
            for summary in summaries
            if persona_lookup.operational_status(summary_persona_ids(summary)) != STATUS_RETIRED
        ]
        page, next_token = ctx.page_slice(kept, page_token, page_size)
        return page, next_token, len(kept), len(summaries) - len(kept)

    def _prefetch_independent_owner_reads(
        *,
        norm_state: Optional[str],
        norm_exclude_state: Optional[str],
        persona_id: Optional[str],
        hide_retired_personas: bool,
        page_token: Optional[str],
        page_size: int,
    ) -> None:
        """Overlap the independent owner reads with the Persona directory read.

        The Persona directory, the registry summary page and the ranking
        snapshot do not depend on each other, yet were awaited one after
        another. Each prefetch runs in a copy of the request context and lands
        in the per-request read cache, so the handler's own call below joins
        the in-flight read (value or failure, under the request deadline). It
        is only done when a request cache exists; without one a prefetch would
        be a second, uncoordinated read.
        """
        service = ctx.service
        if service is None or request_read_cache.get() is None:
            return
        from services.control_plane.bff.personas.service import _PERSONA_FLEET_READ_EXECUTOR

        submit_owner_read(
            _PERSONA_FLEET_READ_EXECUTOR,
            service.get_latest_ranking_snapshot,
            warm_key="strategy_list_ranking_snapshot",
        )
        if not hide_retired_personas:
            submit_owner_read(
                _PERSONA_FLEET_READ_EXECUTOR,
                lambda: service.list_strategy_summaries_page(
                    state=norm_state,
                    exclude_state=norm_exclude_state,
                    persona_id=persona_id,
                    page_token=page_token,
                    page_size=page_size,
                ),
                warm_key="strategy_list_owner_page",
            )

    @router.get("/bff/strategies")
    def bff_list_strategies(
        state: Optional[str] = None,
        exclude_state: Optional[str] = None,
        persona_id: Optional[str] = None,
        hide_retired_personas: bool = False,
        page_token: Optional[str] = None,
        page_size: int = Query(default=20, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: strategy list (execute-plans Strategy DTO compatibility)."""
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        norm_state = str(state).strip() if state and str(state).strip() else None
        norm_exclude_state = (
            str(exclude_state).strip()
            if exclude_state and str(exclude_state).strip()
            else None
        )
        if norm_state and norm_exclude_state:
            raise ctx.bff_error(
                400,
                ErrorCode.VALIDATION_FAILED,
                "Conflicting state filters",
                "state and exclude_state cannot be combined",
                precondition_failed="exclude_state",
            )
        snapshot_at = ctx.utc_now()
        detail_by_id: Dict[str, Optional[Dict[str, Any]]] = {}
        state_counts: Optional[Dict[str, int]] = None
        # One Persona directory read for the whole request: every row's
        # personaStates, operationalStatus and the hide filter come from it.
        _prefetch_independent_owner_reads(
            norm_state=norm_state,
            norm_exclude_state=norm_exclude_state,
            persona_id=persona_id,
            hide_retired_personas=hide_retired_personas,
            page_token=page_token,
            page_size=page_size,
        )
        persona_lookup = ctx.read_persona_state_lookup(identity, snapshot_at)
        # Full candidate set selected by state / exclude_state / persona_id,
        # before the hide filter. None until a path has loaded it.
        candidates: Optional[List[Dict[str, Any]]] = None
        precomputed_operational_counts: Optional[Dict[str, int]] = None
        operational_counts_failed = False
        operational_counts_reason = "candidate set could not be read"
        hidden_retired_persona_count = 0
        bounded_hide = (
            ctx.service.list_strategy_summaries_hide_retired_unfiltered_page(
                persona_lookup=persona_lookup,
                summary_persona_ids_fn=summary_persona_ids,
                operational_counts_fn=operational_counts,
                status_retired=STATUS_RETIRED,
                page_slice_fn=ctx.page_slice,
                page_token=page_token,
                page_size=page_size,
            )
            if (
                ctx.service
                and hide_retired_personas
                and norm_state is None
                and norm_exclude_state is None
                and persona_id is None
                and hasattr(
                    ctx.service, "list_strategy_summaries_hide_retired_unfiltered_page"
                )
            )
            else None
        )
        owner_walk = (
            ctx.service.list_all_strategy_summary_pages(
                state=norm_state,
                exclude_state=norm_exclude_state,
                persona_id=persona_id,
            )
            if (ctx.service and hide_retired_personas and bounded_hide is None)
            else None
        )
        owner_page = (
            ctx.service.list_strategy_summaries_page(
                state=norm_state,
                exclude_state=norm_exclude_state,
                persona_id=persona_id,
                page_token=page_token,
                page_size=page_size,
            )
            if (ctx.service and bounded_hide is None and owner_walk is None)
            else None
        )
        if bounded_hide is not None:
            page_summaries = list(bounded_hide["page_summaries"])
            next_page_token = bounded_hide["next_page_token"]
            total = int(bounded_hide["total"])
            hidden_retired_persona_count = int(
                bounded_hide["hidden_retired_persona_count"]
            )
            state_counts = bounded_hide["state_counts"]
            precomputed_operational_counts = bounded_hide["operational_counts"]
        elif owner_walk is not None:
            candidates = list(owner_walk["items"])
            for summary in candidates:
                strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip() if isinstance(summary, dict) else ""
                if not strategy_id:
                    raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy summary entry", "strategy_store_unavailable")
            state_counts = owner_walk["state_counts"]
            page_summaries, next_page_token, total, hidden_retired_persona_count = _hide_retired_and_slice(
                candidates, persona_lookup, page_token, page_size
            )
        elif owner_page is not None:
            if not isinstance(owner_page, dict) or not isinstance(owner_page.get("items"), list):
                raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy list payload", "strategy_store_unavailable")
            page_summaries = list(owner_page["items"])
            for summary in page_summaries:
                strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip() if isinstance(summary, dict) else ""
                if not strategy_id:
                    raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy summary entry", "strategy_store_unavailable")
            total = int(owner_page.get("total") or 0)
            next_page_token = owner_page.get("next_page_token")
            if ctx.service and hasattr(ctx.service, "resolve_strategy_state_counts"):
                state_counts = ctx.service.resolve_strategy_state_counts(
                    owner_page=owner_page
                )
            if not next_page_token and page_token in (None, ""):
                candidates = page_summaries
            elif total > STRATEGY_OPERATIONAL_COUNT_MAX_CANDIDATES:
                # Counting needs the whole candidate set. Past this size the
                # default view stays one bounded owner read and says so.
                operational_counts_failed = True
                operational_counts_reason = (
                    f"candidate set of {total} exceeds the {STRATEGY_OPERATIONAL_COUNT_MAX_CANDIDATES} "
                    "row bound for counting; use hide_retired_personas or a narrower filter"
                )
            else:
                try:
                    walked = ctx.service.list_all_strategy_summary_pages(
                        state=norm_state,
                        exclude_state=norm_exclude_state,
                        persona_id=persona_id,
                    )
                except Exception:  # noqa: BLE001 - counts degrade, the page itself is unaffected
                    walked = None
                if walked is None:
                    operational_counts_failed = True
                else:
                    candidates = list(walked["items"])
        else:
            want_retired = bool(norm_state and norm_state.lower() == "retired")
            if ctx.service:
                if hasattr(ctx.service, "list_strategy_summaries_for_filter") and want_retired:
                    summaries = ctx.service._invoke_read_owner(
                        lambda: ctx.service.list_strategy_summaries_for_filter(
                            include_retired=True
                        )
                    )
                else:
                    summaries = ctx.service._invoke_read_owner(
                        ctx.service.list_strategy_summaries
                    )
            else:
                summaries = ctx.list_strategy_summaries_records()
            if not isinstance(summaries, list):
                raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy list payload", "strategy_store_unavailable")
            for summary in summaries:
                strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip() if isinstance(summary, dict) else ""
                if not strategy_id:
                    raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy summary entry", "strategy_store_unavailable")
            if ctx.service and hasattr(ctx.service, "resolve_strategy_state_counts"):
                state_counts = ctx.service.resolve_strategy_state_counts(
                    fallback_summaries=summaries
                )
            if persona_id:
                summaries = [
                    s
                    for s in summaries
                    if persona_id in (s.get("persona_ids") or s.get("personaIds") or [])
                ]
            if norm_state:
                wanted_lower = norm_state.lower()
                wanted_dto = normalize_strategy_dto_state(wanted_lower)
                filtered_summaries: List[Dict[str, Any]] = []
                for summary in summaries:
                    strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip()
                    if (
                        "current_spec_version_id" in summary
                        or summary.get("lifecycle_state") is not None
                        or summary.get("state") is not None
                        or summary.get("status") is not None
                    ):
                        lifecycle_raw = (
                            summary.get("lifecycle_state")
                            or summary.get("state")
                            or summary.get("status")
                        )
                    else:
                        detail = (
                            ctx.service.get_strategy_spec_detail(strategy_id, version_selector="current")
                            if ctx.service
                            else None
                        )
                        detail_by_id[strategy_id] = detail
                        lifecycle_raw = (detail or summary).get("lifecycle_state") or summary.get("lifecycle_state")
                    raw_clean = str(lifecycle_raw or "").strip().lower()
                    if (
                        ctx.normalize_lifecycle_state(lifecycle_raw) == norm_state
                        or raw_clean == wanted_lower
                        or normalize_strategy_dto_state(lifecycle_raw) == wanted_dto
                    ):
                        filtered_summaries.append(summary)
                summaries = filtered_summaries
            elif norm_exclude_state:
                excluded_lower = norm_exclude_state.lower()
                excluded_dto = normalize_strategy_dto_state(excluded_lower)
                filtered_summaries = []
                for summary in summaries:
                    strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip()
                    if (
                        "current_spec_version_id" in summary
                        or summary.get("lifecycle_state") is not None
                        or summary.get("state") is not None
                        or summary.get("status") is not None
                    ):
                        lifecycle_raw = (
                            summary.get("lifecycle_state")
                            or summary.get("state")
                            or summary.get("status")
                        )
                    else:
                        detail = (
                            ctx.service.get_strategy_spec_detail(strategy_id, version_selector="current")
                            if ctx.service
                            else None
                        )
                        detail_by_id[strategy_id] = detail
                        lifecycle_raw = (detail or summary).get("lifecycle_state") or summary.get("lifecycle_state")
                    raw_clean = str(lifecycle_raw or "").strip().lower()
                    dto_state = normalize_strategy_dto_state(lifecycle_raw)
                    if raw_clean == "retired" or dto_state == "retired":
                        continue
                    if excluded_lower == "draft":
                        if is_true_draft_lifecycle_state(lifecycle_raw):
                            continue
                    elif (
                        ctx.normalize_lifecycle_state(lifecycle_raw) == norm_exclude_state
                        or raw_clean == excluded_lower
                        or dto_state == excluded_dto
                    ):
                        continue
                    filtered_summaries.append(summary)
                summaries = filtered_summaries
            candidates = summaries
            if hide_retired_personas:
                page_summaries, next_page_token, total, hidden_retired_persona_count = _hide_retired_and_slice(
                    summaries, persona_lookup, page_token, page_size
                )
            else:
                total = len(summaries)
                page_summaries, next_page_token = ctx.page_slice(summaries, page_token, page_size)
        ranking_snapshot, ranking_read_failed = (
            ctx.service.get_latest_ranking_snapshot() if ctx.service else (None, False)
        )
        ranking_index = _build_ranking_strategy_index(
            ranking_snapshot if isinstance(ranking_snapshot, dict) else None
        )
        page_items: List[Dict[str, Any]] = []
        for summary in page_summaries:
            strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip()
            if "current_spec_version_id" in summary:
                detail = summary
            elif strategy_id in detail_by_id:
                detail = detail_by_id[strategy_id]
            else:
                detail = (
                    ctx.service.get_strategy_spec_detail(strategy_id, version_selector="current")
                    if ctx.service
                    else None
                )
            page_items.append(
                ctx.project_strategy_dto(
                    summary,
                    detail=detail,
                    ranking_snapshot=ranking_snapshot,
                    ranking_read_failed=ranking_read_failed,
                    ranking_index=ranking_index,
                    persona_lookup=persona_lookup,
                )
            )
        meta = dict(
            ctx.read_surface_meta(
                "strategy_specs",
                "strategy_list",
                snapshot_at=snapshot_at,
                total=total,
            )
        )
        meta["state_counts"] = state_counts
        surfaces = dict(meta.get("surfaces") or {})
        surfaces["persona_states"] = persona_lookup.surface()
        if precomputed_operational_counts is not None and not operational_counts_failed:
            meta["operational_counts"] = precomputed_operational_counts
        elif candidates is not None and not operational_counts_failed:
            meta["operational_counts"] = operational_counts(
                persona_lookup.operational_status(summary_persona_ids(summary))
                for summary in candidates
            )
        else:
            meta["operational_counts"] = None
            surfaces["operational_counts"] = {
                "status": "unavailable",
                "source": "strategy_summary_pages",
                "reason": operational_counts_reason,
            }
        meta["surfaces"] = surfaces
        meta["hidden_retired_persona_count"] = hidden_retired_persona_count
        return {
            "data": page_items,
            "items": page_items,
            "page_info": {"next_page_token": next_page_token, "total": total},
            "meta": meta,
        }

    @router.post("/bff/strategies", status_code=201)
    def bff_create_strategy(
        payload: Dict[str, Any] = Body(...),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
    ):
        """BFF: create strategy stub (execute-plans compatibility)."""
        identity = ctx.extract_identity(authorization)
        ctx.require_operator_role(identity)
        principal = ctx.write_principal(identity)
        ctx.reject_body_idempotency_key(payload)
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ctx.bff_error(
                422, ErrorCode.VALIDATION_FAILED, "name is required",
                "Strategy name must be a non-empty string",
                precondition_failed="name",
            )
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key)
        dry_run = ctx.request_dry_run_requested()
        return ctx.service.create_strategy(
            payload=payload,
            identity=identity,
            principal=principal,
            resolved_key=resolved_key,
            dry_run=dry_run,
        )


    return router

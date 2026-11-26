"""Strategy collection routes (list, create)."""
from __future__ import annotations

from concurrent.futures import Future
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from ..service import (
    is_true_draft_lifecycle_state,
    normalize_strategy_dto_state,
)
from ..service import STRATEGY_OPERATIONAL_COUNT_MAX_CANDIDATES
from .common import (
    STATUS_RETIRED,
    STUB_TEST_PERSONA_PROBE,
    StrategyRouteContext,
    _build_ranking_strategy_index,
    operational_counts,
    strategy_stub_reason,
    summary_persona_ids,
)

try:
    from services.control_plane.bff.models import ErrorCode
    from services.control_plane.bff.core.owner_reads import (
        remaining_management_read_budget,
        request_read_cache,
        submit_owner_read,
    )
except (ImportError, ValueError):
    from models import ErrorCode
    from core.owner_reads import (
        remaining_management_read_budget,
        request_read_cache,
        submit_owner_read,
    )


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
        hide_input_needed: bool,
        page_token: Optional[str],
        page_size: int,
    ) -> Any:
        """Overlap the independent owner reads with the Persona directory read.

        The Persona directory, the registry summary page and the ranking
        snapshot do not depend on each other, yet were awaited one after
        another. Each prefetch runs in a copy of the request context and lands
        in the per-request read cache, so the handler's own call below joins
        the in-flight read (value or failure, under the request deadline). It
        is only done when a request cache exists; without one a prefetch would
        be a second, uncoordinated read.

        The registry page port may be uncached, so the page read is not left to
        the cache: its Future is returned and the handler consumes that single
        result or failure (HTTPException included) instead of reading again.
        """
        service = ctx.service
        if service is None:
            return None
        from services.control_plane.bff.personas.service import _PERSONA_FLEET_READ_EXECUTOR

        if request_read_cache.get() is not None:
            submit_owner_read(
                _PERSONA_FLEET_READ_EXECUTOR,
                service.get_latest_ranking_snapshot,
                warm_key="strategy_list_ranking_snapshot",
            )
        if hide_retired_personas or hide_input_needed:
            # The owner-side hide input carries the test Persona ids, which
            # exist only after the Persona directory read; a page read sent
            # earlier would be wasted, so none is prefetched.
            return None
        try:
            return submit_owner_read(
                _PERSONA_FLEET_READ_EXECUTOR,
                lambda: service.list_strategy_summaries_page(
                    state=norm_state,
                    exclude_state=norm_exclude_state,
                    persona_id=persona_id,
                    page_token=page_token,
                    page_size=page_size,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - submission failure is an owner failure
            failed: Future = Future()
            failed.set_exception(exc)
            return failed

    @router.get("/bff/strategies")
    def bff_list_strategies(
        state: Optional[str] = None,
        exclude_state: Optional[str] = None,
        persona_id: Optional[str] = None,
        hide_retired_personas: bool = False,
        include_test_personas: bool = False,
        include_test: bool = False,
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
        visible_state_counts: Optional[Dict[str, int]] = None
        # One Persona directory read for the whole request: every row's
        # personaStates, operationalStatus and the hide filter come from it.
        include_test_personas = include_test_personas or include_test
        hide_test = not include_test_personas
        page_future = _prefetch_independent_owner_reads(
            norm_state=norm_state,
            norm_exclude_state=norm_exclude_state,
            persona_id=persona_id,
            hide_retired_personas=hide_retired_personas,
            hide_input_needed=hide_test,
            page_token=page_token,
            page_size=page_size,
        )
        persona_lookup = ctx.read_persona_state_lookup(identity, snapshot_at)
        # Strategies whose personas are all test-scoped are hidden by default
        # and counted in meta; include_test_personas=true returns them. Only
        # needed when the one directory read found test-scoped personas.
        # Unbound synthetic / raw-seed stubs are hidden under the same switch.
        # The Registry owner excludes the unbound stubs and the rows whose
        # Personas are all test-scoped in the same read that selects and counts
        # the page, so hiding never forces a walk over the owner pages. Only
        # hiding retired Personas still needs the whole candidate set.
        owner_hide_kwargs: Dict[str, Any] = (
            {
                "hide_stubs": True,
                "hide_persona_ids": sorted(persona_lookup.test_persona_ids)
                if persona_lookup.available
                else [],
            }
            if hide_test
            else {}
        )
        walk_hide_test = False
        hidden_test_persona_strategy_count = 0
        hidden_unbound_stub_strategy_count = 0

        def _hide_test_and_stubs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
            nonlocal hidden_test_persona_strategy_count, hidden_unbound_stub_strategy_count
            visible: List[Dict[str, Any]] = []
            for row in rows:
                reason = strategy_stub_reason(row, persona_lookup)
                if reason is None:
                    visible.append(row)
                elif reason == STUB_TEST_PERSONA_PROBE:
                    hidden_test_persona_strategy_count += 1
                else:
                    hidden_unbound_stub_strategy_count += 1
            return visible

        walk_for_hiding = hide_retired_personas or walk_hide_test
        # Full candidate set selected by state / exclude_state / persona_id,
        # before the hide filter. None until a path has loaded it.
        candidates: Optional[List[Dict[str, Any]]] = None
        precomputed_operational_counts: Optional[Dict[str, int]] = None
        operational_counts_failed = False
        operational_counts_reason = "candidate set could not be read"
        hidden_retired_persona_count = 0
        def _overlap_owner_read(fn: Any) -> Any:
            from services.control_plane.bff.personas.service import (
                _PERSONA_FLEET_READ_EXECUTOR,
            )

            fut = submit_owner_read(_PERSONA_FLEET_READ_EXECUTOR, fn)

            def _join() -> Any:
                try:
                    return fut.result(timeout=remaining_management_read_budget())
                except BaseException:
                    fut.cancel()
                    raise

            _join.cancel = fut.cancel  # type: ignore[attr-defined]
            return _join

        def _join_prefetched_first_page() -> Any:
            def _join() -> Any:
                try:
                    return page_future.result(
                        timeout=remaining_management_read_budget()
                    )
                except BaseException:
                    page_future.cancel()
                    raise

            return ctx.service._invoke_read_owner(_join)

        bounded_hide = (
            ctx.service.list_strategy_summaries_hide_retired_unfiltered_page(
                persona_lookup=persona_lookup,
                summary_persona_ids_fn=summary_persona_ids,
                operational_counts_fn=operational_counts,
                status_retired=STATUS_RETIRED,
                page_slice_fn=ctx.page_slice,
                page_token=page_token,
                page_size=page_size,
                hide_retired=hide_retired_personas,
                stub_reason_fn=(
                    (lambda row: strategy_stub_reason(row, persona_lookup))
                    if hide_test
                    else None
                ),
                test_probe_reason=STUB_TEST_PERSONA_PROBE,
                owner_hide_kwargs=owner_hide_kwargs,
                first_page_fn=(
                    _join_prefetched_first_page if page_future is not None else None
                ),
                submit_overlap_fn=_overlap_owner_read,
            )
            if (
                ctx.service
                and (hide_retired_personas or walk_hide_test)
                and norm_state is None
                and norm_exclude_state is None
                and persona_id is None
                and hasattr(
                    ctx.service, "list_strategy_summaries_hide_retired_unfiltered_page"
                )
            )
            else None
        )
        # The walk sends the same owner hide input as the page read, so the
        # owner hides and counts test / stub rows and returns the visible
        # counts on its first page (no extra owner call).
        owner_walk = (
            ctx.service.list_all_strategy_summary_pages(
                state=norm_state,
                exclude_state=norm_exclude_state,
                persona_id=persona_id,
                **owner_hide_kwargs,
            )
            if (ctx.service and walk_for_hiding and bounded_hide is None)
            else None
        )
        if not (ctx.service and bounded_hide is None and owner_walk is None):
            owner_page = None
        elif page_future is not None:
            def _join_page() -> Any:
                # Bounded by the request deadline; a timeout is translated by
                # the owner-error envelope and the owner read is not retried.
                try:
                    return page_future.result(
                        timeout=remaining_management_read_budget()
                    )
                except BaseException:
                    page_future.cancel()
                    raise

            owner_page = ctx.service._invoke_read_owner(_join_page)
        else:
            owner_page = ctx.service.list_strategy_summaries_page(
                state=norm_state,
                exclude_state=norm_exclude_state,
                persona_id=persona_id,
                page_token=page_token,
                page_size=page_size,
                **owner_hide_kwargs,
            )
        # The owner answered the hide input when it returns the visible counts
        # and the hidden counts by reason from the same read.
        owner_hid = (
            isinstance(owner_page, dict)
            and isinstance(owner_page.get("hidden"), dict)
            and isinstance(owner_page.get("visible_state_counts"), dict)
        )
        if (
            owner_page is not None
            and hide_test
            and not owner_hid
            and persona_lookup.test_persona_ids
        ):
            # An owner that does not take the hide input (older Registry): the
            # page it returned cannot be made exact, so classify the whole
            # candidate set here as before.
            owner_page = None
            owner_walk = ctx.service.list_all_strategy_summary_pages(
                state=norm_state,
                exclude_state=norm_exclude_state,
                persona_id=persona_id,
            )
        if bounded_hide is not None and page_future is not None:
            # A prefetched page that the bounded read did not consume (offset
            # > 0) is dropped; no-op once it has run.
            page_future.cancel()
        if bounded_hide is not None:
            page_summaries = list(bounded_hide["page_summaries"])
            next_page_token = bounded_hide["next_page_token"]
            total = int(bounded_hide["total"])
            hidden_retired_persona_count = int(
                bounded_hide["hidden_retired_persona_count"]
            )
            hidden_test_persona_strategy_count = int(
                bounded_hide.get("hidden_test_persona_strategy_count") or 0
            )
            hidden_unbound_stub_strategy_count = int(
                bounded_hide.get("hidden_unbound_stub_strategy_count") or 0
            )
            state_counts = bounded_hide["state_counts"]
            visible_state_counts = bounded_hide.get("visible_state_counts")
            precomputed_operational_counts = bounded_hide["operational_counts"]
        elif owner_walk is not None:
            candidates = list(owner_walk["items"])
            walk_hidden = owner_walk.get("hidden")
            if hide_test and owner_walk.get("owner_hid") and isinstance(walk_hidden, dict):
                # The owner already excluded and counted the test / stub rows
                # over the whole candidate set.
                by_reason = walk_hidden.get("by_reason") or {}
                hidden_test_persona_strategy_count = int(
                    by_reason.get(STUB_TEST_PERSONA_PROBE) or 0
                )
                hidden_unbound_stub_strategy_count = sum(
                    int(v) for k, v in by_reason.items() if k != STUB_TEST_PERSONA_PROBE
                )
                visible_state_counts = owner_walk.get("visible_state_counts")
            elif hide_test:
                # Older owner without the hide answer: classify here, counts
                # stay null (never fabricated).
                candidates = _hide_test_and_stubs(candidates)
            for summary in candidates:
                strategy_id = str(summary.get("strategy_id") or summary.get("id") or "").strip() if isinstance(summary, dict) else ""
                if not strategy_id:
                    raise ctx.bff_error(503, ErrorCode.DEPENDENCY_UNAVAILABLE, "Invalid strategy summary entry", "strategy_store_unavailable")
            state_counts = owner_walk["state_counts"]
            if hide_retired_personas:
                page_summaries, next_page_token, total, hidden_retired_persona_count = _hide_retired_and_slice(
                    candidates, persona_lookup, page_token, page_size
                )
            else:
                total = len(candidates)
                page_summaries, next_page_token = ctx.page_slice(candidates, page_token, page_size)
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
            if owner_hid:
                by_reason = owner_page["hidden"].get("by_reason") or {}
                hidden_test_persona_strategy_count = int(
                    by_reason.get(STUB_TEST_PERSONA_PROBE) or 0
                )
                hidden_unbound_stub_strategy_count = sum(
                    int(v) for k, v in by_reason.items() if k != STUB_TEST_PERSONA_PROBE
                )
                visible_state_counts = ctx.service.resolve_visible_state_counts(
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
                        **owner_hide_kwargs,
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
            if hide_test:
                summaries = _hide_test_and_stubs(summaries)
            candidates = summaries
            if hide_retired_personas:
                page_summaries, next_page_token, total, hidden_retired_persona_count = _hide_retired_and_slice(
                    summaries, persona_lookup, page_token, page_size
                )
            else:
                total = len(summaries)
                page_summaries, next_page_token = ctx.page_slice(summaries, page_token, page_size)
        if hide_test and (
            (owner_page is not None and not owner_hid)
            or (bounded_hide is not None and not bounded_hide.get("stubs_applied"))
        ):
            # Bounded owner page: hide stubs on the returned page and subtract
            # the stubs found in the candidate set when it was read (exact),
            # else on the page alone. Hidden counts are never silent.
            basis = candidates if candidates is not None else page_summaries
            visible_basis = _hide_test_and_stubs(list(basis))
            hidden_here = len(basis) - len(visible_basis)
            if hidden_here:
                if candidates is not None and candidates is not page_summaries:
                    page_summaries, next_page_token = ctx.page_slice(
                        visible_basis, page_token, page_size
                    )
                    total = len(visible_basis)
                else:
                    page_summaries = [
                        row for row in page_summaries if strategy_stub_reason(row, persona_lookup) is None
                    ]
                    total = max(0, total - hidden_here)
                if candidates is not None:
                    candidates = visible_basis
        try:
            ranking_snapshot, ranking_read_failed = (
                ctx.service.get_latest_ranking_snapshot() if ctx.service else (None, False)
            )
        except TimeoutError:
            # Joining the warmed ranking read ran out of request budget. Same
            # truthful outcome as an owner read failure: telemetry missing,
            # no retry and no second owner read.
            ranking_snapshot, ranking_read_failed = None, True
        ranking_index = _build_ranking_strategy_index(
            ranking_snapshot if isinstance(ranking_snapshot, dict) else None
        )
        runtime_truth = ctx.read_strategy_runtime_truth(snapshot_at, persona_lookup)
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
                    runtime_truth=runtime_truth,
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
        if visible_state_counts is None and not hide_test:
            # Nothing is hidden, so every counted row is visible.
            visible_state_counts = state_counts
        meta["visible_state_counts"] = visible_state_counts
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
        meta["hidden_test_persona_strategy_count"] = hidden_test_persona_strategy_count
        meta["hidden_unbound_stub_strategy_count"] = hidden_unbound_stub_strategy_count
        meta["include_test_personas"] = bool(include_test_personas)
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

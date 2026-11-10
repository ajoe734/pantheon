"""Strategies domain application service and business logic.

Part of BFF-ROUTER-USECASE-CORRECTIVE-001.
Encapsulates read store access, persistence ports, and strategy seed store operations
away from route handlers.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable, Dict, List, Optional, Tuple, Set, Union
import uuid

from fastapi import HTTPException

from services.control_plane.bff.ports.read_surface_ports import ReadSurfacePorts


try:
    from services.control_plane.bff.models import ErrorCode
except (ImportError, ValueError):
    from models import ErrorCode

try:
    from services.control_plane.bff.source_management_client import SourceManagementClient
except (ImportError, ValueError):
    from ..source_management_client import SourceManagementClient  # type: ignore

try:
    from services.control_plane.bff.ports.strategy_write_owner import StrategyWriteOwnerPort
except (ImportError, ValueError):
    from ..ports.strategy_write_owner import StrategyWriteOwnerPort  # type: ignore

try:
    from services.source_ingestion.replication_bridge import (
        StrategySeedReplicationBridge,
        StrategySeedReplicationBridgeError,
    )
except (ImportError, ValueError):
    StrategySeedReplicationBridge = None  # type: ignore
    StrategySeedReplicationBridgeError = Exception  # type: ignore

try:
    from services.source_ingestion.strategy_seed_store import (
        SeedReviewDecision,
        StrategySpecSeedReviewError,
        StrategySpecSeedStore,
        StrategySpecSeedStoreError,
    )
except (ImportError, ValueError):
    SeedReviewDecision = None  # type: ignore
    StrategySpecSeedReviewError = Exception  # type: ignore
    StrategySpecSeedStore = None  # type: ignore
    StrategySpecSeedStoreError = Exception  # type: ignore

_DEFAULT_SEED_STORE_CLASS = StrategySpecSeedStore

# Registry summary paging bounds (owner caps page_size at 200).
STRATEGY_OWNER_PAGE_SIZE = 200
STRATEGY_CANDIDATE_WALK_MAX_PAGES = 100
# operational_counts without hide_retired_personas walks the candidate set only
# up to this size (one owner page); larger sets keep the one-read default view.
STRATEGY_OPERATIONAL_COUNT_MAX_CANDIDATES = STRATEGY_OWNER_PAGE_SIZE

ROLE_TRADING = "trading"
ROLE_ROLLBACK_BASELINE = "rollback_baseline"


def _clean_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def project_strategy_rules(
    entries: Any,
    *,
    strategy_id: str,
) -> List[Dict[str, Any]]:
    """One rule per approved execution_bundle of ``strategy_id``.

    ``entries`` is the registry owner response of
    ``GET /api/registry/strategies/{strategy_id}/strategy-artifacts``: a list
    of RegistryEntryView (``{"entry": {...}}``) or bare entries. Role is
    ``rollback_baseline`` only when the registry id is named in
    ``lineage.parent_registry_ids`` of another approved bundle of the same
    strategy. No registry id is ever derived by rewriting another id.
    """
    approved: List[Dict[str, Any]] = []
    for item in entries if isinstance(entries, list) else []:
        entry = (
            item.get("entry")
            if isinstance(item, dict) and isinstance(item.get("entry"), dict)
            else item
        )
        if not isinstance(entry, dict):
            continue
        if entry.get("artifact_type") not in (None, "execution_bundle"):
            continue
        if str(entry.get("artifact_state") or "").strip().lower() != "approved":
            continue
        if str(entry.get("strategy_id") or "").strip() != strategy_id:
            continue
        registry_id = str(entry.get("registry_id") or "").strip()
        if not registry_id:
            continue
        approved.append(entry)

    parent_ids_by_bundle: Dict[str, List[str]] = {}
    for entry in approved:
        lineage = _clean_dict(entry.get("lineage"))
        parents = lineage.get("parent_registry_ids")
        parent_ids_by_bundle[str(entry["registry_id"])] = (
            [str(p) for p in parents if str(p).strip()]
            if isinstance(parents, list)
            else []
        )

    rules: List[Dict[str, Any]] = []
    for entry in approved:
        registry_id = str(entry["registry_id"])
        is_parent_of_other = any(
            registry_id in parents
            for other_id, parents in parent_ids_by_bundle.items()
            if other_id != registry_id
        )
        metadata = _clean_dict(entry.get("metadata"))
        artifact = _clean_dict(metadata.get("strategy_artifact"))
        mutation = _clean_dict(artifact.get("mutation_surface"))
        controls = mutation.get("controls")
        immutable = mutation.get("immutable_parameters")
        rules.append(
            {
                "registryId": registry_id,
                "version": entry.get("version"),
                "role": ROLE_ROLLBACK_BASELINE if is_parent_of_other else ROLE_TRADING,
                "strategyLogic": _clean_dict(artifact.get("strategy_logic")),
                "parameters": _clean_dict(artifact.get("parameters")),
                "adjustableControls": list(controls) if isinstance(controls, list) else [],
                "immutableParameters": list(immutable) if isinstance(immutable, list) else [],
            }
        )
    return rules

log = logging.getLogger(__name__)

_STRATEGY_DTO_LIFECYCLE_MAP: Dict[str, str] = {
    "draft": "draft",
    "candidate": "review",
    "review": "review",
    "approved": "approved",
    "active": "deployed",
    "deployed": "deployed",
    "paused": "paused",
    "retired": "retired",
    "archived": "retired",
    "rejected": "retired",
    "superseded": "retired",
    "paper": "paper_running",
    "paper_running": "paper_running",
    "canary": "canary_running",
    "canary_running": "canary_running",
    "canary_authorized_not_started": "canary_authorized_not_started",
    "live": "live_running",
    "live_running": "live_running",
    "needs_human_approval": "needs_human_approval",
    "rollback_required": "rollback_required",
    "stopped": "stopped",
    "failed": "failed",
    "provisioning": "provisioning",
    "provisioning_failed": "failed",
    "research_only": "research_only",
    "consultable": "consultable",
    "paper_owner": "paper_owner",
    "live_owner": "live_owner",
    "frozen": "frozen",
    "suspended": "suspended",
}

STRATEGY_DEFAULT_STATE_COUNT_KEYS: Tuple[str, ...] = (
    "draft",
    "review",
    "approved",
    "paper_running",
    "canary_running",
    "live_running",
    "deployed",
    "paused",
    "retired",
)


def normalize_strategy_dto_state(value: Any) -> str:
    clean = str(value or "").strip().lower()
    return _STRATEGY_DTO_LIFECYCLE_MAP.get(clean, "draft")


def is_known_strategy_lifecycle_state(value: Any) -> bool:
    clean = str(value or "").strip().lower()
    return bool(clean) and clean in _STRATEGY_DTO_LIFECYCLE_MAP


def is_true_draft_lifecycle_state(value: Any) -> bool:
    clean = str(value or "").strip().lower()
    return clean in {"", "draft"}


def list_strategy_summaries(read_store: Any) -> List[Dict[str, Any]]:
    """Return canonical strategy specs from the strategy read owner."""
    return list(read_store.list_strategy_specs() or [])


class StrategiesService:
    def __init__(
        self,
        *,
        read_surface: Optional[Union[ReadSurfacePorts, Callable[[], ReadSurfacePorts]]] = None,
        get_read_store: Optional[Callable[[], ReadSurfacePorts]] = None,
        strategy_write_owner: Optional[Union[StrategyWriteOwnerPort, Callable[[], StrategyWriteOwnerPort]]] = None,
        get_strategy_write_owner: Optional[Callable[[], StrategyWriteOwnerPort]] = None,
        list_strategy_summaries: Optional[Callable[[], List[Dict[str, Any]]]] = None,
        bff_error: Optional[Callable[..., HTTPException]] = None,
        seed_store: Optional[StrategySpecSeedStore] = None,
        replication_bridge: Optional[StrategySeedReplicationBridge] = None,
        utc_now: Optional[Callable[[], str]] = None,
        normalize_lifecycle_state: Optional[Callable[[Any], str]] = None,
        normalize_risk_level: Optional[Callable[[Any], str]] = None,
        stable_json_hash: Optional[Callable[[Dict[str, Any]], str]] = None,
        idempotency_store: Optional[Dict[str, Dict[str, Any]]] = None,
        idempotency_check: Optional[Callable[..., Optional[Dict[str, Any]]]] = None,
        dry_run_success_response: Optional[Callable[..., Any]] = None,
        seed_replication_idempotency: Optional[Dict[str, Dict[str, Any]]] = None,
        seed_review_idempotency: Optional[Dict[str, Dict[str, Any]]] = None,
    ):
        self._read_surface = read_surface
        self._get_read_store = get_read_store
        self._strategy_write_owner = strategy_write_owner
        self._get_strategy_write_owner = get_strategy_write_owner
        self._list_strategy_summaries = list_strategy_summaries
        self._bff_error = bff_error
        self._seed_store = seed_store
        self._replication_bridge = replication_bridge
        self._utc_now = utc_now
        self._normalize_lifecycle_state = normalize_lifecycle_state
        self._normalize_risk_level = normalize_risk_level
        self._stable_json_hash = stable_json_hash
        self._idempotency_store = idempotency_store
        self._idempotency_check = idempotency_check
        self._dry_run_success_response = dry_run_success_response
        self._seed_replication_idempotency = seed_replication_idempotency
        self._seed_review_idempotency = seed_review_idempotency

    def _get_read_store_port(self) -> ReadSurfacePorts:
        if self._read_surface is not None:
            return self._read_surface() if callable(self._read_surface) else self._read_surface
        if self._get_read_store is not None:
            return self._get_read_store()
        raise NotImplementedError("Neither read_surface nor get_read_store dependency was supplied")

    def _get_write_owner_port(self) -> Optional[StrategyWriteOwnerPort]:
        if self._strategy_write_owner is not None:
            return self._strategy_write_owner() if callable(self._strategy_write_owner) else self._strategy_write_owner
        if self._get_strategy_write_owner is not None:
            return self._get_strategy_write_owner()
        try:
            rs = self._get_read_store_port()
            if hasattr(rs, "upsert_strategy") or hasattr(rs, "create_strategy_spec"):
                return rs
        except Exception:
            pass
        return None

    @property
    def seed_store(self) -> Any:
        if self._seed_store is not None:
            return self._seed_store() if callable(self._seed_store) else self._seed_store
        if StrategySpecSeedStore is not None and StrategySpecSeedStore is not _DEFAULT_SEED_STORE_CLASS:
            return StrategySpecSeedStore()
        if StrategySpecSeedStore is not None and os.environ.get("PYTEST_CURRENT_TEST") and os.environ.get("STRATEGY_SEED_STORE_PATH") and not any(os.environ.get(k) for k in ("PANTHEON_SOURCE_INGEST_API_URL", "PANTHEON_SOURCE_INGEST_URL", "SOURCE_INGEST_SERVICE_URL", "SOURCE_INGEST_URL")):
            return StrategySpecSeedStore(path=os.environ["STRATEGY_SEED_STORE_PATH"])
        return SourceManagementClient()

    def get_seed_store_path(self) -> str:
        return str(self.seed_store.path)

    def list_strategy_summaries(self) -> List[Dict[str, Any]]:
        if self._list_strategy_summaries is not None:
            return self._list_strategy_summaries()
        read_store = self._get_read_store_port()
        if callable(getter := getattr(read_store, "list_strategy_summaries", None)):
            return list(getter() or [])
        return list_strategy_summaries(read_store)

    def list_strategy_summaries_for_filter(
        self,
        *,
        include_retired: bool = False,
    ) -> List[Dict[str, Any]]:
        if include_retired:
            if self._list_strategy_summaries is not None:
                try:
                    return list(self._list_strategy_summaries(include_retired=True) or [])  # type: ignore[call-arg]
                except TypeError:
                    pass
            try:
                read_store = self._get_read_store_port()
            except Exception:
                read_store = None
            if read_store is not None:
                getter_summaries = getattr(read_store, "list_strategy_summaries", None)
                if callable(getter_summaries):
                    try:
                        return list(getter_summaries(include_retired=True) or [])
                    except TypeError:
                        pass
                getter_specs = getattr(read_store, "list_strategy_specs", None)
                if callable(getter_specs):
                    try:
                        return list(getter_specs(include_retired=True) or [])
                    except TypeError:
                        pass
        return self.list_strategy_summaries()

    def list_strategy_summaries_page(
        self,
        *,
        state: Optional[str] = None,
        exclude_state: Optional[str] = None,
        persona_id: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Optional[Dict[str, Any]]:
        norm_state = str(state).strip() if state and str(state).strip() else None
        norm_exclude_state = (
            str(exclude_state).strip()
            if exclude_state and str(exclude_state).strip()
            else None
        )
        if norm_state and norm_exclude_state:
            if self._bff_error:
                raise self._bff_error(
                    400,
                    ErrorCode.VALIDATION_FAILED,
                    "Conflicting state filters",
                    "state and exclude_state cannot be combined",
                    precondition_failed="exclude_state",
                )
            raise HTTPException(
                status_code=400,
                detail="state and exclude_state cannot be combined",
            )
        try:
            read_store = self._get_read_store_port()
        except Exception:
            return None
        getter = getattr(read_store, "list_strategy_summaries_page", None) or getattr(
            read_store, "list_strategy_specs_page", None
        )
        if not callable(getter):
            return None
        call_kwargs: Dict[str, Any] = {
            "state": norm_state,
            "persona_id": persona_id,
            "page_token": page_token,
            "page_size": page_size,
        }
        if norm_exclude_state is not None:
            call_kwargs["exclude_state"] = norm_exclude_state
        try:
            try:
                page = getter(**call_kwargs)
            except TypeError:
                if norm_exclude_state is not None:
                    return None
                raise
        except HTTPException as exc:
            if exc.status_code == 400 and self._bff_error:
                detail_obj = exc.detail if isinstance(exc.detail, dict) else {}
                err_obj = (
                    detail_obj.get("error")
                    if isinstance(detail_obj.get("error"), dict)
                    else {}
                )
                reason = str(
                    err_obj.get("reason")
                    or detail_obj.get("detail")
                    or exc.detail
                    or ""
                )
                if "exclude_state" in reason or "conflicting_state_filters" in reason:
                    raise self._bff_error(
                        400,
                        ErrorCode.VALIDATION_FAILED,
                        "Conflicting state filters",
                        "state and exclude_state cannot be combined",
                        precondition_failed="exclude_state",
                    ) from exc
                raise self._bff_error(
                    400,
                    ErrorCode.VALIDATION_FAILED,
                    "Invalid page_token",
                    "page_token must be a non-negative integer offset",
                ) from exc
            return self._invoke_read_owner(lambda: (_ for _ in ()).throw(exc))
        except Exception as exc:
            return self._invoke_read_owner(lambda: (_ for _ in ()).throw(exc))
        return page if isinstance(page, dict) else None

    def list_all_strategy_summary_pages(
        self,
        *,
        state: Optional[str] = None,
        exclude_state: Optional[str] = None,
        persona_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Walk every owner summary page of one candidate set (page_size 200).

        Returns ``{"items", "total", "state_counts"}`` or ``None`` when the
        owner has no paged view. The registry paging is by offset token, so a
        repeated or non-advancing token is a broken owner and fails closed.
        """
        items: List[Dict[str, Any]] = []
        state_counts: Optional[Dict[str, int]] = None
        total = 0
        page_token: Optional[str] = None
        seen_tokens: Set[str] = set()
        for page_number in range(STRATEGY_CANDIDATE_WALK_MAX_PAGES):
            page = self.list_strategy_summaries_page(
                state=state,
                exclude_state=exclude_state,
                persona_id=persona_id,
                page_token=page_token,
                page_size=STRATEGY_OWNER_PAGE_SIZE,
            )
            if page is None:
                return None
            page_items = page.get("items")
            if not isinstance(page_items, list):
                raise self._candidate_walk_error("Invalid strategy list payload")
            items.extend(page_items)
            if page_number == 0:
                total = int(page.get("total") or 0)
                state_counts = self.resolve_strategy_state_counts(owner_page=page)
            next_token = page.get("next_page_token")
            if not next_token:
                return {"items": items, "total": total, "state_counts": state_counts}
            next_token = str(next_token)
            if next_token in seen_tokens or next_token == page_token:
                raise self._candidate_walk_error("Strategy list paging did not advance")
            seen_tokens.add(next_token)
            page_token = next_token
        raise self._candidate_walk_error(
            "Strategy candidate set exceeds the bounded owner page walk"
        )

    def list_strategy_summaries_hide_retired_unfiltered_page(
        self,
        *,
        persona_lookup: Any,
        summary_persona_ids_fn: Callable[[Dict[str, Any]], List[str]],
        operational_counts_fn: Callable[[Any], Dict[str, int]],
        status_retired: str,
        page_slice_fn: Callable[
            [List[Dict[str, Any]], Optional[str], int],
            Tuple[List[Dict[str, Any]], Optional[str]],
        ],
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Optional[Dict[str, Any]]:
        """Bounded resolution for ``hide_retired_personas=true`` with no state filter.

        Instead of walking every 200-row owner page across thousands of unbound
        draft strategies, reads the first unfiltered owner page, walks additional
        unfiltered pages only until enough non-retired-persona rows exist to fill
        ``(page_token, page_size)``, and resolves exact ``operational_counts`` and
        ``hidden_retired_persona_count`` from the bounded ``exclude_state=draft``
        non-draft population plus the remaining unbound draft count.
        """
        start = 0
        if page_token is not None and str(page_token).strip() != "":
            try:
                start = int(str(page_token).strip())
            except (TypeError, ValueError) as exc:
                if self._bff_error:
                    raise self._bff_error(
                        400,
                        ErrorCode.VALIDATION_FAILED,
                        "Invalid page_token",
                        "page_token must be a non-negative integer offset",
                    ) from exc
                raise HTTPException(
                    status_code=400,
                    detail="page_token must be a non-negative integer offset",
                ) from exc
            if start < 0:
                if self._bff_error:
                    raise self._bff_error(
                        400,
                        ErrorCode.VALIDATION_FAILED,
                        "Invalid page_token",
                        "page_token must be a non-negative integer offset",
                    )
                raise HTTPException(
                    status_code=400,
                    detail="page_token must be a non-negative integer offset",
                )
        page_slice_fn([], page_token, page_size)
        first_page = self.list_strategy_summaries_page(
            state=None,
            exclude_state=None,
            persona_id=None,
            page_token=None,
            page_size=STRATEGY_OWNER_PAGE_SIZE,
        )
        if first_page is None:
            return None
        first_items = first_page.get("items")
        if not isinstance(first_items, list):
            raise self._candidate_walk_error("Invalid strategy list payload")
        for summary in first_items:
            sid = (
                str(summary.get("strategy_id") or summary.get("id") or "").strip()
                if isinstance(summary, dict)
                else ""
            )
            if not sid:
                raise self._candidate_walk_error("Invalid strategy summary entry")

        unfiltered_total = int(first_page.get("total") or 0)
        state_counts = self.resolve_strategy_state_counts(owner_page=first_page)
        first_next_token = first_page.get("next_page_token")

        if not first_next_token:
            kept = [
                s
                for s in first_items
                if persona_lookup.operational_status(summary_persona_ids_fn(s))
                != status_retired
            ]
            page_summaries, next_page_token = page_slice_fn(kept, page_token, page_size)
            op_counts = operational_counts_fn(
                persona_lookup.operational_status(summary_persona_ids_fn(s))
                for s in first_items
            )
            return {
                "page_summaries": page_summaries,
                "next_page_token": next_page_token,
                "total": len(kept),
                "hidden_retired_persona_count": len(first_items) - len(kept),
                "state_counts": state_counts,
                "operational_counts": op_counts,
            }

        if not getattr(persona_lookup, "available", True):
            if start + page_size <= len(first_items):
                page_summaries = list(first_items[start : start + page_size])
            else:
                target_page = self.list_strategy_summaries_page(
                    state=None,
                    exclude_state=None,
                    persona_id=None,
                    page_token=page_token,
                    page_size=page_size,
                )
                if not isinstance(target_page, dict) or not isinstance(
                    target_page.get("items"), list
                ):
                    raise self._candidate_walk_error("Invalid strategy list payload")
                page_summaries = list(target_page["items"])
                for summary in page_summaries:
                    sid = (
                        str(summary.get("strategy_id") or summary.get("id") or "").strip()
                        if isinstance(summary, dict)
                        else ""
                    )
                    if not sid:
                        raise self._candidate_walk_error("Invalid strategy summary entry")
            next_offset = start + len(page_summaries)
            next_page_token = (
                str(next_offset)
                if (next_offset < unfiltered_total and page_summaries)
                else None
            )
            return {
                "page_summaries": page_summaries,
                "next_page_token": next_page_token,
                "total": unfiltered_total,
                "hidden_retired_persona_count": 0,
                "state_counts": state_counts,
                "operational_counts": {
                    "running": 0,
                    "idle": 0,
                    "retired": 0,
                    "unbound": 0,
                    "unknown": unfiltered_total,
                },
            }

        walked_items: List[Dict[str, Any]] = list(first_items)
        kept = [
            s
            for s in first_items
            if persona_lookup.operational_status(summary_persona_ids_fn(s))
            != status_retired
        ]
        next_token: Optional[str] = str(first_next_token)
        seen_tokens: Set[str] = {next_token}
        page_number = 1
        while len(kept) < start + page_size and next_token:
            if page_number >= STRATEGY_CANDIDATE_WALK_MAX_PAGES:
                raise self._candidate_walk_error(
                    "Strategy candidate set exceeds the bounded owner page walk"
                )
            page = self.list_strategy_summaries_page(
                state=None,
                exclude_state=None,
                persona_id=None,
                page_token=next_token,
                page_size=STRATEGY_OWNER_PAGE_SIZE,
            )
            if page is None or not isinstance(page.get("items"), list):
                raise self._candidate_walk_error("Invalid strategy list payload")
            page_items = page["items"]
            for summary in page_items:
                sid = (
                    str(summary.get("strategy_id") or summary.get("id") or "").strip()
                    if isinstance(summary, dict)
                    else ""
                )
                if not sid:
                    raise self._candidate_walk_error("Invalid strategy summary entry")
            walked_items.extend(page_items)
            for s in page_items:
                if (
                    persona_lookup.operational_status(summary_persona_ids_fn(s))
                    != status_retired
                ):
                    kept.append(s)
            raw_next = page.get("next_page_token")
            if not raw_next:
                next_token = None
                break
            cand_next = str(raw_next)
            if cand_next in seen_tokens or cand_next == next_token:
                raise self._candidate_walk_error("Strategy list paging did not advance")
            seen_tokens.add(cand_next)
            next_token = cand_next
            page_number += 1

        seen_by_id: Dict[str, Dict[str, Any]] = {
            str(s.get("strategy_id") or s.get("id") or "").strip(): s
            for s in walked_items
        }
        non_draft_expected: Optional[int] = None
        if isinstance(state_counts, dict):
            non_draft_expected = sum(
                int(v)
                for k, v in state_counts.items()
                if k not in ("draft", "retired")
            )
        seen_non_draft = sum(
            1
            for s in seen_by_id.values()
            if not is_true_draft_lifecycle_state(
                s.get("lifecycle_state") or s.get("state") or s.get("status")
            )
        )
        if next_token is not None and (
            non_draft_expected is None or seen_non_draft < non_draft_expected
        ):
            non_draft_walk = self.list_all_strategy_summary_pages(
                state=None,
                exclude_state="draft",
                persona_id=None,
            )
            if non_draft_walk is not None:
                for s in non_draft_walk.get("items") or ():
                    sid = (
                        str(s.get("strategy_id") or s.get("id") or "").strip()
                        if isinstance(s, dict)
                        else ""
                    )
                    if not sid:
                        raise self._candidate_walk_error("Invalid strategy summary entry")
                    seen_by_id.setdefault(sid, s)

        op_counts = operational_counts_fn(
            persona_lookup.operational_status(summary_persona_ids_fn(s))
            for s in seen_by_id.values()
        )
        remaining_unbound_drafts = max(0, unfiltered_total - len(seen_by_id))
        op_counts["unbound"] = int(op_counts.get("unbound") or 0) + remaining_unbound_drafts
        hidden_count = int(op_counts.get("retired") or 0)
        total = max(0, unfiltered_total - hidden_count)
        page_summaries = kept[start : start + page_size]
        next_offset = start + len(page_summaries)
        next_page_token = (
            str(next_offset) if (next_offset < total and page_summaries) else None
        )
        return {
            "page_summaries": page_summaries,
            "next_page_token": next_page_token,
            "total": total,
            "hidden_retired_persona_count": hidden_count,
            "state_counts": state_counts,
            "operational_counts": op_counts,
        }

    def _candidate_walk_error(self, message: str) -> Exception:
        if self._bff_error:
            return self._bff_error(
                503, ErrorCode.DEPENDENCY_UNAVAILABLE, message, "strategy_store_unavailable"
            )
        return HTTPException(status_code=503, detail=message)

    def get_strategy_rules(
        self, strategy_id: str
    ) -> Tuple[Optional[List[Dict[str, Any]]], str]:
        """Rules of one strategy from its approved execution bundles.

        Returns ``(rules, "ok")`` or ``(None, "unavailable")``. A failed owner
        read never raises: the detail stays readable and says rules are
        unavailable.
        """
        try:
            read_store = self._get_read_store_port()
            getter = getattr(read_store, "read_registry_strategy_artifacts_for_strategy", None)
            if not callable(getter):
                return None, "unavailable"
            result = getter(strategy_id)
        except Exception as exc:  # noqa: BLE001 - rules degrade, detail stays 200
            log.warning("strategy rules read failed for %s: %s", strategy_id, exc)
            return None, "unavailable"
        if getattr(result, "status", None) != "ok":
            return None, "unavailable"
        return project_strategy_rules(
            list(getattr(result, "items", None) or []), strategy_id=strategy_id
        ), "ok"

    def resolve_strategy_state_counts(
        self,
        *,
        owner_page: Optional[Dict[str, Any]] = None,
        fallback_summaries: Optional[List[Dict[str, Any]]] = None,
    ) -> Optional[Dict[str, int]]:
        """Resolve per-DTO-state counts from the bounded owner page read or
        in-memory summary fallback; return ``None`` on any failure and never
        fabricate zero counts when the count read fails."""
        try:
            read_store = None
            try:
                read_store = self._get_read_store_port()
            except Exception:
                read_store = None
            if read_store is not None and callable(
                custom_getter := getattr(read_store, "get_strategy_state_counts", None)
            ):
                custom_counts = custom_getter()
                if (
                    isinstance(custom_counts, dict)
                    and custom_counts
                    and all(
                        isinstance(k, str)
                        and k.strip()
                        and isinstance(v, int)
                        and not isinstance(v, bool)
                        and v >= 0
                        for k, v in custom_counts.items()
                    )
                ):
                    counts = {k: 0 for k in STRATEGY_DEFAULT_STATE_COUNT_KEYS}
                    for k, v in custom_counts.items():
                        counts[str(k).strip()] = int(v)
                    return counts
                return None

            if owner_page is not None:
                if not isinstance(owner_page, dict):
                    return None
                raw_counts = owner_page.get("state_counts")
                if (
                    not isinstance(raw_counts, dict)
                    or not raw_counts
                    or not all(
                        isinstance(k, str)
                        and k.strip()
                        and isinstance(v, int)
                        and not isinstance(v, bool)
                        and v >= 0
                        for k, v in raw_counts.items()
                    )
                ):
                    return None
                counts = {k: 0 for k in STRATEGY_DEFAULT_STATE_COUNT_KEYS}
                for k, v in raw_counts.items():
                    counts[str(k).strip()] = int(v)
                return counts

            if not isinstance(fallback_summaries, list):
                return None
            counts = {k: 0 for k in STRATEGY_DEFAULT_STATE_COUNT_KEYS}
            for summary in fallback_summaries:
                if not isinstance(summary, dict):
                    return None
                raw_state = (
                    summary.get("lifecycle_state")
                    or summary.get("state")
                    or summary.get("status")
                )
                if raw_state is None and "current_spec_version_id" not in summary:
                    return None
                dto_state = normalize_strategy_dto_state(raw_state or "draft")
                counts[dto_state] = counts.get(dto_state, 0) + 1
            return counts
        except Exception:
            return None

    def _invoke_read_owner(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except HTTPException as exc:
            if isinstance(exc.detail, dict) and "error" in exc.detail:
                raise
            status = 403 if exc.status_code in (401, 403) else (exc.status_code if exc.status_code >= 500 else 503)
            code = ErrorCode.FORBIDDEN if status == 403 else ErrorCode.DEPENDENCY_UNAVAILABLE
            if self._bff_error:
                raise self._bff_error(status, code, str(exc.detail), str(exc.detail)) from exc
            raise
        except Exception as exc:
            status = 403 if getattr(exc, "status_code", None) in (401, 403) else 503
            code = ErrorCode.FORBIDDEN if status == 403 else ErrorCode.DEPENDENCY_UNAVAILABLE
            msg = str(getattr(exc, "detail", None) or exc or "Canonical strategy read owner unavailable")
            if self._bff_error:
                raise self._bff_error(status, code, msg, msg) from exc
            raise HTTPException(status_code=status, detail=msg) from exc

    def get_strategy_spec_detail(self, strategy_id: str, version_selector: str = "current") -> Optional[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "get_strategy_spec_detail", None)
        return self._invoke_read_owner(getter, strategy_id, version_selector=version_selector) if callable(getter) else None

    def get_strategy(self, strategy_id: str) -> Optional[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        if callable(getter := getattr(read_store, "get_strategy_spec", None)) and (res := self._invoke_read_owner(getter, strategy_id)):
            return res
        if callable(getter := getattr(read_store, "get_strategy", None)) and (res := self._invoke_read_owner(getter, strategy_id)):
            return res
        return None

    def get_strategy_registry_state(self, strategy_id: str) -> Tuple[Optional[str], str]:
        """Current registry state of one strategy, read like GET /bff/strategies/{id}.

        Returns ``(state, "ok")`` (``state`` is None when the registry has no such
        strategy) or ``(None, "unavailable")`` when the owner read fails. Never
        raises and never writes.
        """
        try:
            detail = self.get_strategy_spec_detail(strategy_id, version_selector="current")
            summary = (
                detail
                if isinstance(detail, dict) and (detail.get("strategy_id") or detail.get("id"))
                else self.get_strategy(strategy_id)
            )
        except Exception as exc:  # noqa: BLE001 - the caller degrades, never fails
            log.warning("strategy state read failed for %s: %s", strategy_id, exc)
            return None, "unavailable"
        if not isinstance(summary, dict):
            return None, "ok"
        detail_dict = detail if isinstance(detail, dict) else {}
        raw = (
            detail_dict.get("lifecycle_state")
            or detail_dict.get("state")
            or detail_dict.get("status")
            or summary.get("lifecycle_state")
            or summary.get("state")
            or summary.get("status")
        )
        if not raw:
            return None, "ok"
        normalize = self._normalize_lifecycle_state
        return (str(normalize(raw)) if normalize is not None else str(raw)), "ok"

    def get_latest_ranking_snapshot(self) -> Tuple[Optional[Dict[str, Any]], bool]:
        from services.control_plane.bff.core.owner_reads import (
            cached_owner_call,
            latest_ranking_snapshot,
            selected_tenant,
        )

        try:
            read_store = self._get_read_store_port()
        except NotImplementedError:
            read_store = None
        except Exception:
            return None, True

        def _read() -> Tuple[Optional[Dict[str, Any]], bool]:
            if read_store is not None:
                getter = getattr(read_store, "get_latest_ranking_snapshot", None)
                if callable(getter):
                    try:
                        snap = getter()
                        if snap is not None and not isinstance(snap, dict):
                            to_dict = getattr(snap, "to_canonical_dict", None)
                            candidate = to_dict() if callable(to_dict) else None
                            snap = candidate if isinstance(candidate, dict) else None
                        return snap, False
                    except Exception:
                        return None, True
                if hasattr(read_store, "list_ranking_snapshots") or hasattr(read_store, "_records_table"):
                    try:
                        snap = latest_ranking_snapshot(read_store)
                        return (snap if isinstance(snap, dict) else None), False
                    except Exception:
                        return None, True
            try:
                snap = latest_ranking_snapshot(None)
                return (snap if isinstance(snap, dict) else None), False
            except Exception:
                return None, True

        return cached_owner_call(
            ("strategy_latest_ranking_snapshot", id(self), id(read_store), selected_tenant.get()),
            _read,
        )

    def ensure_strategy_exists(self, strategy_id: str) -> None:
        strategy = self.get_strategy(strategy_id)
        if strategy:
            return
        if self._bff_error:
            raise self._bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "Strategy not found",
                f"Strategy {strategy_id} does not exist",
            )
        raise HTTPException(status_code=404, detail=f"Strategy {strategy_id} does not exist")

    def list_strategy_spec_versions(self, strategy_id: str) -> List[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "list_strategy_spec_versions", None)
        if callable(getter):
            return self._invoke_read_owner(getter, strategy_id) or []
        return []

    def list_research_experiments(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        raw = read_store.list_research_experiments() or []
        if strategy_id:
            return [e for e in raw if (e.get("linked_strategy_id") or e.get("strategy_id")) == strategy_id]
        return raw

    def list_research_artifacts(self, strategy_id: Optional[str] = None) -> List[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        raw = read_store.list_research_artifacts() or []
        if strategy_id:
            return [a for a in raw if (a.get("linked_strategy_id") or a.get("strategy_id")) == strategy_id]
        return raw

    def get_strategy_lineage(self, strategy_id: str) -> Tuple[List[Dict[str, Any]], List[str]]:
        read_store = self._get_read_store_port()
        # The lineage owner resolves the strategy root (registry id / strategy id) and
        # returns only the connected subgraph; no whole-population scan here.
        edges = read_store.get_lineage_graph(root_id=strategy_id, depth=3) or []
        nodes_seen: Set[str] = set()
        related: List[Dict[str, Any]] = []
        for edge in edges:
            related.append(edge)
            for key in (
                str(edge.get("from_artifact_id") or edge.get("source_id") or ""),
                str(edge.get("to_artifact_id") or edge.get("target_id") or ""),
                str(edge.get("strategy_id") or ""),
            ):
                if key:
                    nodes_seen.add(key)
        nodes_seen.add(strategy_id)
        return related, sorted(nodes_seen)

    def list_ooda_packets_for_strategy(self, strategy_id: str) -> List[Dict[str, Any]]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "list_ooda_packets_for_strategy", None)
        if callable(getter):
            return getter(strategy_id) or []
        return []

    def persist_strategy(
        self,
        record: Dict[str, Any],
        *,
        actor: Dict[str, Any],
        command_key: str,
    ) -> bool:
        writer = self._get_write_owner_port()
        if writer is None:
            if self._bff_error:
                raise self._bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Canonical strategy writer unavailable",
                    "Cannot persist strategy without an authoritative domain store",
                )
            raise HTTPException(status_code=503, detail="Canonical strategy writer unavailable")
        written = False
        try:
            res = None
            if hasattr(writer, "upsert_strategy"):
                res = writer.upsert_strategy({**record, "actor": actor, "command_key": command_key})
            elif hasattr(writer, "create_strategy_spec"):
                res = writer.create_strategy_spec({**record, "actor": actor, "command_key": command_key})
            if res:
                written = True
        except HTTPException:
            raise
        except Exception as exc:
            if self._bff_error:
                raise self._bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Canonical strategy persistence failed",
                    str(exc),
                ) from exc
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        if not written:
            if self._bff_error:
                raise self._bff_error(
                    503,
                    ErrorCode.DEPENDENCY_UNAVAILABLE,
                    "Canonical strategy writer unavailable",
                    "Cannot persist strategy without an authoritative domain store",
                )
            raise HTTPException(status_code=503, detail="Canonical strategy writer unavailable")
        return True

    def get_persona_route_policy(self, persona_id: str) -> Dict[str, Any]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "get_route_policy_for_persona", None)
        if callable(getter):
            return getter(persona_id) or {}
        return {}

    def get_persona_capability_snapshot(self, persona_id: str) -> Dict[str, Any]:
        read_store = self._get_read_store_port()
        getter = getattr(read_store, "get_capability_snapshot_for_persona", None)
        if callable(getter):
            return getter(persona_id) or {}
        return {}

    # --- Seed store use cases ---
    def list_seeds(self) -> List[Any]:
        return self.seed_store.list_all()

    def get_seed(self, seed_id: str) -> Optional[Any]:
        return self.seed_store.get(seed_id)

    def record_seed_review_decision(self, seed_id: str, **kwargs: Any) -> Tuple[Any, Any]:
        return self.seed_store.record_review_decision(seed_id, **kwargs)

    def submit_seed_replication(
        self,
        *,
        seed_id: str,
        payload: Dict[str, Any],
        operator_id: str,
        resolved_key: str,
    ) -> Dict[str, Any]:
        """Execute replication command objective: idempotency check, bridge submit, replay decision, cache write."""
        request_hash = (
            self._stable_json_hash(
                {
                    "route": "POST /bff/management/strategy-seeds/{seed_id}/submit-replication",
                    "seed_id": seed_id,
                    "payload": payload,
                }
            )
            if self._stable_json_hash is not None
            else str(hash(json.dumps(payload, sort_keys=True, default=str)))
        )
        if self._seed_replication_idempotency is not None:
            existing = self._seed_replication_idempotency.get(resolved_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    if self._bff_error:
                        raise self._bff_error(
                            409,
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "Idempotency key was already used with a different payload",
                            f"Key {resolved_key!r} is bound to a different request hash",
                            precondition_failed="idempotency_conflict",
                            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                        )
                    raise HTTPException(status_code=409, detail="Idempotency key conflict")
                cached = json.loads(json.dumps(existing.get("result") or {}))
                cached.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
                return cached

        bridge = self._replication_bridge or (
            StrategySeedReplicationBridge(seed_store=self.seed_store)
            if StrategySeedReplicationBridge is not None
            else None
        )
        if bridge is None:
            raise NotImplementedError("StrategySeedReplicationBridge is unavailable")

        submission = bridge.submit_seed_to_replication(
            seed_id,
            requested_by=operator_id,
            idempotency_key=resolved_key,
            created_at=payload.get("created_at") or None,
            strategy_spec_version=str(payload.get("strategy_spec_version") or "1.0.0"),
        )

        snapshot_at = submission.created_at or (self._utc_now() if self._utc_now else "2026-09-27T00:00:00Z")
        result = {
            "data": {
                "seed_id": submission.seed_id,
                "replication_ref": submission.replication_ref,
                "experiment_task_id": submission.experiment_task_id,
                "strategy_id": submission.strategy_id,
                "strategy_spec_version": submission.strategy_spec_version,
                "research_task_id": submission.research_task.get("task_id"),
                "status": submission.research_task.get("status") or "queued",
                "experiment_task": dict(submission.experiment_task),
                "registry_write_performed": False,
                "execution_route": "none",
                "deployment_authority": "none",
                "approved_artifact_created": False,
                "deployment_plan_created": False,
                "runtime_binding_created": False,
                "idempotent_replay": submission.idempotent_replay,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "research_only": True,
                "execution_route": "none",
                "idempotency": {
                    "idempotencyKey": resolved_key,
                    "replayed": False,
                },
            },
        }
        if self._seed_replication_idempotency is not None:
            self._seed_replication_idempotency[resolved_key] = {
                "request_hash": request_hash,
                "result": result,
            }
        return result

    def review_seed(
        self,
        *,
        seed_id: str,
        payload: Dict[str, Any],
        action: str,
        operator_id: str,
        target_refs: List[Dict[str, Any]],
        resolved_key: str,
        result_builder: Callable[[Any, Any, str, str, bool], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Execute review command objective: idempotency check, store decision, replay handling, cache write."""
        request_hash = (
            self._stable_json_hash(
                {
                    "route": "POST /bff/management/strategy-seeds/{seed_id}/review",
                    "seed_id": seed_id,
                    "action": action,
                    "payload": payload,
                }
            )
            if self._stable_json_hash is not None
            else str(hash(json.dumps(payload, sort_keys=True, default=str)))
        )
        if self._seed_review_idempotency is not None:
            existing = self._seed_review_idempotency.get(resolved_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    if self._bff_error:
                        raise self._bff_error(
                            409,
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "Idempotency key was already used with a different payload",
                            f"Key {resolved_key!r} is bound to a different request hash",
                            precondition_failed="idempotency_conflict",
                            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                        )
                    raise HTTPException(status_code=409, detail="Idempotency key conflict")
                cached = json.loads(json.dumps(existing.get("result") or {}))
                cached.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
                return cached

        snapshot_at = self._utc_now() if self._utc_now else "2026-09-27T00:00:00Z"
        updated, decision = self.seed_store.record_review_decision(
            seed_id,
            decision=action,
            reviewer_id=operator_id,
            reason=str(payload.get("reason") or ""),
            target_refs=target_refs,
            created_at=payload.get("created_at") or snapshot_at,
            idempotency_key=resolved_key,
            request_hash=request_hash,
        )
        replayed = bool(getattr(decision, "idempotent_replay", False))
        result = result_builder(updated, decision, snapshot_at, resolved_key, replayed)
        if self._seed_review_idempotency is not None:
            self._seed_review_idempotency[resolved_key] = {
                "request_hash": request_hash,
                "result": result,
            }
        return result

    def merge_seed(
        self,
        *,
        seed_id: str,
        payload: Dict[str, Any],
        target_seed_id: str,
        operator_id: str,
        target_refs: List[Dict[str, Any]],
        resolved_key: str,
        result_builder: Callable[[Any, Any, str, str, bool], Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Execute merge command objective: idempotency check, store merge, replay handling, cache write."""
        request_hash = (
            self._stable_json_hash(
                {
                    "route": "POST /bff/management/strategy-seeds/{seed_id}/merge",
                    "seed_id": seed_id,
                    "payload": payload,
                }
            )
            if self._stable_json_hash is not None
            else str(hash(json.dumps(payload, sort_keys=True, default=str)))
        )
        if self._seed_review_idempotency is not None:
            existing = self._seed_review_idempotency.get(resolved_key)
            if existing is not None:
                if existing.get("request_hash") != request_hash:
                    if self._bff_error:
                        raise self._bff_error(
                            409,
                            ErrorCode.IDEMPOTENCY_CONFLICT,
                            "Idempotency key was already used with a different payload",
                            f"Key {resolved_key!r} is bound to a different request hash",
                            precondition_failed="idempotency_conflict",
                            suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
                        )
                    raise HTTPException(status_code=409, detail="Idempotency key conflict")
                cached = json.loads(json.dumps(existing.get("result") or {}))
                cached.setdefault("meta", {}).setdefault("idempotency", {})["replayed"] = True
                return cached

        snapshot_at = self._utc_now() if self._utc_now else "2026-09-27T00:00:00Z"
        updated, decision = self.seed_store.merge_seed(
            seed_id,
            target_seed_id=target_seed_id,
            reviewer_id=operator_id,
            reason=str(payload.get("reason") or ""),
            target_refs=target_refs,
            created_at=payload.get("created_at") or snapshot_at,
            idempotency_key=resolved_key,
            request_hash=request_hash,
        )
        replayed = bool(getattr(decision, "idempotent_replay", False))
        result = result_builder(updated, decision, snapshot_at, resolved_key, replayed)
        if self._seed_review_idempotency is not None:
            self._seed_review_idempotency[resolved_key] = {
                "request_hash": request_hash,
                "result": result,
            }
        return result

    def create_strategy(
        self,
        *,
        payload: Dict[str, Any],
        identity: Any,
        principal: Dict[str, Any],
        resolved_key: str,
        dry_run: bool,
    ) -> Dict[str, Any]:
        """Execute the strategy creation command objective, including replay check, dry-run, and persistence."""
        request_hash = (
            self._stable_json_hash({"route": "POST /bff/strategies", "payload": payload, "principal": principal})
            if self._stable_json_hash is not None
            else str(hash(json.dumps(payload, sort_keys=True, default=str)))
        )
        if not dry_run and self._idempotency_check is not None:
            cached = self._idempotency_check(resolved_key, request_hash)
            if cached is not None:
                return cached

        name = str(payload.get("name") or "").strip()
        snapshot_at = self._utc_now() if self._utc_now is not None else "2026-09-27T00:00:00Z"
        strategy_id = f"strategy-{snapshot_at[:10].replace('-', '')}-{uuid.uuid4().hex[:8]}"
        state = (
            self._normalize_lifecycle_state(payload.get("state") or "draft")
            if self._normalize_lifecycle_state is not None
            else str(payload.get("state") or "draft")
        )
        risk = (
            self._normalize_risk_level(payload.get("risk"))
            if self._normalize_risk_level is not None
            else str(payload.get("risk") or "medium")
        )
        record = {
            "id": strategy_id,
            "strategy_id": strategy_id,
            "name": name,
            "owner": str(payload.get("owner") or getattr(identity, "operator_id", "operator")),
            "updatedAt": snapshot_at,
            "state": state,
            "risk": risk,
            "alpha": str(payload.get("alpha") or ""),
            "capitalPoolId": str(payload.get("capitalPoolId") or payload.get("capital_pool_id") or ""),
            "personaIds": list(payload.get("personaIds") or payload.get("persona_ids") or []),
            "pnl": None,
            "pnl30d": None,
            "sharpe": None,
            "drawdown": None,
            "totalTrades": None,
            "metricsAsOf": None,
            "metricsResolution": "missing",
            "metricsPersonaId": None,
            "availableActions": ["edit", "submit", "retire"],
            "labelKey": f"strategy.{strategy_id}",
        }
        if dry_run:
            if self._dry_run_success_response is not None:
                return self._dry_run_success_response(
                    record,
                    snapshot_at=snapshot_at,
                    idempotency_key=resolved_key,
                    evidence_kind="strategy.create",
                )
            return {"data": record, "meta": {"snapshot_at": snapshot_at, "dry_run": True}}

        self.persist_strategy(
            record,
            actor=principal,
            command_key=resolved_key,
        )
        result = {
            "data": record,
            "meta": {"snapshot_at": snapshot_at},
        }
        if self._idempotency_store is not None:
            self._idempotency_store[resolved_key] = {"request_hash": request_hash, "result": result}
        return result


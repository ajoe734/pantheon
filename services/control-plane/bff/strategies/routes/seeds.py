"""StrategySpecSeed governed review inbox, review, merge, and replication routes."""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from fastapi import APIRouter, Body, Header, HTTPException, Query

from services.control_plane.persona.persona_strategy_discovery import (
    PersonaStrategyDiscoveryService,
    extract_persona_strategy_profile,
)
from services.source_ingestion.replication_bridge import (
    StrategySeedReplicationBridge,
    StrategySeedReplicationBridgeError,
)
from services.source_ingestion.strategy_seed_store import (
    SeedReviewDecision,
    StrategySpecSeedReviewError,
    StrategySpecSeedStore,
    StrategySpecSeedStoreError,
)

from .common import StrategyRouteContext

try:
    from services.control_plane.bff.models import ErrorCode, OperatorIdentity
except (ImportError, ValueError):
    from models import ErrorCode, OperatorIdentity

log = logging.getLogger(__name__)

_SEED_KINDS_RISK = frozenset({"risk_constraint", "execution_constraint"})
_SEED_KINDS_NEGATIVE = frozenset({"negative", "negative_memory"})


def build_seeds_router(ctx: StrategyRouteContext) -> APIRouter:
    router = APIRouter()

    def _strategy_seed_replication_idempotency_check(
        resolved_key: str,
        request_hash: str,
    ) -> Optional[Dict[str, Any]]:
        existing = ctx.strategy_seed_replication_idempotency.get(resolved_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise ctx.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        import json as _json
        result = _json.loads(_json.dumps(existing.get("result") or {}))
        meta = result.setdefault("meta", {})
        idempotency = meta.setdefault("idempotency", {})
        idempotency["replayed"] = True
        return result

    def _require_strategy_seed_submit_role(identity: OperatorIdentity) -> None:
        if {"operator", "admin"}.intersection(identity.roles):
            return
        raise ctx.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Strategy seed replication submit requires operator role",
            "Read-role users cannot submit StrategySpecSeed replication tasks.",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )

    def _strategy_seed_replication_error(exc: StrategySeedReplicationBridgeError) -> HTTPException:
        if exc.code == "seed_not_found":
            return ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "StrategySpecSeed not found",
                str(exc),
                precondition_failed="seed_id",
            )
        if exc.code == "invalid_seed_status":
            return ctx.bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "StrategySpecSeed is not eligible for replication",
                str(exc),
                precondition_failed="status",
                suggestion="Promote the seed to StrategySpec before submitting replication.",
            )
        return ctx.bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "StrategySpecSeed replication request is invalid",
            str(exc),
            precondition_failed=exc.code or "replication_request",
        )

    def _strategy_seed_replication_response(
        *,
        seed_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        resolved_key: str,
    ) -> Dict[str, Any]:
        request_hash = ctx.stable_json_hash(
            {
                "route": "POST /bff/management/strategy-seeds/{seed_id}/submit-replication",
                "seed_id": seed_id,
                "payload": payload,
            }
        )
        cached = _strategy_seed_replication_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached

        try:
            submission = StrategySeedReplicationBridge().submit_seed_to_replication(
                seed_id,
                requested_by=identity.operator_id,
                idempotency_key=resolved_key,
                created_at=payload.get("created_at") or None,
                strategy_spec_version=str(payload.get("strategy_spec_version") or "1.0.0"),
            )
        except StrategySeedReplicationBridgeError as exc:
            raise _strategy_seed_replication_error(exc) from exc

        snapshot_at = submission.created_at or ctx.utc_now()
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
        ctx.strategy_seed_replication_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    def _strategy_seed_review_idempotency_check(
        resolved_key: str,
        request_hash: str,
    ) -> Optional[Dict[str, Any]]:
        existing = ctx.strategy_seed_review_idempotency.get(resolved_key)
        if existing is None:
            return None
        if existing.get("request_hash") != request_hash:
            raise ctx.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                f"Key {resolved_key!r} is bound to a different request hash",
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        import json as _json
        result = _json.loads(_json.dumps(existing.get("result") or {}))
        meta = result.setdefault("meta", {})
        idempotency = meta.setdefault("idempotency", {})
        idempotency["replayed"] = True
        return result

    def _require_strategy_seed_review_role(identity: OperatorIdentity) -> None:
        if {"operator", "admin"}.intersection(identity.roles):
            return
        raise ctx.bff_error(
            403,
            ErrorCode.FORBIDDEN,
            "Strategy seed review command requires operator role",
            "Read-role users cannot execute StrategySpecSeed review actions.",
            precondition_failed="role_check",
            suggestion="Escalate to a user with operator or admin role",
        )

    def _strategy_seed_review_error(exc: Exception) -> HTTPException:
        code = getattr(exc, "code", "")
        if code == "idempotency_conflict":
            return ctx.bff_error(
                409,
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "Idempotency key was already used with a different payload",
                str(exc),
                precondition_failed="idempotency_conflict",
                suggestion="Use a new Idempotency-Key or resubmit the original payload unchanged",
            )
        if code in {"seed_not_found", "merge_target_not_found"}:
            return ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "StrategySpecSeed not found",
                str(exc),
                precondition_failed="seed_id",
            )
        if code in {"terminal_seed_status", "invalid_status_transition", "invalid_merge_target"}:
            return ctx.bff_error(
                409,
                ErrorCode.OPERATION_NOT_ALLOWED,
                "StrategySpecSeed review action is not allowed",
                str(exc),
                precondition_failed="status",
            )
        return ctx.bff_error(
            422,
            ErrorCode.VALIDATION_FAILED,
            "StrategySpecSeed review request is invalid",
            str(exc),
            precondition_failed=code or "review_request",
        )

    def _strategy_seed_status_value(seed: Any) -> str:
        status = getattr(seed, "status", "")
        return status.value if hasattr(status, "value") else str(status or "")

    def _strategy_seed_source_kind(seed: Any) -> str:
        metadata = dict(getattr(seed, "metadata", {}) or {})
        return str(
            metadata.get("source_kind")
            or metadata.get("source_type")
            or metadata.get("source_connector_kind")
            or "strategy_spec_seed"
        )

    def _strategy_seed_strategy_family(seed: Any) -> str:
        metadata = dict(getattr(seed, "metadata", {}) or {})
        family = (
            metadata.get("strategy_family")
            or metadata.get("strategy_kind")
            or metadata.get("archetype")
        )
        if family:
            return str(family)
        hints = list(getattr(seed, "feature_hints", []) or [])
        return str(hints[0]) if hints else ""

    def _strategy_seed_allowed_actions(status: str, seed_kind: str = "") -> List[str]:
        actions_by_status = {
            "draft": ["accept", "reject", "request-evidence", "archive", "merge"],
            "needs_more_evidence": ["accept", "reject", "request-evidence", "archive", "merge"],
            "accepted": ["convert-to-spec-seed", "reject", "request-evidence", "archive", "merge"],
            "promoted_to_strategy_spec": ["submit-replication"],
            "rejected": [],
            "archived_as_insight": [],
            "merged": [],
            "converted_to_risk_constraint": [],
            "converted_to_negative": [],
        }
        actions = list(actions_by_status.get(status, []))
        if status in {"draft", "needs_more_evidence", "accepted"}:
            if seed_kind in _SEED_KINDS_RISK and "convert-to-risk" not in actions:
                actions.append("convert-to-risk")
            if seed_kind in _SEED_KINDS_NEGATIVE and "convert-to-negative" not in actions:
                actions.append("convert-to-negative")
        return actions

    def _strategy_seed_metadata_suggestions(seed: Any) -> List[Dict[str, Any]]:
        metadata = dict(getattr(seed, "metadata", {}) or {})
        raw_items = metadata.get("suggested_actions") or metadata.get("suggestions") or []
        if isinstance(raw_items, dict):
            raw_items = [raw_items]
        if isinstance(raw_items, str):
            raw_items = [{"type": raw_items}]
        suggestions: List[Dict[str, Any]] = []
        iterable = raw_items if isinstance(raw_items, list) else []
        for raw in iterable:
            if not isinstance(raw, dict):
                continue
            action_type = str(raw.get("type") or raw.get("action") or "").strip()
            if not action_type:
                continue
            item = dict(raw)
            item["type"] = action_type
            item.setdefault("source", "seed_metadata")
            item.setdefault("mode", "suggestion")
            item.setdefault("requires_operator_review", True)
            item.setdefault("auto_promote", False)
            suggestions.append(item)

        recommended = metadata.get("recommended_action")
        if isinstance(recommended, str):
            recommended = {"type": recommended}
        if isinstance(recommended, dict):
            action_type = str(recommended.get("type") or recommended.get("action") or "").strip()
            if action_type:
                item = dict(recommended)
                item["type"] = action_type
                item.setdefault("source", "seed_metadata")
                item.setdefault("mode", "suggestion")
                item.setdefault("requires_operator_review", True)
                item.setdefault("auto_promote", False)
                suggestions.append(item)
        return suggestions

    def _strategy_seed_persona_suggestions(
        seed: Any,
        *,
        snapshot_at: str,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        suggestions: List[Dict[str, Any]] = []
        if ctx.list_persona_records is None:
            return suggestions
        try:
            personas = ctx.list_persona_records(tenant_id)
        except Exception as exc:  # pragma: no cover
            log.warning("Persona read surface unavailable for seed inbox suggestions: %s", exc)
            return suggestions

        read_store = ctx.get_read_store_port()
        for persona in personas:
            persona_id = str(persona.get("persona_id") or persona.get("id") or "").strip()
            if not persona_id:
                continue
            try:
                route_policy = read_store.get_route_policy_for_persona(persona_id) or {}
                capability_snapshot = read_store.get_capability_snapshot_for_persona(persona_id) or {}
                profile = extract_persona_strategy_profile(
                    persona,
                    route_policy=route_policy,
                    capability_snapshot=capability_snapshot,
                )
                matches = PersonaStrategyDiscoveryService().match_candidates(
                    profile,
                    strategy_seeds=[seed],
                    strategy_specs=[],
                    created_at=snapshot_at,
                    include_blocked=True,
                )
            except Exception as exc:  # pragma: no cover
                log.warning("Persona strategy suggestion failed for %s: %s", persona_id, exc)
                continue
            for match in matches:
                payload = match.to_dict()
                action = payload.get("recommended_action") or {}
                action_type = str(action.get("type") or "").strip()
                if (
                    payload.get("matched_object_id") == getattr(seed, "seed_id", None)
                    and action_type == "promote_seed_candidate"
                ):
                    suggestions.append(
                        {
                            "type": "promote_seed_candidate",
                            "source": "persona_strategy_discovery",
                            "mode": "suggestion",
                            "requires_operator_review": True,
                            "auto_promote": False,
                            "persona_id": persona_id,
                            "match_id": payload.get("match_id"),
                            "score": payload.get("score"),
                            "blockers": (payload.get("metadata") or {}).get("blockers") or [],
                        }
                    )
        return suggestions

    def _strategy_seed_suggestions(
        seed: Any,
        *,
        snapshot_at: str,
        tenant_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        seen: Set[Tuple[str, str, str]] = set()
        suggestions: List[Dict[str, Any]] = []
        for item in [
            *_strategy_seed_metadata_suggestions(seed),
            *_strategy_seed_persona_suggestions(
                seed,
                snapshot_at=snapshot_at,
                tenant_id=tenant_id,
            ),
        ]:
            key = (
                str(item.get("type") or ""),
                str(item.get("source") or ""),
                str(item.get("match_id") or item.get("persona_id") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            suggestions.append(item)
        return suggestions

    def _strategy_seed_similar_existing_strategies(seed: Any) -> List[Dict[str, Any]]:
        metadata = dict(getattr(seed, "metadata", {}) or {})
        raw = metadata.get("similar_existing_strategies") or []
        if isinstance(raw, str):
            raw = [{"strategy_id": raw}]
        if isinstance(raw, list) and raw:
            return [
                dict(item) if isinstance(item, dict) else {"strategy_id": str(item)}
                for item in raw[:5]
            ]

        family = _strategy_seed_strategy_family(seed)
        if not family:
            return []
        try:
            candidates = ctx.list_strategy_summaries_records()
        except Exception:  # pragma: no cover
            return []
        similar: List[Dict[str, Any]] = []
        for item in candidates:
            strategy_family = str(
                item.get("strategy_family")
                or (item.get("metadata") or {}).get("strategy_family")
                or item.get("archetype")
                or ""
            )
            if strategy_family != family:
                continue
            similar.append(
                {
                    "strategy_id": item.get("strategy_id") or item.get("id"),
                    "title": item.get("title") or item.get("name"),
                    "strategy_family": strategy_family,
                }
            )
            if len(similar) >= 5:
                break
        return similar

    def _strategy_seed_recommended_action(
        *,
        status: str,
        seed_kind: str = "",
        suggestions: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        for item in suggestions:
            if str(item.get("type") or "") == "promote_seed_candidate":
                return dict(item)
        if status == "accepted":
            if seed_kind in _SEED_KINDS_RISK:
                return {"type": "convert-to-risk", "mode": "operator_decision"}
            if seed_kind in _SEED_KINDS_NEGATIVE:
                return {"type": "convert-to-negative", "mode": "operator_decision"}
            return {"type": "convert-to-spec-seed", "mode": "operator_decision"}
        if status in {"draft", "needs_more_evidence"}:
            if seed_kind in _SEED_KINDS_RISK:
                return {"type": "accept", "mode": "operator_decision", "next": "convert-to-risk"}
            if seed_kind in _SEED_KINDS_NEGATIVE:
                return {"type": "accept", "mode": "operator_decision", "next": "convert-to-negative"}
            return {"type": "accept", "mode": "operator_decision"}
        if status == "promoted_to_strategy_spec":
            return {"type": "submit-replication", "mode": "operator_decision"}
        return {"type": "none", "mode": "terminal"}

    def _strategy_seed_negative_memory_warning(seed: Any) -> Dict[str, Any]:
        raw = getattr(seed, "negative_memory_match", None)
        if not raw:
            return {"warning_level": "info", "similarity": 0.0, "reason": ""}
        match = dict(raw) if isinstance(raw, dict) else (raw.to_dict() if hasattr(raw, "to_dict") else {})
        return {
            "warning_level": str(match.get("warning_level") or "info"),
            "similarity": float(match.get("similarity") or 0.0),
            "reason": str(match.get("reason") or ""),
            "matched_memory_id": match.get("matched_memory_id"),
            "matched_memory_kind": match.get("matched_memory_kind"),
            "matched_terms": list(match.get("matched_terms") or []),
        }

    def _strategy_seed_card(
        seed: Any,
        *,
        snapshot_at: str,
        include_audit: bool = False,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        status = _strategy_seed_status_value(seed)
        suggestions = _strategy_seed_suggestions(
            seed,
            snapshot_at=snapshot_at,
            tenant_id=tenant_id,
        )
        lineage = dict(getattr(seed, "lineage", {}) or {})
        metadata = dict(getattr(seed, "metadata", {}) or {})
        evidence_refs = list(getattr(seed, "evidence_item_ids", []) or [])
        citation_refs = list(getattr(seed, "citation_refs", []) or [])
        seed_kind = str(metadata.get("seed_kind") or "strategy_spec_seed")
        source_surface = str(metadata.get("source_surface") or "")
        card = {
            "id": seed.seed_id,
            "seed_id": seed.seed_id,
            "source": {
                "source_id": seed.source_id,
                "source_ids": list(getattr(seed, "source_ids", []) or []),
                "source_kind": _strategy_seed_source_kind(seed),
                "source_surface": source_surface or None,
                "evidence_bundle_id": seed.evidence_bundle_id,
            },
            "seed_kind": seed_kind,
            "strategy_family": _strategy_seed_strategy_family(seed),
            "hypothesis": seed.hypothesis,
            "market": {
                "asset_class": list(getattr(seed, "asset_class", []) or []),
                "market_scope": list(getattr(seed, "market_scope", []) or []),
                "holding_period": getattr(seed, "holding_period", None),
            },
            "asset": list(getattr(seed, "asset_class", []) or []),
            "required_data": list(getattr(seed, "required_data", []) or []),
            "evidence_count": len(set([*evidence_refs, *citation_refs])),
            "confidence": getattr(seed, "confidence", None),
            "negative_memory_warning": _strategy_seed_negative_memory_warning(seed),
            "similar_existing_strategies": _strategy_seed_similar_existing_strategies(seed),
            "recommended_action": _strategy_seed_recommended_action(
                status=status,
                seed_kind=seed_kind,
                suggestions=suggestions,
            ),
            "suggested_actions": suggestions,
            "review_status": status,
            "status": status,
            "allowedActions": _strategy_seed_allowed_actions(status, seed_kind),
            "lineage_refs": {
                "evidence_bundle_id": seed.evidence_bundle_id,
                "source_ids": list(getattr(seed, "source_ids", []) or []),
                "evidence_item_ids": evidence_refs,
                "citation_refs": citation_refs,
                "trace_refs": list(getattr(seed, "trace_refs", []) or []),
                "registry_write_performed": lineage.get("registry_write_performed", False),
                "execution_route": lineage.get("execution_route") or "none",
            },
            "created_at": getattr(seed, "created_at", None),
        }
        if include_audit:
            card["review_decisions"] = list(lineage.get("review_decisions") or [])
            card["last_review_decision"] = lineage.get("last_review_decision")
        return card

    def _strategy_seed_matches_filters(
        seed: Any,
        *,
        status: Optional[str],
        source_kind: Optional[str],
        strategy_family: Optional[str],
        seed_kind: Optional[str],
        min_confidence: Optional[float],
    ) -> bool:
        if status and _strategy_seed_status_value(seed) != status:
            return False
        if source_kind and _strategy_seed_source_kind(seed) != source_kind:
            return False
        if strategy_family and _strategy_seed_strategy_family(seed) != strategy_family:
            return False
        if seed_kind:
            metadata = dict(getattr(seed, "metadata", {}) or {})
            actual_seed_kind = str(metadata.get("seed_kind") or "strategy_spec_seed")
            if actual_seed_kind != seed_kind:
                return False
        if min_confidence is not None and float(getattr(seed, "confidence", 0.0) or 0.0) < min_confidence:
            return False
        return True

    def _strategy_seed_list_response(
        *,
        status: Optional[str],
        source_kind: Optional[str],
        strategy_family: Optional[str],
        seed_kind: Optional[str],
        min_confidence: Optional[float],
        page_token: Optional[str],
        page_size: int,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = ctx.utc_now()
        store = StrategySpecSeedStore()
        seeds = [
            seed
            for seed in store.list_all()
            if _strategy_seed_matches_filters(
                seed,
                status=status,
                source_kind=source_kind,
                strategy_family=strategy_family,
                seed_kind=seed_kind,
                min_confidence=min_confidence,
            )
        ]
        cards = [
            _strategy_seed_card(seed, snapshot_at=snapshot_at, tenant_id=tenant_id)
            for seed in seeds
        ]
        page_items, next_page_token = ctx.page_slice(cards, page_token, page_size)
        return {
            "data": {
                "id": "management_strategy_seeds",
                "items": page_items,
                "summary": {
                    "total_items": len(cards),
                    "returned_items": len(page_items),
                    "research_only": True,
                    "execution_route": "none",
                },
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": len(cards),
                "page_size": page_size,
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "store_path": str(store.path),
                "count": len(cards),
                "filters": {
                    "status": status,
                    "source_kind": source_kind,
                    "strategy_family": strategy_family,
                    "seed_kind": seed_kind,
                    "min_confidence": min_confidence,
                },
                "research_only": True,
                "execution_route": "none",
            },
        }

    def _strategy_seed_detail_response(
        seed_id: str,
        *,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        snapshot_at = ctx.utc_now()
        store = StrategySpecSeedStore()
        seed = store.get(seed_id)
        if seed is None:
            raise ctx.bff_error(
                404,
                ErrorCode.RESOURCE_NOT_FOUND,
                "StrategySpecSeed not found",
                f"StrategySpecSeed not found: {seed_id}",
                precondition_failed="seed_id",
            )
        return {
            "data": _strategy_seed_card(
                seed,
                snapshot_at=snapshot_at,
                include_audit=True,
                tenant_id=tenant_id,
            ),
            "meta": {
                "snapshot_at": snapshot_at,
                "store_path": str(store.path),
                "research_only": True,
                "execution_route": "none",
            },
        }

    def _strategy_seed_review_action(payload: Dict[str, Any]) -> str:
        action = str(
            payload.get("action")
            or payload.get("decision")
            or payload.get("type")
            or ""
        ).strip().lower().replace("-", "_")
        aliases = {
            "request_more_evidence": "request_evidence",
            "needs_more_evidence": "request_evidence",
            "convert": "convert_to_spec_seed",
            "convert_to_strategy_spec": "convert_to_spec_seed",
            "archive_as_insight": "archive",
            "archived_as_insight": "archive",
            "convert_risk": "convert_to_risk",
            "convert_negative": "convert_to_negative",
            "convert_to_risk_constraint": "convert_to_risk",
        }
        action = aliases.get(action, action)
        if not action:
            raise ctx.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "StrategySpecSeed review action is required",
                "Set action to accept, reject, request-evidence, convert-to-spec-seed, convert-to-risk, convert-to-negative, or archive.",
                precondition_failed="action",
            )
        if action == "merge":
            raise ctx.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Use the merge endpoint for StrategySpecSeed merge actions",
                "POST /bff/management/strategy-seeds/{seed_id}/merge handles merge review decisions.",
                precondition_failed="action",
            )
        return action

    def _strategy_seed_target_refs(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
        raw = payload.get("target_refs") or payload.get("targetRefs") or []
        refs: List[Dict[str, Any]] = []
        if isinstance(raw, dict):
            raw = [raw]
        if isinstance(raw, list):
            refs.extend(dict(item) for item in raw if isinstance(item, dict))
        for key, ref_type in (
            ("strategy_spec_id", "strategy_spec"),
            ("strategySpecId", "strategy_spec"),
            ("target_strategy_id", "strategy_spec"),
            ("targetStrategyId", "strategy_spec"),
        ):
            value = str(payload.get(key) or "").strip()
            if value:
                refs.append({"type": ref_type, "id": value})
        return refs

    def _strategy_seed_review_result(
        *,
        updated_seed: Any,
        decision: SeedReviewDecision,
        snapshot_at: str,
        resolved_key: str,
        replayed: bool = False,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "data": {
                "seed_id": updated_seed.seed_id,
                "status": _strategy_seed_status_value(updated_seed),
                "review_status": _strategy_seed_status_value(updated_seed),
                "decision": decision.to_dict(),
                "seed": _strategy_seed_card(
                    updated_seed,
                    snapshot_at=snapshot_at,
                    include_audit=True,
                    tenant_id=tenant_id,
                ),
                "registry_write_performed": False,
                "execution_route": "none",
            },
            "meta": {
                "snapshot_at": snapshot_at,
                "research_only": True,
                "execution_route": "none",
                "idempotency": {
                    "idempotencyKey": resolved_key,
                    "replayed": replayed,
                },
            },
        }

    def _strategy_seed_review_response(
        *,
        seed_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        resolved_key: str,
    ) -> Dict[str, Any]:
        action = _strategy_seed_review_action(payload)
        request_hash = ctx.stable_json_hash(
            {
                "route": "POST /bff/management/strategy-seeds/{seed_id}/review",
                "seed_id": seed_id,
                "action": action,
                "payload": payload,
            }
        )
        cached = _strategy_seed_review_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        snapshot_at = ctx.utc_now()
        try:
            updated, decision = StrategySpecSeedStore().record_review_decision(
                seed_id,
                decision=action,
                reviewer_id=identity.operator_id,
                reason=str(payload.get("reason") or ""),
                target_refs=_strategy_seed_target_refs(payload),
                created_at=payload.get("created_at") or snapshot_at,
                idempotency_key=resolved_key,
                request_hash=request_hash,
            )
        except (StrategySpecSeedReviewError, StrategySpecSeedStoreError) as exc:
            raise _strategy_seed_review_error(exc) from exc
        result = _strategy_seed_review_result(
            updated_seed=updated,
            decision=decision,
            snapshot_at=snapshot_at,
            resolved_key=resolved_key,
            replayed=bool(getattr(decision, "idempotent_replay", False)),
            tenant_id=ctx.bff_tenant_id(identity),
        )
        ctx.strategy_seed_review_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    def _strategy_seed_merge_response(
        *,
        seed_id: str,
        payload: Dict[str, Any],
        identity: OperatorIdentity,
        resolved_key: str,
    ) -> Dict[str, Any]:
        target_seed_id = str(
            payload.get("target_seed_id")
            or payload.get("targetSeedId")
            or payload.get("target_id")
            or payload.get("targetId")
            or ""
        ).strip()
        if not target_seed_id:
            raise ctx.bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "StrategySpecSeed merge target is required",
                "Set target_seed_id to the StrategySpecSeed that will absorb this candidate.",
                precondition_failed="target_seed_id",
            )
        request_hash = ctx.stable_json_hash(
            {
                "route": "POST /bff/management/strategy-seeds/{seed_id}/merge",
                "seed_id": seed_id,
                "payload": payload,
            }
        )
        cached = _strategy_seed_review_idempotency_check(resolved_key, request_hash)
        if cached is not None:
            return cached
        snapshot_at = ctx.utc_now()
        try:
            updated, decision = StrategySpecSeedStore().merge_seed(
                seed_id,
                target_seed_id=target_seed_id,
                reviewer_id=identity.operator_id,
                reason=str(payload.get("reason") or ""),
                target_refs=_strategy_seed_target_refs(payload),
                created_at=payload.get("created_at") or snapshot_at,
                idempotency_key=resolved_key,
                request_hash=request_hash,
            )
        except (StrategySpecSeedReviewError, StrategySpecSeedStoreError) as exc:
            raise _strategy_seed_review_error(exc) from exc
        result = _strategy_seed_review_result(
            updated_seed=updated,
            decision=decision,
            snapshot_at=snapshot_at,
            resolved_key=resolved_key,
            replayed=bool(getattr(decision, "idempotent_replay", False)),
            tenant_id=ctx.bff_tenant_id(identity),
        )
        ctx.strategy_seed_review_idempotency[resolved_key] = {
            "request_hash": request_hash,
            "result": result,
        }
        return result

    @router.get("/bff/management/strategy-seeds")
    async def bff_list_strategy_seed_inbox(
        status: Optional[str] = None,
        source_kind: Optional[str] = None,
        strategy_family: Optional[str] = None,
        seed_kind: Optional[str] = None,
        min_confidence: Optional[float] = Query(default=None, ge=0.0, le=1.0),
        page_token: Optional[str] = None,
        page_size: int = Query(default=50, ge=1, le=200),
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: governed StrategySpecSeed review inbox read model."""
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        return _strategy_seed_list_response(
            status=status,
            source_kind=source_kind,
            strategy_family=strategy_family,
            seed_kind=seed_kind,
            min_confidence=min_confidence,
            page_token=page_token,
            page_size=page_size,
            tenant_id=ctx.bff_tenant_id(identity),
        )

    @router.get("/bff/management/strategy-seeds/{seed_id}")
    async def bff_get_strategy_seed_card(
        seed_id: str,
        authorization: Optional[str] = Header(default=None),
    ):
        """BFF: governed StrategySpecSeed review card."""
        identity = ctx.extract_identity(authorization)
        ctx.require_read_role(identity)
        return _strategy_seed_detail_response(
            seed_id,
            tenant_id=ctx.bff_tenant_id(identity),
        )

    @router.post("/bff/management/strategy-seeds/{seed_id}/review", status_code=202)
    async def bff_review_strategy_seed(
        seed_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: apply a governed StrategySpecSeed review decision."""
        identity = ctx.extract_identity(authorization)
        _require_strategy_seed_review_role(identity)
        ctx.reject_body_idempotency_key(payload)
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        return _strategy_seed_review_response(
            seed_id=seed_id,
            payload=payload,
            identity=identity,
            resolved_key=resolved_key,
        )

    @router.post("/bff/management/strategy-seeds/{seed_id}/merge", status_code=202)
    async def bff_merge_strategy_seed(
        seed_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: merge a StrategySpecSeed candidate into another seed candidate."""
        identity = ctx.extract_identity(authorization)
        _require_strategy_seed_review_role(identity)
        ctx.reject_body_idempotency_key(payload)
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        return _strategy_seed_merge_response(
            seed_id=seed_id,
            payload=payload,
            identity=identity,
            resolved_key=resolved_key,
        )

    @router.post("/bff/management/strategy-seeds/{seed_id}/submit-replication", status_code=202)
    async def bff_submit_strategy_seed_replication(
        seed_id: str,
        payload: Dict[str, Any] = Body(default_factory=dict),
        authorization: Optional[str] = Header(default=None),
        idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
        x_idempotency_key: Optional[str] = Header(default=None, alias="X-Idempotency-Key"),
    ):
        """BFF: submit a promoted StrategySpecSeed to research replication."""
        identity = ctx.extract_identity(authorization)
        _require_strategy_seed_submit_role(identity)
        ctx.reject_body_idempotency_key(payload)
        resolved_key = ctx.resolve_final_idempotency_key(idempotency_key, x_idempotency_key)
        return _strategy_seed_replication_response(
            seed_id=seed_id,
            payload=payload,
            identity=identity,
            resolved_key=resolved_key,
        )

    return router

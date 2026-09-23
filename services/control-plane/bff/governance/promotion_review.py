"""Promotion-Review domain projection and revision helpers.

Encapsulates review ID generation, revision tracking, stage path resolution,
and decision projections decoupled from main.py globals.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Optional

try:
    from ..auth.policy import bff_error as _bff_error
    from ..models import ErrorCode
except (ImportError, ValueError):
    from auth.policy import bff_error as _bff_error
    from models import ErrorCode


_PROMOTION_REVIEW_ID_PREFIX = "promotion-review:"
_PROMOTION_REVIEW_TARGET_PREFIX = "promotion_review:"
_PROMOTION_REVIEW_REVISION_MARKER = "--snapshot-"
_PROMOTION_REVIEW_REVISION_RE = re.compile(
    r"^(?P<recommendation_id>.+)--snapshot-(?P<digest>[0-9a-f]{32})$"
)
_PROMOTION_REVIEW_ID_QUARTER_RE = re.compile(r"pm12-(?P<quarter>\d{4}-q[1-4])-", re.IGNORECASE)
_PROMOTION_REVIEW_PROMOTION_ACTION_IDS = frozenset({"promote_to_canary_candidate"})
_PROMOTION_REVIEW_DECISIONS = frozenset({
    "approve",
    "approve_with_conditions",
    "reject",
})


def _promotion_review_clean_id(review_id: Any) -> str:
    clean_id = str(review_id or "").strip()
    if clean_id.startswith(_PROMOTION_REVIEW_ID_PREFIX):
        clean_id = clean_id[len(_PROMOTION_REVIEW_ID_PREFIX):]
    if clean_id.startswith(_PROMOTION_REVIEW_TARGET_PREFIX):
        clean_id = clean_id[len(_PROMOTION_REVIEW_TARGET_PREFIX):]
    return clean_id


def _promotion_review_target_id(review_id: Any) -> str:
    return f"{_PROMOTION_REVIEW_TARGET_PREFIX}{_promotion_review_clean_id(review_id)}"


def _promotion_review_revision_id(
    recommendation_id: Any,
    ranking_snapshot_id: Any,
) -> str:
    clean_recommendation_id = _promotion_review_clean_id(recommendation_id)
    clean_snapshot_id = str(ranking_snapshot_id or "").strip()
    if not clean_recommendation_id or not clean_snapshot_id:
        return clean_recommendation_id
    digest = hashlib.sha256(
        f"{clean_recommendation_id}\x00{clean_snapshot_id}".encode("utf-8")
    ).hexdigest()[:32]
    return (
        f"{clean_recommendation_id}"
        f"{_PROMOTION_REVIEW_REVISION_MARKER}{digest}"
    )


def _promotion_review_revision_recommendation_id(review_id: Any) -> str:
    clean_id = _promotion_review_clean_id(review_id)
    match = _PROMOTION_REVIEW_REVISION_RE.fullmatch(clean_id)
    if match is None:
        return clean_id
    return match.group("recommendation_id")


def _promotion_review_record_revision_id(command: Dict[str, Any]) -> str:
    from .human_inbox import _human_inbox_promotion_recommendation_id

    params = command.get("params") if isinstance(command.get("params"), dict) else {}
    recommendation_id = _human_inbox_promotion_recommendation_id(command)
    ranking_snapshot_id = str(params.get("ranking_snapshot_id") or "").strip()
    expected_revision_id = _promotion_review_revision_id(
        recommendation_id,
        ranking_snapshot_id,
    )
    asserted_ids = [
        str(params.get(key) or "").strip()
        for key in ("review_id", "promotion_review_id")
        if str(params.get(key) or "").strip()
    ]
    if ranking_snapshot_id:
        if asserted_ids and any(
            _promotion_review_clean_id(asserted_id) != expected_revision_id
            for asserted_id in asserted_ids
        ):
            return ""
        return expected_revision_id
    # Snapshotless legacy records predate revision identities. They remain
    # readable under the stable recommendation id but cannot authorize a
    # snapshot-bound decision or allocation.
    if asserted_ids and any(
        _promotion_review_clean_id(asserted_id) != recommendation_id
        for asserted_id in asserted_ids
    ):
        return ""
    return recommendation_id


def _promotion_review_quarter_from_id(review_id: Any) -> Optional[str]:
    match = _PROMOTION_REVIEW_ID_QUARTER_RE.search(_promotion_review_clean_id(review_id))
    if match is None:
        return None
    return match.group("quarter").upper()


def _promotion_review_stage_path(recommendation: Dict[str, Any]) -> Dict[str, Any]:
    action_id = str(recommendation.get("action_id") or "").strip()
    stage = str(
        recommendation.get("stage") or recommendation.get("state") or ""
    ).strip().lower()
    if "canary" in stage:
        from_stage = "canary"
    elif "live" in stage:
        from_stage = "live"
    else:
        from_stage = "paper"

    if action_id in _PROMOTION_REVIEW_PROMOTION_ACTION_IDS:
        if from_stage == "canary":
            target_stage = "live_candidate"
            review_kind = "canary_to_live_review"
        elif from_stage == "live":
            target_stage = "live_rebalance_review"
            review_kind = "live_ranking_review"
        else:
            target_stage = "canary_candidate"
            review_kind = "paper_to_canary_review"
    elif action_id in {"reduce_capital_access", "freeze_persona", "suspend_persona", "retire_persona"}:
        target_stage = "risk_containment_review"
        review_kind = "risk_containment_review"
    elif action_id in {"increase_research_budget", "grant_tool_access"}:
        target_stage = "resource_change_review"
        review_kind = "resource_change_review"
    else:
        target_stage = "governance_review"
        review_kind = "ranking_governance_review"

    return {
        "from_stage": from_stage,
        "target_stage": target_stage,
        "review_kind": review_kind,
        "eventual_live_stage": "live",
        "live_requires_separate_human_gate": target_stage != "risk_containment_review",
    }


def _promotion_review_submission_projection(
    review_id: Any,
    *,
    include_source_recommendation: bool = False,
    command_store: Any = None,
) -> Optional[Dict[str, Any]]:
    resolved_store = command_store
    if resolved_store is None:
        try:
            from ..main import command_store as _main_command_store
            resolved_store = _main_command_store
        except Exception:
            pass
    from ..personas.service import (
        _promotion_review_submission_projection as _personas_promotion_review_submission_projection,
    )
    return _personas_promotion_review_submission_projection(
        review_id,
        include_source_recommendation=include_source_recommendation,
        command_store=resolved_store,
    )


def _latest_promotion_review_command(
    review_id: Any,
    command_store: Any = None,
) -> Optional[Dict[str, Any]]:
    from .human_inbox import (
        _human_inbox_decision_projection_from_record,
        _human_inbox_decision_recommendation_id,
    )
    resolved_store = command_store
    if resolved_store is None:
        try:
            from ..main import command_store as _main_command_store
            resolved_store = _main_command_store
        except Exception:
            pass
    if resolved_store is None:
        return None
    clean_id = _promotion_review_clean_id(review_id)
    commands = getattr(resolved_store, "_get_all_commands", None)
    if callable(commands):
        record_list = commands()
    else:
        record_list = []
    for record in reversed(record_list):
        if (
            _human_inbox_decision_recommendation_id(record) == clean_id
            and _human_inbox_decision_projection_from_record(record) is not None
        ):
            return record
    return None


def _promotion_review_decision_projection(
    review_id: Any,
    command_store: Any = None,
) -> Optional[Dict[str, Any]]:
    from .human_inbox import _human_inbox_decision_projection_from_record

    record = _latest_promotion_review_command(review_id, command_store=command_store)
    if record is None:
        return None
    return _human_inbox_decision_projection_from_record(record)


def _raise_if_promotion_review_direct_mutation_requested(payload: Dict[str, Any]) -> None:
    mutation_fields = (
        "live_capital_mutation",
        "liveCapitalMutation",
        "liveCapitalSideEffects",
        "runtime_mutation",
        "runtimeMutation",
    )
    for field in mutation_fields:
        if bool(payload.get(field)):
            raise _bff_error(
                422,
                ErrorCode.VALIDATION_FAILED,
                "Promotion review decisions cannot request direct live/runtime mutation",
                f"{field} must be false or omitted; promotion requires a human-gated command receipt only.",
                precondition_failed=field,
                suggestion="Submit the promotion review decision without live/runtime mutation flags.",
            )


def _promotion_review_stored_source(
    recommendation: Dict[str, Any],
) -> Dict[str, Any]:
    stored = json.loads(json.dumps(recommendation))
    # Command params are visible on governance read surfaces. Persist the
    # authoritative immutable ranking tuple, never submitter-supplied evidence.
    stored["evidence_refs"] = []
    stored["evidence_ref_ids"] = []
    return stored

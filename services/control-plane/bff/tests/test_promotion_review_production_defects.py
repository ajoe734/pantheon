"""Focused regression coverage for BFF-PROMOTION-REVIEW-DEFECT-REPAIR-001.

Each test exercises a live imported production instance directly
(``ManagementService``, ``CommandAdapterService``, ``CommandStore``) --
never ``services.control_plane.bff.main`` -- so this suite stays outside
the composition root and needs no ``composition_allowlist`` entry.

Covers the four production defects that
``test_bff_promotion_review_governance.py`` caught once its class-level
``ManagementService``/``CommandStore`` monkeypatches were removed:

1. A persona-readiness ``readiness_blocker`` Human Inbox item must carry
   ``blocking_reasons``/``research_context`` from its source persona row
   (``management_read_models/service.py::ManagementService.get_human_inbox``).
2. The ``approval_queue`` Human Inbox surface must report the read
   store's real ``dataset_source("approval_queue_items")`` (degrading to
   ``local_snapshot``/``degraded``) instead of a hardcoded ``read_store``.
3. A command record built by
   ``command_adapters/service.py::CommandAdapterService.sem_command_response``
   must mirror ``params.live_capital_mutation`` into
   ``audit.live_capital_side_effects``.
4. ``command_queue.py::CommandStore`` must lazily initialize its
   per-instance ``_cache`` to ``None`` and keep it in sync with reads/writes.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.control_plane.bff.command_adapters.service import CommandAdapterService
from services.control_plane.bff.command_queue import CommandStore
from services.control_plane.bff.management_read_models.service import ManagementService
from services.control_plane.bff.models import (
    CommandType,
    ObjectType,
    OperatorIdentity,
    TargetObject,
    utc_now,
)


class _PersonaReadinessStore:
    """Minimal read-store stub exposing only what the persona_readiness
    Human Inbox contributor reads (``list_personas``/``list_persona_league``)."""

    def list_personas(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return [
            {
                "persona_id": "persona-defect-one",
                "name": "Defect One Persona",
                "lifecycle_state": "active",
                "updated_at": "2026-07-13T12:00:00Z",
                "metadata": {
                    "persona_status": "needs_human_approval",
                    "current_work": "Review the bounded readiness packet",
                    "research_status": {
                        "summary": "Research admission awaits review.",
                        "pending_task_ids": ["TASK-DEFECT-ONE"],
                        "can_deploy": False,
                    },
                    "current_research_projects": [{"project_id": "research-defect-one"}],
                    "data_source_status": {"state": "read_ok"},
                },
            },
        ]

    def list_persona_league(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return [
            {
                "persona_id": "persona-defect-one",
                "governance_required": True,
                "recommendation": "hold_for_risk_owner_review",
                "status": "needs_human_approval",
            },
        ]


def test_human_inbox_persona_readiness_item_carries_blocking_reasons_and_research_context() -> None:
    """Defect one (originally caught at L1191): a readiness_blocker item's
    blocking_reasons/research_context must be populated from the source
    persona row, not omitted."""
    svc = ManagementService(read_store=_PersonaReadinessStore())

    result = svc.get_human_inbox(source_type="readiness_blocker")

    items = result["data"]["items"]
    assert [item["persona_id"] for item in items] == ["persona-defect-one"]
    item = items[0]
    assert "TASK-DEFECT-ONE" in " ".join(item["blocking_reasons"])
    assert item["research_context"]["current_research_projects"] == [
        {"project_id": "research-defect-one"}
    ]


class _ApprovalQueueLocalSnapshotStore:
    """Minimal read-store stub whose approval_queue records are sourced
    from a local BFF snapshot fallback rather than a backend read store."""

    def list_approval_queue_items(self, **_kwargs: Any) -> List[Dict[str, Any]]:
        return [
            {
                "decision_id": "approval-defect-two",
                "decision_type": "DeploymentPlan",
                "decision_state": "pending",
                "risk_level": "high",
                "submitted_at": "2026-07-13T00:00:00Z",
            },
        ]

    def dataset_source(self, dataset: str) -> str:
        return "local_snapshot" if dataset == "approval_queue_items" else "read_store"


def test_human_inbox_approval_queue_reports_local_snapshot_source() -> None:
    """Defect two (originally caught at L1423): the approval_queue surface
    must report the store's real dataset_source, degrading to
    local_snapshot/degraded, instead of hardcoding read_store."""
    svc = ManagementService(read_store=_ApprovalQueueLocalSnapshotStore())

    result = svc.get_human_inbox(source_type="approval")

    surface = result["meta"]["surfaces"]["approval_queue"]
    assert surface["source"] == "local_snapshot"
    assert surface["status"] == "degraded"


def _dummy_bff_error(
    status_code: int,
    code: Any,
    message: str,
    detail: str = "",
    **_kwargs: Any,
) -> Exception:
    return Exception(f"{status_code} {code}: {message} ({detail})")


def test_sem_command_response_mirrors_live_capital_mutation_into_audit(tmp_path: Any) -> None:
    """Defect three (originally caught at L1657): a command record whose
    params assert params.live_capital_mutation == False must also carry
    audit.live_capital_side_effects == False."""
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    svc = CommandAdapterService(
        command_store=store,
        bff_error=_dummy_bff_error,
        utc_now_fn=utc_now,
    )
    identity = OperatorIdentity(operator_id="op-defect-three", roles=["approver"])

    response = svc.sem_command_response(
        command_type=CommandType.HUMAN_GATE_APPROVE,
        target_type=ObjectType.HUMAN_GATE_ITEM,
        target_id="promotion-review:defect-three",
        payload={"decision": "approve", "live_capital_mutation": False},
        identity=identity,
        idempotency_key="idem-defect-three",
    )

    assert response.status_code == 202
    records = store._get_all_commands()
    assert len(records) == 1
    assert records[0]["params"]["live_capital_mutation"] is False
    assert records[0]["audit"]["live_capital_side_effects"] is False


def test_command_store_cache_is_lazily_initialized_and_kept_in_sync(tmp_path: Any) -> None:
    """Defect four (originally caught at L1776): a freshly constructed
    CommandStore lazily initializes _cache to None (not an eager empty
    list), and submit_command keeps that cache in sync without a second
    disk read."""
    store = CommandStore(str(tmp_path / "commands.jsonl"))
    assert store._cache is None

    assert store._get_all_commands() == []
    assert store._cache == []

    target = TargetObject(type=ObjectType.RANKING, id="rec-defect-four")
    store.submit_command(
        command_id="cmd-defect-four",
        command_type=CommandType.QUARTERLY_RANKING_RECOMMENDATION_SUBMIT,
        target=target,
        submitted_at=utc_now(),
        params={},
        audit_context={},
    )

    assert len(store._cache) == 1
    assert store._cache[0]["command_id"] == "cmd-defect-four"

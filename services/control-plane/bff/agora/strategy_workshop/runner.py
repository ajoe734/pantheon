"""Durable worker wiring Workshop conversation to the reconstruction engine.

Per AGORA-WORKSHOP-CORE-20260813 the reconstruction algorithm in
``reconstruction.py`` is unchanged.  This module is the one worker path that
every caller (message admission and the explicit ``/reconstruct`` endpoint)
goes through: it derives the current conversation/version identity, invokes
the existing engine, and persists exactly one effective result per workshop
as a durable, upsert-safe workshop card so replay, staleness, crash, and
restart all converge on the same effective result and Next-Best-Question.

Concurrency model: this worker executes synchronously inside the caller's
request, the same pattern ``interaction/runner.py`` uses for persona
invocations.  There is no cross-process lease; durability comes from the
workshop card being written to the durable store (Postgres in production)
before the caller's response is returned, and idempotent convergence comes
from the deterministic engine plus the create-then-readback Registry draft
call being safe to repeat.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from .operations import CanonicalOperationError, WorkshopCanonicalOperations
from .reconstruction import StrategyReconstructionResult, reconstruct_strategy_from_events

RECONSTRUCTION_CARD_TYPE = "strategy_reconstruction"


def reconstruction_card_id(workshop_id: str) -> str:
    """One deterministic card id per workshop: the durable "one effective result" slot."""
    return "card_reconstruction_" + hashlib.sha256(workshop_id.encode("utf-8")).hexdigest()[:20]


def _messages_from_events(events: List[Dict[str, Any]]) -> List[str]:
    messages: List[str] = []
    for event in events:
        if event.get("event_type") == "message":
            msg = event.get("redacted_summary") or event.get("content") or ""
            if msg:
                messages.append(str(msg))
    return messages


def _registry_draft_payload(
    *,
    base_entry: Dict[str, Any],
    base_registry_id: str,
    strategy_id: str,
    workshop_id: str,
    sequence_no: int,
    tenant_id: str,
    user_id: str,
    result: StrategyReconstructionResult,
) -> Optional[Dict[str, Any]]:
    version_parts = str(base_entry.get("version") or "").split(".")
    if len(version_parts) != 3 or not all(part.isdigit() for part in version_parts):
        return None
    base_doc = dict((base_entry.get("metadata") or {}).get("strategy_spec") or {})
    if not base_doc:
        return None
    version_parts[2] = str(int(version_parts[2]) + 1)
    next_version = ".".join(version_parts)
    digest = hashlib.sha256(f"{workshop_id}:{sequence_no}".encode("utf-8")).hexdigest()[:20]
    return {
        "registry_id": f"reg-ws-recon-{digest}",
        "strategy_id": strategy_id,
        "version": next_version,
        "artifact_state": "draft",
        "lineage": {"parent_registry_ids": [base_registry_id]},
        "metadata": {
            "tenant_id": tenant_id,
            "owner_user_id": user_id,
            "workshop_id": workshop_id,
            "source": "reconstruction_worker",
            "reconstruction_id": result.reconstruction_id,
            "based_on_sequence_no": sequence_no,
            "completeness_grade": result.completeness.grade,
        },
        # The reconstruction engine assesses completeness; it does not
        # synthesize a new StrategySpec document (that would be a second
        # reconstruction model, explicitly out of scope).  The draft carries
        # the base document forward unmodified, annotated with reconstruction
        # lineage, so a human or a later governed patch can act on it.
        "strategy_spec": base_doc,
    }


def _maybe_create_registry_draft(
    *,
    canonical: WorkshopCanonicalOperations,
    session: Dict[str, Any],
    workshop_id: str,
    sequence_no: int,
    tenant_id: str,
    user_id: str,
    result: StrategyReconstructionResult,
) -> Optional[Dict[str, Any]]:
    """Create-or-adopt a canonical Registry draft from the reconstruction result.

    Best-effort: this never blocks persistence of the reconstruction result
    itself.  No active StrategySpec to draft from, or a downstream Registry
    that is unavailable/unconfigured, both degrade to "no draft this round"
    rather than failing the worker.
    """
    base_registry_id = str(session.get("active_strategy_spec_registry_id") or "")
    strategy_id = str(session.get("strategy_id") or "")
    if not base_registry_id or not strategy_id:
        return None
    if result.completeness.grade == "insufficient":
        return None
    try:
        base_readback = canonical.get_strategy_spec(base_registry_id)
    except CanonicalOperationError:
        return None
    base_entry = dict(base_readback.get("entry") or {})
    payload = _registry_draft_payload(
        base_entry=base_entry,
        base_registry_id=base_registry_id,
        strategy_id=strategy_id,
        workshop_id=workshop_id,
        sequence_no=sequence_no,
        tenant_id=tenant_id,
        user_id=user_id,
        result=result,
    )
    if payload is None:
        return None
    try:
        readback = canonical.create_strategy_spec(payload)
    except CanonicalOperationError:
        return None
    entry = readback.get("entry") or {}
    return {
        "registry_id": payload["registry_id"],
        "strategy_id": str(entry.get("strategy_id") or strategy_id),
        "version": payload["version"],
        "artifact_state": "draft",
    }


def run_reconstruction_worker(
    *,
    store: Any,
    canonical: WorkshopCanonicalOperations,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
    session: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Admit-or-resume the one reconstruction job for this workshop's conversation.

    Returns ``{"result": <StrategyReconstructionResult dict>,
    "registry_draft_ref": <dict|None>, "job_status": "replayed"|"completed",
    "card": <persisted workshop card>}``.
    """
    session = session if session is not None else store.get_session(workshop_id)
    if session is None:
        raise ValueError(f"workshop not found: {workshop_id}")

    events = store.list_events(workshop_id)
    sequence_no = max((int(event.get("sequence_no", 0)) for event in events), default=0)
    card_id = reconstruction_card_id(workshop_id)
    existing = next(
        (card for card in store.list_workshop_cards(workshop_id) if card.get("card_id") == card_id),
        None,
    )
    existing_payload = dict((existing or {}).get("payload") or {})
    if (
        existing is not None
        and existing.get("status") == "completed"
        and int(existing_payload.get("based_on_sequence_no", -1)) == sequence_no
    ):
        # Replay: the conversation has not advanced since the last effective
        # result, so the durable card is returned unchanged.  No re-invoking
        # the engine, no repeat Registry draft call.
        return {
            "result": existing_payload.get("reconstruction"),
            "registry_draft_ref": existing_payload.get("registry_draft_ref"),
            "job_status": "replayed",
            "card": existing,
        }

    messages_content = _messages_from_events(events)
    store.record_workshop_card({
        "card_id": card_id,
        "card_type": RECONSTRUCTION_CARD_TYPE,
        "workshop_id": workshop_id,
        "status": "running",
        "title": "Strategy reconstruction in progress",
        "summary": f"Reconstructing from {len(messages_content)} message(s) up to sequence {sequence_no}.",
        "payload": {"based_on_sequence_no": sequence_no, "job_status": "running"},
        "evidence_refs": [],
        "allowed_actions": {},
    })

    result = reconstruct_strategy_from_events(
        workshop_id=workshop_id,
        sequence_no=sequence_no,
        events=events,
        messages_content=messages_content,
    )
    registry_draft_ref = _maybe_create_registry_draft(
        canonical=canonical,
        session=session,
        workshop_id=workshop_id,
        sequence_no=sequence_no,
        tenant_id=tenant_id,
        user_id=user_id,
        result=result,
    )
    result_dict = result.model_dump(mode="json")
    completed_payload = {
        "reconstruction": result_dict,
        "based_on_sequence_no": sequence_no,
        "registry_draft_ref": registry_draft_ref,
        "job_status": "completed",
    }
    completed_card = store.record_workshop_card({
        "card_id": card_id,
        "card_type": RECONSTRUCTION_CARD_TYPE,
        "workshop_id": workshop_id,
        "status": "completed",
        "title": f"Strategy reconstruction: {result.completeness.grade}",
        "summary": (
            result.next_best_question.text
            if result.next_best_question else "Reconstruction complete."
        ),
        "payload": completed_payload,
        "evidence_refs": result.evidence_refs,
        "allowed_actions": {},
    })
    return {
        "result": result_dict,
        "registry_draft_ref": registry_draft_ref,
        "job_status": "completed",
        "card": completed_card,
    }


__all__ = ["run_reconstruction_worker", "reconstruction_card_id", "RECONSTRUCTION_CARD_TYPE"]

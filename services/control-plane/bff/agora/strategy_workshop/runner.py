"""Durable worker wiring Workshop conversation to the reconstruction engine.

Semantic interpretation belongs to the restricted agent; typed validation
belongs to ``reconstruction.py``. This module is the one worker path that
every caller (message admission and the explicit ``/reconstruct`` endpoint)
goes through: it derives the current conversation/version identity, invokes
the existing engine, and persists exactly one effective result per workshop
as a durable, upsert-safe workshop card so replay, staleness, crash, and
restart all converge on the same effective result and Next-Best-Question.

Job model (AGORA-WORKSHOP-RECONSTRUCTION-HOSTED-20261127): message admission
only queues a job (``admit_reconstruction_job``) and the route runs it after
the response (``run_reconstruction_job``). The job record is the workshop's
one reconstruction card; ``payload.job_id`` owns it and every transition is a
compare-and-set on that token, so there is one job (one provider call) per
message sequence_no across the message route, ``/reconstruct`` and replicas.
``/reconstruct`` waits on an in-flight job by reading the card. A job whose
replica died is taken over after a lease. Provider calls are data-only and
cannot execute financial actions.
"""
from __future__ import annotations

import hashlib
import os
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from .operations import CanonicalOperationError, WorkshopCanonicalOperations
from .reconstruction import StrategyReconstructionResult
from .semantic_provider import (
    ENGINE_VERSION, REASON_PRIVATE_CONTENT_UNAVAILABLE, REASON_PROVIDER_ERROR, RETRYABLE_REASONS,
    ReconstructionProviderError, reconstruct_strategy_with_agent,
)

RECONSTRUCTION_CARD_TYPE = "strategy_reconstruction"


def reconstruction_card_id(workshop_id: str) -> str:
    """One deterministic card id per workshop: the durable "one effective result" slot."""
    return "card_reconstruction_" + hashlib.sha256(workshop_id.encode("utf-8")).hexdigest()[:20]


def _messages_from_events(
    events: List[Dict[str, Any]], *, private_content_store: Any,
    tenant_id: str, user_id: str, workshop_id: str,
) -> List[str]:
    messages: List[str] = []
    for event in events:
        if event.get("event_type") == "message":
            private_ref = event.get("private_content_ref")
            if private_ref:
                if private_content_store is None:
                    # Store-only CLIs do not load the product privacy package.
                    from services.control_plane.privacy.private_content_models import PrivateContentStoreUnavailable

                    raise PrivateContentStoreUnavailable("Workshop message content is unavailable.")
                msg = private_content_store.get_for_owner(
                    private_content_ref=private_ref, tenant_id=tenant_id,
                    owner_user_id=user_id, purpose="workshop_reconstruction",
                    request_id=f"{workshop_id}:{event.get('event_id', event.get('sequence_no'))}",
                ).decode("utf-8")
            else:
                # Legacy non-private events only. A private pointer must never
                # fall back to the fixed redaction placeholder as user input.
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
    draft_doc = dict((result.draft_proposal or {}).get("strategy_spec") or {})
    if not draft_doc or draft_doc.get("strategy_id") != strategy_id:
        return None
    version_parts[2] = str(int(version_parts[2]) + 1)
    next_version = ".".join(version_parts)
    digest = hashlib.sha256(f"{ENGINE_VERSION}:{tenant_id}:{workshop_id}:{sequence_no}".encode("utf-8")).hexdigest()[:20]
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
        # Persist the validated semantic proposal, never the unchanged base.
        "strategy_spec": draft_doc,
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
    stored_doc = (entry.get("metadata") or {}).get("strategy_spec") or entry.get("strategy_spec")
    if (entry.get("registry_id") != payload["registry_id"]
            or entry.get("artifact_state") != "draft"
            or entry.get("version") != payload["version"]
            or stored_doc != payload["strategy_spec"]):
        # At-least-once agent calls can propose different content after a
        # crash/race. Never attach an older same-ID draft as proof of this one.
        return None
    return {
        "registry_id": payload["registry_id"],
        "strategy_id": str(entry.get("strategy_id") or strategy_id),
        "version": payload["version"],
        "artifact_state": "draft",
    }


def _reconstruction_job_lease_seconds() -> float:
    """A queued/running job older than this is treated as abandoned (crashed
    BFF replica) and may be taken over. Well above the provider timeout."""
    raw = os.getenv("PANTHEON_WORKSHOP_RECONSTRUCTION_JOB_LEASE_SECONDS", "").strip()
    try:
        return max(float(raw), 30.0) if raw else 180.0
    except ValueError:
        return 180.0


def reconstruction_wait_seconds() -> float:
    """How long ``POST /reconstruct`` waits on an in-flight job (no provider call)."""
    raw = os.getenv("PANTHEON_WORKSHOP_RECONSTRUCTION_WAIT_SECONDS", "").strip()
    try:
        return min(max(float(raw), 0.0), 300.0) if raw else 90.0
    except ValueError:
        return 90.0


def _message_sequence_no(events: List[Dict[str, Any]]) -> int:
    """The job identity: the last conversation message, not any later event.

    Reconstruction reads messages only, so persona opinions or other
    non-message events must not start a second provider call."""
    return max(
        (int(event.get("sequence_no", 0)) for event in events if event.get("event_type") == "message"),
        default=0,
    )


def _current_card(store: Any, workshop_id: str) -> Optional[Dict[str, Any]]:
    card_id = reconstruction_card_id(workshop_id)
    return next(
        (card for card in store.list_workshop_cards(workshop_id) if card.get("card_id") == card_id),
        None,
    )


def _write_job_card(store: Any, card: Dict[str, Any], *, expected_job_id: Optional[str]) -> Optional[Dict[str, Any]]:
    writer = getattr(store, "compare_and_set_workshop_card", None)
    if writer is None:  # minimal test fakes without the conditional write
        return store.record_workshop_card(card)
    return writer(card, expected_job_id=expected_job_id)


def _card_is_for(payload: Dict[str, Any], sequence_no: int, events: List[Dict[str, Any]]) -> bool:
    return (
        payload.get("engine_version") == ENGINE_VERSION
        and int(payload.get("based_on_sequence_no", -1)) == sequence_no
        and (not any(event.get("private_content_ref") for event in events)
             or payload.get("private_content_reader_version") == 1
             or payload.get("job_status") != "completed")
    )


def _job_is_stale(payload: Dict[str, Any], now: float) -> bool:
    started = max(float(payload.get("admitted_at_epoch") or 0), float(payload.get("started_at_epoch") or 0))
    return now - started > _reconstruction_job_lease_seconds()


def _job_view(card: Optional[Dict[str, Any]], *, admitted: bool, sequence_no: int) -> Dict[str, Any]:
    payload = dict((card or {}).get("payload") or {})
    return {
        "admitted": admitted,
        "job_id": payload.get("job_id"),
        "job_status": payload.get("job_status"),
        "sequence_no": sequence_no,
        "card": card,
    }


def _scoped_session(store: Any, workshop_id: str, tenant_id: str, user_id: str,
                    session: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    session = session if session is not None else store.get_session(workshop_id)
    if session is None:
        raise ValueError(f"workshop not found: {workshop_id}")
    if session.get("tenant_id") != tenant_id or session.get("user_id") != user_id:
        raise ValueError("workshop reconstruction scope mismatch")
    return session


def admit_reconstruction_job(
    *,
    store: Any,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
    session: Optional[Dict[str, Any]] = None,
    retry_failed: bool = False,
    clock: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """Durably queue the one reconstruction job for the latest message.

    No provider call and no private-content read happen here, so message
    admission stays fast. The job record is the workshop's reconstruction
    card: ``payload.job_id`` is the owner token and every transition is a
    compare-and-set on it, so concurrent callers (message route, an explicit
    ``/reconstruct``, another replica) converge on one job per sequence_no.

    Returns ``{"admitted": bool, "job_id", "job_status", "sequence_no", "card"}``.
    ``admitted`` is True only for the caller that must run the job.
    """
    _scoped_session(store, workshop_id, tenant_id, user_id, session)
    events = store.list_events(workshop_id)
    sequence_no = _message_sequence_no(events)
    card: Optional[Dict[str, Any]] = None
    for _attempt in range(3):
        card = _current_card(store, workshop_id)
        payload = dict((card or {}).get("payload") or {})
        if card is not None and _card_is_for(payload, sequence_no, events):
            status = payload.get("job_status")
            if status == "completed":
                return _job_view(card, admitted=False, sequence_no=sequence_no)
            if status == "failed" and not (
                retry_failed and payload.get("failure_reason") in RETRYABLE_REASONS
            ):
                return _job_view(card, admitted=False, sequence_no=sequence_no)
            if status in {"queued", "running"} and not _job_is_stale(payload, clock()):
                return _job_view(card, admitted=False, sequence_no=sequence_no)
        same_round = card is not None and int(payload.get("based_on_sequence_no", -1)) == sequence_no
        job_id = "rjob_" + uuid.uuid4().hex
        written = _write_job_card(store, {
            "card_id": reconstruction_card_id(workshop_id),
            "card_type": RECONSTRUCTION_CARD_TYPE,
            "workshop_id": workshop_id,
            "status": "running",
            "title": "Strategy reconstruction queued",
            "summary": f"Queued for the conversation up to message sequence {sequence_no}.",
            "payload": {
                "job_id": job_id,
                "job_status": "queued",
                "engine_version": ENGINE_VERSION,
                "based_on_sequence_no": sequence_no,
                "admitted_at_epoch": clock(),
                "attempt": (int(payload.get("attempt") or 1) + 1) if same_round else 1,
            },
            "evidence_refs": [],
            "allowed_actions": {},
        }, expected_job_id=payload.get("job_id"))
        if written is not None:
            return _job_view(written, admitted=True, sequence_no=sequence_no)
    # Lost the slot three times: another writer owns the job; report it.
    return _job_view(_current_card(store, workshop_id) or card, admitted=False, sequence_no=sequence_no)


def _failed_card(workshop_id: str, *, job_id: str, sequence_no: int, attempt: Any,
                 reason: str, status_code: int, adapter_error_code: Optional[str]) -> Dict[str, Any]:
    return {
        "card_id": reconstruction_card_id(workshop_id), "card_type": RECONSTRUCTION_CARD_TYPE,
        "workshop_id": workshop_id, "status": "failed",
        "title": "Semantic reconstruction unavailable",
        "summary": f"Reconstruction failed: {reason}",
        "payload": {"job_id": job_id, "job_status": "failed", "engine_version": ENGINE_VERSION,
                    "based_on_sequence_no": sequence_no, "attempt": attempt,
                    "failure_reason": reason, "failure_status": status_code,
                    "adapter_error_code": adapter_error_code},
        "evidence_refs": [], "allowed_actions": {},
    }


def run_reconstruction_job(
    *,
    store: Any,
    canonical: WorkshopCanonicalOperations,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
    job_id: str,
    session: Optional[Dict[str, Any]] = None,
    private_content_store: Any = None,
    clock: Callable[[], float] = time.time,
) -> Dict[str, Any]:
    """Run one admitted job: exactly one provider call, result on the card.

    Every write is conditional on ``job_id``; a job superseded by a newer
    message never overwrites the newer job's card. Provider and content
    failures are recorded on the card with a typed reason and re-raised.
    """
    session = _scoped_session(store, workshop_id, tenant_id, user_id, session)
    card = _current_card(store, workshop_id)
    payload = dict((card or {}).get("payload") or {})
    if payload.get("job_id") != job_id or payload.get("job_status") != "queued":
        return {"job_status": "superseded", "job_id": job_id, "card": card,
                "result": None, "registry_draft_ref": None}
    sequence_no = int(payload["based_on_sequence_no"])
    attempt = payload.get("attempt")
    # The job reads the conversation it was admitted for, even if newer
    # messages arrived since (they have their own job).
    events = [event for event in store.list_events(workshop_id)
              if int(event.get("sequence_no", 0)) <= sequence_no]
    running = _write_job_card(store, {
        **{k: card[k] for k in ("card_id", "card_type", "workshop_id")},
        "status": "running",
        "title": "Strategy reconstruction in progress",
        "summary": f"Reconstructing the conversation up to message sequence {sequence_no}.",
        "payload": {**payload, "job_status": "running", "started_at_epoch": clock()},
        "evidence_refs": [], "allowed_actions": {},
    }, expected_job_id=job_id)
    if running is None:
        return {"job_status": "superseded", "job_id": job_id, "card": _current_card(store, workshop_id),
                "result": None, "registry_draft_ref": None}

    def fail(reason: str, status_code: int, adapter_error_code: Optional[str] = None) -> None:
        _write_job_card(store, _failed_card(
            workshop_id, job_id=job_id, sequence_no=sequence_no, attempt=attempt, reason=reason,
            status_code=status_code, adapter_error_code=adapter_error_code,
        ), expected_job_id=job_id)

    try:
        messages_content = _messages_from_events(
            events, private_content_store=private_content_store,
            tenant_id=tenant_id, user_id=user_id, workshop_id=workshop_id,
        )
    except Exception:
        fail(REASON_PRIVATE_CONTENT_UNAVAILABLE, 503)
        raise
    try:
        result = reconstruct_strategy_with_agent(
            workshop_id=workshop_id, sequence_no=sequence_no, events=events,
            messages_content=messages_content, tenant_id=tenant_id, user_id=user_id,
        )
    except ReconstructionProviderError as exc:
        fail(exc.reason, exc.status_code, exc.adapter_error_code)
        raise
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
    completed_card = _write_job_card(store, {
        "card_id": reconstruction_card_id(workshop_id),
        "card_type": RECONSTRUCTION_CARD_TYPE,
        "workshop_id": workshop_id,
        "status": "completed",
        "title": f"Strategy reconstruction: {result.completeness.grade}",
        "summary": (
            result.next_best_question.text
            if result.next_best_question else "Reconstruction complete."
        ),
        "payload": {
            "job_id": job_id,
            "job_status": "completed",
            "attempt": attempt,
            "engine_version": ENGINE_VERSION,
            "private_content_reader_version": 1,
            "reconstruction": result_dict,
            "based_on_sequence_no": sequence_no,
            "registry_draft_ref": registry_draft_ref,
        },
        "evidence_refs": result.evidence_refs,
        "allowed_actions": {},
    }, expected_job_id=job_id)
    return {
        "result": result_dict,
        "registry_draft_ref": registry_draft_ref,
        "job_status": "completed" if completed_card is not None else "superseded",
        "job_id": job_id,
        "card": completed_card if completed_card is not None else _current_card(store, workshop_id),
    }


def wait_for_reconstruction_job(
    *,
    store: Any,
    workshop_id: str,
    job_id: Optional[str],
    timeout_seconds: float,
    poll_seconds: float = 0.5,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> Optional[Dict[str, Any]]:
    """Poll the durable card until ``job_id`` is terminal, superseded or the
    deadline passes. Reads only: never calls the provider."""
    deadline = monotonic() + max(timeout_seconds, 0.0)
    while True:
        card = _current_card(store, workshop_id)
        payload = dict((card or {}).get("payload") or {})
        if (payload.get("job_id") != job_id
                or payload.get("job_status") in {"completed", "failed"}
                or monotonic() >= deadline):
            return card
        sleep(min(poll_seconds, max(deadline - monotonic(), 0.0)))


def provider_error_from_card(card: Optional[Dict[str, Any]]) -> ReconstructionProviderError:
    payload = dict((card or {}).get("payload") or {})
    return ReconstructionProviderError(
        int(payload.get("failure_status") or 502),
        reason=str(payload.get("failure_reason") or REASON_PROVIDER_ERROR),
        adapter_error_code=payload.get("adapter_error_code"),
    )


def run_reconstruction_worker(
    *,
    store: Any,
    canonical: WorkshopCanonicalOperations,
    workshop_id: str,
    tenant_id: str,
    user_id: str,
    session: Optional[Dict[str, Any]] = None,
    private_content_store: Any = None,
    wait_seconds: float = 0.0,
) -> Dict[str, Any]:
    """Admit-or-resume the reconstruction job for the latest message, inline.

    Used by ``POST /reconstruct`` and store-level callers. A completed job for
    the same message is replayed without a provider call; a job queued by
    message admission is awaited for ``wait_seconds`` (reads only); a failed
    job is replayed as its typed error unless the reason is transient.

    Returns ``{"result", "registry_draft_ref", "job_status", "job_id", "card"}``
    where ``job_status`` is ``completed`` (this call ran it), ``replayed``,
    or ``queued``/``running`` (still in flight after the wait).
    """
    admission = admit_reconstruction_job(
        store=store, workshop_id=workshop_id, tenant_id=tenant_id, user_id=user_id,
        session=session, retry_failed=True,
    )
    if admission["admitted"]:
        return run_reconstruction_job(
            store=store, canonical=canonical, workshop_id=workshop_id, tenant_id=tenant_id,
            user_id=user_id, job_id=admission["job_id"], session=session,
            private_content_store=private_content_store,
        )
    card = admission["card"]
    if admission["job_status"] in {"queued", "running"} and wait_seconds > 0:
        card = wait_for_reconstruction_job(
            store=store, workshop_id=workshop_id, job_id=admission["job_id"],
            timeout_seconds=wait_seconds,
        )
    payload = dict((card or {}).get("payload") or {})
    status = payload.get("job_status")
    if status == "failed":
        raise provider_error_from_card(card)
    return {
        "result": payload.get("reconstruction") if status == "completed" else None,
        "registry_draft_ref": payload.get("registry_draft_ref"),
        "job_status": "replayed" if status == "completed" else status,
        "job_id": payload.get("job_id"),
        "card": card,
    }


__all__ = [
    "admit_reconstruction_job", "run_reconstruction_job", "wait_for_reconstruction_job",
    "run_reconstruction_worker", "reconstruction_card_id", "reconstruction_wait_seconds",
    "provider_error_from_card", "RECONSTRUCTION_CARD_TYPE",
]

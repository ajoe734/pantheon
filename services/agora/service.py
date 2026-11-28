"""
Agora Write Service.

Coordinates writes for all Agora direct-write groups with strict write-authority
matrix enforcement, delegating persistence directly to AgoraStore (backed by
Pantheon's PostgresJsonOwnerStore).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence, Union

from .habit_profile import ALL_STRATEGIES, data_family_for, derive_habit_profile, profile_key
from .store import AgoraEvidenceStore, AgoraStore, DictRecord, build_agora_store, _utc_now_rfc3339
from .write_authority import WRITE_AUTHORITY_MATRIX, assert_authorized, is_authorized

WRITE_AUTHORITY_MATRIX.setdefault(("AgoraServantTurn", "enqueue"), ["operator", "admin", "trader"])
WRITE_AUTHORITY_MATRIX.setdefault(("AgoraServantTurn", "process"), ["system", "persona"])
WRITE_AUTHORITY_MATRIX.setdefault(("AgoraServantSession", "purge"), ["system"])
WRITE_AUTHORITY_MATRIX.setdefault(("AgoraHabitProfile", "refresh"), ["operator", "admin", "trader", "system", "persona"])
WRITE_AUTHORITY_MATRIX.setdefault(("AgoraTraderReport", "create"), ["operator", "admin", "trader", "system", "persona"])
WRITE_AUTHORITY_MATRIX.setdefault(("AgoraTraderReport", "process"), ["system", "persona", "operator", "admin", "trader"])
WRITE_AUTHORITY_MATRIX.setdefault(("AgoraTraderReport", "purge"), ["system", "persona"])

_logger = logging.getLogger(__name__)


class AgoraEvidenceWriteService:
    """Human teaching writes owned by Agora; Observe/Learn authority only."""

    TYPES = {
        "signal_feedback": ("AgoraSignalFeedback", "record", "feedback"),
        "note": ("AgoraNote", "create", "note"),
        "insight": ("AgoraInsight", "create", "insight"),
        "training_example": ("AgoraTrainingExample", "create", "training_example"),
        "decision": ("AgoraDecisionLock", "create", "decision"),
        "rejection": ("AgoraDecisionLock", "create", "rejection"),
        "deferral": ("AgoraDecisionLock", "create", "deferral"),
        "intervention": ("AgoraDecisionLock", "create", "intervention"),
    }

    def __init__(self, store: AgoraEvidenceStore) -> None:
        self.store = store

    def submit(self, evidence_type: str, body: Any, *, scope: Any, idempotency_key: str, admitted_at: str) -> Any:
        from services.control_plane.bff.agora.dataset_extraction.models import AgoraInteractionEvidenceRequest
        from services.control_plane.bff.agora.dataset_extraction.extractor import (
            evidence_request_digest, sanitize_content_payload,
        )
        resource, operation, kind = self.TYPES[evidence_type]
        assert_authorized(resource, operation, scope.roles)
        content, _ = sanitize_content_payload(body.content)
        content["agora_human_evidence"] = {
            "type": evidence_type, "subject_ref": body.subject_ref,
            "actor_id": scope.operator_id, "environment_ceiling": "paper",
            "runtime_effect": "none",
        }
        evidence = AgoraInteractionEvidenceRequest(
            evidence_id=body.evidence_id, interaction_kind=kind,
            persona_id=body.persona_id, content=content,
            source_refs=list(dict.fromkeys([body.subject_ref, *body.source_refs])),
            captured_at=body.captured_at.isoformat(),
            consent_granted=body.consent_granted, retention_days=body.retention_days,
            learning_eligible=body.learning_eligible,
        )
        return self.store.admit(
            evidence, tenant_id=scope.tenant_id, user_id=scope.user_id,
            idempotency_key=idempotency_key,
            request_digest=evidence_request_digest(evidence), admitted_at=admitted_at,
        )

    @staticmethod
    def public_record(entry: Any) -> Any:
        if entry is None or "agora_human_evidence" not in entry.get("content", {}):
            return None
        from datetime import datetime, timedelta, timezone
        created = datetime.fromisoformat(str(entry["created_at"]).replace("Z", "+00:00"))
        if not entry.get("consent_granted") or created + timedelta(days=entry["retention_days"]) <= datetime.now(timezone.utc):
            return None
        return {key: entry.get(key) for key in (
            "evidence_id", "tenant_id", "user_id", "persona_id", "interaction_kind",
            "content", "source_refs", "captured_at", "created_at", "status", "error_message",
        )}

    def get(self, evidence_id: str, *, tenant_id: str, user_id: str) -> Any:
        return self.public_record(self.store.get(evidence_id, tenant_id=tenant_id, user_id=user_id))

    def observe(self, *, tenant_id: str, user_id: str, persona_id: str, since: Any = None) -> Any:
        from datetime import datetime
        items = []
        for entry in self.store.list_persona(tenant_id=tenant_id, user_id=user_id, persona_id=persona_id):
            item = self.public_record(entry)
            created = datetime.fromisoformat(str(entry["created_at"]).replace("Z", "+00:00"))
            if item:
                if since is None or created > since:
                    items.append(item)
        return sorted(items, key=lambda item: (str(item["created_at"]), item["evidence_id"]))


_UNSET_IMITATION_RESOLVER = object()


class AgoraWriteService:
    """Direct write owner service for Agora operations."""

    def __init__(
        self,
        store: Optional[AgoraStore] = None,
        *,
        dsn: Optional[str] = None,
        schema: str = "agora",
        dataset_store: Optional[Any] = None,
        imitation_candidate_resolver: Optional[Any] = _UNSET_IMITATION_RESOLVER,
    ) -> None:
        from .shadow_mode import build_default_imitation_candidate_resolver

        self.store = store or AgoraStore(dsn=dsn, schema=schema)
        self.dataset_store = dataset_store
        if imitation_candidate_resolver is _UNSET_IMITATION_RESOLVER:
            self.imitation_candidate_resolver: Optional[Any] = build_default_imitation_candidate_resolver()
        else:
            self.imitation_candidate_resolver = imitation_candidate_resolver
        try:
            from services.control_plane.bff.agora.dataset_extraction.extractor import (
                ensure_journal_evidence_emission_wired,
            )

            ensure_journal_evidence_emission_wired()
        except Exception as exc:
            _logger.warning("Failed to wire Agora journal evidence emission: %s", exc)

    def create_session(
        self,
        session_id: str,
        title: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraSession", "create", actor_roles)
        return self.store.create_session(
            session_id=session_id,
            title=title,
            actor_id=actor_id,
            payload=payload or {},
            created_at=created_at,
        )

    def open_committee_session(
        self,
        session_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        opened_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSession", "open", actor_roles)
        return self.store.open_committee_session(session_id, opened_at=opened_at)

    def close_committee_session(
        self,
        session_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
        memo_ids: Optional[List[str]] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSession", "close", actor_roles)
        return self.store.close_committee_session(
            session_id, closed_at=closed_at, outcome=outcome, memo_ids=memo_ids
        )

    def close_session(
        self,
        session_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        closed_at: Optional[str] = None,
        outcome: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSession", "close", actor_roles)
        return self.store.close_session(session_id, closed_at=closed_at, outcome=outcome)

    def append_session_message(
        self,
        session_id: str,
        message_id: str,
        content: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSessionMessage", "append", actor_roles)
        return self.store.append_session_message(
            session_id=session_id,
            message_id=message_id,
            content=content,
            actor_id=actor_id,
            payload=payload or {},
            created_at=created_at,
        )

    # -------------------------------------------------------------------------
    # Servant conversations (AGORA-SERVANT-SESSION-DURABLE-20261127)
    # -------------------------------------------------------------------------
    def ensure_servant_session(
        self,
        session_id: str,
        *,
        tenant_id: str,
        user_id: str,
        operator_id: str,
        persona_id: str,
        actor_roles: Union[Sequence[str], str],
        session_type: str = "interactive",
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraSession", "create", actor_roles)
        return self.store.ensure_servant_session(
            session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            operator_id=operator_id,
            persona_id=persona_id,
            session_type=session_type,
            created_at=created_at,
        )

    def get_servant_session(
        self, session_id: str, *, tenant_id: str, user_id: str
    ) -> Optional[DictRecord]:
        """Owner-scoped read: another trader's (or tenant's) session reads as absent."""
        session = self.store.get_servant_session(session_id)
        if session is None:
            return None
        if str(session.get("tenantId")) != str(tenant_id) or str(session.get("userId")) != str(user_id):
            return None
        return session

    def enqueue_servant_turn(
        self, turn: Dict[str, Any], actor_roles: Union[Sequence[str], str]
    ) -> tuple[bool, DictRecord]:
        assert_authorized("AgoraServantTurn", "enqueue", actor_roles)
        return self.store.enqueue_servant_turn(turn)

    def list_servant_turns(
        self, session_id: str, statuses: Sequence[str] = ("queued", "running")
    ) -> List[DictRecord]:
        return self.store.list_servant_turns(session_id, statuses)

    def claim_servant_turn(
        self,
        *,
        lease_owner: str,
        actor_roles: Union[Sequence[str], str],
        lease_seconds: float = 600,
        now: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraServantTurn", "process", actor_roles)
        return self.store.claim_servant_turn(lease_owner=lease_owner, lease_seconds=lease_seconds, now=now)

    def finish_servant_turn(
        self,
        turn: Dict[str, Any],
        *,
        status: str,
        actor_roles: Union[Sequence[str], str],
        answer_message_id: Optional[str] = None,
        reason: Optional[str] = None,
        finished_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraServantTurn", "process", actor_roles)
        return self.store.finish_servant_turn(
            turn,
            status=status,
            answer_message_id=answer_message_id,
            reason=reason,
            finished_at=finished_at,
        )

    def purge_expired_servant_sessions(
        self,
        *,
        actor_roles: Union[Sequence[str], str],
        now: Optional[str] = None,
        retention_days: Optional[int] = None,
    ) -> Dict[str, int]:
        """Named cleanup path: sessions untouched for 180 days (and their turns) are deleted."""
        assert_authorized("AgoraServantSession", "purge", actor_roles)
        kwargs: Dict[str, Any] = {"now": now}
        if retention_days is not None:
            kwargs["retention_days"] = retention_days
        return self.store.purge_expired_servant_sessions(**kwargs)

    def submit_committee_memo(
        self,
        session_id: str,
        memo_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Dict[str, Any],
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraCommitteeMemo", "submit", actor_roles)
        return self.store.submit_committee_memo(
            session_id=session_id,
            memo_id=memo_id,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
        )

    def publish_committee_memo(
        self,
        session_id: str,
        memo_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        published_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraCommitteeMemo", "publish", actor_roles)
        return self.store.publish_committee_memo(
            session_id=session_id,
            memo_id=memo_id,
            actor_id=actor_id,
            published_at=published_at,
        )

    def create_evidence_pack(
        self,
        session_id: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraCommitteeEvidencePack", "create", actor_roles)
        return self.store.create_evidence_pack(
            session_id=session_id,
            payload=payload or {},
            actor_id=actor_id,
            created_at=created_at,
        )

    def append_evidence_files(
        self,
        session_id: str,
        files: List[Dict[str, Any]],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        uploaded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraCommitteeEvidencePack", "append_files", actor_roles)
        return self.store.append_evidence_files(
            session_id=session_id,
            files=files,
            actor_id=actor_id,
            uploaded_at=uploaded_at,
        )

    def create_note(
        self,
        note_id: str,
        title: str,
        content: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraNote", "create", actor_roles)
        return self.store.create_note(
            note_id=note_id,
            title=title,
            content=content,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
        )

    def create_insight(
        self,
        insight_id: str,
        title: str,
        content: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraInsight", "create", actor_roles)
        return self.store.create_insight(
            insight_id=insight_id,
            title=title,
            content=content,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
        )

    def create_training_example(
        self,
        example_id: str,
        topic: str,
        input_data: Dict[str, Any],
        expected_output: Dict[str, Any],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraTrainingExample", "create", actor_roles)
        return self.store.create_training_example(
            example_id=example_id,
            topic=topic,
            input_data=input_data,
            expected_output=expected_output,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
        )

    def create_signal(
        self,
        signal_id: str,
        symbol: str,
        action: str,
        confidence: float,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraSignal", "create", actor_roles)
        return self.store.create_signal(
            signal_id=signal_id,
            symbol=symbol,
            action=action,
            confidence=confidence,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
        )

    def record_signal_feedback(
        self,
        signal_id: str,
        rating: int,
        comments: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        recorded_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraSignalFeedback", "record", actor_roles)
        return self.store.record_signal_feedback(
            signal_id=signal_id,
            rating=rating,
            comments=comments,
            actor_id=actor_id,
            recorded_at=recorded_at,
        )

    def create_feedback(
        self,
        feedback_id: str,
        target_id: str,
        content: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraFeedback", "create", actor_roles)
        return self.store.create_feedback(
            feedback_id=feedback_id,
            target_id=target_id,
            content=content,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
        )

    def create_handoff(
        self,
        handoff_id: str,
        source_lane: str,
        target_lane: str,
        summary: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraHandoff", "create", actor_roles)
        return self.store.create_handoff(
            handoff_id=handoff_id,
            source_lane=source_lane,
            target_lane=target_lane,
            summary=summary,
            actor_id=actor_id,
            payload=payload,
            created_at=created_at,
        )

    def record_audit_event(
        self,
        event_type: str,
        actor_id: str,
        target_id: str,
        actor_roles: Union[Sequence[str], str],
        payload: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
        recorded_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraAuditEvent", "record", actor_roles)
        return self.store.record_audit_event(
            event_type=event_type,
            actor_id=actor_id,
            target_id=target_id,
            payload=payload,
            event_id=event_id,
            recorded_at=recorded_at,
        )

    def create_journal_entry(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> DictRecord:
        raise RuntimeError(
            "Agora decision journal writer slice is retired; "
            "use canonical governance decision journal owner (services.governance.decision_journal)."
        )

    def patch_journal_entry(
        self,
        *args: Any,
        **kwargs: Any,
    ) -> Optional[DictRecord]:
        raise RuntimeError(
            "Agora decision journal writer slice is retired; "
            "use canonical governance decision journal owner (services.governance.decision_journal)."
        )

    def create_workshop(
        self,
        initial_message: str,
        created_by: str,
        actor_roles: Union[Sequence[str], str],
        workshop_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraWorkshop", "create", actor_roles)
        return self.store.create_workshop(
            initial_message=initial_message,
            created_by=created_by,
            workshop_id=workshop_id,
            created_at=created_at,
        )

    def append_workshop_message(
        self,
        workshop_id: str,
        content: str,
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        message_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraWorkshop", "mutate", actor_roles)
        return self.store.append_workshop_message(
            workshop_id=workshop_id,
            content=content,
            actor_id=actor_id,
            message_id=message_id,
            created_at=created_at,
        )

    def create_proposal(
        self,
        payload: Dict[str, Any],
        created_by: str,
        actor_roles: Union[Sequence[str], str],
        proposal_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraProposal", "create", actor_roles)
        return self.store.create_proposal(
            payload=payload,
            created_by=created_by,
            proposal_id=proposal_id,
            created_at=created_at,
        )

    def modify_proposal(
        self,
        proposal_id: str,
        action: str,
        reason: str,
        proposed_value: Optional[Dict[str, Any]],
        actor_id: str,
        actor_roles: Union[Sequence[str], str],
        updated_at: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraProposal", "modify", actor_roles)
        return self.store.modify_proposal(
            proposal_id=proposal_id,
            action=action,
            reason=reason,
            proposed_value=proposed_value,
            actor_id=actor_id,
            updated_at=updated_at,
        )

    def create_interaction(
        self,
        payload: Dict[str, Any],
        created_by: str,
        actor_roles: Union[Sequence[str], str],
        interaction_id: Optional[str] = None,
        created_at: Optional[str] = None,
    ) -> DictRecord:
        assert_authorized("AgoraInteraction", "create", actor_roles)
        return self.store.create_interaction(
            payload=payload,
            created_by=created_by,
            interaction_id=interaction_id,
            created_at=created_at,
        )

    # -------------------------------------------------------------------------
    # Shadow-mode decision locks (design A4). Insert-only; reads are scoped.
    # -------------------------------------------------------------------------
    def _resolve_dataset_store(self) -> Optional[Any]:
        if self.dataset_store is not None:
            return self.dataset_store
        try:
            from services.control_plane.bff.agora.dataset_extraction.router import _default_store

            return _default_store()
        except Exception:
            return None

    def _emit_decision_lock_evidence(self, lock: Dict[str, Any]) -> Optional[Any]:
        lock_id = str(
            lock.get("human_decision_id")
            or lock.get("decision_id")
            or lock.get("record_id")
            or lock.get("decision_event_id")
            or ""
        ).strip()
        try:
            ds_store = self._resolve_dataset_store()
            if ds_store is None:
                return None
            trader_identity = lock.get("trader_identity") if isinstance(lock.get("trader_identity"), dict) else {}
            tenant_id = str(lock.get("tenant_id") or trader_identity.get("tenant_id") or "").strip()
            user_id = str(
                lock.get("user_id")
                or lock.get("agora_user_id")
                or trader_identity.get("user_id")
                or trader_identity.get("agora_user_id")
                or ""
            ).strip()
            if not tenant_id or not user_id or not lock_id:
                return None
            from services.control_plane.bff.agora.dataset_extraction.extractor import (
                extract_decision_lock_evidence,
            )

            record, _ = extract_decision_lock_evidence(lock, store=ds_store)
            return record
        except Exception as exc:
            _logger.warning(
                "Best-effort decision-lock learning evidence extraction failed for lock_id=%s: %s",
                lock_id,
                exc,
            )
            return None

    def _attach_trader_imitation_arm(self, lock: Dict[str, Any]) -> Dict[str, Any]:
        from .shadow_mode import (
            canonical_checksum,
            predict_trader_imitation_action,
            unavailable,
        )

        prepared = dict(lock)
        arms = dict(prepared.get("arms")) if isinstance(prepared.get("arms"), dict) else {}
        existing = arms.get("trader_imitation")
        if isinstance(existing, dict) and existing.get("available") is True:
            prepared["arms"] = arms
            opt = dict(prepared.get("optional_alt_versions")) if isinstance(prepared.get("optional_alt_versions"), dict) else {}
            opt.setdefault("trader_imitation", dict(existing))
            prepared["optional_alt_versions"] = opt
            return prepared

        candidate: Any = None
        resolver = self.imitation_candidate_resolver
        if resolver is not None:
            trader_identity = prepared.get("trader_identity") if isinstance(prepared.get("trader_identity"), dict) else {}
            tenant_id = str(prepared.get("tenant_id") or trader_identity.get("tenant_id") or "").strip()
            user_id = str(
                prepared.get("user_id")
                or prepared.get("agora_user_id")
                or trader_identity.get("user_id")
                or trader_identity.get("agora_user_id")
                or ""
            ).strip()
            strategy_id = str(
                prepared.get("strategy_id")
                or ((prepared.get("decision_time_snapshot") or {}) if isinstance(prepared.get("decision_time_snapshot"), dict) else {}).get("strategy_id")
                or ""
            ).strip()
            try:
                if hasattr(resolver, "resolve_candidate"):
                    candidate = resolver.resolve_candidate(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        strategy_id=strategy_id or None,
                    )
                elif hasattr(resolver, "get_trader_imitation_state"):
                    state = resolver.get_trader_imitation_state(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        strategy_id=strategy_id or None,
                    )
                    candidate = state.get("candidate") if isinstance(state, dict) else state
                elif callable(resolver):
                    raw_cand = resolver(
                        tenant_id=tenant_id,
                        user_id=user_id,
                        strategy_id=strategy_id,
                    )
                    if isinstance(raw_cand, dict) and (
                        "candidate" in raw_cand or "active_candidate" in raw_cand
                    ) and not (
                        raw_cand.get("policy_weights")
                        or raw_cand.get("policy")
                        or raw_cand.get("artifact")
                        or raw_cand.get("weights")
                    ):
                        candidate = raw_cand.get("candidate") or raw_cand.get("active_candidate")
                    else:
                        candidate = raw_cand
            except Exception as exc:
                _logger.warning("Failed to resolve trader imitation candidate for lock: %s", exc)
                candidate = None

        imitation_arm: Optional[Dict[str, Any]] = None
        if isinstance(candidate, dict):
            if candidate.get("available") is False:
                imitation_arm = unavailable(
                    str(
                        candidate.get("unavailable_reason")
                        or candidate.get("reason")
                        or "no_trader_imitation_candidate_on_decision_event"
                    )
                )
            else:
                snap = (
                    prepared.get("decision_time_snapshot")
                    if isinstance(prepared.get("decision_time_snapshot"), dict)
                    else {}
                )
                pred = predict_trader_imitation_action(candidate, snapshot=snap, event=prepared)
                if pred.get("available") is True:
                    imitation_arm = pred

        changed = False
        if imitation_arm is not None:
            arms["trader_imitation"] = dict(imitation_arm)
            changed = True
        elif "trader_imitation" not in arms:
            arms["trader_imitation"] = unavailable("no_trader_imitation_candidate_on_decision_event")
            changed = True

        if changed:
            prepared["arms"] = arms
            opt = dict(prepared.get("optional_alt_versions")) if isinstance(prepared.get("optional_alt_versions"), dict) else {}
            opt["trader_imitation"] = dict(arms["trader_imitation"])
            prepared["optional_alt_versions"] = opt
            if "checksum" in prepared:
                prepared["checksum"] = canonical_checksum(prepared)
        return prepared

    def create_decision_lock(
        self,
        lock: Dict[str, Any],
        actor_roles: Union[Sequence[str], str],
    ) -> DictRecord:
        assert_authorized("AgoraDecisionLock", "create", actor_roles)
        enriched = self._attach_trader_imitation_arm(lock)
        created = self.store.create_decision_lock(enriched)
        self._emit_decision_lock_evidence(created)
        self._mark_habit_profiles_stale(created)
        return created

    def record_decision_lock_paper_proxy_fill(
        self,
        human_decision_id: str,
        *,
        tenant_id: str,
        fill: Dict[str, Any],
        actor_roles: Union[Sequence[str], str] = ("system",),
        user_id: Optional[str] = None,
        decision_event_id: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraDecisionLock", "record_paper_proxy_fill", actor_roles)
        updated = self.store.record_decision_lock_paper_proxy_fill(
            human_decision_id,
            tenant_id=tenant_id,
            fill=fill,
            user_id=user_id,
            decision_event_id=decision_event_id,
        )
        if updated is not None:
            self._mark_habit_profiles_stale(updated)
        return updated

    # -------------------------------------------------------------------------
    # Trader habit profile facts (AGORA-TRADER-HABIT-PROFILE-20261127, SD §6)
    # -------------------------------------------------------------------------
    def _mark_habit_profiles_stale(self, lock: Dict[str, Any]) -> None:
        """Event-driven: a new lock flags the trader's profiles; the next read recomputes."""
        try:
            tenant_id = str(lock.get("tenant_id") or "").strip()
            user_id = str(lock.get("user_id") or "").strip()
            if tenant_id and user_id:
                self.store.mark_habit_profiles_stale(tenant_id, user_id)
        except Exception as exc:  # the lock is already durable; the next read still recomputes
            _logger.warning("habit profile stale-mark failed: %s", exc)

    def _habit_interaction_events(self, tenant_id: str, user_id: str) -> List[Dict[str, Any]]:
        events: List[Dict[str, Any]] = []
        for message in self.store.list_servant_user_messages(tenant_id, user_id):
            meta = message.get("metadata") if isinstance(message.get("metadata"), dict) else {}
            context = meta.get("command_context") if isinstance(meta.get("command_context"), dict) else {}
            family = data_family_for(
                " ".join(str(context.get(k) or "") for k in ("widget_id", "quick_task", "page"))
            )
            if family:
                events.append({
                    "session_id": message.get("session_id"), "at": message.get("createdAt"),
                    "family": family, "ref": message.get("id"),
                })
        return events

    def refresh_habit_profile(
        self,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: Optional[str] = None,
        journal_entries: Sequence[Dict[str, Any]] = (),
        actor_roles: Union[Sequence[str], str] = ("trader",),
        computed_at: Optional[str] = None,
    ) -> DictRecord:
        """Derive this trader's habit facts from their own records and store them when they changed.

        Owner-scoped: only the caller's tenant/user rows are read. Returns the stored profile
        (``current``) of the trader and strategy; a deterministic function of its inputs.
        """
        assert_authorized("AgoraHabitProfile", "refresh", actor_roles)
        now = computed_at or _utc_now_rfc3339()
        strategy = None if strategy_id in (None, "", ALL_STRATEGIES) else str(strategy_id)
        locks = self.store.list_trader_decision_locks(tenant_id, user_id)
        profile = derive_habit_profile(
            tenant_id=tenant_id,
            user_id=user_id,
            strategy_id=strategy,
            locks=locks,
            journal_entries=journal_entries,
            interaction_events=self._habit_interaction_events(tenant_id, user_id),
            computed_at=now,
        )
        _changed, row = self.store.put_habit_profile(profile, now=now)
        return DictRecord(row["current"])

    def get_habit_profile(
        self, *, tenant_id: str, user_id: str, strategy_id: Optional[str] = None
    ) -> Optional[DictRecord]:
        """Owner-scoped read of the stored profile; another trader's key reads as absent."""
        row = self.store.get_habit_row(profile_key(tenant_id, user_id, strategy_id))
        if row is None or str(row.get("tenant_id")) != str(tenant_id) or str(row.get("user_id")) != str(user_id):
            return None
        return DictRecord(row["current"])

    # -------------------------------------------------------------------------
    # Trader reports (AGORA-SERVANT-REVISIONS-AND-REPORT-20261127, SD §9.3)
    # -------------------------------------------------------------------------
    def put_trader_report(
        self,
        report: Dict[str, Any],
        actor_roles: Union[Sequence[str], str] = ("trader",),
        *,
        now: Optional[str] = None,
        enforce_daily_cap: bool = True,
    ) -> tuple[bool, DictRecord]:
        assert_authorized("AgoraTraderReport", "create", actor_roles)
        return self.store.put_trader_report(
            report,
            now=now,
            enforce_daily_cap=enforce_daily_cap,
        )

    def get_trader_report(
        self,
        report_id: str,
        *,
        tenant_id: str,
        user_id: str,
    ) -> Optional[DictRecord]:
        return self.store.get_trader_report(report_id, tenant_id=tenant_id, user_id=user_id)

    def list_trader_reports(
        self,
        *,
        tenant_id: str,
        user_id: str,
        strategy_id: Optional[str] = None,
        cadence: Optional[str] = None,
        limit: int = 26,
        now: Optional[str] = None,
    ) -> List[DictRecord]:
        return self.store.list_trader_reports(
            tenant_id,
            user_id,
            strategy_id=strategy_id,
            cadence=cadence,
            limit=limit,
            now=now,
        )

    def count_on_demand_trader_reports_for_day(
        self,
        *,
        tenant_id: str,
        user_id: str,
        report_day: str,
        now: Optional[str] = None,
    ) -> int:
        return self.store.count_on_demand_trader_reports_for_day(
            tenant_id,
            user_id,
            report_day,
            now=now,
        )

    def claim_trader_report(
        self,
        *,
        lease_owner: str,
        actor_roles: Union[Sequence[str], str] = ("persona",),
        lease_seconds: float = 600,
        now: Optional[str] = None,
    ) -> Optional[DictRecord]:
        assert_authorized("AgoraTraderReport", "process", actor_roles)
        return self.store.claim_trader_report(
            lease_owner=lease_owner,
            lease_seconds=lease_seconds,
            now=now,
        )

    def purge_expired_trader_reports(
        self,
        *,
        actor_roles: Union[Sequence[str], str] = ("system",),
        now: Optional[str] = None,
    ) -> Dict[str, int]:
        assert_authorized("AgoraTraderReport", "purge", actor_roles)
        return self.store.purge_expired_trader_reports(now=now)

    def extract_decision_lock_evidence(
        self,
        lock: Dict[str, Any],
        actor_roles: Union[Sequence[str], str],
        *,
        persona_id: Optional[str] = None,
        retention_days: int = 30,
        extracted_at: Optional[str] = None,
    ) -> Any:
        assert_authorized("AgoraDecisionLock", "create", actor_roles)
        from services.control_plane.bff.agora.dataset_extraction.extractor import (
            extract_decision_lock_evidence,
        )

        ds_store = self._resolve_dataset_store()
        if ds_store is None:
            raise RuntimeError("Agora dataset store is not configured")
        return extract_decision_lock_evidence(
            lock,
            store=ds_store,
            persona_id=persona_id,
            retention_days=retention_days,
            extracted_at=extracted_at,
        )

    def extract_journal_entry_evidence(
        self,
        entry: Dict[str, Any],
        actor_roles: Union[Sequence[str], str],
        *,
        decision_lock: Optional[Dict[str, Any]] = None,
        snapshot: Optional[Dict[str, Any]] = None,
        persona_id: Optional[str] = None,
        retention_days: int = 30,
        extracted_at: Optional[str] = None,
    ) -> Any:
        assert_authorized("DecisionJournalEntry", "create", actor_roles)
        from services.control_plane.bff.agora.dataset_extraction.extractor import (
            extract_journal_entry_evidence,
        )

        ds_store = self._resolve_dataset_store()
        if ds_store is None:
            raise RuntimeError("Agora dataset store is not configured")
        return extract_journal_entry_evidence(
            entry,
            store=ds_store,
            decision_lock=decision_lock,
            snapshot=snapshot,
            persona_id=persona_id,
            retention_days=retention_days,
            extracted_at=extracted_at,
        )

    def _lock_readable(
        self, lock: Optional[DictRecord], tenant_id: str, user_id: str, roles: Union[Sequence[str], str]
    ) -> bool:
        if lock is None or str(lock.get("tenant_id")) != str(tenant_id):
            return False
        if is_authorized("AgoraDecisionLock", "read_all", roles):
            return True
        return str(lock.get("user_id")) == str(user_id) and is_authorized(
            "AgoraDecisionLock", "create", roles
        )

    def get_decision_lock_for_event(
        self,
        decision_event_id: str,
        *,
        tenant_id: str,
        user_id: str,
        actor_roles: Union[Sequence[str], str],
    ) -> Optional[DictRecord]:
        lock = self.store.get_decision_lock(
            self.store.decision_lock_id(tenant_id, decision_event_id)
        )
        return lock if self._lock_readable(lock, tenant_id, user_id, actor_roles) else None

    def list_decision_locks(
        self,
        *,
        tenant_id: str,
        user_id: str,
        actor_roles: Union[Sequence[str], str],
    ) -> List[DictRecord]:
        return [
            lock
            for lock in self.store.list_decision_locks()
            if self._lock_readable(lock, tenant_id, user_id, actor_roles)
        ]


def build_agora_write_service(
    *,
    dsn: Optional[str] = None,
    schema: str = "agora",
    bootstrap: bool = True,
    dataset_store: Optional[Any] = None,
    imitation_candidate_resolver: Optional[Any] = _UNSET_IMITATION_RESOLVER,
) -> AgoraWriteService:
    """Factory creating an AgoraWriteService backed by durable Postgres storage."""
    store = build_agora_store(dsn=dsn, schema=schema, bootstrap=bootstrap)
    return AgoraWriteService(
        store=store,
        dataset_store=dataset_store,
        imitation_candidate_resolver=imitation_candidate_resolver,
    )


__all__ = ["AgoraWriteService", "build_agora_write_service"]

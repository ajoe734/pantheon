"""Real selected-Persona interaction runner for PINT-012."""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Callable, Dict, List, Optional

try:
    from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError
except ImportError:  # pragma: no cover - package entrypoint fallback
    from ...openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

from .provider import (
    authority_boundary,
    build_participant_admission,
    build_provider_prompt,
    validate_provider_opinion,
)
from .store import InteractionLifecycleStore


_TRANSIENT_PROVIDER_DEGRADED_REASONS = {
    "OPENCLAW_UNAVAILABLE": 503,
    "OPENCLAW_GATEWAY_UNAVAILABLE": 503,
    "OPENCLAW_GATEWAY_TIMEOUT": 504,
    "OPENCLAW_GATEWAY_INVOCATION_FAILED": 502,
    "UPSTREAM_UNAVAILABLE": 503,
    "OPENCLAW_TIMEOUT": 504,
    "OPENCLAW_RATE_LIMITED": 429,
}


def _outbox(kind: str, identity: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "outbox_id": f"iob:{kind}:{identity}",
        "projection_kind": kind,
        "payload": payload,
    }


def drain_interaction_outbox(store: InteractionLifecycleStore, workshop_store: Any) -> int:
    """Idempotently project durable interaction outbox rows to Workshop views."""
    from agora.strategy_workshop.events import _ws_publish

    def dispatch(kind: str, payload: Dict[str, Any]) -> None:
        if kind == "workshop_event":
            workshop_store.create_event(payload)
        elif kind == "workshop_card":
            existing = next(
                (item for item in workshop_store.list_workshop_cards(payload["workshop_id"])
                 if item.get("card_id") == payload.get("card_id")),
                None,
            )
            if existing is None:
                workshop_store.record_workshop_card(payload)
            else:
                comparable = (
                    "card_type", "workshop_id", "status", "title", "summary",
                    "payload", "evidence_refs", "allowed_actions",
                )
                if any(existing.get(key) != payload.get(key) for key in comparable):
                    raise ValueError("workshop card projector identity reused with different content")
        elif kind == "workshop_sse":
            _ws_publish(
                payload["workshop_id"], payload["event_type"], payload["data"],
                event_id=str(payload["data"].get("event_id") or ""),
            )
        elif kind in {"interaction_queued", "interaction_admitted", "interaction.queued"}:
            pass
        else:  # pragma: no cover - durable corruption guard
            raise ValueError(f"unknown interaction projection kind: {kind}")

    return store.drain_outbox(dispatch)


def _best_effort_drain(store: InteractionLifecycleStore, workshop_store: Any) -> int:
    """Projection failure never turns an accepted canonical write into 500."""
    try:
        return drain_interaction_outbox(store, workshop_store)
    except Exception:  # durable outbox remains pending for explicit recovery
        return 0


def _event_id(tenant_id: str, user_id: str, interaction_id: str, stage: str) -> str:
    seed = f"{tenant_id}:{user_id}:{interaction_id}:{stage}"
    return "evt-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:20]


def _snapshot_for(read_store: Any, persona: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    persona_id = str(persona.get("persona_id") or persona.get("id") or "")
    metadata = persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
    snapshot_id = str(
        persona.get("capability_snapshot_id")
        or metadata.get("capability_snapshot_id")
        or ""
    ).strip()
    if snapshot_id:
        getter = getattr(read_store, "get_capability_snapshot", None)
        snapshot = getter(snapshot_id) if callable(getter) else None
    else:
        snapshot = read_store.get_capability_snapshot_for_persona(persona_id)
    if not isinstance(snapshot, dict):
        return None
    snapshot_persona = str(snapshot.get("persona_id") or persona_id)
    return snapshot if snapshot_persona == persona_id else None


def _provider_error(exc: Exception) -> Dict[str, Any]:
    if isinstance(exc, OpenClawOpsClientError):
        return {
            "code": exc.error_code,
            "message": exc.message[:300],
            "retryable": exc.status_code in {0, 429, 502, 503, 504},
        }
    return {
        "code": "OPENCLAW_PERSONA_OPINION_INVALID",
        "message": str(exc)[:300] or "Persona provider response was invalid",
        "retryable": False,
    }


def _raise_transient_provider_degraded(provider_payload: Dict[str, Any]) -> None:
    provider_data = (
        provider_payload.get("data")
        if isinstance(provider_payload.get("data"), dict)
        else provider_payload
    )
    if str(provider_data.get("status") or "").strip().lower() != "degraded":
        return
    output = provider_data.get("output") if isinstance(provider_data.get("output"), dict) else {}
    reason = str(output.get("reason") or "").strip().upper()
    status_code = _TRANSIENT_PROVIDER_DEGRADED_REASONS.get(reason)
    if status_code is None:
        return
    message = str(output.get("message") or reason or "Persona provider is temporarily unavailable")
    raise OpenClawOpsClientError(message, status_code=status_code, error_code=reason)


def _synthesize(opinions: List[Dict[str, Any]], failed_persona_ids: List[str], created_at: str) -> Optional[Dict[str, Any]]:
    if not opinions:
        return None
    conclusions = [str(opinion["conclusion"]) for opinion in opinions]
    unique = set(conclusions)
    status = "recommendation"
    if failed_persona_ids:
        status = "degraded"
    elif "insufficient_evidence" in unique:
        status = "more_research_required"
    elif len(unique) > 1:
        status = "no_consensus"
    elif unique & {"conditional", "abstain"}:
        status = "options"

    disagreements: List[Dict[str, Any]] = []
    if len(opinions) >= 2:
        anchor = opinions[0]
        for other in opinions[1:]:
            if other["conclusion"] != anchor["conclusion"]:
                disagreements.append({
                    "opinion_ids": [anchor["opinion_id"], other["opinion_id"]],
                    "cause": "independent_persona_conclusions_differ",
                    "detail": (
                        f"{anchor['participant']['persona_id']} ({anchor['conclusion']}): "
                        f"{anchor['rationale']} || "
                        f"{other['participant']['persona_id']} ({other['conclusion']}): "
                        f"{other['rationale']}"
                    ),
                })

    evidence: Dict[tuple[str, str], Dict[str, Any]] = {}
    for opinion in opinions:
        for ref in opinion.get("evidence_refs") or []:
            evidence[(str(ref.get("ref_type")), str(ref.get("ref_id")))] = ref
    summary = " | ".join(
        f"{opinion['participant']['display_name']}: {opinion['rationale']}"
        for opinion in opinions
    )
    if failed_persona_ids:
        summary += " | Missing provider results: " + ", ".join(failed_persona_ids)
    return {
        "synthesis_id": "syn-" + hashlib.sha256(
            "\0".join(opinion["opinion_id"] for opinion in opinions).encode("utf-8")
        ).hexdigest()[:20],
        "status": status,
        "opinion_ids": [opinion["opinion_id"] for opinion in opinions],
        "summary": summary,
        "agreements": sorted(unique) if len(unique) == 1 else [],
        "disagreements": disagreements,
        "risk_notes": [risk for opinion in opinions for risk in opinion.get("risks") or []],
        "conditions": [
            condition
            for opinion in opinions
            for condition in opinion.get("invalidation_conditions") or []
        ],
        "evidence_refs": list(evidence.values()),
        "created_at": created_at,
        "authority": authority_boundary(),
    }


def run_selected_persona_interaction(
    *,
    workshop_store: Any,
    read_store: Any,
    workshop_id: str,
    interaction_id: str,
    topic: str,
    mode: str,
    participants: List[str],
    context_refs: List[Dict[str, Any]],
    environment: str,
    tenant_id: str,
    user_id: str,
    operator_id: str,
    trace_id: str,
    occurred_at: str,
    human_submitted_at: Optional[str] = None,
    proposal_snapshot: Optional[Dict[str, Any]] = None,
    proposal_etag: Optional[str] = None,
    client_factory: Optional[Callable[[], OpenClawOpsClient]] = None,
    lifecycle_store: Optional[InteractionLifecycleStore] = None,
    frozen_participants: Optional[List[Dict[str, Any]]] = None,
    invocation_attempt: int = 0,
    lease_owner: Optional[str] = None,
    lease_duration_seconds: int = 300,
) -> Dict[str, Any]:
    from agora.strategy_workshop.events import _ws_publish

    frozen: List[tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    if frozen_participants is not None:
        by_id = {str(item["participant"]["persona_id"]): item for item in frozen_participants}
        for persona_id in participants:
            item = by_id.get(persona_id)
            if item is None:
                raise ValueError(f"persisted frozen Persona admission missing for {persona_id}")
            frozen.append((item["persona_profile"], item["participant"], item["admission"]))
    else:
        personas = {
            str(persona.get("persona_id") or persona.get("id") or ""): persona
            for persona in read_store.list_personas(include_market_persona_defaults=True)
            if isinstance(persona, dict)
        }
        for persona_id in participants:
            persona = personas.get(persona_id)
            snapshot = _snapshot_for(read_store, persona or {}) if persona else None
            if persona is None or snapshot is None:
                raise ValueError(f"selected Persona {persona_id} lost canonical admission before invocation")
            participant, admission = build_participant_admission(
                persona=persona,
                capability_snapshot=snapshot,
                environment=environment,
                tenant_id=tenant_id,
                captured_at=occurred_at,
            )
            frozen.append((persona, participant, admission))

    opinions: List[Dict[str, Any]] = []
    invocations: List[Dict[str, Any]] = []
    failed_persona_ids: List[str] = []
    in_progress_persona_ids: List[str] = []
    if lifecycle_store is not None and invocation_attempt > 0:
        prior = lifecycle_store.get(interaction_id, tenant_id, user_id)
        if prior is None:
            raise RuntimeError("retry interaction disappeared before provider selection")
        latest_by_persona: Dict[str, Dict[str, Any]] = {}
        for prior_invocation in prior.get("provider_invocations") or []:
            persona_id = str((prior_invocation.get("participant") or {}).get("persona_id") or "")
            if persona_id:
                latest_by_persona[persona_id] = prior_invocation
        opinions_by_invocation = {
            str(opinion.get("provider_invocation_id") or ""): opinion
            for opinion in prior.get("opinions") or []
        }
        retry_targets: List[tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
        for item in frozen:
            persona_id = str(item[1]["persona_id"])
            latest = latest_by_persona.get(persona_id)
            if latest is None:
                retry_targets.append(item)
                continue
            latest_status = str(latest.get("status") or "")
            if latest_status == "succeeded":
                persisted_opinion = opinions_by_invocation.get(str(latest.get("invocation_id") or ""))
                if persisted_opinion is None:
                    raise RuntimeError("succeeded Persona invocation has no persisted opinion")
                opinions.append(persisted_opinion)
                invocations.append(latest)
            elif latest_status == "failed" and (latest.get("error") or {}).get("retryable") is True:
                retry_targets.append(item)
            elif latest_status == "failed":
                failed_persona_ids.append(persona_id)
                invocations.append(latest)
            else:
                in_progress_persona_ids.append(persona_id)
                invocations.append(latest)
        frozen = retry_targets

    attempt_key = "" if invocation_attempt == 0 else f":retry:{invocation_attempt}"
    def attempt_event(component: str) -> str:
        return f"{component}{attempt_key}"

    requested_event_id = _event_id(tenant_id, user_id, interaction_id, attempt_event("requested"))
    requested_event = {
        "spec_version": "1.9",
        "event_id": requested_event_id,
        "event_type": "opinion_requested",
        "interaction_id": interaction_id,
        "topic": topic,
        "requester": {"actor_type": "human", "actor_id": user_id, "display_name": "Operator"},
        "participants": [item[1] for item in frozen],
        "context_refs": context_refs,
        "status": "running",
        "authority": authority_boundary(),
        "trace_id": trace_id,
        "created_at": occurred_at,
    }
    requested_projection = {
        "event_id": requested_event_id,
        "workshop_id": workshop_id,
        "actor_type": "operator",
        "event_type": "opinion_requested",
        "private_content_ref": f"agora-interaction://{interaction_id}/request",
        "redacted_summary": "Independent Persona opinions requested.",
        "payload_refs_json": requested_event,
        "trace_id": trace_id,
    }
    started_sse = {
        "workshop_id": workshop_id,
        "event_type": "consultation.started",
        "data": {
        "interaction_id": interaction_id,
        "participants": [item[1] for item in frozen],
        "trace_id": trace_id,
        "event_id": requested_event_id,
        },
    }
    if lifecycle_store is not None:
        lifecycle_store.mark_running(interaction_id)
        lifecycle_store.enqueue(interaction_id, _outbox("workshop_event", requested_event_id, requested_projection))
        lifecycle_store.enqueue(interaction_id, _outbox("workshop_sse", requested_event_id, started_sse))
        _best_effort_drain(lifecycle_store, workshop_store)
    else:
        workshop_store.create_event(requested_projection)
        _ws_publish(workshop_id, "consultation.started", started_sse["data"])

    client = (client_factory or OpenClawOpsClient)()
    lease_owner = lease_owner or f"worker:{uuid.uuid4().hex}"
    for index, (persona, participant, admission) in enumerate(frozen):
        invocation_id = "inv-" + hashlib.sha256(
            f"{interaction_id}\0{participant['persona_id']}\0{participant['persona_version']}\0{invocation_attempt}".encode("utf-8")
        ).hexdigest()[:20]
        request_correlation_id = f"{trace_id}:{invocation_id}"
        prompt, context_pack = build_provider_prompt(
            topic=topic,
            mode=mode,
            interaction_id=interaction_id,
            participant=participant,
            persona_profile=persona,
            context_refs=context_refs,
            tenant_id=tenant_id,
            submitted_at=human_submitted_at or occurred_at,
        )
        invocation = {
            "invocation_id": invocation_id,
            "interaction_id": interaction_id,
            "participant": participant,
            "provider_kind": "openclaw",
            "request_correlation_id": request_correlation_id,
            "request_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "status": "running",
            "started_at": occurred_at,
            "authority": authority_boundary(),
        }
        if lifecycle_store is not None:
            durable_invocation, claimed = lifecycle_store.claim_invocation(
                interaction_id,
                invocation,
                lease_owner=lease_owner,
                lease_duration_seconds=lease_duration_seconds,
            )
            if not claimed:
                stored_invocation = durable_invocation["invocation"]
                stored_opinion = durable_invocation.get("opinion")
                if stored_invocation.get("status") == "succeeded" and stored_opinion:
                    opinions.append(stored_opinion)
                elif stored_invocation.get("status") == "failed":
                    failed_persona_ids.append(participant["persona_id"])
                # Another worker may still hold the unexpired claim.  Do not
                # invoke the provider again; the aggregate remains degraded.
                elif stored_invocation.get("status") == "running":
                    in_progress_persona_ids.append(participant["persona_id"])
                invocations.append(stored_invocation)
                continue
        if lifecycle_store is not None and lease_owner:
            lifecycle_store.heartbeat_interaction(interaction_id, lease_owner=lease_owner)
        try:
            ensure_result = client.ensure_persona_opinion_agent(
                admission,
                persona_profile=persona,
            )
            if str(ensure_result.get("execution_authority") or "") != "none":
                raise ValueError("adapter admission returned unexpected execution authority")
            provider_payload = client.invoke_assistant_provider(
                provider="openclaw",
                mode="user",
                prompt=prompt,
                context_pack=context_pack,
                operator_id=operator_id,
                metadata={
                    "purpose": "persona_opinion",
                    "invocation_id": invocation_id,
                    "allowed_tools": [],
                    "execution_authority": "none",
                },
                trace_id=request_correlation_id,
                agent_id=admission["agent_id"],
                persona_admission=admission,
                idempotency_key=invocation_id,
            )
            _raise_transient_provider_degraded(provider_payload)
            raw_opinion, response_correlation_id, response_sha = validate_provider_opinion(
                provider_payload,
                expected_agent_id=admission["agent_id"],
            )
            invocation.update({
                "status": "succeeded",
                "response_correlation_id": response_correlation_id,
                "response_sha256": response_sha,
                "completed_at": occurred_at,
                "error": None,
            })
            opinion_id = "opn-" + hashlib.sha256(
                f"{invocation_id}\0{response_sha}".encode("utf-8")
            ).hexdigest()[:20]
            opinion = {
                "opinion_id": opinion_id,
                "interaction_id": interaction_id,
                "participant": participant,
                "provider_invocation_id": invocation_id,
                **raw_opinion,
                "provenance": {
                    "content_origin": "selected_persona_provider_response",
                    "provider_kind": "openclaw",
                    "provider_invocation_id": invocation_id,
                    "request_correlated": True,
                    "response_correlated": True,
                    "canned_template": False,
                    "magic_topic_trigger": False,
                    "simulation": False,
                },
                "created_at": occurred_at,
                "authority": authority_boundary(),
            }
            opinions.append(opinion)
            event_id = _event_id(tenant_id, user_id, interaction_id, attempt_event(f"opinion:{index}:{participant['persona_id']}"))
            opinion_event = {
                "event_id": event_id,
                "workshop_id": workshop_id,
                "actor_type": "persona_session",
                "event_type": "opinion_offered",
                "private_content_ref": (
                    f"openclaw://{admission['agent_id']}/responses/{response_correlation_id}"
                ),
                "redacted_summary": (
                    f"{participant['display_name']} returned {opinion['conclusion']} "
                    f"at confidence {opinion['confidence']}."
                ),
                "payload_refs_json": {
                    "spec_version": "1.9",
                    "event_id": event_id,
                    "event_type": "opinion_offered",
                    "interaction_id": interaction_id,
                    "provider_invocation": invocation,
                    "opinion": opinion,
                    "trace_id": trace_id,
                    "created_at": occurred_at,
                    "authority": authority_boundary(),
                },
                "trace_id": trace_id,
            }
            if lifecycle_store is not None:
                applied = lifecycle_store.finish_invocation(
                    interaction_id,
                    invocation=invocation,
                    opinion=opinion,
                    error=None,
                    outbox=[_outbox("workshop_event", event_id, opinion_event)],
                    lease_owner=lease_owner,
                )
                if applied:
                    _best_effort_drain(lifecycle_store, workshop_store)
                    if lease_owner:
                        lifecycle_store.heartbeat_interaction(interaction_id, lease_owner=lease_owner)
            else:
                workshop_store.create_event(opinion_event)
        except Exception as exc:  # noqa: BLE001
            error = _provider_error(exc)
            invocation.update({
                "status": "failed",
                "completed_at": occurred_at,
                "error": error,
            })
            failed_persona_ids.append(participant["persona_id"])
            event_id = _event_id(tenant_id, user_id, interaction_id, attempt_event(f"failed:{index}:{participant['persona_id']}"))
            failed_event = {
                "event_id": event_id,
                "workshop_id": workshop_id,
                "actor_type": "openclaw_provider",
                "event_type": "provider_invocation_failed",
                "private_content_ref": f"openclaw://{admission['agent_id']}/errors/{invocation_id}",
                "redacted_summary": f"{participant['display_name']} provider invocation failed closed.",
                "payload_refs_json": {
                    "spec_version": "1.9",
                    "event_id": event_id,
                    "event_type": "provider_invocation_failed",
                    "interaction_id": interaction_id,
                    "provider_invocation": invocation,
                    "opinion": None,
                    "trace_id": trace_id,
                    "created_at": occurred_at,
                    "authority": authority_boundary(),
                },
                "trace_id": trace_id,
            }
            failed_sse = {
                "workshop_id": workshop_id,
                "event_type": "workshop.openclaw.degraded",
                "data": {
                "workshop_id": workshop_id,
                "interaction_id": interaction_id,
                "persona_id": participant["persona_id"],
                "error_code": error["code"],
                "message": error["message"],
                "trace_id": trace_id,
                "event_id": event_id,
                },
            }
            if lifecycle_store is not None:
                applied = lifecycle_store.finish_invocation(
                    interaction_id,
                    invocation=invocation,
                    opinion=None,
                    error=error,
                    outbox=[
                        _outbox("workshop_event", event_id, failed_event),
                        _outbox("workshop_sse", event_id, failed_sse),
                    ],
                    lease_owner=lease_owner,
                )
                if applied:
                    _best_effort_drain(lifecycle_store, workshop_store)
                    if lease_owner:
                        lifecycle_store.heartbeat_interaction(interaction_id, lease_owner=lease_owner)
            else:
                workshop_store.create_event(failed_event)
                _ws_publish(workshop_id, "workshop.openclaw.degraded", failed_sse["data"])
        invocations.append(invocation)

    if in_progress_persona_ids:
        return {
            "status": "running",
            "opinions": opinions,
            "invocations": invocations,
            "synthesis": None,
            "missing_participant_ids": [],
            "in_progress_participant_ids": in_progress_persona_ids,
        }

    synthesis = _synthesize(opinions, failed_persona_ids, occurred_at)
    final_status = "completed" if opinions and not failed_persona_ids else ("degraded" if opinions else "failed")
    closed_event_id = _event_id(tenant_id, user_id, interaction_id, attempt_event("closed"))
    closed_event = {
        "event_id": closed_event_id,
        "workshop_id": workshop_id,
        "actor_type": "operator",
        "event_type": "thread_closed",
        "private_content_ref": f"agora-interaction://{interaction_id}/result",
        "redacted_summary": f"Interaction finished with status {final_status}.",
        "payload_refs_json": {
            "spec_version": "1.9",
            "event_id": closed_event_id,
            "event_type": "thread_closed",
            "interaction_id": interaction_id,
            "status": final_status,
            "opinion_ids": [opinion["opinion_id"] for opinion in opinions],
            "missing_participant_ids": failed_persona_ids,
            "synthesis": synthesis,
            "trace_id": trace_id,
            "created_at": occurred_at,
            "authority": authority_boundary(),
        },
        "trace_id": trace_id,
    }

    evidence_refs = list((synthesis or {}).get("evidence_refs") or [])
    card_payload = {
        "consultation_id": interaction_id,
        "participant_persona_refs": participants,
        "status": (synthesis or {}).get("status") or final_status,
        "consensus_summary": (synthesis or {}).get("summary") or "No Persona provider returned a valid opinion.",
        "opinions": opinions,
        "provider_invocations": invocations,
        "synthesis": synthesis,
        "missing_participant_ids": failed_persona_ids,
        "freshness": occurred_at,
        "authority": authority_boundary(),
    }
    if mode == "propose_action" and proposal_snapshot:
        authoritative_refs = list(
            proposal_snapshot.get("available_approval_decision_refs") or []
        )
        proposal_payload = {
            "proposal_id": proposal_snapshot["proposal_id"],
            "proposal_ref": proposal_snapshot["proposal_id"],
            "proposal_refs": [proposal_snapshot["proposal_id"]],
            "proposal": proposal_snapshot,
            "etag": proposal_etag,
            "proposal_etag": proposal_etag,
            "approval_refs": authoritative_refs,
            "available_approval_decision_refs": authoritative_refs,
            "approval_decision_refs_authority": "canonical_read_store",
            "approval_decision_readiness": proposal_snapshot.get(
                "approval_decision_readiness"
            ),
            "interaction_result": card_payload,
            "execution_authority": "none",
            "authority": authority_boundary(),
        }
        workshop_card = {
            "card_id": f"card_proposal_{interaction_id}{attempt_key.replace(':', '_')}",
            "card_type": "governed_proposal",
            "workshop_id": workshop_id,
            "status": "informational",
            "title": "Governed candidate measure proposed",
            "summary": card_payload["consensus_summary"],
            "payload": proposal_payload,
            "evidence_refs": evidence_refs,
            "allowed_actions": {},
        }
    else:
        workshop_card = {
            "card_id": f"card_consult_{interaction_id}{attempt_key.replace(':', '_')}",
            "card_type": "consult_result",
            "workshop_id": workshop_id,
            "status": final_status,
            "title": "Independent Persona consultation",
            "summary": card_payload["consensus_summary"],
            "payload": card_payload,
            "evidence_refs": evidence_refs,
            "allowed_actions": {},
        }
    completed_sse = {
        "workshop_id": workshop_id,
        "event_type": "consultation.completed",
        "data": {
            "interaction_id": interaction_id,
            "status": final_status,
            "opinion_ids": [opinion["opinion_id"] for opinion in opinions],
            "missing_participant_ids": failed_persona_ids,
            "trace_id": trace_id,
            "event_id": closed_event_id,
        },
    }
    if lifecycle_store is not None:
        applied = lifecycle_store.finalize(
            interaction_id,
            status=final_status,
            synthesis=synthesis,
            missing_participant_ids=failed_persona_ids,
            degraded_participant_ids=failed_persona_ids,
            outbox=[
                _outbox("workshop_event", closed_event_id, closed_event),
                _outbox("workshop_card", workshop_card["card_id"], workshop_card),
                _outbox("workshop_sse", closed_event_id, completed_sse),
            ],
            lease_owner=lease_owner,
        )
        if applied:
            _best_effort_drain(lifecycle_store, workshop_store)
    else:
        workshop_store.create_event(closed_event)
        workshop_store.record_workshop_card(workshop_card)
        _ws_publish(workshop_id, "consultation.completed", completed_sse["data"])
    return {
        "status": final_status,
        "opinions": opinions,
        "invocations": invocations,
        "synthesis": synthesis,
        "missing_participant_ids": failed_persona_ids,
    }

"""Real selected-Persona interaction runner for PINT-012."""
from __future__ import annotations

import hashlib
from typing import Any, Callable, Dict, List, Optional

from openclaw_ops_client import OpenClawOpsClient, OpenClawOpsClientError

from .provider import (
    authority_boundary,
    build_participant_admission,
    build_provider_prompt,
    validate_provider_opinion,
)


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
    proposal_snapshot: Optional[Dict[str, Any]] = None,
    proposal_etag: Optional[str] = None,
    client_factory: Optional[Callable[[], OpenClawOpsClient]] = None,
) -> Dict[str, Any]:
    from agora.strategy_workshop.router import _ws_publish

    personas = {
        str(persona.get("persona_id") or persona.get("id") or ""): persona
        for persona in read_store.list_personas(include_market_persona_defaults=True)
        if isinstance(persona, dict)
    }
    frozen: List[tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]] = []
    for persona_id in participants:
        persona = personas.get(persona_id)
        snapshot = _snapshot_for(read_store, persona or {}) if persona else None
        if persona is None or snapshot is None:
            raise ValueError(f"selected Persona {persona_id} lost canonical admission before invocation")
        participant, admission = build_participant_admission(
            persona=persona,
            capability_snapshot=snapshot,
            environment=environment,
            captured_at=occurred_at,
        )
        frozen.append((persona, participant, admission))

    requested_event_id = _event_id(tenant_id, user_id, interaction_id, "requested")
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
    workshop_store.create_event({
        "event_id": requested_event_id,
        "workshop_id": workshop_id,
        "actor_type": "operator",
        "event_type": "opinion_requested",
        "private_content_ref": f"agora-interaction://{interaction_id}/request",
        "redacted_summary": "Independent Persona opinions requested.",
        "payload_refs_json": requested_event,
        "trace_id": trace_id,
    })
    _ws_publish(workshop_id, "consultation.started", {
        "interaction_id": interaction_id,
        "participants": [item[1] for item in frozen],
        "trace_id": trace_id,
        "event_id": requested_event_id,
    })

    opinions: List[Dict[str, Any]] = []
    invocations: List[Dict[str, Any]] = []
    failed_persona_ids: List[str] = []
    client = (client_factory or OpenClawOpsClient)()
    for index, (persona, participant, admission) in enumerate(frozen):
        invocation_id = "inv-" + hashlib.sha256(
            f"{interaction_id}\0{participant['persona_id']}\0{participant['persona_version']}".encode("utf-8")
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
            submitted_at=occurred_at,
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
            )
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
            event_id = _event_id(tenant_id, user_id, interaction_id, f"opinion:{index}:{participant['persona_id']}")
            workshop_store.create_event({
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
            })
        except Exception as exc:  # noqa: BLE001
            error = _provider_error(exc)
            invocation.update({
                "status": "failed",
                "completed_at": occurred_at,
                "error": error,
            })
            failed_persona_ids.append(participant["persona_id"])
            event_id = _event_id(tenant_id, user_id, interaction_id, f"failed:{index}:{participant['persona_id']}")
            workshop_store.create_event({
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
            })
            _ws_publish(workshop_id, "workshop.openclaw.degraded", {
                "workshop_id": workshop_id,
                "interaction_id": interaction_id,
                "persona_id": participant["persona_id"],
                "error_code": error["code"],
                "message": error["message"],
                "trace_id": trace_id,
            })
        invocations.append(invocation)

    synthesis = _synthesize(opinions, failed_persona_ids, occurred_at)
    final_status = "completed" if opinions and not failed_persona_ids else ("degraded" if opinions else "failed")
    closed_event_id = _event_id(tenant_id, user_id, interaction_id, "closed")
    workshop_store.create_event({
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
    })

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
        workshop_store.record_workshop_card({
            "card_id": f"card_proposal_{interaction_id}",
            "card_type": "governed_proposal",
            "workshop_id": workshop_id,
            "status": "informational",
            "title": "Governed candidate measure proposed",
            "summary": card_payload["consensus_summary"],
            "payload": proposal_payload,
            "evidence_refs": evidence_refs,
            "allowed_actions": {},
        })
    else:
        workshop_store.record_workshop_card({
            "card_id": f"card_consult_{interaction_id}",
            "card_type": "consult_result",
            "workshop_id": workshop_id,
            "status": final_status,
            "title": "Independent Persona consultation",
            "summary": card_payload["consensus_summary"],
            "payload": card_payload,
            "evidence_refs": evidence_refs,
            "allowed_actions": {},
        })
    _ws_publish(workshop_id, "consultation.completed", {
        "interaction_id": interaction_id,
        "status": final_status,
        "opinion_ids": [opinion["opinion_id"] for opinion in opinions],
        "missing_participant_ids": failed_persona_ids,
        "trace_id": trace_id,
        "event_id": closed_event_id,
    })
    return {
        "status": final_status,
        "opinions": opinions,
        "invocations": invocations,
        "synthesis": synthesis,
        "missing_participant_ids": failed_persona_ids,
    }

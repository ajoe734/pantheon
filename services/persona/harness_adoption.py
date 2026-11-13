"""Paper harness adoption and review, owned by the existing Persona aggregate.

Owner packets supply observation facts; callers supply packet ids, never scores
or a verdict. Memory and governance deliveries are durable, replayable intents.
No model call, trading action, credential issuance or alternate memory store.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote
from urllib.request import Request, urlopen

from pydantic import Field

from services.persona.harness import HarnessError, ObservationRequest, PersonaPatch, StrictModel, digest


class ObservationFacts(StrictModel):
    """Frozen owner measurements on paired paper arms, on one trading date.

    The producer must retain the virtual-book window evidence and confounders.
    A missing paired measurement is recorded as confounded, never zero-filled.
    Utility is the same predeclared, higher-is-better criterion in both arms.
    """
    patch_id: str
    base_version: int = Field(ge=1)
    candidate_version: int = Field(ge=2)
    trading_date: date
    criterion: str = Field(min_length=1, pattern=r"\S")
    base_utility: float | None = Field(default=None, allow_inf_nan=False)
    candidate_utility: float | None = Field(default=None, allow_inf_nan=False)
    confounders: list[str]
    evidence_refs: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)


def relevant_experiences(state: dict, patch: PersonaPatch) -> list[dict]:
    components = set(patch.proposed_change)
    return [copy.deepcopy(row["experience"]) for row in state["patches"].values()
            if row.get("experience") and components.intersection(row["proposal"]["proposed_change"])]


class HarnessAdoption:
    def __init__(self, harness: Any, *, now: Callable = lambda: datetime.now(timezone.utc),
                 observation_trading_days: int = 10, cooldown_days: int = 7):
        if observation_trading_days < 1 or cooldown_days < 1:
            raise ValueError("positive observation and cooldown windows required")
        self.harness, self.now = harness, now
        self.observation_trading_days, self.cooldown_days = observation_trading_days, cooldown_days
        self.consultation_factory = self._consultation
        self.packet_reader = self._packets
        self.sender = self._send

    @staticmethod
    def _consultation(tenant: str) -> Any:
        from services.consultation.client import ConsultationServiceClient
        return ConsultationServiceClient(tenant_id=tenant)

    @staticmethod
    def _packets() -> dict:
        from services.persona.cron_ooda_closure import resolve_ooda_packet_store_path
        from services.persona.persona_cycle_controller import read_packets
        return read_packets(resolve_ooda_packet_store_path())

    def verify(self, persona: Any, binding: dict, memo_id: str) -> bool:
        from services.persona.consult_triggers import evaluate_gate, required_consultation
        proposal = binding["proposal"]
        state = persona.metadata["persona_harness"]
        comparisons = state["patches"][proposal["patch_id"]].get("comparisons", [])
        if not comparisons:
            return False
        comparison = comparisons[-1]
        if (comparison.get("status") != "complete" or comparison.get("decision") != "CANDIDATE_BETTER"
                or comparison.get("environment") != "paper"
                or comparison.get("base_version") != proposal["base_version"]
                or comparison.get("candidate_digest") != binding["candidate_digest"]
                or comparison.get("base_digest") != state["versions"][proposal["base_version"] - 1]["content_digest"]
                or comparison.get("replay", {}).get("deny_all_verified") is not True):
            return False
        client = self.consultation_factory(persona.tenant_id)
        memo = client.get_memo(memo_id)
        if not memo or memo.get("status") != "published":
            return False
        request = client.get_request(memo["request_id"])
        if (not request or request.get("tenant_id") != persona.tenant_id
                or request.get("status") != "published"
                or request.get("from_persona_id") != persona.persona_id
                or request.get("target_type") != "harness_patch"
                or request.get("target_id") != proposal["patch_id"]
                or memo.get("target_type") != "harness_patch" or memo.get("target_id") != proposal["patch_id"]):
            return False
        required = required_consultation("harness_patch", proposal["patch_id"],
            initiator_persona_id=persona.persona_id, touches=proposal["proposed_change"],
            target_version=str(proposal["base_version"]), candidate_digest=binding["candidate_digest"])
        panel = request.get("metadata", {}).get("panel", {})
        expected = required.to_panel_metadata()
        if panel.get("gated_decision") != expected["gated_decision"] or panel.get("initiator_persona_id") != persona.persona_id:
            return False
        # Recompute from each real persona's transcript, not the synthesis' claimed gate.
        recommendations = {}
        for transcript in client.list_transcripts():
            if transcript.get("request_id") != request["request_id"]:
                continue
            for event in transcript.get("events", []):
                actor = event.get("actor", {})
                if (actor.get("actor_type") == "persona" and event.get("evidence_refs")
                        and event.get("event_type") == "persona_contribution"):
                    recommendations[actor.get("actor_id")] = event.get("content", {}).get("recommendation")
        participants = [p.to_dict() for p in required.participants]
        gate = evaluate_gate(participants, recommendations)
        if gate.operator_decision_required:
            raise HarnessError("risk_persona_requires_operator", 409)
        # Conditional approvals need their conditions resolved by governance.
        if any(recommendations.get(p.persona_id) != "approve" for p in required.participants):
            raise HarnessError("panel_conditions_require_operator", 409)
        if memo.get("recommendation") != "approve":
            raise HarnessError("panel_conditions_require_operator", 409)
        return True

    def cooldown(self, state: dict, row: dict) -> None:
        until = (self.now() + timedelta(days=self.cooldown_days)).isoformat()
        for component in row["proposal"]["proposed_change"]:
            state.setdefault("component_cooldowns", {})[component] = until

    def cooldown_reasons(self, state: dict, row: dict) -> list[str]:
        return ["component_cooldown:" + component for component in row["proposal"]["proposed_change"]
                if component in state.get("component_cooldowns", {})
                and datetime.fromisoformat(state["component_cooldowns"][component]) > self.now()]

    @staticmethod
    def queue_governance(persona: Any, state: dict, row: dict, reason: str) -> None:
        proposal = row["proposal"]
        ref = "harness-governance-" + digest({"tenant": persona.tenant_id, "persona": persona.persona_id,
                                           "patch": proposal["patch_id"], "reason": reason})
        payload = {"decision_id": ref, "tenant_id": persona.tenant_id, "target_type": "persona",
                   "target_id": persona.persona_id, "target_version": str(proposal["base_version"]),
                   "action_type": ("mutate_consult_policy" if "consult_policy" in proposal["proposed_change"]
                                   else "mutate_persona_route_policy"), "created_by_id": "persona-svc",
                   "created_by_role": "evolution_controller", "persona_id": persona.persona_id,
                   "rationale": reason + ": " + proposal["expected_effect"],
                   "evidence_refs": [{"ref_type": "service_handoff", "ref_id": proposal["patch_id"],
                                      "storage_ref": {"owner": "persona-svc", "persona_id": persona.persona_id,
                                                      "kind": "persona_harness_patch", "digest": digest(proposal)}}],
                   "metadata": {"operator_required": True, "reason": reason, "patch": proposal,
                                "experience_refs": row.get("experience_refs", []),
                                "rollback_target": proposal["rollback_target"]}}
        if row.get("experience"):
            payload["metadata"]["experience"] = row["experience"]
        state.setdefault("outbox", {}).setdefault(ref, {"kind": "governance", "payload": payload,
                                                     "status": "pending", "attempts": 0})
        row["governance_proposal_ref"] = ref

    def observe(self, persona_id: str, patch_id: str, request: ObservationRequest, actor: str) -> dict:
        try:
            packets = self.packet_reader()
            source_error = False
        except Exception:
            packets, source_error = {}, True

        def reduce(persona: Any, state: dict) -> tuple[dict, dict]:
            row = (state or {}).get("patches", {}).get(patch_id)
            if not row:
                raise HarnessError("patch_not_found", 404)
            if row.get("experience"):
                return state, row
            if row["status"] != "committed" or state["active_experiment"] != patch_id:
                raise HarnessError("patch_is_not_active_experiment", 409)
            reasons, facts = (["observation_source_unavailable"] if source_error else []), []
            if len(set(request.packet_ids)) != len(request.packet_ids):
                reasons.append("duplicate_observation_packet")
            adopted = datetime.fromisoformat(row["adopted_at"])
            # Review the first recorded trading window, rather than letting a
            # caller choose ten favourable days from a longer experiment.
            available = []
            for pid, packet in packets.items():
                cycle = packet.get("persona_cycle") or {}
                bundle = cycle.get("observe_bundle") or {}
                raw = bundle.get("harness_observation") or {}
                if (packet.get("tenant_id") == persona.tenant_id and packet.get("environment") == "paper"
                        and packet.get("persona_ids") == [persona_id] and cycle.get("phase") == "terminal"
                        and isinstance(raw, dict) and raw.get("patch_id") == patch_id
                        and raw.get("candidate_version") == row["version"]):
                    try:
                        day = date.fromisoformat(raw["trading_date"])
                        if day > adopted.date():
                            available.append((day, pid))
                    except (ValueError, TypeError, KeyError):
                        pass
            first_window = {pid for _, pid in sorted(available)[:row["observation_trading_days"]]}
            if set(request.packet_ids) != first_window:
                reasons.append("observation_window_selection_mismatch")
            for pid in request.packet_ids:
                packet = packets.get(pid) or {}
                cycle = packet.get("persona_cycle") or {}
                bundle = cycle.get("observe_bundle") or {}
                if (packet.get("tenant_id") != persona.tenant_id or packet.get("environment") != "paper"
                        or packet.get("persona_ids") != [persona_id] or cycle.get("phase") != "terminal"
                        or bundle.get("persona_id") != persona_id or bundle.get("unavailable")
                        or bundle.get("harness_version") != row["version"]):
                    reasons.append("observation_packet_unresolved_or_wrong_scope")
                    continue
                try:
                    fact = ObservationFacts.model_validate_json(json.dumps(bundle.get("harness_observation")))
                    if (fact.patch_id != patch_id or fact.base_version != row["proposal"]["base_version"]
                            or fact.candidate_version != row["version"]
                            or fact.trading_date <= adopted.date() or fact.trading_date > self.now().date()
                            or datetime.fromisoformat(packet["updated_at"]) < adopted):
                        raise ValueError("observation binding")
                    facts.append(fact)
                except (ValueError, TypeError, KeyError):
                    reasons.append("observation_facts_missing_or_invalid")
            if len({f.trading_date for f in facts}) != len(facts):
                reasons.append("duplicate_trading_date")
            if len(facts) != row["observation_trading_days"]:
                reasons.append("observation_window_incomplete")
            if any(f.criterion != row["observation_criterion"] for f in facts):
                reasons.append("observation_criterion_changed")
            if reasons:
                row.setdefault("observation_checks", []).append({"at": self.now().isoformat(),
                    "packet_ids": request.packet_ids, "reasons": sorted(set(reasons))})
                return state, {"status": "refused", "reasons": sorted(set(reasons))}
            confounded = any(f.confounders or f.base_utility is None or f.candidate_utility is None for f in facts)
            delta = None if confounded else sum(f.candidate_utility - f.base_utility for f in facts)
            outcome = "confounded" if confounded else "helped" if delta > 0 else "hurt" if delta < 0 else "no_effect"
            experience_id = "harness-experience-" + digest({"tenant": persona.tenant_id, "persona": persona_id,
                                                          "patch": patch_id})
            cue = "Harness component review: " + ", ".join(sorted(row["proposal"]["proposed_change"]))
            lesson = {"helped": "Reuse only under the recorded paired paper conditions.",
                      "hurt": "Propose restoration of the recorded base before another experiment.",
                      "no_effect": "No measured improvement; keep this evidence when proposing another change.",
                      "confounded": "The paired evidence cannot isolate the effect; collect an unconfounded window."}[outcome]
            experience = {"source_event_type": "harness_experience", "source_event_id": experience_id,
                "tenant_id": persona.tenant_id, "write_authority": "persona-svc",
                "sponsor_persona_id": persona_id, "contributing_persona_ids": [persona_id],
                "cue": cue, "lesson": lesson, "summary": cue + ": " + outcome + ". " + lesson,
                "outcome": outcome, "confidence": min(f.confidence for f in facts),
                "evidence_refs": [*request.packet_ids, *sorted({r for f in facts for r in f.evidence_refs})],
                "harness_version": row["version"], "proposal_ids": [patch_id],
                "body": json.dumps({"facts": [f.model_dump(mode="json") for f in facts], "delta": delta}, sort_keys=True),
                "tags": ["harness_experience", *sorted(row["proposal"]["proposed_change"])]}
            row.update(status="observed", experience=experience)
            state["active_experiment"] = None
            self.cooldown(state, row)
            state.setdefault("outbox", {})[experience_id] = {"kind": "memory", "payload": experience,
                                                             "status": "pending", "attempts": 0}
            if outcome == "hurt":
                self.queue_governance(persona, state, row, "hurt_rollback_proposal")
            self.harness._event(state, "observed", actor, patch_id=patch_id, outcome=outcome)
            return state, row

        result = self.harness._mutate(persona_id, actor, reduce)
        if result.get("status") != "refused":
            self.deliver(persona_id, actor)
            result = self.harness.read(persona_id)["patches"][patch_id]
        return result

    def deliver(self, persona_id: str, actor: str) -> dict:
        """Replay unfinished intents; failed delivery never rolls back owner evidence.

        Network I/O occurs outside CAS; receiving owners enforce stable identities.
        Concurrent/restarted callers can only redeliver the identical payload.
        """
        for ref, intent in self.harness.read(persona_id).get("outbox", {}).items():
            if intent["status"] == "delivered":
                continue
            try:
                receipt = self.sender(intent["kind"], copy.deepcopy(intent["payload"]))
                self._validate_receipt(intent["kind"], intent["payload"], receipt)
                error = None
            except Exception as exc:
                receipt, error = None, type(exc).__name__
                if getattr(exc, "code", None):
                    error += ":" + str(exc.code)

            def reduce(persona: Any, state: dict) -> tuple[dict, dict]:
                stored = state["outbox"][ref]
                if stored["status"] != "delivered":
                    stored.update(status="pending" if error else "delivered", receipt=receipt,
                                  error=error, attempts=stored["attempts"] + 1)
                return state, stored
            self.harness._mutate(persona_id, actor, reduce, paper_only=False)
        return self.harness.read(persona_id).get("outbox", {})

    @staticmethod
    def _validate_receipt(kind: str, payload: dict, receipt: dict) -> None:
        if not isinstance(receipt, dict):
            raise HarnessError("invalid_delivery_receipt")
        if kind == "memory":
            if (receipt.get("source_event_id") != payload["source_event_id"]
                    or receipt.get("source_event_type") != "harness_experience"
                    or receipt.get("sponsor_persona_id") != payload["sponsor_persona_id"]
                    or not receipt.get("persona_memory_ids") or not receipt.get("institutional_entry_ids")):
                raise HarnessError("memory_receipt_binding_mismatch")
        elif (receipt.get("decision_id") != payload["decision_id"]
              or receipt.get("tenant_id") != payload["tenant_id"]
              or receipt.get("target_id") != payload["target_id"]
              or receipt.get("metadata", {}).get("patch") != payload["metadata"]["patch"]):
            raise HarnessError("governance_receipt_binding_mismatch")

    @staticmethod
    def _send(kind: str, payload: dict) -> dict:
        from services.runtime_auth import resolve_runtime_manager_auth
        memory = kind == "memory"
        base = (os.getenv("PANTHEON_MEMORY_API_URL") or os.getenv("MEMORY_API_URL")) if memory else os.getenv("PANTHEON_EVOLUTION_API_URL")
        headers = {"Content-Type": "application/json", "X-Tenant-Id": payload["tenant_id"]}
        headers.update(resolve_runtime_manager_auth(
            token_env_key="PANTHEON_MEMORY_AUTH_TOKEN" if memory else "EVOLUTION_AUTH_TOKEN",
            token_file_env_key="PANTHEON_MEMORY_AUTH_TOKEN_FILE" if memory else "EVOLUTION_AUTH_TOKEN_FILE",
            default_token="").headers())
        if not base or not headers.get("Authorization"):
            raise HarnessError("delivery_configuration_unavailable", 503)
        path = "/api/memory/writebacks/learn-feedback" if memory else "/api/evolution/proposals"
        if not memory:
            # Evolution's ordinary POST rejects duplicate ids. Resolve a prior
            # receipt first and verify the owner/patch binding before reusing it.
            from urllib.error import HTTPError
            try:
                with urlopen(Request(base.rstrip("/") + path + "/" + quote(payload["decision_id"], safe=""),
                                     headers=headers), timeout=10) as response:
                    existing = json.load(response)
                if (existing.get("tenant_id") != payload["tenant_id"]
                        or existing.get("target_id") != payload["target_id"]
                        or existing.get("metadata", {}).get("patch") != payload["metadata"]["patch"]):
                    raise HarnessError("governance_receipt_binding_mismatch", 409)
                return existing
            except HTTPError as exc:
                if exc.code != 404:
                    raise
        with urlopen(Request(base.rstrip("/") + path, data=json.dumps(payload).encode(), headers=headers), timeout=10) as response:
            return json.load(response)

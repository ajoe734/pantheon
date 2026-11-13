"""Versioned paper harness records in the existing Persona owner.

This layer has no model, trading, memory-write or permission-granting capability.
Shadow/adoption owners supply a trusted verifier; absent evidence keeps the base.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ALWAYS_ON_TEXT_BUDGET = 12000  # UTF-8 bytes, conservatively counts all surface text
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
STATE_KEY = "persona_harness"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Guardrails(StrictModel):
    forbidden_solo_actions: list[str]
    allowed_tools: list[str]


class ConsultTrigger(StrictModel):
    action: str = Field(min_length=1)
    required_roles: list[str] = Field(min_length=1)


class Skill(StrictModel):
    name: str = Field(min_length=1)
    trigger: str = Field(min_length=1)
    scripts: list[str] = Field(min_length=1)
    guidance: str


class Workflow(StrictModel):
    cadence_seconds: int = Field(ge=1)
    steps: list[str] = Field(min_length=1)


class CycleThresholds(StrictModel):
    minimum_evidence_count: int = Field(ge=1)
    minimum_confidence: float = Field(ge=0, le=1)


class RetrievalPolicy(StrictModel):
    scopes: list[Literal["own_private", "institutional"]]
    max_items: int = Field(ge=1, le=100)
    minimum_confidence: float = Field(ge=0, le=1)


class HarnessSurface(StrictModel):
    identity_inputs: list[str] = Field(min_length=1)
    guardrails: Guardrails
    consult_policy: list[ConsultTrigger]
    tool_guidance: dict[str, str]
    skills: list[Skill]
    workflow: Workflow
    cycle_thresholds: CycleThresholds
    memory_retrieval_policy: RetrievalPolicy


class HarnessVersion(StrictModel):
    schema_version: Literal["persona_harness.v1"] = "persona_harness.v1"
    persona_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    parent_version: int | None
    patch_id: str | None
    created_at: str
    created_by: str
    content_digest: str
    surface: HarnessSurface


class EvidenceReference(StrictModel):
    kind: Literal["packet", "virtual_book", "memo", "lesson", "trade_reflection"]
    ref: str = Field(min_length=1, pattern=r"\S")
    moment: str = Field(min_length=1, pattern=r"\S")


class PersonaPatch(StrictModel):
    schema_version: Literal["persona_harness_patch.v1"] = "persona_harness_patch.v1"
    patch_id: str = Field(min_length=1, pattern=r"\S")
    persona_id: str = Field(min_length=1)
    session_id: str = Field(min_length=1, pattern=r"\S")
    base_version: int = Field(ge=1)
    patch_type: Literal["route_policy", "consult_policy", "memory", "behavior",
                        "tool_profile", "skill", "workflow", "threshold", "memory_policy"]
    proposed_change: dict[str, Any] = Field(min_length=1)
    evidence_refs: list[EvidenceReference] = Field(min_length=1)
    expected_effect: str = Field(min_length=1, pattern=r"\S")
    rollback_target: int = Field(ge=1)


class ActivatePatch(StrictModel):
    adoption_ref: str = Field(min_length=1, pattern=r"\S")


class RollbackPatch(StrictModel):
    patch_id: str = Field(min_length=1)
    reason: str = Field(min_length=1, pattern=r"\S")


class NegativeExperienceRequest(StrictModel):
    reason: str = Field(min_length=1, pattern=r"\S")


class ObservationRequest(StrictModel):
    packet_ids: list[str] = Field(min_length=1, max_length=100)


class HarnessError(ValueError):
    def __init__(self, reason: str, status_code: int = 422):
        super().__init__(reason)
        self.status_code = status_code


# Verifiers must resolve real owner records, scope them to this persona/tenant,
# and bind adoption to digest, base_version and patch_id. Never accept HTTP booleans.
EvidenceVerifier = Callable[[Any, EvidenceReference], bool]
AdoptionVerifier = Callable[[Any, dict[str, Any], str], bool]


def digest(surface: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(surface, sort_keys=True, ensure_ascii=True,
                                     allow_nan=False).encode()).hexdigest()


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [k, *_strings(v)]]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def surface_reasons(surface: HarnessSurface, script_root: Path) -> list[str]:
    reasons = []
    raw = surface.model_dump(mode="json")
    if any(not text.strip() for text in _strings(raw)):
        reasons.append("blank_surface_text")
    if sum(len(s.encode("utf-8")) for s in _strings(raw)) > ALWAYS_ON_TEXT_BUDGET:
        reasons.append("always_on_text_budget_exceeded")
    tools = set(surface.guardrails.allowed_tools)
    if not set(surface.tool_guidance).issubset(tools):
        reasons.append("tool_guidance_outside_allowed_tools")
    actions = [t.action for t in surface.consult_policy]
    if len(actions) != len(set(actions)):
        reasons.append("duplicate_consult_trigger")
    for skill in surface.skills:
        if not skill.trigger.strip():
            reasons.append("skill_trigger_missing")
        for script in skill.scripts:
            path = (script_root / script).resolve()
            if Path(script).is_absolute() or not path.is_relative_to(script_root.resolve()) or not path.is_file():
                reasons.append("skill_script_missing_or_outside_repository")
    return sorted(set(reasons))


PATCH_FIELDS = {
    "route_policy": {"identity_inputs"},
    "behavior": {"identity_inputs", "guardrails"},
    "consult_policy": {"consult_policy"},
    "tool_profile": {"guardrails", "tool_guidance"},
    "memory": {"memory_retrieval_policy"},
    "memory_policy": {"memory_retrieval_policy"},
    "skill": {"skills"},
    "workflow": {"workflow"},
    "threshold": {"cycle_thresholds"},
}


def precheck(base: HarnessSurface, patch: PersonaPatch, script_root: Path = REPOSITORY_ROOT) -> tuple[HarnessSurface | None, list[str]]:
    """Full component replacement: omitted nested restrictions cannot disappear."""
    if not set(patch.proposed_change).issubset(PATCH_FIELDS[patch.patch_type]):
        return None, ["immutable_or_wrong_patch_surface"]
    try:
        candidate = HarnessSurface.model_validate({**base.model_dump(), **patch.proposed_change})
    except ValidationError:
        return None, ["invalid_candidate_schema"]
    reasons = surface_reasons(candidate, script_root)
    if not set(candidate.guardrails.allowed_tools).issubset(base.guardrails.allowed_tools):
        reasons.append("new_tool_permission")
    if not set(base.guardrails.forbidden_solo_actions).issubset(candidate.guardrails.forbidden_solo_actions):
        reasons.append("removed_forbidden_solo_action")
    triggers = {t.action: set(t.required_roles) for t in candidate.consult_policy}
    for trigger in base.consult_policy:
        if not set(trigger.required_roles).issubset(triggers.get(trigger.action, set())):
            reasons.append("removed_consult_trigger_or_reviewer")
    if candidate.workflow.cadence_seconds < base.workflow.cadence_seconds:
        reasons.append("increased_workflow_frequency")
    if not set(base.workflow.steps).issubset(candidate.workflow.steps):
        reasons.append("removed_workflow_step")
    for key, value in base.cycle_thresholds.model_dump().items():
        if candidate.cycle_thresholds.model_dump()[key] < value:
            reasons.append("lowered_cycle_threshold")
    memory, old_memory = candidate.memory_retrieval_policy, base.memory_retrieval_policy
    if (not set(memory.scopes).issubset(old_memory.scopes)
            or memory.max_items > old_memory.max_items
            or memory.minimum_confidence < old_memory.minimum_confidence):
        reasons.append("expanded_memory_retrieval")
    return candidate, sorted(set(reasons))


def _reflection_evidence(persona: Any, ref: EvidenceReference) -> bool:
    """Only resolve existing canonical reflections locally; other owners fail closed.

    The moment is the immutable facts snapshot reference, not caller prose.
    Broader packet/book/memo/lesson resolution composes through EvidenceVerifier.
    """
    from services.persona.trade_reflection_pipeline import reflection_schema_errors
    return ref.kind == "trade_reflection" and any(
        not reflection_schema_errors(row)
        and row.get("persona_id") == persona.persona_id
        and row.get("reflection_id") == ref.ref
        and row.get("facts_snapshot_ref") == ref.moment
        for row in persona.metadata.get("trade_reflections", [])
    )


class PersistentHarnessOwner:
    def __init__(self, persona_owner: Any, *, evidence_verifier: EvidenceVerifier | None = None,
                 adoption_verifier: AdoptionVerifier | None = None, script_root: Path = REPOSITORY_ROOT,
                 shadow_provider: Callable[[str], Any] | None = None,
                 packet_store_path: Callable[[], Any] | None = None):
        from services.persona.harness_adoption import HarnessAdoption
        self.personas = persona_owner
        self.evidence_verifier = evidence_verifier or _reflection_evidence
        self.script_root = script_root
        # Owner-connected shadow runner: provider factory (None = incomplete record) and frozen packet store.
        self.shadow_provider = shadow_provider or env_shadow_provider
        self.packet_store_path = packet_store_path
        self.adoption = HarnessAdoption(self)
        self.adoption_verifier = adoption_verifier or self.adoption.verify

    def read(self, persona_id: str) -> dict[str, Any]:
        state = self.personas.get(persona_id).metadata.get(STATE_KEY)
        if not state:
            return {"status": "unavailable", "reason": "harness_not_initialized", "active_version": None}
        return copy.deepcopy(state)

    def _mutate(self, persona_id: str, actor: str, reduce: Callable, *, paper_only: bool = True) -> dict[str, Any]:
        from services.persona.write_owner import PersonaConcurrentUpdate
        for _ in range(4):
            result: dict[str, Any] = {}
            def update(persona: Any) -> dict[str, Any]:
                if paper_only and (persona.lifecycle_state != "paper_owner" or persona.status != "active"):
                    raise HarnessError("paper_owner_required", 409)
                if not persona.tenant_id:
                    raise HarnessError("persona_tenant_binding_missing", 403)
                state = copy.deepcopy(persona.metadata.get(STATE_KEY))
                state, response = reduce(persona, state)
                result.update(response)
                return {STATE_KEY: state}
            try:
                self.personas.try_metadata_cas(persona_id, guard=lambda p: True,
                                              metadata_updates=update, actor_id=actor)
                return result
            except PersonaConcurrentUpdate:
                continue
        raise HarnessError("concurrent_harness_update", 409)

    @staticmethod
    def _version(persona_id: str, surface: HarnessSurface, number: int, parent: int | None,
                 patch_id: str | None, actor: str) -> dict[str, Any]:
        return HarnessVersion(persona_id=persona_id, version=number, parent_version=parent,
                              patch_id=patch_id, created_by=actor,
                              created_at=datetime.now(timezone.utc).isoformat(),
                              content_digest=digest(surface.model_dump(mode="json")),
                              surface=surface).model_dump(mode="json")

    def initialize(self, persona_id: str, surface: HarnessSurface, actor: str) -> dict[str, Any]:
        reasons = surface_reasons(surface, self.script_root)
        if reasons:
            raise HarnessError(",".join(reasons))
        def reduce(persona: Any, state: Any) -> tuple[dict, dict]:
            if state:
                raise HarnessError("harness_already_initialized", 409)
            version = self._version(persona_id, surface, 1, None, None, actor)
            state = {"active_version": 1, "versions": [version], "patches": {},
                     "active_experiment": None, "events": []}
            self._event(state, "initialized", actor, version=1)
            return state, state
        return self._mutate(persona_id, actor, reduce)

    @staticmethod
    def _event(state: dict, action: str, actor: str, **fields: Any) -> None:
        state["events"].append({"action": action, "actor_id": actor,
                                "at": datetime.now(timezone.utc).isoformat(), **fields})

    def propose(self, persona_id: str, patch: PersonaPatch, actor: str) -> dict[str, Any]:
        if patch.persona_id != persona_id:
            raise HarnessError("patch_persona_mismatch")
        def reduce(persona: Any, state: Any) -> tuple[dict, dict]:
            if not state:
                raise HarnessError("harness_not_initialized", 409)
            if patch.patch_id in state["patches"]:
                if state["patches"][patch.patch_id]["proposal"] == patch.model_dump(mode="json"):
                    return state, state["patches"][patch.patch_id]
                raise HarnessError("patch_id_conflict", 409)
            if patch.base_version != state["active_version"] or patch.rollback_target != patch.base_version:
                raise HarnessError("stale_base_or_invalid_rollback_target", 409)
            from services.persona.harness_adoption import relevant_experiences
            row = {"proposal": patch.model_dump(mode="json"), "status": "draft", "checks": [],
                   "experience_refs": relevant_experiences(state, patch) + [
                       copy.deepcopy(r["operator_feedback"]) for r in state["patches"].values()
                       if r.get("operator_feedback") and set(patch.proposed_change).intersection(
                           r["proposal"]["proposed_change"])]}
            if persona.lifecycle_state != "paper_owner" or persona.status != "active":
                row["status"] = "governance_required"
                self.adoption.queue_governance(persona, state, row, "outside_paper")
            state["patches"][patch.patch_id] = row
            self._event(state, "proposed", actor, patch_id=patch.patch_id)
            return state, row
        result = self._mutate(persona_id, actor, reduce, paper_only=False)
        if result["status"] == "governance_required":
            self.adoption.deliver(persona_id, actor)
        return result

    def record_comparison(self, persona_id: str, patch_id: str, record: dict[str, Any], actor: str) -> dict[str, Any]:
        """Append a shadow comparison record. It never changes status or activates anything."""
        def reduce(persona: Any, state: Any) -> tuple[dict, dict]:
            if not state or patch_id not in state["patches"]:
                raise HarnessError("patch_not_found", 404)
            row = state["patches"][patch_id]
            if row["status"] != "previewed":
                raise HarnessError("precheck_required", 409)
            if record.get("patch_id") != patch_id or record.get("persona_id") != persona_id:
                raise HarnessError("comparison_binding_mismatch")
            row.setdefault("comparisons", []).append(copy.deepcopy(record))
            self._event(state, "shadow_compared", actor, patch_id=patch_id,
                        status=record.get("status"), decision=record.get("decision"))
            return state, {"patch_id": patch_id, "comparisons": len(row["comparisons"])}
        return self._mutate(persona_id, actor, reduce)

    def record_negative_experience(self, persona_id: str, patch_id: str, reason: str, actor: str) -> dict[str, Any]:
        """Owner-written negative experience ("never again"): consumed by later proposals' experience_refs."""
        def reduce(persona: Any, state: Any) -> tuple[dict, dict]:
            if not state or patch_id not in state["patches"]:
                raise HarnessError("patch_not_found", 404)
            row = state["patches"][patch_id]
            if row.get("operator_feedback"):
                return state, row  # idempotent; measured observation is kept separately
            proposal = row["proposal"]
            eid = "harness-experience-" + digest({"tenant": persona.tenant_id, "persona": persona_id,
                                                  "patch": patch_id, "source": "operator_never_again"})
            cue = "Harness component review: " + ", ".join(sorted(proposal["proposed_change"]))
            lesson = "Operator: never propose this change again. " + reason.strip()
            experience = {"source_event_type": "harness_experience", "source_event_id": eid,
                "tenant_id": persona.tenant_id, "write_authority": "persona-svc",
                "sponsor_persona_id": persona_id, "contributing_persona_ids": [persona_id],
                "cue": cue, "lesson": lesson, "summary": cue + ": never_again. " + lesson,
                "outcome": "never_again", "confidence": 1.0,
                "evidence_refs": [r["ref"] for r in proposal["evidence_refs"]],
                "harness_version": row.get("version"), "proposal_ids": [patch_id],
                "body": json.dumps({"operator": actor, "reason": reason.strip()}, sort_keys=True),
                "tags": ["harness_experience", "never_again", *sorted(proposal["proposed_change"])]}
            row["operator_feedback"] = experience
            state.setdefault("outbox", {})[eid] = {"kind": "memory", "payload": experience,
                                                   "status": "pending", "attempts": 0}
            self._event(state, "never_again", actor, patch_id=patch_id)
            return state, row
        self._mutate(persona_id, actor, reduce, paper_only=False)
        self.adoption.deliver(persona_id, actor)
        return self.read(persona_id)["patches"][patch_id]

    def _check(self, persona: Any, state: dict, row: dict) -> tuple[HarnessSurface | None, list[str]]:
        patch = PersonaPatch.model_validate(row["proposal"])
        if state["active_version"] != patch.base_version:
            return None, ["stale_base_version"]
        base = HarnessVersion.model_validate(state["versions"][patch.base_version - 1])
        if base.content_digest != digest(base.surface.model_dump(mode="json")):
            return None, ["base_content_digest_mismatch"]
        candidate, reasons = precheck(base.surface, patch, self.script_root)
        for ref in patch.evidence_refs:
            try:
                verified = self.evidence_verifier(persona, ref)
            except Exception:
                verified = False
            if verified is not True:
                reasons.append("trajectory_evidence_unresolved")
        return candidate, sorted(set(reasons))

    def operate(self, persona_id: str, patch_id: str, action: str, actor: str,
                adoption_ref: str | None = None, reason: str | None = None) -> dict[str, Any]:
        if action not in {"precheck", "activate", "rollback"}:
            raise HarnessError("unknown_harness_action")
        def reduce(persona: Any, state: Any) -> tuple[dict, dict]:
            if not state or patch_id not in state["patches"]:
                raise HarnessError("patch_not_found", 404)
            row = state["patches"][patch_id]
            patch = PersonaPatch.model_validate(row["proposal"])
            if action == "rollback":
                if (state["active_version"] != row.get("version")
                        or row["status"] not in {"committed", "observed"}
                        or state["active_experiment"] not in {None, patch_id}):
                    raise HarnessError("patch_is_not_active_experiment", 409)
                state["active_version"] = patch.rollback_target
                state["active_experiment"] = None
                row["status"] = "rolled_back"
                self.adoption.cooldown(state, row)
                self._event(state, "rolled_back", actor, patch_id=patch_id,
                            version=patch.rollback_target, reason=reason)
                return state, row
            if row["status"] in {"committed", "rolled_back", "observed", "governance_required"}:
                raise HarnessError("patch_already_terminal", 409)
            candidate, reasons = self._check(persona, state, row)
            if action == "activate":
                reasons.extend(self.adoption.cooldown_reasons(state, row))
                if state["active_experiment"] is not None:
                    reasons.append("active_experiment_exists")
                if row["status"] != "previewed":
                    reasons.append("precheck_required")
                try:
                    approved = (self.adoption_verifier is not None and candidate is not None
                                and self.adoption_verifier(persona, {
                                    "proposal": row["proposal"],
                                    "candidate_digest": digest(candidate.model_dump(mode="json")),
                                }, adoption_ref) is True)
                except HarnessError as exc:
                    approved = False
                    reasons.append(str(exc))
                    if str(exc) in {"risk_persona_requires_operator", "panel_conditions_require_operator"}:
                        self.adoption.queue_governance(persona, state, row, str(exc))
                except Exception:
                    approved = False
                if not approved:
                    reasons.append("adoption_evidence_unavailable_or_denied")
            reasons = sorted(set(reasons))
            row["checks"].append({"action": action, "passed": not reasons, "reasons": reasons})
            if reasons:
                self._event(state, "refused", actor, patch_id=patch_id, reasons=reasons)
                return state, {"status": "refused", "reasons": reasons, "patch": row}
            if action == "precheck":
                row["status"] = "previewed"
            else:
                version = self._version(persona_id, candidate, len(state["versions"]) + 1,
                                        patch.base_version, patch_id, actor)
                state["versions"].append(version)
                state["active_version"] = version["version"]
                state["active_experiment"] = patch_id
                row.update(status="committed", version=version["version"], adoption_ref=adoption_ref)
                row["adopted_at"] = self.adoption.now().isoformat()
                row["observation_trading_days"] = self.adoption.observation_trading_days
                row["observation_criterion"] = "net_of_costs_total_return"
            self._event(state, action, actor, patch_id=patch_id, version=state["active_version"])
            return state, row
        result = self._mutate(persona_id, actor, reduce)
        if action == "activate":
            self.adoption.deliver(persona_id, actor)
        return result


def env_shadow_provider(persona_id: str) -> Any:
    """Dedicated deny-all replay agent from owner config; None when not configured."""
    import os
    agent = os.getenv("PANTHEON_SHADOW_REPLAY_AGENT_ID", "").strip()
    probe_url = os.getenv("PANTHEON_SHADOW_REPLAY_POLICY_URL", "").strip()
    if not agent or not probe_url:
        return None
    from urllib.parse import quote
    from urllib.request import Request, urlopen
    from integrations.openclaw.persona_ooda_runtime import run_persona_ooda_turn
    from services.persona.harness_shadow_eval import DenyAllReplayProvider

    def probe(agent_id: str) -> dict:
        with urlopen(Request(probe_url.rstrip("/") + "/" + quote(agent_id, safe="")), timeout=10) as response:
            return json.load(response)
    return DenyAllReplayProvider(agent, probe, lambda agent_id, prompt: run_persona_ooda_turn(agent_id, prompt))


def register_harness_routes(app: Any, owner: Any, harness: PersistentHarnessOwner) -> None:
    from fastapi import Header, HTTPException
    from services.persona.write_owner import (
        PersonaAuthorityError, PersonaNotFound, _AUTHENTICATED_MUTATION_ROLES,
        _AUTHENTICATED_READ_ROLES, _require_persona_plane_owner, resolve_persona_tenant_scope,
    )

    def scope(persona_id: str, authorization: str | None, tenant_id: str, write: bool) -> Any:
        try:
            authority, tenant = resolve_persona_tenant_scope(
                authorization, tenant_id,
                _AUTHENTICATED_MUTATION_ROLES if write else _AUTHENTICATED_READ_ROLES)
            if write:
                _require_persona_plane_owner(authority)
            persona = owner.get(persona_id)
            if not persona.tenant_id or persona.tenant_id != tenant:
                raise HTTPException(403, "exact_persona_tenant_required")
            return authority
        except PersonaAuthorityError as exc:
            raise HTTPException(exc.status_code, f"{exc.code}: {exc.message}") from exc
        except PersonaNotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    def call(operation: Callable) -> dict:
        try:
            result = operation()
            if result.get("status") == "refused":
                raise HTTPException(422, result)
            return result
        except HarnessError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @app.get("/api/personas/{persona_id}/harness")
    def read(persona_id: str, authorization: str | None = Header(default=None),
             tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        scope(persona_id, authorization, tenant_id, False)
        return harness.read(persona_id)

    @app.post("/api/personas/{persona_id}/harness", status_code=201)
    def initialize(persona_id: str, body: HarnessSurface, authorization: str | None = Header(default=None),
                   tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        return call(lambda: harness.initialize(persona_id, body, authority.actor_id))

    @app.post("/api/personas/{persona_id}/harness/patches", status_code=201)
    def propose(persona_id: str, body: PersonaPatch, authorization: str | None = Header(default=None),
                tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        return call(lambda: harness.propose(persona_id, body, authority.actor_id))

    @app.post("/api/personas/{persona_id}/harness/patches/{patch_id}/precheck")
    def check(persona_id: str, patch_id: str, authorization: str | None = Header(default=None),
              tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        return call(lambda: harness.operate(persona_id, patch_id, "precheck", authority.actor_id))

    @app.post("/api/personas/{persona_id}/harness/patches/{patch_id}/activate")
    def activate(persona_id: str, patch_id: str, body: ActivatePatch, authorization: str | None = Header(default=None),
                 tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        return call(lambda: harness.operate(persona_id, patch_id, "activate", authority.actor_id, body.adoption_ref))

    @app.post("/api/personas/{persona_id}/harness/rollback")
    def rollback(persona_id: str, body: RollbackPatch, authorization: str | None = Header(default=None),
                 tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        return call(lambda: harness.operate(persona_id, body.patch_id, "rollback", authority.actor_id, reason=body.reason))

    # Same Persona-plane authority and tenant boundary as every harness write.
    # The body supplies references only; facts are resolved from owner packets.
    @app.post("/api/personas/{persona_id}/harness/patches/{patch_id}/observe")
    def observe(persona_id: str, patch_id: str, body: ObservationRequest,
                authorization: str | None = Header(default=None),
                tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        return call(lambda: harness.adoption.observe(persona_id, patch_id, body, authority.actor_id))

    @app.post("/api/personas/{persona_id}/harness/patches/{patch_id}/shadow", status_code=201)
    def shadow(persona_id: str, patch_id: str, authorization: str | None = Header(default=None),
               tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        from services.persona.harness_shadow_eval import run_shadow_comparison

        def run() -> dict:
            store = harness.packet_store_path() if harness.packet_store_path else None
            if store is None:
                from services.persona.cron_ooda_closure import resolve_ooda_packet_store_path
                store = resolve_ooda_packet_store_path()
            return run_shadow_comparison(harness, persona_id, patch_id, packet_store_path=store,
                                         provider=harness.shadow_provider(persona_id), actor=authority.actor_id)
        return call(run)

    @app.post("/api/personas/{persona_id}/harness/patches/{patch_id}/negative-experience", status_code=201)
    def negative_experience(persona_id: str, patch_id: str, body: NegativeExperienceRequest,
                            authorization: str | None = Header(default=None),
                            tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        return call(lambda: harness.record_negative_experience(persona_id, patch_id, body.reason, authority.actor_id))

    @app.post("/api/personas/{persona_id}/harness/deliver")
    def deliver(persona_id: str, authorization: str | None = Header(default=None),
                tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority = scope(persona_id, authorization, tenant_id, True)
        return call(lambda: {"outbox": harness.adoption.deliver(persona_id, authority.actor_id)})

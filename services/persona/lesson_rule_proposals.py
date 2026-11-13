"""Endorsed lessons become typed rule proposals through existing owners only (M6).

Three conversions, each citing the lesson ids and the decision case file:

* ``harness_patch``  -> the existing harness pipeline (paper rules apply).
* ``gate_check``     -> a Governance decision (needs an operator or committee).
* ``pool_policy``    -> a Governance decision (needs an operator or committee).

This module has no model call, no trading, no memory write and no capability to
change execution, risk or capital limits. Unendorsed, expired, foreign-persona
or uncited lessons fail closed with a recorded reason. Agents only propose.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Mapping, Protocol
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

import jsonschema
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from services.persona.harness import EvidenceReference, PersonaPatch

ENDORSED_STATES = frozenset({"endorsed", "merged"})
HARNESS_PATCH_TYPES = ("consult_policy", "threshold")
GOVERNANCE_TARGETS = {"gate_check": "lesson_gate_check_proposal",
                      "pool_policy": "pool_policy_parameter_proposal"}
MAX_LESSONS = 8
# Pool-policy parameters that touch execution, risk or capital are never proposable here.
FORBIDDEN_PARAMETER_MARKERS = ("capital", "risk", "leverage", "limit", "execution", "order", "stop", "kill")
LESSON_MEMORY_PREFIX = "pmem-lesson-"


class LessonRuleError(ValueError):
    """Fail-closed refusal with a recorded machine reason."""

    def __init__(self, reason: str, status_code: int = 422):
        super().__init__(reason)
        self.reason = reason
        self.status_code = status_code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class HarnessPatchSpec(_Strict):
    session_id: str = Field(min_length=1, pattern=r"\S")
    base_version: int = Field(ge=1)
    patch_type: Literal["consult_policy", "threshold"]
    proposed_change: dict[str, Any] = Field(min_length=1)
    rollback_target: int = Field(ge=1)


class GateCheckSpec(_Strict):
    gate: str = Field(min_length=1, pattern=r"\S")
    check_name: str = Field(min_length=1, pattern=r"\S")
    description: str = Field(min_length=1, pattern=r"\S")
    parameters: dict[str, Any] = Field(default_factory=dict)


class PoolPolicySpec(_Strict):
    pool_id: str = Field(min_length=1, pattern=r"\S")
    parameter: str = Field(min_length=1, pattern=r"\S")
    proposed_value: Any
    current_value: Any = None


class LessonRuleRequest(_Strict):
    kind: Literal["harness_patch", "gate_check", "pool_policy"]
    lesson_ids: list[str] = Field(min_length=1, max_length=MAX_LESSONS)
    case_file: dict[str, Any]
    expected_effect: str = Field(min_length=1, pattern=r"\S")
    harness_patch: HarnessPatchSpec | None = None
    gate_check: GateCheckSpec | None = None
    pool_policy: PoolPolicySpec | None = None

    @model_validator(mode="after")
    def _exactly_the_declared_spec(self) -> "LessonRuleRequest":
        specs = {"harness_patch": self.harness_patch, "gate_check": self.gate_check,
                 "pool_policy": self.pool_policy}
        if specs[self.kind] is None or any(v is not None for k, v in specs.items() if k != self.kind):
            raise ValueError("exactly the spec named by kind is required")
        if len(set(self.lesson_ids)) != len(self.lesson_ids):
            raise ValueError("lesson_ids must be distinct")
        return self


class LessonReader(Protocol):
    def get(self, lesson_id: str, tenant_id: str, authorization: str | None) -> dict[str, Any] | None: ...


class MemoryLessonReader:
    """Reads one lesson candidate from the Memory owner with the caller's own authorization."""

    def __init__(self, base_url: str | None = None, urlopen_fn: Callable = urlopen):
        self.base_url = (base_url or os.getenv("PANTHEON_MEMORY_API_URL") or os.getenv("MEMORY_API_URL") or "").rstrip("/")
        self._urlopen = urlopen_fn

    def get(self, lesson_id: str, tenant_id: str, authorization: str | None) -> dict[str, Any] | None:
        if not self.base_url or not authorization:
            raise LessonRuleError("lesson_source_unavailable", 503)
        request = Request(f"{self.base_url}/api/memory/trade-lessons/{quote(lesson_id, safe='')}",
                          headers={"Authorization": authorization, "X-Tenant-Id": tenant_id})
        try:
            with self._urlopen(request, timeout=10) as response:
                return json.load(response)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise LessonRuleError("lesson_source_unavailable", 503) from exc
        except Exception as exc:
            raise LessonRuleError("lesson_source_unavailable", 503) from exc


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _case_file_errors(case_file: Mapping[str, Any], persona_id: str, tenant_id: str,
                      lesson_ids: list[str]) -> list[str]:
    from services.knowledge import case_file as cf
    from pathlib import Path
    schema = json.loads((Path(cf.__file__).parent / "case_file.schema.json").read_text(encoding="utf-8"))
    try:
        jsonschema.validate(case_file, schema)
    except jsonschema.ValidationError:
        return ["case_file_schema_invalid"]
    body = {k: v for k, v in case_file.items() if k not in ("case_file_id", "digest")}
    digest = cf._digest(body)
    if case_file["digest"] != digest or case_file["case_file_id"] != f"casefile-{digest[:24]}":
        return ["case_file_digest_mismatch"]
    subject = case_file["subject"]
    if subject["persona_id"] != persona_id or subject["tenant_id"] != tenant_id:
        return ["case_file_subject_mismatch"]
    cited = {str(item.get("source_id")) for item in case_file["groups"]["lessons"]["items"]}
    return [f"lesson_not_in_case_file:{lid}" for lid in lesson_ids if LESSON_MEMORY_PREFIX + lid not in cited]


def _lesson_errors(lesson: dict[str, Any] | None, lesson_id: str, persona_id: str, tenant_id: str,
                   now: datetime) -> list[str]:
    if not lesson:
        return [f"lesson_not_found:{lesson_id}"]
    errors = []
    if lesson.get("lesson_candidate_id") != lesson_id:
        errors.append(f"lesson_identity_mismatch:{lesson_id}")
    if lesson.get("review_state") not in ENDORSED_STATES:
        errors.append(f"lesson_not_endorsed:{lesson_id}")
    if lesson.get("persona_id") != persona_id:
        errors.append(f"lesson_belongs_to_another_persona:{lesson_id}")
    if lesson.get("tenant_id") != tenant_id:
        errors.append(f"lesson_tenant_mismatch:{lesson_id}")
    try:
        if datetime.fromisoformat(str(lesson["expiry"]).replace("Z", "+00:00")) <= now:
            errors.append(f"lesson_expired:{lesson_id}")
    except (KeyError, ValueError):
        errors.append(f"lesson_expiry_unreadable:{lesson_id}")
    return errors


def _spec_errors(request: LessonRuleRequest) -> list[str]:
    if request.kind == "pool_policy":
        parameter = request.pool_policy.parameter.lower()
        if any(marker in parameter for marker in FORBIDDEN_PARAMETER_MARKERS):
            return ["pool_parameter_touches_execution_risk_or_capital"]
    return []


class GovernanceSender(Protocol):
    def __call__(self, payload: dict[str, Any]) -> dict[str, Any]: ...


def http_governance_sender(payload: dict[str, Any]) -> dict[str, Any]:
    """Create (or read back) the pending Governance decision. Propose-only principal; no decide role."""
    from services.service_token_file import configured_service_token
    base = (os.getenv("PANTHEON_GOVERNANCE_API_URL") or os.getenv("PANTHEON_GOVERNANCE_SERVICE_URL") or "").rstrip("/")
    try:
        token = configured_service_token("LESSON_RULE_PROPOSER_TOKEN")
    except RuntimeError:
        token = ""
    if not base or not token:
        raise LessonRuleError("governance_unavailable", 503)
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}",
               "Idempotency-Key": payload["decision_id"]}
    url = base + "/api/governance/approvals"
    try:
        try:
            with urlopen(Request(url, data=json.dumps(payload).encode(), headers=headers), timeout=10) as r:
                return json.load(r)
        except HTTPError as exc:
            if exc.code != 409:
                raise
            with urlopen(Request(url + "/" + quote(payload["decision_id"], safe=""), headers=headers), timeout=10) as r:
                return json.load(r)
    except LessonRuleError:
        raise
    except Exception as exc:
        raise LessonRuleError("governance_unavailable", 503) from exc


class LessonRuleConverter:
    def __init__(self, records: Any, harness: Any, *, reader: LessonReader | None = None,
                 governance_sender: GovernanceSender | None = None,
                 proposer_id: str | None = None,
                 now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.records, self.harness = records, harness
        self.reader = reader or MemoryLessonReader()
        self.send = governance_sender or http_governance_sender
        self.proposer_id = proposer_id or os.getenv("PANTHEON_LESSON_RULE_PROPOSER_ID") or "persona-svc"
        self.now = now
        harness.evidence_verifier = self.evidence_verifier(harness.evidence_verifier)

    # -- evidence resolution for the harness pipeline ------------------------
    def evidence_verifier(self, base: Callable[[Any, EvidenceReference], bool]) -> Callable:
        """A ``lesson`` evidence ref resolves only to an owner-recorded, validated conversion."""
        def verify(persona: Any, ref: EvidenceReference) -> bool:
            if ref.kind != "lesson":
                return base(persona, ref)
            return any(r.get("kind") == "harness_patch" and r.get("persona_id") == persona.persona_id
                       and r.get("tenant_id") == persona.tenant_id and ref.ref in r["lesson_ids"]
                       and r.get("case_file_id") == ref.moment for r in self.records.list_all())
        return verify

    def list(self, persona_id: str, tenant_id: str) -> list[dict[str, Any]]:
        return [r for r in self.records.list_all()
                if r.get("persona_id") == persona_id and r.get("tenant_id") == tenant_id]

    def _validate(self, persona_id: str, tenant_id: str, request: LessonRuleRequest,
                  authorization: str | None) -> None:
        errors = _spec_errors(request)
        now = self.now()
        for lesson_id in request.lesson_ids:
            errors += _lesson_errors(self.reader.get(lesson_id, tenant_id, authorization),
                                     lesson_id, persona_id, tenant_id, now)
        errors += _case_file_errors(request.case_file, persona_id, tenant_id, request.lesson_ids)
        if errors:
            raise LessonRuleError(";".join(sorted(set(errors))))

    def convert(self, persona_id: str, tenant_id: str, request: LessonRuleRequest, actor: str,
                authorization: str | None) -> dict[str, Any]:
        self._validate(persona_id, tenant_id, request, authorization)
        spec = getattr(request, request.kind).model_dump(mode="json")
        lesson_ids = sorted(request.lesson_ids)
        content = {"kind": request.kind, "persona_id": persona_id, "tenant_id": tenant_id,
                   "lesson_ids": lesson_ids, "case_file_id": request.case_file["case_file_id"],
                   "spec": spec, "expected_effect": request.expected_effect}
        proposal_digest = _digest(content)
        proposal_id = "lrp-" + proposal_digest[:32]
        record = self.records.get(proposal_id)
        if record is None:
            record = {"proposal_id": proposal_id, **content, "proposal_digest": proposal_digest,
                      "status": "recorded", "created_by": actor, "created_at": self.now().isoformat(),
                      "changes_execution_or_limits": False}
            committed, current = self.records.compare_and_set(proposal_id, None, record)
            record = record if committed else (current or self.records.get(proposal_id))
        if record.get("status") in ("pending_governance_decision", "harness_proposed"):
            return record
        return self._deliver(record, request, actor)

    def _save(self, record: dict[str, Any], **changes: Any) -> dict[str, Any]:
        updated = {**record, **changes}
        committed, current = self.records.compare_and_set(record["proposal_id"], record, updated)
        return updated if committed else (current or self.records.get(record["proposal_id"]))

    def _deliver(self, record: dict[str, Any], request: LessonRuleRequest, actor: str) -> dict[str, Any]:
        if record["kind"] == "harness_patch":
            spec = request.harness_patch
            try:
                patch = PersonaPatch(
                    patch_id="lrp-patch-" + record["proposal_id"][4:20], persona_id=record["persona_id"],
                    session_id=spec.session_id, base_version=spec.base_version, patch_type=spec.patch_type,
                    proposed_change=spec.proposed_change, expected_effect=request.expected_effect,
                    rollback_target=spec.rollback_target,
                    evidence_refs=[EvidenceReference(kind="lesson", ref=lid, moment=record["case_file_id"])
                                   for lid in record["lesson_ids"]])
                row = self.harness.propose(record["persona_id"], patch, actor)
            except ValidationError as exc:
                raise LessonRuleError("harness_patch_invalid") from exc
            except ValueError as exc:  # HarnessError
                raise LessonRuleError(str(exc), getattr(exc, "status_code", 422)) from exc
            return self._save(record, status="harness_proposed", patch_id=patch.patch_id,
                              harness_status=row["status"],
                              governance_decision_id=row.get("governance_proposal_ref"))
        spec = record["spec"]
        subject_target = spec["gate"] if record["kind"] == "gate_check" else spec["pool_id"]
        payload = {
            "decision_id": "lessonrule-" + record["proposal_id"][4:],
            "expected_version": 0, "tenant_id": record["tenant_id"], "owner_user_id": self.proposer_id,
            "target_type": GOVERNANCE_TARGETS[record["kind"]], "target_id": subject_target,
            "target_version": record["proposal_digest"][:16], "risk_level": "high",
            "persona_id": record["persona_id"], "proposal_id": record["proposal_id"],
            "proposal_content_digest": record["proposal_digest"],
            "subject": {"proposal_kind": record["kind"], "lesson_ids": ",".join(record["lesson_ids"]),
                        "case_file_id": record["case_file_id"], "proposal_digest": record["proposal_digest"],
                        "change": _canonical(spec), "expected_effect": record["expected_effect"]},
        }
        try:
            receipt = self.send(payload)
        except LessonRuleError as exc:
            self._save(record, status="governance_delivery_pending", error=exc.reason)
            raise
        if (receipt.get("decision_id") != payload["decision_id"] or receipt.get("tenant_id") != record["tenant_id"]
                or receipt.get("target_id") != subject_target
                or receipt.get("target_type") != payload["target_type"]):
            self._save(record, status="governance_delivery_pending", error="governance_receipt_binding_mismatch")
            raise LessonRuleError("governance_receipt_binding_mismatch", 502)
        return self._save(record, status="pending_governance_decision", error=None,
                          governance_decision_id=payload["decision_id"],
                          governance_decision_state=receipt.get("decision_state"))


def build_lesson_rule_converter(harness: Any) -> LessonRuleConverter:
    """Production wiring: durable owner store beside the persona store; lesson evidence resolves through it."""
    from services.foundation.reliable_delivery import build_record_store
    records = build_record_store(
        backend=os.getenv("PERSONA_LESSON_RULE_STORE_BACKEND", os.getenv("PERSONA_STORE_BACKEND", "json")),
        dsn=os.getenv("PERSONA_STORE_DSN") or os.getenv("DATABASE_URL"),
        table_name=os.getenv("PERSONA_LESSON_RULE_STORE_TABLE", "persona.lesson_rule_proposals"),
        json_path=os.getenv("PERSONA_LESSON_RULE_STORE_PATH", "/tmp/pantheon/persona/lesson_rule_proposals.json"),
        owner_service="persona-svc")
    converter = LessonRuleConverter(records, harness)
    return converter


def register_lesson_rule_routes(app: Any, owner: Any, converter: LessonRuleConverter) -> None:
    from fastapi import Header, HTTPException
    from services.persona.write_owner import (
        PersonaAuthorityError, PersonaNotFound, _AUTHENTICATED_MUTATION_ROLES,
        _AUTHENTICATED_READ_ROLES, _require_persona_plane_owner, resolve_persona_tenant_scope,
    )

    def scope(persona_id: str, authorization: str | None, tenant_id: str, write: bool) -> Any:
        try:
            authority, tenant = resolve_persona_tenant_scope(
                authorization, tenant_id, _AUTHENTICATED_MUTATION_ROLES if write else _AUTHENTICATED_READ_ROLES)
            if write:
                _require_persona_plane_owner(authority)
            persona = owner.get(persona_id)
            if not persona.tenant_id or persona.tenant_id != tenant:
                raise HTTPException(403, "exact_persona_tenant_required")
            return authority, tenant
        except PersonaAuthorityError as exc:
            raise HTTPException(exc.status_code, f"{exc.code}: {exc.message}") from exc
        except PersonaNotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/api/personas/{persona_id}/lesson-rule-proposals", status_code=201)
    def convert(persona_id: str, body: LessonRuleRequest, authorization: str | None = Header(default=None),
                tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        authority, tenant = scope(persona_id, authorization, tenant_id, True)
        try:
            return converter.convert(persona_id, tenant, body, authority.actor_id, authorization)
        except LessonRuleError as exc:
            raise HTTPException(exc.status_code, {"reason": exc.reason}) from exc

    @app.get("/api/personas/{persona_id}/lesson-rule-proposals")
    def list_proposals(persona_id: str, authorization: str | None = Header(default=None),
                       tenant_id: str = Header(alias="X-Tenant-Id")) -> dict:
        _, tenant = scope(persona_id, authorization, tenant_id, False)
        return {"proposals": converter.list(persona_id, tenant)}

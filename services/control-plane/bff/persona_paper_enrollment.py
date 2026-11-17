"""Enrol a paper-provisioned persona for its daily cycle.

Paper provisioning leaves the persona owner record at ``research_only`` with no
strategy pool and no harness, so the per-persona cycle never runs (the cycle
registrar and every paper consumer require ``paper_owner``).  This module closes
that gap using only existing owner routes:

1. each lifecycle step (``research_only -> consultable -> paper_owner``) is a
   real Governance ``persona_lifecycle_transition`` decision made by the paper
   automated gate identity, then the Persona owner's governed lifecycle route
   (which re-verifies the decision itself);
2. pool version 1 through the owner's seed route (it reads the approved paper
   artifact from owner records and fails closed on zero/several artifacts or a
   missing gate baseline);
3. harness version 1 through the owner's initialise route, rendered from the
   persona owner record and the existing governed consult policy / tool profile.

Nothing here writes the lifecycle field, a pool or a harness directly, and it
invents no guardrail.  A persona without an active paper capital binding and an
active paper runtime binding is never advanced.  Every step is GET-first and
idempotent, so restarts and concurrent replicas converge.
"""
from __future__ import annotations

import hashlib
import json
import re
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote

# Persona-owner lifecycle path to the state every cycle consumer requires.
LIFECYCLE_PATH = ("research_only", "consultable", "paper_owner")
ENROLLED_STATE = "paper_owner"
APPROVAL_TTL = timedelta(hours=24)
TARGET_TYPE = "persona_lifecycle_transition"

# Coordinator starting values for harness version 1 fields the design leaves
# numeric (SD_PERSONA_CYCLE_RECONNECT section 6.8).  They are permissive
# cycle parameters, not guardrails; the reviewer confirms them.  Daily cadence
# follows the design (R1: one turn per persona per day) and the steps are the
# section 6.1 stages.
DAILY_CADENCE_SECONDS = 86400
CYCLE_STEPS = ("observe", "orient", "decide", "act", "learn")
MINIMUM_EVIDENCE_COUNT = 3
MINIMUM_CONFIDENCE = 0.6
RETRIEVAL_SCOPES = ("own_private", "institutional")
RETRIEVAL_MAX_ITEMS = 10

NON_TRADING_CLASSES = frozenset({"consult_panel", "probe"})


def is_probe_or_test_persona(persona: Mapping[str, Any]) -> bool:
    """Release probe, test and acceptance personas are never enrolled.

    Same classification the persona evaluator uses to exclude them from
    promotion (``persona_evaluator_agent._probe_persona``).  The evaluator
    ships as its own container so the rule is mirrored here and a parity test
    pins both to the same answers.
    """

    metadata = persona.get("metadata") if isinstance(persona.get("metadata"), Mapping) else {}
    text = json.dumps({k: persona.get(k) for k in ("persona_id", "id", "name", "mandate")}).lower()
    text += " ".join(str(v).lower() for v in metadata.values() if isinstance(v, str))
    if any(
        str(source.get(k) or "").lower() in {"test", "acceptance", "fixture", "synthetic"}
        for source in (persona, metadata)
        for k in ("data_scope", "dataScope")
    ):
        return True
    if any(
        str(source.get(k) or "").lower() in {"true", "1", "yes"}
        for source in (persona, metadata)
        for k in ("is_test", "test_data", "acceptance_run", "is_acceptance")
    ):
        return True
    return ("probe" in text and any(word in text for word in ("release", "acceptance"))) or any(
        metadata.get(k) is True for k in ("is_probe", "release_probe", "acceptance_probe", "is_test")
    )


class EnrollmentRefused(Exception):
    """Enrolment cannot proceed; ``reason`` is the readable readback reason."""

    def __init__(self, reason: str, *, retryable: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retryable = retryable


class OwnerTransport(Protocol):
    def get(self, owner: str, path: str) -> Mapping[str, Any] | None: ...

    def post(self, owner: str, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass
class EnrollmentOutcome:
    """Readback of one enrolment attempt (stored on the provisioning record)."""

    persona_id: str
    status: str  # enrolled | not_eligible | refused | unavailable
    reason: str | None = None
    lifecycle_state: str | None = None
    steps: dict[str, Any] = field(default_factory=dict)
    attempted_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "persona_id": self.persona_id,
            "status": self.status,
            "reason": self.reason,
            "lifecycle_state": self.lifecycle_state,
            "steps": dict(self.steps),
            "attempted_at": self.attempted_at,
        }


class HttpGovernanceProposer:
    """Proposer-side Governance transport under its own verified principal.

    Governance refuses an action-target decision (``persona_lifecycle_transition``)
    whose decider is its proposer, so the proposal cannot be made by the paper
    automated gate identity.  This transport presents a separate service
    principal (``PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN`` with subject
    ``PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID``); with either missing the
    enrolment fails closed with a readable reason.  It only ever POSTs a
    proposal; it never reviews or decides.
    """

    URL_ENVS = ("PANTHEON_GOVERNANCE_APPROVAL_API_URL", "PANTHEON_GOVERNANCE_SERVICE_URL")

    def __init__(self, *, tenant_id: str, opener: Any = None, timeout_seconds: float = 30.0) -> None:
        self.tenant_id = tenant_id
        self._opener = opener or urllib.request.urlopen
        self._timeout = timeout_seconds

    @staticmethod
    def actor_id() -> str:
        return _text(os.getenv("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_ID"))

    def _base(self) -> str:
        for name in self.URL_ENVS:
            value = _text(os.getenv(name)).rstrip("/")
            if value:
                return value
        raise EnrollmentRefused("governance_url_unconfigured")

    def _token(self) -> str:
        from services.service_token_file import configured_service_token

        token = configured_service_token("PANTHEON_PERSONA_ENROLLMENT_PROPOSER_TOKEN")
        if not token or not self.actor_id():
            raise EnrollmentRefused("governance_proposer_identity_unconfigured")
        return token

    def get(self, owner: str, path: str) -> Mapping[str, Any] | None:
        raise EnrollmentRefused("governance_proposer_is_write_only")

    def post(self, owner: str, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if owner != "governance" or path != "/api/governance/approvals":
            raise EnrollmentRefused("governance_proposer_may_only_propose")
        request = urllib.request.Request(
            self._base() + path,
            data=json.dumps(dict(payload)).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token()}",
                "X-Tenant-Id": self.tenant_id,
                "Idempotency-Key": "persona-enroll-" + _digest(
                    self.tenant_id, json.dumps(dict(payload), sort_keys=True)
                ),
            },
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise EnrollmentRefused(f"governance_proposal_http_{exc.code}", retryable=exc.code >= 500) from exc
        return value if isinstance(value, dict) else {}


def _digest(*parts: str) -> str:
    return hashlib.sha256(json.dumps(list(parts)).encode("utf-8")).hexdigest()[:24]


def lifecycle_decision_id(tenant_id: str, persona_id: str, source: str, target: str) -> str:
    """Deterministic Governance decision id for one exact lifecycle step."""

    return f"apv-persona-enroll-{_digest(tenant_id, persona_id, source, target)}"


_CREDENTIAL_RE = re.compile(
    r"(?i)(bearer\s+[\w.\-~+/=]+|(?:token|secret|password|authorization|api[_-]?key)[\"']?\s*[:=]\s*[\"']?[^\s,;\"'}]+)"
)
_STATUS_RE = re.compile(r"(?<!\d)([45]\d\d)(?!\d)")


def _error_status(exc: BaseException) -> int | None:
    for name in ("code", "status_code", "status"):
        value = getattr(exc, name, None)
        if isinstance(value, int) and 400 <= value <= 599:
            return value
    match = _STATUS_RE.search(str(exc))
    return int(match.group(1)) if match else None


def _error_detail(exc: BaseException, limit: int = 160) -> str:
    detail = ""
    reader = getattr(exc, "read", None)
    if callable(reader):
        try:
            raw = reader()
            detail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        except Exception:  # noqa: BLE001
            detail = ""
    detail = detail or str(exc)
    detail = _CREDENTIAL_RE.sub("[redacted]", " ".join(detail.split()))
    return detail[:limit]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def render_harness_surface_v1(persona: Mapping[str, Any]) -> dict[str, Any]:
    """Harness version 1 content, rendered from existing governed sources only.

    Identity comes from the persona owner record; the tool profile is the
    existing read-only-plus-proposal profile; forbidden solo actions and the
    consult policy are the governed v1 consult policy (decision types that need
    a risk-persona consultation).  Missing identity data fails closed.
    """

    from integrations.openclaw.persona_agent_sync import (
        PERSONA_AGENT_TOOL_PROFILE,
        assert_persona_agent_tool_profile,
    )
    from services.persona.consult_triggers import GATED_DECISION_TYPES, ROLE_RISK

    mandate = _text(persona.get("mandate"))
    family = _text(persona.get("strategy_family"))
    if not mandate:
        raise EnrollmentRefused("harness_source_missing:mandate")
    if not family:
        raise EnrollmentRefused("harness_source_missing:strategy_family")
    try:
        assert_persona_agent_tool_profile(PERSONA_AGENT_TOOL_PROFILE)
    except ValueError as exc:
        raise EnrollmentRefused(f"harness_source_invalid:tool_profile:{exc}") from exc
    identity = [f"Mandate: {mandate}", f"Strategy family: {family}"]
    for key, label in (("route_policy_id", "Route policy"), ("consult_policy_id", "Consult policy")):
        if _text(persona.get(key)):
            identity.append(f"{label}: {_text(persona.get(key))}")
    return {
        "identity_inputs": identity,
        "guardrails": {
            "forbidden_solo_actions": sorted(GATED_DECISION_TYPES),
            "allowed_tools": [str(t) for t in PERSONA_AGENT_TOOL_PROFILE["allow"]],
        },
        "consult_policy": [
            {"action": action, "required_roles": [ROLE_RISK]}
            for action in sorted(GATED_DECISION_TYPES)
        ],
        "tool_guidance": {},
        "skills": [],
        "workflow": {"cadence_seconds": DAILY_CADENCE_SECONDS, "steps": list(CYCLE_STEPS)},
        "cycle_thresholds": {
            "minimum_evidence_count": MINIMUM_EVIDENCE_COUNT,
            "minimum_confidence": float(MINIMUM_CONFIDENCE),
        },
        "memory_retrieval_policy": {
            "scopes": list(RETRIEVAL_SCOPES),
            "max_items": RETRIEVAL_MAX_ITEMS,
            "minimum_confidence": float(MINIMUM_CONFIDENCE),
        },
    }


PUBLISH_SETTLED = frozenset({"published", "current", "deferred", "skipped"})
ENVELOPE_ARTIFACT_PREFIX = "artifact-persona-pool-"


def enrollment_settled(existing: Any) -> bool:
    """True when an enrolled readback needs no further reconcile pass.

    Enrolled readbacks written before the first-publish step carry no ``publish`` step and are
    therefore unsettled once, so the reconciler publishes the already-enrolled personas.
    """

    if not isinstance(existing, Mapping) or existing.get("status") != "enrolled":
        return False
    steps = existing.get("steps") if isinstance(existing.get("steps"), Mapping) else {}
    publish = steps.get("publish") if isinstance(steps.get("publish"), Mapping) else {}
    return _text(publish.get("status")) in PUBLISH_SETTLED


def retry_due(existing: Any, now: datetime, min_interval_seconds: int) -> bool:
    """Whether a refused/unavailable enrolment may be attempted again.

    ``min_interval_seconds`` between attempts bounds the attempts per hour per
    persona (3600 / interval).  Enrolled and never-attempted records are not
    throttled here; an unparsable timestamp is treated as due.  An enrolled record is only
    ever re-attempted while its first pool publish is unsettled (see ``enrollment_settled``),
    and that retry is throttled the same way.
    """

    if not isinstance(existing, Mapping) or existing.get("status") not in {
        "refused", "unavailable", "enrolled"
    }:
        return True
    raw = _text(existing.get("attempted_at"))
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return True
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return now - last >= timedelta(seconds=max(0, min_interval_seconds))


class PersonaPaperEnrollment:
    """GET-first, idempotent enrolment of one paper-provisioned persona."""

    def __init__(
        self,
        *,
        tenant_id: str,
        persona_id: str,
        capital_pool_id: str,
        persona_capital_binding_id: str,
        deployment_plan_id: str,
        persona_owner: Any,
        transport: OwnerTransport,
        runtime_binding_reader: Callable[[str, str], "list[Mapping[str, Any]]"],
        governance_actor_id: str,
        proposer: OwnerTransport | None = None,
        proposer_actor_id: str | None = None,
        now: Callable[[], datetime] | None = None,
        pool_publisher: Callable[[int], Mapping[str, Any]] | None = None,
    ) -> None:
        self.pool_publisher = pool_publisher
        self.tenant_id = tenant_id
        self.persona_id = persona_id
        self.capital_pool_id = capital_pool_id
        self.persona_capital_binding_id = persona_capital_binding_id
        self.deployment_plan_id = deployment_plan_id
        self.persona_owner = persona_owner
        self.transport = transport
        self.runtime_binding_reader = runtime_binding_reader
        self.governance_actor_id = governance_actor_id
        self.proposer = proposer
        self.proposer_actor_id = _text(proposer_actor_id)
        self._now = now or (lambda: datetime.now(timezone.utc))

    # -- entry --------------------------------------------------------------
    def enroll(self) -> EnrollmentOutcome:
        outcome = EnrollmentOutcome(
            persona_id=self.persona_id, status="refused", attempted_at=_utc(self._now())
        )
        try:
            persona = self._eligible_persona(outcome)
            if persona is None:
                return outcome
            self._ensure_pool(outcome)
            persona = self._advance_lifecycle(persona, outcome)
            self._ensure_harness(persona, outcome)
            outcome.status = "enrolled"
            outcome.lifecycle_state = ENROLLED_STATE
            outcome.reason = None
            # Last, and never able to change the enrolled state: a failure is only a readback.
            self._ensure_first_publish(outcome)
        except EnrollmentRefused as exc:
            outcome.status = "unavailable" if exc.retryable else "refused"
            outcome.reason = exc.reason
        except Exception as exc:  # noqa: BLE001 - owners are injected; never fail paper
            outcome.status = "unavailable"
            outcome.reason = f"{exc.__class__.__name__}: {exc}"[:300]
        return outcome

    # -- eligibility ----------------------------------------------------------
    def _eligible_persona(self, outcome: EnrollmentOutcome) -> Mapping[str, Any] | None:
        persona = self.persona_owner.get_persona(self.persona_id)
        if persona is None:
            return self._not_eligible(outcome, "persona_owner_record_missing")
        outcome.lifecycle_state = _text(persona.get("lifecycle_state")) or None
        metadata = persona.get("metadata") if isinstance(persona.get("metadata"), Mapping) else {}
        if _text(persona.get("tenant_id") or metadata.get("tenant_id")) != self.tenant_id:
            return self._not_eligible(outcome, "persona_tenant_mismatch")
        if _text(persona.get("status") or "active") != "active":
            return self._not_eligible(outcome, "persona_not_active")
        if (
            metadata.get("trading") is False
            or _text(metadata.get("persona_class")) in NON_TRADING_CLASSES
            or metadata.get("probe")
        ):
            return self._not_eligible(outcome, "persona_not_a_paper_trading_persona")
        if is_probe_or_test_persona(persona):
            return self._not_eligible(outcome, "release_probe_or_test_persona_not_enrolled")
        if outcome.lifecycle_state not in LIFECYCLE_PATH:
            return self._not_eligible(outcome, f"owner_lifecycle_not_enrollable:{outcome.lifecycle_state}")
        # Already paper_owner: the runtime may legitimately be paused (after the close), so
        # the active-binding gate only guards the first lifecycle advance.
        if outcome.lifecycle_state != ENROLLED_STATE:
            reason = self._paper_binding_gap()
            if reason:
                return self._not_eligible(outcome, reason)
        return persona

    @staticmethod
    def _not_eligible(outcome: EnrollmentOutcome, reason: str) -> None:
        outcome.status = "not_eligible"
        outcome.reason = reason
        return None

    def _paper_binding_gap(self) -> str | None:
        """None only with an active paper capital binding and exactly one active paper runtime."""

        binding = self.transport.get("capital", f"/api/bindings/{quote(self.persona_capital_binding_id, safe='')}")
        if binding is None:
            return "paper_capital_binding_missing"
        if (
            _text(binding.get("status")) != "active"
            or _text(binding.get("persona_id")) != self.persona_id
            or _text(binding.get("capital_pool_id")) != self.capital_pool_id
            or _text(binding.get("role")) != "paper_owner"
            or _text(binding.get("allowed_deployment_scope")) != "paper"
        ):
            return "paper_capital_binding_not_active"
        active = [
            b
            for b in self.runtime_binding_reader(self.deployment_plan_id, self.capital_pool_id) or []
            if _text(b.get("state") or b.get("status")).lower() in {"active", "running", "ok"}
        ]
        if len(active) != 1:
            return "paper_runtime_binding_not_active"
        runtime = active[0]
        meta = runtime.get("metadata") if isinstance(runtime.get("metadata"), Mapping) else {}
        if not (
            _text(runtime.get("deployment_mode") or runtime.get("deployment_stage")) == "paper"
            and (
                _text(runtime.get("plan_id")) == self.deployment_plan_id
                # The first pool publish replaces the binding under its own plan.
                or _text(runtime.get("artifact_id")).startswith(ENVELOPE_ARTIFACT_PREFIX)
            )
            and _text(runtime.get("capital_pool_id")) == self.capital_pool_id
            and _text(runtime.get("persona_capital_binding_id")) == self.persona_capital_binding_id
            and _text(meta.get("persona_id")) == self.persona_id
            and _text(meta.get("tenant_id")) == self.tenant_id
        ):
            return "paper_runtime_binding_identity_mismatch"
        return None

    # -- pool / harness ---------------------------------------------------
    def _ensure_pool(self, outcome: EnrollmentOutcome) -> None:
        existing = self.persona_owner.get_strategy_pool(self.persona_id, tenant_id=self.tenant_id)
        if existing is not None:
            outcome.steps["pool"] = {"status": "present", "version": (existing.get("pool_version") or existing.get("version"))}
            return
        try:
            seeded = self.persona_owner.seed_strategy_pool(self.persona_id, tenant_id=self.tenant_id)
        except Exception as exc:  # noqa: BLE001
            # A concurrent replica may have seeded between the read and write.
            existing = self.persona_owner.get_strategy_pool(self.persona_id, tenant_id=self.tenant_id)
            if existing is None:
                raise EnrollmentRefused(f"pool_seed_refused:{exc}"[:300]) from exc
            seeded = existing
        outcome.steps["pool"] = {"status": "seeded", "version": (seeded.get("pool_version") or seeded.get("version"))}

    def _ensure_first_publish(self, outcome: EnrollmentOutcome) -> None:
        """Bind the running paper runtime to the seeded pool v1 envelope (read-before-write).

        The seeded v1 has one member, the artifact already trading, so this is not a membership
        change and needs no risk-panel memo; later versions stay with the pool cycle.
        """

        if self.pool_publisher is None:
            outcome.steps["publish"] = {"status": "skipped", "reason": "pool_publisher_unconfigured"}
            return
        try:
            active = [
                b
                for b in self.runtime_binding_reader(self.deployment_plan_id, self.capital_pool_id) or []
                if _text(b.get("state") or b.get("status")).lower() in {"active", "running", "ok"}
            ]
            if not active:
                # Paused/stopped runtime: wait (unsettled) and publish once after it is active.
                outcome.steps["publish"] = {
                    "status": "waiting",
                    "reason": "paper_runtime_binding_not_active",
                    "attempted_at": outcome.attempted_at,
                }
                return
            if len(active) == 1 and _text(active[0].get("artifact_id")).startswith(
                ENVELOPE_ARTIFACT_PREFIX
            ):
                outcome.steps["publish"] = {
                    "status": "current",
                    "artifact_id": _text(active[0].get("artifact_id")),
                }
                return
            pool = self.persona_owner.get_strategy_pool(self.persona_id, tenant_id=self.tenant_id) or {}
            version = int(pool.get("pool_version") or pool.get("version") or 0)
            if version != 1:
                outcome.steps["publish"] = {
                    "status": "deferred",
                    "reason": f"pool_advanced_past_seed:v{version}",
                }
                return
            receipt = self.pool_publisher(version)
            status = _text(receipt.get("status"))
            step = {
                "status": "published" if status == "published" else "dispatched",
                "pool_version": version,
                "envelope_artifact_id": receipt.get("envelope_artifact_id"),
                "attempted_at": outcome.attempted_at,
            }
            if status != "published":
                step["reason"] = _text(receipt.get("note")) or "binding_move_unconfirmed"
                outcome.reason = f"pool_publish_unconfirmed:{step['reason']}"[:300]
            outcome.steps["publish"] = step
        except Exception as exc:  # noqa: BLE001 - paper keeps trading on the previous artifact
            reason = _text(getattr(exc, "reason", None)) or f"{exc.__class__.__name__}: {exc}"
            outcome.steps["publish"] = {
                "status": "failed",
                "reason": reason[:300],
                "attempted_at": outcome.attempted_at,
            }
            outcome.reason = f"pool_publish_failed:{reason}"[:300]

    def _ensure_harness(self, persona: Mapping[str, Any], outcome: EnrollmentOutcome) -> None:
        existing = self.persona_owner.get_harness(self.persona_id, tenant_id=self.tenant_id)
        if existing is not None:
            outcome.steps["harness"] = {"status": "present", "active_version": existing.get("active_version")}
            return
        current = self.persona_owner.get_persona(self.persona_id) or persona
        surface = render_harness_surface_v1(current)
        try:
            initialised = self.persona_owner.initialize_harness(
                self.persona_id, surface, tenant_id=self.tenant_id
            )
        except Exception as exc:  # noqa: BLE001
            existing = self.persona_owner.get_harness(self.persona_id, tenant_id=self.tenant_id)
            if existing is None:
                raise EnrollmentRefused(f"harness_initialise_refused:{exc}"[:300]) from exc
            initialised = existing
        outcome.steps["harness"] = {
            "status": "initialised",
            "active_version": initialised.get("active_version"),
        }

    # -- governed lifecycle ------------------------------------------------
    def _advance_lifecycle(self, persona: Mapping[str, Any], outcome: EnrollmentOutcome) -> Mapping[str, Any]:
        state = _text(persona.get("lifecycle_state"))
        steps: list[dict[str, Any]] = []
        outcome.steps["lifecycle"] = steps
        while state != ENROLLED_STATE:
            target = LIFECYCLE_PATH[LIFECYCLE_PATH.index(state) + 1]
            decision_id = self._governance_decision(state, target)
            try:
                self.persona_owner.advance_lifecycle_governed(
                    self.persona_id,
                    actor_id=self.governance_actor_id,
                    target_state=target,
                    governance_decision_id=decision_id,
                    tenant_id=self.tenant_id,
                )
            except Exception as exc:  # noqa: BLE001
                readback = self.persona_owner.get_persona(self.persona_id) or {}
                if _text(readback.get("lifecycle_state")) != target:
                    # Refused or unavailable: the lifecycle is left as it was.
                    raise EnrollmentRefused(
                        f"lifecycle_{state}_to_{target}_refused:{exc}"[:300]
                    ) from exc
            readback = self.persona_owner.get_persona(self.persona_id) or {}
            if _text(readback.get("lifecycle_state")) != target:
                raise EnrollmentRefused(f"lifecycle_{state}_to_{target}_not_confirmed", retryable=True)
            steps.append({"from": state, "to": target, "governance_decision_id": decision_id})
            state = target
            persona = readback
        outcome.lifecycle_state = state
        return persona

    def _governance_decision(self, source: str, target: str) -> str:
        """Propose, review and decide the exact transition; return the approved id."""

        decision_id = lifecycle_decision_id(self.tenant_id, self.persona_id, source, target)
        path = f"/api/governance/approvals/{quote(decision_id, safe='')}"
        key = _digest(self.tenant_id, self.persona_id, source, target)
        record = self.transport.get("governance", path)
        if record is None:
            if self.proposer is None or not self.proposer_actor_id:
                raise EnrollmentRefused("governance_proposer_identity_unconfigured")
            if self.proposer_actor_id == self.governance_actor_id:
                raise EnrollmentRefused("governance_proposer_must_differ_from_gate")
            expires = _utc(self._now() + APPROVAL_TTL)
            try:
                self.proposer.post(
                    "governance",
                    "/api/governance/approvals",
                    {
                        "decision_id": decision_id,
                        "expected_version": 0,
                        "tenant_id": self.tenant_id,
                        "owner_user_id": self.proposer_actor_id,
                        "target_type": TARGET_TYPE,
                        "target_id": self.persona_id,
                        "target_version": key,
                        "risk_level": "low",
                        "persona_id": self.persona_id,
                        "expires_at": expires,
                        "subject": {
                            "persona_id": self.persona_id,
                            "from_state": source,
                            "to_state": target,
                        },
                    },
                )
            except Exception:  # noqa: BLE001 - response loss reconciled by GET below
                pass
            record = self.transport.get("governance", path)
        if record is None:
            raise EnrollmentRefused(f"governance_decision_not_persisted:{source}_to_{target}", retryable=True)
        self._require_own_decision(record, decision_id, source, target)
        gate = {"actor_role": "automated_gate", "actor_id": self.governance_actor_id}
        failure: dict[str, Any] | None = None
        if record.get("decision_state") == "proposed":
            failure = self._transition("review", path + "/review", {**gate, "expected_version": int(record.get("version") or 0)})
            record = self._read(path, source, target)
        if record.get("decision_state") == "under_review":
            failure = self._transition(
                "decide",
                path + "/decide",
                {
                    **gate,
                    "outcome": "approved",
                    "rationale": "Paper enrolment of a provisioned paper persona",
                    "expires_at": record.get("expires_at"),
                    "expected_version": int(record.get("version") or 0),
                },
            )
            record = self._read(path, source, target)
        if record.get("decision_state") != "decided" or record.get("decision") != "approved":
            reason = (
                f"governance_decision_not_approved:{source}_to_{target}:"
                f"{record.get('decision_state')}/{record.get('decision')}"
            )
            retryable = False
            if failure:
                status = failure["status"]
                reason += (
                    f":{failure['step']}_failed:http_{status if status is not None else 'transport'}"
                    f":{failure['detail']}"
                )
                retryable = status is None or status >= 500
            raise EnrollmentRefused(reason[:400], retryable=retryable)
        return decision_id

    def _read(self, path: str, source: str, target: str) -> Mapping[str, Any]:
        record = self.transport.get("governance", path)
        if record is None:
            raise EnrollmentRefused(f"governance_decision_not_persisted:{source}_to_{target}", retryable=True)
        return record

    def _transition(self, step: str, path: str, payload: Mapping[str, Any]) -> dict[str, Any] | None:
        """POST a governance transition; the authoritative readback still decides.

        A failed call is returned (step, HTTP status or ``None`` for transport
        failures, redacted detail) so a still-unapproved readback can name it.
        """

        try:
            self.transport.post("governance", path, payload)
        except Exception as exc:  # noqa: BLE001 - the authoritative readback decides
            return {"step": step, "status": _error_status(exc), "detail": _error_detail(exc)}
        return None

    def _proposed_by_independent_principal(self, record: Mapping[str, Any]) -> bool:
        owner = _text(record.get("owner_user_id"))
        if not owner or owner == self.governance_actor_id:
            return False
        return not self.proposer_actor_id or owner == self.proposer_actor_id

    def _require_own_decision(
        self, record: Mapping[str, Any], decision_id: str, source: str, target: str
    ) -> None:
        subject = (record.get("metadata") or {}).get("subject") or record.get("subject") or {}
        if not (
            record.get("decision_id") == decision_id
            and record.get("target_type") == TARGET_TYPE
            and record.get("target_id") == self.persona_id
            and record.get("tenant_id") == self.tenant_id
            and self._proposed_by_independent_principal(record)
            and subject.get("persona_id") == self.persona_id
            and subject.get("from_state") == source
            and subject.get("to_state") == target
        ):
            raise EnrollmentRefused(
                f"governance_decision_identity_mismatch:{source}_to_{target}"
            )

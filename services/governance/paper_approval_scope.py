"""Owner-side dev paper approval safety boundary.

This module defines three small, dependency-free contracts that Governance
and every approval consumer share:

* the dedicated ``pantheon-dev-paper-provisioner`` principal grant, which is
  admitted only with an exact tenant, exact ``scope`` claim, exact role set
  and an explicit dev feature flag, and never falls back to generic role
  handling;
* the durable ``authorization_scope`` value stamped on decisions proposed by
  that principal, plus the ``ApprovalUsageContext`` consumers must present
  before a scoped approval admits anything;
* the immutable Registry candidate verification (spec checksum / bundle
  checksum / paper signal interface / lineage) that Governance performs at
  review and decide, and that Registry re-performs before it admits a scoped
  approval.

No service-main imports.  Registry validators are imported lazily so the
control-plane ``ApprovalDecision`` object can import this module cheaply.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

DEV_PAPER_PROVISIONER_SUBJECT = "pantheon-dev-paper-provisioner"
DEV_PAPER_APPROVAL_SCOPE = "pantheon:dev-paper-approval"
DEV_PAPER_TENANT_ID = "tenant-dev"
DEV_PAPER_ROLES = frozenset({"automated_gate"})
DEV_PAPER_ENVIRONMENT = "dev"
DEV_PAPER_AUTHORIZATION_SCOPE: dict[str, Any] = {
    "environment": "dev",
    "allowed_target_stages": ["paper"],
    "max_capital_scale_pct": 0,
}
PERSONA_LIFECYCLE_TARGET = "persona_lifecycle_transition"
# Operator decision 2026-11-15: the paper automated gate may decide exactly the
# two paper-ladder persona lifecycle steps.  Everything else (freeze, retire,
# live_owner, canary, live) stays with the operator.
PAPER_LADDER_TRANSITIONS = frozenset({("research_only", "consultable"), ("consultable", "paper_owner")})
PERSONA_LIFECYCLE_GRANT_PREFIX = "enabled;persona_lifecycle=paper_ladder;proposer="
PERSONA_LIFECYCLE_GRANT_ID_PREFIX = "dev-paper-grant:persona-lifecycle-paper-ladder:"
# Operator direction 2026-11-15 (design section 6A of SD_DISCOVERY_DEMAND_AND_EVOLUTION_TRIGGERS):
# inside paper, research admits a hypothesis-card StrategySpec that the discovery scorer matched
# to an open persona demand; the paper automated gate decides it.  Explicit grant widening only.
CARD_ADMISSION_GRANT_VALUE = "paper_research"
CARD_ADMISSION_GRANT_ID_PREFIX = "dev-paper-grant:card-admission-paper-research:"
# Operator decision P-4 (2026-11-15, "paper 自動核准") applied to pool risk policy: inside paper, in dev, at
# capital scale 0, the gate decides a pool risk-policy proposal that verifies against the operator outer
# bounds and carries risk-officer draft provenance.  Explicit grant widening only; canary/live never.
RISK_POLICY_GRANT_VALUE = "paper_pool"
RISK_POLICY_GRANT_ID_PREFIX = "dev-paper-grant:risk-policy-paper-pool:"
RISK_POLICY_TARGET = "capital_pool_activation"
RISK_POLICY_BOUNDS_ENV = "PANTHEON_PAPER_RISK_PROFILE_BOUNDS"
# Default operator outer bounds for the low profile (mirrors services/capital/risk_policy.py).
RISK_POLICY_DEFAULT_BOUNDS: dict[str, dict[str, float]] = {
    "low": {
        # Operator decision 2026-11-07 (DEV_PLAN_INSTRUMENT_EXPRESSION_LAYER Q3): gross and leverage <= 2.0x pool equity.
        "gross_limit": 2.0,
        "max_leverage": 2.0,
        # Operator decision 2026-11-23: single name <= 20% of NAV, cash reserve >= 10% of NAV.
        "max_single_name_weight": 0.20,
        "min_cash_reserve_pct": 0.10,
    },
}
_RISK_POLICY_UPPER_BOUND_KEYS = ("gross_limit", "max_leverage", "max_single_name_weight")
RISK_POLICY_REQUIRED_LIMITS = ("gross_limit", "net_limit", "max_leverage",
                               "max_single_name_weight", "min_cash_reserve_pct")
_GRANT_KEYS = frozenset({"persona_lifecycle", "proposer", "card_admission", "card_proposer",
                         "risk_policy", "risk_proposer"})
_PROPOSER_SUBJECT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:@-")
_ACTIVE_RUNTIME_STATES = frozenset({"active", "running", "ok"})
PAPER_SIGNAL_INTERFACE = "services.execution.lean_runtime.paper_signal_producer:Strategy"
PAPER_APPROVAL_MAX_TTL = timedelta(hours=24)
# The research admission owner proposes a card admission valid for 72h (admission.py); the card widening
# admits that bound plus one minute for the proposer-clock to created_at gap, and nothing longer.  The
# baseline grant keeps its 24h limit.
CARD_ADMISSION_MAX_TTL = timedelta(hours=72, minutes=1)

_SCOPE_KEYS = frozenset({"environment", "allowed_target_stages", "max_capital_scale_pct"})
_KNOWN_STAGES = frozenset({"paper", "canary", "live"})
_USAGE_KEYS = frozenset({"environment", "target_stage", "capital_scale_pct"})


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class AuthorizationScopeError(ValueError):
    """A stored/presented authorization_scope is malformed."""


class UsageContextViolation(ValueError):
    """A scoped approval does not admit the presented usage context."""


class PaperApprovalDenied(ValueError):
    """The dedicated paper principal or its command is outside authority."""

    def __init__(self, message: str, status_code: int = 403) -> None:
        super().__init__(message)
        self.status_code = status_code


class PaperCandidateInvalid(ValueError):
    """The Registry candidate does not match the immutable paper contract."""


class PaperCandidateUnavailable(RuntimeError):
    """The Registry owner could not be read; never treated as a pass."""


# ---------------------------------------------------------------------------
# Primitive helpers
# ---------------------------------------------------------------------------

def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _text(source: Mapping[str, Any], key: str) -> str:
    value = source.get(key)
    return value if isinstance(value, str) else ""


def _canonical_sha256(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return f"sha256:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def parse_rfc3339(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} is required")
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError(f"{label} is not an RFC3339 timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def current_environment(env: Mapping[str, str] | None = None) -> str:
    source = env if env is not None else os.environ
    return str(source.get("PANTHEON_ENV", "") or "").strip().lower()


# ---------------------------------------------------------------------------
# authorization_scope + usage context
# ---------------------------------------------------------------------------

def normalize_authorization_scope(value: Any) -> dict[str, Any]:
    """Return a canonical copy of a strict authorization_scope or raise."""
    if not isinstance(value, Mapping):
        raise AuthorizationScopeError("authorization_scope must be an object")
    if set(value.keys()) != _SCOPE_KEYS:
        raise AuthorizationScopeError(
            "authorization_scope must contain exactly environment, "
            "allowed_target_stages and max_capital_scale_pct"
        )
    environment = value.get("environment")
    if not isinstance(environment, str) or not environment.strip() or environment != environment.strip().lower():
        raise AuthorizationScopeError("authorization_scope.environment must be a canonical lowercase name")
    stages = value.get("allowed_target_stages")
    if (
        not isinstance(stages, list)
        or not stages
        or any(not isinstance(stage, str) or stage not in _KNOWN_STAGES for stage in stages)
        or len(set(stages)) != len(stages)
    ):
        raise AuthorizationScopeError(
            "authorization_scope.allowed_target_stages must be a non-empty unique list of paper/canary/live"
        )
    max_pct = value.get("max_capital_scale_pct")
    if not _is_number(max_pct) or max_pct < 0 or max_pct > 100:
        raise AuthorizationScopeError("authorization_scope.max_capital_scale_pct must be a number in [0, 100]")
    return {
        "environment": environment,
        "allowed_target_stages": list(stages),
        "max_capital_scale_pct": max_pct,
    }


@dataclass(frozen=True)
class ApprovalUsageContext:
    """The actual environment, stage and capital scale an approval is used for."""

    environment: str
    target_stage: str
    capital_scale_pct: float

    @classmethod
    def coerce(cls, value: Any) -> "ApprovalUsageContext":
        if isinstance(value, cls):
            candidate = value
        elif isinstance(value, Mapping):
            if set(value.keys()) != _USAGE_KEYS:
                raise UsageContextViolation(
                    "usage context must contain exactly environment, target_stage and capital_scale_pct"
                )
            candidate = cls(
                environment=value.get("environment"),  # type: ignore[arg-type]
                target_stage=value.get("target_stage"),  # type: ignore[arg-type]
                capital_scale_pct=value.get("capital_scale_pct"),  # type: ignore[arg-type]
            )
        else:
            raise UsageContextViolation("usage context must be an ApprovalUsageContext")
        if not isinstance(candidate.environment, str) or not candidate.environment.strip():
            raise UsageContextViolation("usage context environment is required")
        if not isinstance(candidate.target_stage, str) or not candidate.target_stage.strip():
            raise UsageContextViolation("usage context target_stage is required")
        if not _is_number(candidate.capital_scale_pct) or candidate.capital_scale_pct < 0:
            raise UsageContextViolation("usage context capital_scale_pct must be a finite non-negative number")
        return candidate


def enforce_authorization_scope(scope: Any, usage_context: Any) -> dict[str, Any] | None:
    """Fail closed unless the presented usage context is inside the scope.

    ``scope is None`` means an unscoped (legacy) approval: nothing to enforce
    and any usage context is ignored.  A scoped approval with no usage
    context is a violation: default consumers cannot treat scoped approvals
    as unrestricted.
    """
    if scope is None:
        return None
    normalized = normalize_authorization_scope(scope)
    if usage_context is None:
        raise UsageContextViolation("Scoped approval requires an explicit usage context")
    context = ApprovalUsageContext.coerce(usage_context)
    if context.environment.strip().lower() != normalized["environment"]:
        raise UsageContextViolation(
            f"Approval scope environment {normalized['environment']!r} does not admit {context.environment!r}"
        )
    if context.target_stage not in normalized["allowed_target_stages"]:
        raise UsageContextViolation(
            f"Approval scope does not admit target_stage {context.target_stage!r}"
        )
    if context.capital_scale_pct > normalized["max_capital_scale_pct"]:
        raise UsageContextViolation(
            f"Approval scope max_capital_scale_pct={normalized['max_capital_scale_pct']!r} "
            f"does not admit capital_scale_pct={context.capital_scale_pct!r}"
        )
    return normalized


def authorization_scope_errors(*, actor_id: Any, owner_user_id: Any, authorization_scope: Any) -> list[str]:
    """Structural + subject-binding errors for a stored/presented scope.

    The dedicated paper subject has no legitimate unscoped legacy authority:
    whenever it is the actor OR the owner, the scope must be present and be
    exactly the dev paper scope.  A stale response, schema loss or a
    broadened value can therefore never turn into generic unrestricted
    authority.  Every other subject keeps its existing unscoped meaning.
    """
    errors: list[str] = []
    normalized = None
    if authorization_scope is not None:
        try:
            normalized = normalize_authorization_scope(authorization_scope)
        except AuthorizationScopeError as exc:
            errors.append(str(exc))
    if DEV_PAPER_PROVISIONER_SUBJECT in (actor_id, owner_user_id):
        if authorization_scope is None:
            errors.append(
                "authorization_scope is required whenever the dedicated dev paper subject "
                "is the actor or owner of a decision"
            )
        elif normalized is not None and normalized != normalize_authorization_scope(DEV_PAPER_AUTHORIZATION_SCOPE):
            errors.append(
                "authorization_scope for the dedicated dev paper subject must be exactly the dev paper scope"
            )
    return errors


def require_dedicated_subject_scope(*, actor_id: Any, owner_user_id: Any, authorization_scope: Any) -> None:
    errors = authorization_scope_errors(
        actor_id=actor_id, owner_user_id=owner_user_id, authorization_scope=authorization_scope
    )
    if errors:
        raise AuthorizationScopeError("; ".join(errors))


# ---------------------------------------------------------------------------
# Dedicated principal grant
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PersonaLifecycleGrant:
    """Explicit widening: the gate may decide the paper-ladder lifecycle steps."""

    proposer_subject: str
    grant_id: str


@dataclass(frozen=True)
class CardAdmissionGrant:
    """Explicit widening: the gate may decide a research-proposed card spec admission."""

    proposer_subject: str
    grant_id: str


@dataclass(frozen=True)
class RiskPolicyGrant:
    """Explicit widening: the gate may decide a paper pool risk-policy proposal inside the outer bounds."""

    proposer_subject: str
    grant_id: str


@dataclass(frozen=True)
class DevPaperGrant:
    subject: str
    tenant_id: str
    persona_lifecycle: PersonaLifecycleGrant | None = None
    card_admission: CardAdmissionGrant | None = None
    risk_policy: RiskPolicyGrant | None = None

    @property
    def authorization_scope(self) -> dict[str, Any]:
        return normalize_authorization_scope(DEV_PAPER_AUTHORIZATION_SCOPE)


def _valid_proposer(value: str) -> bool:
    return bool(value) and value != DEV_PAPER_PROVISIONER_SUBJECT and set(value) <= _PROPOSER_SUBJECT_CHARS


def _grant_all(
    env: Mapping[str, str] | None,
) -> tuple[PersonaLifecycleGrant | None, CardAdmissionGrant | None, RiskPolicyGrant | None]:
    """Parse the mounted grant file once.  Plain ``enabled`` and any malformed content widen nothing.

    Content is ``enabled`` followed by ``;key=value`` pairs.  ``persona_lifecycle=paper_ladder`` needs
    ``proposer=<subject>``; ``card_admission=paper_research`` needs ``card_proposer=<subject>``;
    ``risk_policy=paper_pool`` needs ``risk_proposer=<subject>``.  An
    unknown key, a half pair or an invalid subject makes the whole content a non-widening.
    """
    from services.service_token_file import configured_service_token
    source = env if env is not None else os.environ
    if not str(source.get("GOVERNANCE_DEV_PAPER_GRANT_FILE", "") or "").strip():
        return None, None, None
    try:
        content = configured_service_token("GOVERNANCE_DEV_PAPER_GRANT", source)
    except RuntimeError:
        return None, None, None
    parts = content.split(";")
    if parts[0] != "enabled" or len(parts) == 1:
        return None, None, None
    pairs: dict[str, str] = {}
    for part in parts[1:]:
        key, sep, value = part.partition("=")
        if not sep or key not in _GRANT_KEYS or key in pairs:
            return None, None, None
        pairs[key] = value
    lifecycle = card = risk = None
    if (("persona_lifecycle" in pairs) != ("proposer" in pairs)
            or ("card_admission" in pairs) != ("card_proposer" in pairs)
            or ("risk_policy" in pairs) != ("risk_proposer" in pairs)):
        return None, None, None
    if "persona_lifecycle" in pairs:
        if pairs["persona_lifecycle"] != "paper_ladder" or not _valid_proposer(pairs["proposer"]):
            return None, None, None
        legacy = PERSONA_LIFECYCLE_GRANT_PREFIX + pairs["proposer"]
        # grant id digest of the lifecycle clause alone keeps ids stable for lifecycle-only content
        digest = hashlib.sha256(legacy.encode("utf-8")).hexdigest()[:16]
        lifecycle = PersonaLifecycleGrant(proposer_subject=pairs["proposer"],
                                          grant_id=PERSONA_LIFECYCLE_GRANT_ID_PREFIX + digest)
    if "card_admission" in pairs:
        if pairs["card_admission"] != CARD_ADMISSION_GRANT_VALUE or not _valid_proposer(pairs["card_proposer"]):
            return None, None, None
        clause = f"card_admission={CARD_ADMISSION_GRANT_VALUE};card_proposer={pairs['card_proposer']}"
        digest = hashlib.sha256(clause.encode("utf-8")).hexdigest()[:16]
        card = CardAdmissionGrant(proposer_subject=pairs["card_proposer"], grant_id=CARD_ADMISSION_GRANT_ID_PREFIX + digest)
    if "risk_policy" in pairs:
        if pairs["risk_policy"] != RISK_POLICY_GRANT_VALUE or not _valid_proposer(pairs["risk_proposer"]):
            return None, None, None
        clause = f"risk_policy={RISK_POLICY_GRANT_VALUE};risk_proposer={pairs['risk_proposer']}"
        digest = hashlib.sha256(clause.encode("utf-8")).hexdigest()[:16]
        risk = RiskPolicyGrant(proposer_subject=pairs["risk_proposer"], grant_id=RISK_POLICY_GRANT_ID_PREFIX + digest)
    return lifecycle, card, risk


def _grant_widenings(
    env: Mapping[str, str] | None,
) -> tuple[PersonaLifecycleGrant | None, CardAdmissionGrant | None]:
    return _grant_all(env)[:2]


def configured_persona_lifecycle_grant(env: Mapping[str, str] | None = None) -> PersonaLifecycleGrant | None:
    """The explicit lifecycle widening carried by the mounted grant file, else None."""
    return _grant_widenings(env)[0]


def configured_card_admission_grant(env: Mapping[str, str] | None = None) -> CardAdmissionGrant | None:
    """The explicit card admission widening carried by the mounted grant file, else None."""
    return _grant_widenings(env)[1]


def configured_risk_policy_grant(env: Mapping[str, str] | None = None) -> RiskPolicyGrant | None:
    """The explicit pool risk-policy widening carried by the mounted grant file, else None."""
    return _grant_all(env)[2]


def dev_paper_feature_enabled(env: Mapping[str, str] | None = None) -> bool:
    from services.service_token_file import configured_dev_paper_grant_enabled
    source = env if env is not None else os.environ
    enabled = str(source.get("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "") or "").strip().lower()
    return (enabled == "true" and current_environment(source) == DEV_PAPER_ENVIRONMENT
            and (configured_dev_paper_grant_enabled(source)
                 or any(w is not None for w in _grant_all(source))))


def resolve_dev_paper_grant(ctx: Any, *, env: Mapping[str, str] | None = None) -> DevPaperGrant | None:
    """Return the grant for the dedicated subject, None for anyone else.

    The dedicated subject is recognised by its exact ``sub``.  Once
    recognised it is admitted only with every bound claim exact; any
    deviation raises :class:`PaperApprovalDenied` and never falls back to the
    generic write-authority handling.
    """
    claims = getattr(ctx, "claims", None)
    claims = claims if isinstance(claims, Mapping) else {}
    subject = claims.get("sub")
    actor_id = getattr(ctx, "actor_id", None)
    if subject != DEV_PAPER_PROVISIONER_SUBJECT and actor_id != DEV_PAPER_PROVISIONER_SUBJECT:
        return None
    if not dev_paper_feature_enabled(env):
        raise PaperApprovalDenied("Dev paper approval principal is not enabled in this environment")
    if subject != DEV_PAPER_PROVISIONER_SUBJECT or actor_id != DEV_PAPER_PROVISIONER_SUBJECT:
        raise PaperApprovalDenied("Dev paper approval principal subject is inconsistent")
    if getattr(ctx, "token_kind", None) != "jwt":
        raise PaperApprovalDenied("Dev paper approval principal requires a verified JWT")
    if claims.get("tenant_id") != DEV_PAPER_TENANT_ID:
        raise PaperApprovalDenied("Dev paper approval principal tenant is outside authority")
    if claims.get("scope") != DEV_PAPER_APPROVAL_SCOPE:
        raise PaperApprovalDenied("Dev paper approval principal scope claim is missing or incorrect")
    roles = getattr(ctx, "roles", None)
    if not isinstance(roles, (set, frozenset)) or frozenset(roles) != DEV_PAPER_ROLES:
        raise PaperApprovalDenied("Dev paper approval principal roles must be exactly automated_gate")
    return DevPaperGrant(subject=DEV_PAPER_PROVISIONER_SUBJECT, tenant_id=DEV_PAPER_TENANT_ID,
                         persona_lifecycle=configured_persona_lifecycle_grant(env),
                         card_admission=configured_card_admission_grant(env),
                         risk_policy=configured_risk_policy_grant(env))


# ---------------------------------------------------------------------------
# Proposal / transition admission for the grant
# ---------------------------------------------------------------------------

def validate_paper_expiry(expires_at: Any, *, created_at: Any, now: datetime,
                          max_ttl: timedelta = PAPER_APPROVAL_MAX_TTL) -> datetime:
    """expires_at must be finite, in the future and <= created_at + max_ttl (24h unless widened)."""
    try:
        expiry = parse_rfc3339(expires_at, "expires_at")
        created = parse_rfc3339(created_at, "created_at")
    except ValueError as exc:
        raise PaperApprovalDenied(str(exc), 422) from exc
    if expiry <= now:
        raise PaperApprovalDenied("Paper approval expires_at must be in the future", 422)
    if expiry > created + max_ttl:
        hours = int(max_ttl.total_seconds() // 3600)
        raise PaperApprovalDenied(f"Paper approval expires_at must be at most {hours}h after created_at", 422)
    return expiry


def _enum_text(value: Any) -> str:
    return value.value if hasattr(value, "value") else (value if isinstance(value, str) else "")


def validate_paper_proposal(fields: Mapping[str, Any], *, grant: DevPaperGrant, now: datetime) -> None:
    if _enum_text(fields.get("target_type")) != "registry_entry":
        raise PaperApprovalDenied("Dev paper principal may only propose registry_entry targets")
    if _enum_text(fields.get("risk_level") or "low") != "low":
        raise PaperApprovalDenied("Dev paper principal may only propose low-risk decisions")
    if fields.get("tenant_id") != grant.tenant_id:
        raise PaperApprovalDenied("Dev paper proposal tenant is outside authority")
    if fields.get("owner_user_id") != grant.subject:
        raise PaperApprovalDenied("Dev paper proposal owner must be the dedicated subject")
    for key in ("persona_id", "capital_pool_id", "candidate_digest", "target_id", "target_version"):
        value = fields.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PaperApprovalDenied(f"Dev paper proposal requires {key}", 422)
    validate_paper_expiry(fields.get("expires_at"), created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), now=now)


def require_paper_owned_decision(decision: Mapping[str, Any], *, grant: DevPaperGrant, now: datetime) -> None:
    """The dedicated subject may only act on its own exact-scope proposals.

    The one widening is the research card admission (``grant.card_admission``): a decision owned by
    the named research proposer principal, never by the gate itself.
    """
    widening = grant.card_admission
    if (widening is not None and decision.get("owner_user_id") == widening.proposer_subject
            and decision.get("owner_user_id") != grant.subject):
        require_card_admission_decision(decision, grant=grant, now=now)
        return
    if decision.get("owner_user_id") != grant.subject:
        raise PaperApprovalDenied("Dev paper principal may not act on another owner's decision")
    if decision.get("tenant_id") != grant.tenant_id:
        raise PaperApprovalDenied("Dev paper decision tenant is outside authority")
    if _enum_text(decision.get("target_type")) != "registry_entry":
        raise PaperApprovalDenied("Dev paper principal may only act on registry_entry targets")
    if _enum_text(decision.get("risk_level")) != "low":
        raise PaperApprovalDenied("Dev paper principal may only act on low-risk decisions")
    scope = decision.get("authorization_scope")
    try:
        normalized = normalize_authorization_scope(scope)
    except AuthorizationScopeError as exc:
        raise PaperApprovalDenied("Dev paper decision authorization_scope is missing or malformed") from exc
    if normalized != grant.authorization_scope:
        raise PaperApprovalDenied("Dev paper decision authorization_scope does not match the grant")
    validate_paper_expiry(decision.get("expires_at"), created_at=decision.get("created_at"), now=now)


def is_risk_policy_decision(decision: Mapping[str, Any], grant: DevPaperGrant | None = None) -> bool:
    """A pool risk-policy proposal the gate is widened to decide: capital_pool_activation owned by the named proposer."""
    if _enum_text(decision.get("target_type")) != RISK_POLICY_TARGET:
        return False
    widening = grant.risk_policy if grant is not None else None
    return widening is None or decision.get("owner_user_id") == widening.proposer_subject


def _outer_bounds(profile: str, env: Mapping[str, str] | None = None) -> Mapping[str, float]:
    source = env if env is not None else os.environ
    raw = str(source.get(RISK_POLICY_BOUNDS_ENV) or "").strip()
    try:
        loaded: Any = json.loads(raw) if raw else RISK_POLICY_DEFAULT_BOUNDS
    except ValueError as exc:
        raise PaperApprovalDenied(f"{RISK_POLICY_BOUNDS_ENV} is malformed; paper risk policy fails closed", 503) from exc
    limits = loaded.get(profile) if isinstance(loaded, Mapping) else None
    if not isinstance(limits, Mapping):
        raise PaperApprovalDenied(f"No operator outer bounds configured for profile {profile!r}", 422)
    return limits


def risk_policy_candidate_failure(decision: Mapping[str, Any], env: Mapping[str, str] | None = None) -> str | None:
    """None when the proposed policy verifies; else the named failing field and reason.

    The candidate must be the risk-officer draft carried in the proposal subject (its provenance present,
    every required limit finite and positive, single-name cap below 1, cash reserve below 1) and inside the
    operator outer bounds for the requested profile.
    """
    metadata = decision.get("metadata")
    subject = metadata.get("subject") if isinstance(metadata, Mapping) else None
    subject = subject if isinstance(subject, Mapping) else {}
    draft = subject.get("risk_officer_draft")
    draft = draft if isinstance(draft, Mapping) else {}
    policy, provenance = draft.get("policy"), draft.get("provenance")
    if not isinstance(policy, Mapping) or not policy:
        return "risk_officer_draft.policy: absent; the candidate is not a risk-officer drafted policy"
    if not isinstance(provenance, Mapping) or not provenance:
        return "risk_officer_draft.provenance: absent"
    profile = str(subject.get("requested_risk") or "").strip().lower()
    limits = _outer_bounds(profile, env)
    values: dict[str, float] = {}
    for name in RISK_POLICY_REQUIRED_LIMITS:
        raw = policy.get(name)
        if raw is None and isinstance(policy.get("limits"), Mapping):
            raw = policy["limits"].get(name)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw != raw or raw in (float("inf"), float("-inf")):
            return f"{name}: missing or non-numeric"
        if float(raw) <= 0.0:
            return f"{name}: must be positive, got {raw!r}"
        values[name] = float(raw)
    for name in ("max_single_name_weight", "min_cash_reserve_pct"):
        if not values[name] < 1.0:
            return f"{name}: must be below 1, got {values[name]!r}"
    for name in _RISK_POLICY_UPPER_BOUND_KEYS:
        cap = limits.get(name)
        if cap is not None and values[name] > float(cap) + 1e-12:
            return f"{name}: {values[name]!r} exceeds {profile} bound {cap!r}"
    reserve = limits.get("min_cash_reserve_pct")
    if reserve is not None and values["min_cash_reserve_pct"] + 1e-12 < float(reserve):
        return f"min_cash_reserve_pct: {values['min_cash_reserve_pct']!r} below {profile} bound {reserve!r}"
    return None


def require_paper_risk_policy_decision(decision: Mapping[str, Any], *, grant: DevPaperGrant, now: datetime,
                                       env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Admit the gate on one paper pool risk-policy proposal (identity only) or raise.

    The proposal must come from the named proposer (never the gate), target a capital pool in the grant
    tenant, be low risk (paper, capital scale 0) and carry a bounded expiry.  Whether the policy may be
    approved is ``risk_policy_candidate_failure``; a rejection with a reason is always admissible.
    """
    widening = grant.risk_policy
    if widening is None:
        raise PaperApprovalDenied("Dev paper grant does not authorise pool risk-policy decisions")
    if current_environment(env) != DEV_PAPER_ENVIRONMENT:
        raise PaperApprovalDenied("Pool risk-policy paper grant is limited to the dev paper environment")
    owner = decision.get("owner_user_id")
    if owner != widening.proposer_subject or owner == grant.subject:
        raise PaperApprovalDenied("Pool risk-policy proposal must come from the named proposer principal")
    if decision.get("tenant_id") != grant.tenant_id:
        raise PaperApprovalDenied("Dev paper decision tenant is outside authority")
    if _enum_text(decision.get("risk_level")) != "low":
        raise PaperApprovalDenied("Dev paper principal may only act on low-risk decisions")
    metadata = decision.get("metadata")
    subject = metadata.get("subject") if isinstance(metadata, Mapping) else None
    subject = subject if isinstance(subject, Mapping) else {}
    pool_id = _text(subject, "pool_id")
    if not pool_id or pool_id != decision.get("target_id") or pool_id != decision.get("capital_pool_id"):
        raise PaperApprovalDenied("Pool risk-policy decision pool identity is inconsistent")
    validate_paper_expiry(decision.get("expires_at"), created_at=decision.get("created_at"), now=now)
    return {"grant_id": widening.grant_id, "pool_id": pool_id,
            "risk_direction": subject.get("risk_direction")}


_STAGE_MARKER_KEYS = ("stage", "target_stage", "execution_context", "environment")
_OPEN_PROPOSAL_STATES = frozenset({"proposed", "under_review"})


def _stage_markers_paper_only(decision: Mapping[str, Any]) -> bool:
    """Every stage marker the proposal itself carries must say paper; a stamped scope must be the paper scope."""
    metadata = decision.get("metadata")
    subject = metadata.get("subject") if isinstance(metadata, Mapping) else None
    for source in (subject, metadata):
        if not isinstance(source, Mapping):
            continue
        for key in _STAGE_MARKER_KEYS:
            value = source.get(key)
            if value not in (None, "") and value not in ("paper", DEV_PAPER_ENVIRONMENT):
                return False
    scope = decision.get("authorization_scope")
    if scope is not None:
        try:
            if normalize_authorization_scope(scope)["allowed_target_stages"] != ["paper"]:
                return False
        except AuthorizationScopeError:
            return False
    return True


def _superseding_failure(candidate: Mapping[str, Any], *, target: Mapping[str, Any], grant: DevPaperGrant,
                         now: datetime) -> str | None:
    """Why ``candidate`` is not a valid superseding decision for ``target`` (None when it is)."""
    widening = grant.risk_policy
    if candidate.get("decision_id") == target.get("decision_id"):
        return "is the proposal itself"
    if (candidate.get("tenant_id") != target.get("tenant_id")
            or _enum_text(candidate.get("target_type")) != RISK_POLICY_TARGET
            or candidate.get("target_id") != target.get("target_id")
            or candidate.get("capital_pool_id") != target.get("capital_pool_id")):
        return "is not for the same tenant and pool"
    if _enum_text(candidate.get("decision_state")) != "decided" or _enum_text(candidate.get("decision")) != "approved":
        return "is not an approved decided decision"
    if candidate.get("conditions") or candidate.get("revoked_at") or candidate.get("superseded_by"):
        return "is conditional, revoked or superseded"
    if candidate.get("actor_id") != grant.subject or _enum_text(candidate.get("actor_role")) != "automated_gate":
        return "was not decided by the paper automated gate"
    if candidate.get("owner_user_id") != widening.proposer_subject:
        return "was not proposed by the paper proposer"
    try:
        if normalize_authorization_scope(candidate.get("authorization_scope")) != grant.authorization_scope:
            return "is not stamped with the paper scope"
        if parse_rfc3339(candidate.get("expires_at"), "expires_at") <= now:
            return "is expired"
        if (parse_rfc3339(candidate.get("created_at"), "created_at")
                <= parse_rfc3339(target.get("created_at"), "created_at")):
            return "is not newer than the proposal"
    except (AuthorizationScopeError, ValueError):
        return "has a malformed scope or timestamp"
    metadata = candidate.get("metadata")
    subject = metadata.get("subject") if isinstance(metadata, Mapping) else None
    draft = subject.get("risk_officer_draft") if isinstance(subject, Mapping) else None
    if (not isinstance(draft, Mapping) or not isinstance(draft.get("provenance"), Mapping)
            or not draft["provenance"] or not isinstance(draft.get("policy"), Mapping) or not draft["policy"]):
        return "carries no risk-officer draft provenance"
    if not _stage_markers_paper_only(candidate):
        return "is not a paper-stage decision"
    candidate_digest = candidate.get("candidate_digest")
    if candidate_digest and candidate_digest == target.get("candidate_digest"):
        return "binds the same policy as the proposal"
    return None


def require_superseded_risk_policy_close(
    target: Mapping[str, Any], *, pool_decisions: list[Mapping[str, Any]], grant: DevPaperGrant,
    now: datetime, env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Admit the gate on closing one open paper pool risk-policy proposal superseded by the pool's bound policy.

    Close-only authority, verified on Governance's own records (never the caller's word): dev, a
    capital_pool_activation of a paper pool, same tenant, proposed by the known paper proposer, still open, and a
    newer, approved, unexpired, unrevoked decision by the paper automated gate with risk-officer provenance on the
    same pool.  Returns the superseding decision id and the reason; raises :class:`PaperApprovalDenied` otherwise.
    """
    widening = grant.risk_policy
    if widening is None:
        raise PaperApprovalDenied("Dev paper grant does not authorise pool risk-policy proposal closing")
    if current_environment(env) != DEV_PAPER_ENVIRONMENT:
        raise PaperApprovalDenied("Superseded risk-policy close is limited to the dev paper environment")
    if _enum_text(target.get("target_type")) != RISK_POLICY_TARGET:
        raise PaperApprovalDenied("Superseded close may only act on capital_pool_activation proposals")
    if target.get("tenant_id") != grant.tenant_id:
        raise PaperApprovalDenied("Dev paper decision tenant is outside authority")
    owner = target.get("owner_user_id")
    if owner != widening.proposer_subject or owner == grant.subject:
        raise PaperApprovalDenied("Superseded close may only act on proposals from the named paper proposer")
    pool_id = target.get("target_id")
    if not isinstance(pool_id, str) or not pool_id.strip() or target.get("capital_pool_id") != pool_id:
        raise PaperApprovalDenied("Pool risk-policy decision pool identity is inconsistent")
    if not _stage_markers_paper_only(target):
        raise PaperApprovalDenied("Superseded close may only act on paper-stage proposals")
    if _enum_text(target.get("decision_state")) not in _OPEN_PROPOSAL_STATES:
        raise PaperApprovalDenied("Only an open (proposed or under_review) proposal can be closed as superseded", 409)
    failures: list[str] = []
    best: Mapping[str, Any] | None = None
    for candidate in pool_decisions:
        failure = _superseding_failure(candidate, target=target, grant=grant, now=now)
        if failure is not None:
            failures.append(f"{candidate.get('decision_id')}: {failure}")
            continue
        if best is None or (parse_rfc3339(candidate["created_at"], "created_at")
                            > parse_rfc3339(best["created_at"], "created_at")):
            best = candidate
    if best is None:
        raise PaperApprovalDenied("Proposal is not superseded by a gate-approved decision of the same paper pool")
    superseding_id = str(best["decision_id"])
    return {"grant_id": widening.grant_id, "pool_id": pool_id, "superseded_by": superseding_id,
            "reason": f"Superseded by gate-approved paper risk policy decision {superseding_id}; closed without a human vote"}


def require_card_admission_decision(decision: Mapping[str, Any], *, grant: DevPaperGrant, now: datetime,
                                    env: Mapping[str, str] | None = None) -> dict[str, Any]:
    """Admit the gate on one research-proposed hypothesis-card spec admission or raise (paper only).

    The decision must name the research proposer (distinct from the gate), a ``registry_entry`` target,
    low risk, the grant tenant, the persona and the open demand (``proposal_id``) the spec was matched to,
    and carry a bounded expiry.  Nothing else about the decision is widened.
    """
    widening = grant.card_admission
    if widening is None:
        raise PaperApprovalDenied("Dev paper grant does not authorise research card admission decisions")
    if current_environment(env) != DEV_PAPER_ENVIRONMENT:
        raise PaperApprovalDenied("Card admission paper grant is limited to the paper environment")
    owner = decision.get("owner_user_id")
    if owner != widening.proposer_subject or owner == grant.subject:
        raise PaperApprovalDenied("Card admission proposal must come from the named research proposer principal")
    if decision.get("tenant_id") != grant.tenant_id:
        raise PaperApprovalDenied("Dev paper decision tenant is outside authority")
    if _enum_text(decision.get("target_type")) != "registry_entry":
        raise PaperApprovalDenied("Card admission may only act on registry_entry targets")
    if _enum_text(decision.get("risk_level")) != "low":
        raise PaperApprovalDenied("Dev paper principal may only act on low-risk decisions")
    for key in ("persona_id", "proposal_id", "target_id", "target_version", "candidate_digest"):
        value = decision.get(key)
        if not isinstance(value, str) or not value.strip():
            raise PaperApprovalDenied(f"Card admission decision requires {key} (the open persona demand match)", 422)
    validate_paper_expiry(decision.get("expires_at"), created_at=decision.get("created_at"), now=now,
                          max_ttl=CARD_ADMISSION_MAX_TTL)
    return {"grant_id": widening.grant_id, "demand_id": decision["proposal_id"],
            "persona_id": decision["persona_id"], "registry_id": decision["target_id"]}


def validate_paper_decide_body(body: Mapping[str, Any], *, decision: Mapping[str, Any], now: datetime) -> None:
    outcome = _enum_text(body.get("outcome"))
    if outcome not in {"approved", "rejected"}:
        raise PaperApprovalDenied("Dev paper principal may only record approved or rejected outcomes")
    if body.get("conditions"):
        raise PaperApprovalDenied("Dev paper principal may not attach conditions")
    digest = body.get("candidate_digest")
    if digest is not None and digest != decision.get("candidate_digest"):
        raise PaperApprovalDenied("Dev paper decide candidate_digest must equal the proposal digest")
    if body.get("expires_at") is not None:
        # A decide body may only restate the proposal expiry; any other
        # instant (earlier, later, still inside 24h) is a divergence.
        # Only a widened (card admission) decision has a non-gate owner here; it keeps its own bound.
        max_ttl = (PAPER_APPROVAL_MAX_TTL if decision.get("owner_user_id") == DEV_PAPER_PROVISIONER_SUBJECT
                   else CARD_ADMISSION_MAX_TTL)
        supplied = validate_paper_expiry(body.get("expires_at"), created_at=decision.get("created_at"), now=now,
                                         max_ttl=max_ttl)
        try:
            proposed = parse_rfc3339(decision.get("expires_at"), "proposal expires_at")
        except ValueError as exc:
            raise PaperApprovalDenied(str(exc), 422) from exc
        if supplied != proposed:
            raise PaperApprovalDenied("Dev paper decide expires_at must equal the proposal expires_at")


# ---------------------------------------------------------------------------
# Paper-ladder persona lifecycle decisions (operator decision 2026-11-15)
# ---------------------------------------------------------------------------

def _owner_json_get(base_url: str, token_provider: Callable[[], str], timeout_seconds: float,
                    path: str, label: str) -> Any:
    """Exact owner GET with no redirect; any failure is unavailable, never a pass."""
    try:
        token = token_provider()
        if not token:
            raise RuntimeError("missing credential")
    except RuntimeError as exc:
        raise PaperCandidateUnavailable(f"{label} read principal unavailable") from exc
    request = Request(base_url.rstrip("/") + path,
                      headers={"Accept": "application/json", "Authorization": "Bearer " + token}, method="GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise PaperCandidateUnavailable(f"{label} did not return 200")
            return json.loads(response.read().decode("utf-8"))
    except PaperCandidateUnavailable:
        raise
    except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
        raise PaperCandidateUnavailable(f"{label} read unavailable or malformed") from exc


class PersonaPaperBindingVerifier:
    """Verify, from their owners, the persona's paper capital and runtime bindings.

    ``read_capital(query)`` and ``read_runtime(query)`` return the parsed JSON
    of the Capital ``GET /api/bindings`` and Runtime Manager
    ``GET /api/runtime-bindings`` owner endpoints.  Nothing the proposer sent
    is trusted: the bindings are found from the persona id alone.
    """

    def __init__(self, *, read_capital: Callable[[str], Any], read_runtime: Callable[[str], Any]) -> None:
        self._read_capital = read_capital
        self._read_runtime = read_runtime

    def verify(self, *, tenant_id: str, persona_id: str) -> dict[str, Any]:
        capital = self._read_capital("/api/bindings?persona_id=" + quote(persona_id, safe="")
                                     + "&status=active&role=paper_owner")
        rows = capital if isinstance(capital, list) else []
        capital_bindings = [
            row for row in rows if isinstance(row, Mapping)
            and _text(row, "persona_id") == persona_id
            and _text(row, "status") == "active"
            and _text(row, "role") == "paper_owner"
            and _text(row, "allowed_deployment_scope") == "paper"
            and _text(row, "tenant_id") in ("", tenant_id)
        ]
        if len(capital_bindings) != 1:
            raise PaperApprovalDenied(
                "Persona has no single active paper capital binding verified from the Capital owner"
                if not capital_bindings else "Persona has more than one active paper capital binding")
        binding = capital_bindings[0]
        binding_id, pool_id = _text(binding, "binding_id"), _text(binding, "capital_pool_id")
        if not binding_id or not pool_id:
            raise PaperApprovalDenied("Paper capital binding is missing its binding or pool identity")
        runtime = self._read_runtime("/api/runtime-bindings?pool_id=" + quote(pool_id, safe=""))
        rows = runtime.get("bindings") if isinstance(runtime, Mapping) else None
        active: list[Mapping[str, Any]] = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, Mapping):
                continue
            meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
            state = (_text(row, "state") or _text(row, "status")).lower()
            stage = _text(row, "deployment_mode") or _text(row, "deployment_stage")
            if (state in _ACTIVE_RUNTIME_STATES and stage == "paper"
                    and _text(row, "capital_pool_id") == pool_id
                    and _text(row, "persona_capital_binding_id") == binding_id
                    and _text(meta, "persona_id") == persona_id and _text(meta, "tenant_id") == tenant_id):
                active.append(row)
        if len(active) != 1:
            raise PaperApprovalDenied(
                "Persona has no single active paper runtime binding verified from the Runtime Manager owner")
        runtime_binding = active[0]
        return {
            "capital_binding_id": binding_id,
            "capital_pool_id": pool_id,
            "runtime_binding_id": _text(runtime_binding, "binding_id") or _text(runtime_binding, "id"),
            "deployment_plan_id": _text(runtime_binding, "plan_id"),
        }


def configured_persona_paper_binding_verifier(env: Mapping[str, str] | None = None) -> PersonaPaperBindingVerifier:
    from services.service_token_file import configured_service_token
    source = env if env is not None else os.environ
    try:
        timeout = float(source.get("GOVERNANCE_BINDING_READ_TIMEOUT_SECONDS", "5") or "5")
    except ValueError as exc:
        raise PaperCandidateUnavailable("Binding reader timeout is malformed") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise PaperCandidateUnavailable("Binding reader timeout is malformed")

    def reader(url_key: str, token_key: str, label: str) -> Callable[[str], Any]:
        base = str(source.get(url_key, "") or "")
        parsed = urlsplit(base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise PaperCandidateUnavailable(f"{label} owner URL is not configured")
        return lambda path: _owner_json_get(
            base, lambda: configured_service_token(token_key, source), timeout, path, label)

    return PersonaPaperBindingVerifier(
        read_capital=reader("GOVERNANCE_CAPITAL_BASE_URL", "GOVERNANCE_CAPITAL_SERVICE_TOKEN", "Capital"),
        read_runtime=reader("GOVERNANCE_RUNTIME_MANAGER_BASE_URL", "GOVERNANCE_RUNTIME_MANAGER_SERVICE_TOKEN",
                            "Runtime Manager"),
    )


def is_persona_lifecycle_decision(decision: Mapping[str, Any]) -> bool:
    return _enum_text(decision.get("target_type")) == PERSONA_LIFECYCLE_TARGET


def require_paper_lifecycle_decision(
    decision: Mapping[str, Any], *, grant: DevPaperGrant, now: datetime,
    binding_verifier: PersonaPaperBindingVerifier, env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Admit the gate on one paper-ladder lifecycle decision or raise.

    Returns the evidence (grant id + verified bindings) the decision must carry.
    """
    widening = grant.persona_lifecycle
    if widening is None:
        raise PaperApprovalDenied("Dev paper grant does not authorise persona lifecycle decisions")
    if current_environment(env) != DEV_PAPER_ENVIRONMENT:
        raise PaperApprovalDenied("Persona lifecycle paper grant is limited to the paper environment")
    if decision.get("tenant_id") != grant.tenant_id:
        raise PaperApprovalDenied("Dev paper decision tenant is outside authority")
    owner = decision.get("owner_user_id")
    if owner != widening.proposer_subject or owner == grant.subject:
        raise PaperApprovalDenied("Persona lifecycle proposal must come from the named enrolment proposer principal")
    if _enum_text(decision.get("risk_level")) != "low":
        raise PaperApprovalDenied("Dev paper principal may only act on low-risk decisions")
    metadata = decision.get("metadata")
    subject = metadata.get("subject") if isinstance(metadata, Mapping) else None
    subject = subject if isinstance(subject, Mapping) else {}
    persona_id = _text(subject, "persona_id")
    transition = (_text(subject, "from_state"), _text(subject, "to_state"))
    if not persona_id or persona_id != decision.get("target_id") or persona_id != decision.get("persona_id"):
        raise PaperApprovalDenied("Persona lifecycle decision persona identity is inconsistent")
    if transition not in PAPER_LADDER_TRANSITIONS:
        raise PaperApprovalDenied(
            f"Dev paper grant covers only research_only->consultable and consultable->paper_owner, "
            f"not {transition[0]!r}->{transition[1]!r}")
    validate_paper_expiry(decision.get("expires_at"), created_at=decision.get("created_at"), now=now)
    bindings = binding_verifier.verify(tenant_id=grant.tenant_id, persona_id=persona_id)
    return {"grant_id": widening.grant_id, "persona_id": persona_id,
            "from_state": transition[0], "to_state": transition[1], **bindings}


# ---------------------------------------------------------------------------
# Exact Registry owner reader
# ---------------------------------------------------------------------------

class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


class PaperRegistryReader:
    """Exact ``GET /api/registry/entries/<id>`` with a scoped read principal."""

    def __init__(self, *, base_url: str, service_token: str, timeout_seconds: float = 5.0,
                 token_provider: Callable[[], str] | None = None) -> None:
        try:
            parsed = urlsplit(base_url)
        except ValueError as exc:
            raise PaperCandidateUnavailable("Registry reader URL is malformed") from exc
        if (
            parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or (not service_token and token_provider is None)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0
        ):
            raise PaperCandidateUnavailable("Registry reader URL, scoped principal and timeout required")
        self._opener = build_opener(_NoRedirect())
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.token_provider = token_provider
        self.timeout_seconds = timeout_seconds

    def get_entry_view(self, registry_id: str) -> Mapping[str, Any]:
        if not isinstance(registry_id, str) or not registry_id.strip():
            raise PaperCandidateInvalid("Exact registry ID required")
        try:
            token = self.token_provider() if self.token_provider else self.service_token
            if not token:
                raise RuntimeError("Missing service credential")
        except RuntimeError as exc:
            raise PaperCandidateUnavailable("Registry read principal unavailable") from exc
        request = Request(
            f"{self.base_url}/api/registry/entries/{quote(registry_id, safe='')}",
            headers={"Accept": "application/json", "Authorization": "Bearer " + token},
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                if response.status != 200 or response.headers.get_content_type() != "application/json":
                    raise PaperCandidateUnavailable("Registry did not return a JSON entry")
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403, 404}:
                raise PaperCandidateInvalid("Registry denied exact entry read") from exc
            raise PaperCandidateUnavailable("Registry read unavailable") from exc
        except (URLError, OSError, TimeoutError, ValueError) as exc:
            raise PaperCandidateUnavailable("Registry read unavailable or malformed") from exc
        if not isinstance(body, Mapping) or not isinstance(body.get("entry"), Mapping):
            raise PaperCandidateInvalid("Malformed Registry entry view")
        if body["entry"].get("registry_id") != registry_id:
            raise PaperCandidateInvalid("Registry exact entry ID mismatch")
        return body


def configured_paper_registry_reader(env: Mapping[str, str] | None = None) -> PaperRegistryReader:
    from services.service_token_file import configured_service_token
    source = env if env is not None else os.environ
    try:
        timeout = float(source.get("GOVERNANCE_REGISTRY_TIMEOUT_SECONDS", "5") or "5")
    except ValueError as exc:
        raise PaperCandidateUnavailable("Registry reader timeout is malformed") from exc
    return PaperRegistryReader(
        base_url=str(source.get("GOVERNANCE_REGISTRY_BASE_URL", "") or ""),
        service_token=str(source.get("GOVERNANCE_REGISTRY_SERVICE_TOKEN", "") or ""),
        token_provider=lambda: configured_service_token("GOVERNANCE_REGISTRY_SERVICE_TOKEN", source),
        timeout_seconds=timeout,
    )


# ---------------------------------------------------------------------------
# Immutable paper candidate verification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PaperCandidateExpectation:
    tenant_id: str
    persona_id: str
    capital_pool_id: str
    target_id: str
    target_version: str
    candidate_digest: str


def _require_paper_metadata(
    entry: Mapping[str, Any], label: str, *, expectation: PaperCandidateExpectation
) -> Mapping[str, Any]:
    """Outer owner metadata: paper, zero capital and the exact persona/pool."""
    metadata = entry.get("metadata")
    if not isinstance(metadata, Mapping):
        raise PaperCandidateInvalid(f"{label} metadata is required")
    if metadata.get("execution_context") != "paper":
        raise PaperCandidateInvalid(f"{label} metadata.execution_context must be 'paper'")
    capital = metadata.get("capital_scale_pct")
    if not _is_number(capital) or capital != 0:
        raise PaperCandidateInvalid(f"{label} metadata.capital_scale_pct must be 0")
    if metadata.get("persona_id") != expectation.persona_id:
        raise PaperCandidateInvalid(f"{label} metadata.persona_id does not match the approval persona")
    if metadata.get("capital_pool_id") != expectation.capital_pool_id:
        raise PaperCandidateInvalid(f"{label} metadata.capital_pool_id does not match the approval pool")
    return metadata


def _verify_paper_spec_payload(
    entry: Mapping[str, Any], metadata: Mapping[str, Any], label: str, *, expectation: PaperCandidateExpectation
) -> dict[str, Any]:
    """Checksummed spec content must itself bind tenant/persona/pool/paper/0%.

    The outer Registry metadata is owner-mutable; the embedded
    ``strategy_spec.metadata`` is covered by the candidate checksum, so both
    must agree with the approval expectation.
    """
    from services.registry.paper_strategy_spec import validate_strategy_spec

    spec = metadata.get("strategy_spec")
    if not isinstance(spec, Mapping):
        raise PaperCandidateInvalid(f"{label} metadata.strategy_spec is required")
    errors = validate_strategy_spec(dict(spec))
    if errors:
        raise PaperCandidateInvalid(f"{label} strategy_spec failed schema validation")
    if spec.get("strategy_id") != entry.get("strategy_id"):
        raise PaperCandidateInvalid(f"{label} strategy_spec.strategy_id does not match the entry")
    profile = spec.get("execution_profile")
    if not isinstance(profile, Mapping) or profile.get("execution_mode_hint") != "paper":
        raise PaperCandidateInvalid(f"{label} strategy_spec.execution_profile.execution_mode_hint must be 'paper'")
    embedded = spec.get("metadata")
    if not isinstance(embedded, Mapping):
        raise PaperCandidateInvalid(f"{label} strategy_spec.metadata is required")
    for key, expected in (
        ("tenant_id", expectation.tenant_id),
        ("persona_id", expectation.persona_id),
        ("capital_pool_id", expectation.capital_pool_id),
        ("execution_context", "paper"),
    ):
        if embedded.get(key) != expected:
            raise PaperCandidateInvalid(f"{label} strategy_spec.metadata.{key} does not match the approval expectation")
    embedded_capital = embedded.get("capital_scale_pct")
    if not _is_number(embedded_capital) or embedded_capital != 0:
        raise PaperCandidateInvalid(f"{label} strategy_spec.metadata.capital_scale_pct must be 0")
    computed = _canonical_sha256(spec)
    if computed != _text(entry, "checksum"):
        raise PaperCandidateInvalid(f"{label} strategy_spec checksum does not match the entry checksum")
    return {"kind": "strategy_spec", "checksum": computed}


def _verify_paper_bundle_payload(
    entry: Mapping[str, Any],
    metadata: Mapping[str, Any],
    *,
    expectation: PaperCandidateExpectation,
    read_entry_view: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError,
        strategy_artifact_checksum,
        validate_strategy_artifact,
    )

    artifact = metadata.get("strategy_artifact")
    if not isinstance(artifact, Mapping):
        raise PaperCandidateInvalid("bundle metadata.strategy_artifact is required")
    try:
        validate_strategy_artifact(artifact)
        computed = strategy_artifact_checksum(artifact)
    except StrategyArtifactValidationError as exc:
        raise PaperCandidateInvalid(f"bundle strategy_artifact validation failed: {exc}") from exc
    if computed != _text(entry, "checksum"):
        raise PaperCandidateInvalid("bundle strategy_artifact checksum does not match the entry checksum")
    for artifact_key, entry_key in (("artifact_id", "registry_id"), ("version", "version"), ("strategy_id", "strategy_id")):
        if artifact.get(artifact_key) != entry.get(entry_key):
            raise PaperCandidateInvalid(f"bundle strategy_artifact.{artifact_key} does not match the entry")
    algorithm_ref = artifact.get("algorithm_ref")
    if not isinstance(algorithm_ref, Mapping) or algorithm_ref.get("signal_interface") != PAPER_SIGNAL_INTERFACE:
        raise PaperCandidateInvalid("bundle algorithm_ref.signal_interface is not the paper signal interface")
    binding_intent = artifact.get("binding_intent")
    # Fresh provisioning has no observed RuntimeBinding. The optional object
    # must never be fabricated solely to pass approval. A supplied binding
    # intent is schema-validated above and must agree with the immutable source
    # spec below; absent intent earns authority only through that source spec.
    if binding_intent is not None and (
        not isinstance(binding_intent, Mapping) or binding_intent.get("persona_id") != expectation.persona_id
    ):
        raise PaperCandidateInvalid("bundle binding_intent.persona_id does not match the approval persona")
    if is_portfolio_artifact(artifact):
        return _verify_pool_envelope(
            entry, metadata, artifact, computed,
            expectation=expectation, read_entry_view=read_entry_view,
        )
    artifact_lineage = artifact.get("lineage")
    entry_lineage = entry.get("lineage")
    source_spec_id = artifact_lineage.get("source_strategy_spec_id") if isinstance(artifact_lineage, Mapping) else None
    if not isinstance(source_spec_id, str) or not source_spec_id.strip():
        raise PaperCandidateInvalid("bundle lineage.source_strategy_spec_id is required")
    if not isinstance(entry_lineage, Mapping) or entry_lineage.get("source_strategy_spec_id") != source_spec_id:
        raise PaperCandidateInvalid("bundle entry lineage.source_strategy_spec_id does not match the artifact lineage")

    spec_view = read_entry_view(source_spec_id)
    spec_entry = spec_view.get("entry") if isinstance(spec_view, Mapping) else None
    if not isinstance(spec_entry, Mapping) or spec_entry.get("registry_id") != source_spec_id:
        raise PaperCandidateInvalid("source strategy spec entry could not be read exactly")
    if spec_entry.get("artifact_type") != "strategy_spec" or spec_entry.get("artifact_state") != "approved":
        raise PaperCandidateInvalid("source strategy spec must be an approved strategy_spec entry")
    if spec_entry.get("owner_tenant") != expectation.tenant_id:
        raise PaperCandidateInvalid("source strategy spec belongs to a different tenant")
    if spec_entry.get("strategy_id") != entry.get("strategy_id") or spec_entry.get("version") != entry.get("version"):
        raise PaperCandidateInvalid("source strategy spec strategy/version does not match the bundle")
    # The parent spec must prove the same persona/pool in BOTH its outer owner
    # metadata and its checksummed embedded metadata.
    spec_metadata = _require_paper_metadata(spec_entry, "source strategy spec", expectation=expectation)
    spec_report = _verify_paper_spec_payload(
        spec_entry, spec_metadata, "source strategy spec", expectation=expectation
    )
    return {
        "kind": "execution_bundle",
        "checksum": computed,
        "source_strategy_spec_id": source_spec_id,
        "source_strategy_spec_checksum": spec_report["checksum"],
    }


def is_portfolio_artifact(artifact: Mapping[str, Any]) -> bool:
    from services.registry.portfolio_envelope import is_portfolio

    return is_portfolio(artifact)


def _read_approved_bundle(
    registry_id: str, label: str, *, expectation: PaperCandidateExpectation,
    read_entry_view: Callable[[str], Mapping[str, Any]],
) -> Mapping[str, Any]:
    view = read_entry_view(registry_id)
    found = view.get("entry") if isinstance(view, Mapping) else None
    if not isinstance(found, Mapping) or found.get("registry_id") != registry_id:
        raise PaperCandidateInvalid(f"{label} {registry_id!r} entry could not be read exactly")
    if found.get("artifact_type") != "execution_bundle" or found.get("artifact_state") != "approved":
        raise PaperCandidateInvalid(f"{label} {registry_id!r} must be an approved execution_bundle entry")
    if found.get("owner_tenant") != expectation.tenant_id:
        raise PaperCandidateInvalid(f"{label} {registry_id!r} belongs to a different tenant")
    return found


def _verify_pool_envelope(
    entry: Mapping[str, Any],
    metadata: Mapping[str, Any],
    artifact: Mapping[str, Any],
    computed: str,
    *,
    expectation: PaperCandidateExpectation,
    read_entry_view: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """A persona_portfolio_v1 pool envelope is approved through its members.

    The envelope has no single source spec (its lineage is parent_registry_ids),
    so none is required or invented.  Instead the pool version/digest pinned in
    the checksummed parameters must equal the owner metadata, and every pinned
    member must be an approved paper bundle in the same tenant/persona/pool at
    the pinned version and checksum under the single-spec rules.
    """
    params = artifact.get("parameters")
    params = params if isinstance(params, Mapping) else {}
    pool_version, pool_digest = metadata.get("pool_version"), metadata.get("pool_digest")
    if (
        isinstance(pool_version, bool) or not isinstance(pool_version, int) or pool_version < 1
        or not isinstance(pool_digest, str) or not pool_digest.strip()
    ):
        raise PaperCandidateInvalid("pool envelope metadata.pool_version and pool_digest are required")
    if params.get("pool_version") != pool_version or params.get("pool_digest") != pool_digest:
        raise PaperCandidateInvalid("pool envelope digest does not bind the metadata pool version")
    meta_tenant = metadata.get("tenant_id")
    if meta_tenant is not None and meta_tenant != expectation.tenant_id:
        raise PaperCandidateInvalid("pool envelope metadata.tenant_id does not match the approval tenant")
    lineage = artifact.get("lineage")
    parents = lineage.get("parent_registry_ids") if isinstance(lineage, Mapping) else None
    if not isinstance(parents, list) or not parents or not all(isinstance(p, str) for p in parents):
        raise PaperCandidateInvalid("pool envelope lineage.parent_registry_ids is required")
    entry_lineage = entry.get("lineage")
    if isinstance(entry_lineage, Mapping) and "parent_registry_ids" in entry_lineage and (
        sorted(entry_lineage["parent_registry_ids"]) != sorted(parents)
    ):
        raise PaperCandidateInvalid("pool envelope entry lineage does not match the artifact lineage")

    reports: list[dict[str, Any]] = []
    members = artifact["strategy_logic"].get("members") or []
    seen: set[str] = set()
    for pinned in members:
        member_id = pinned.get("artifact_id")
        if member_id in seen:
            raise PaperCandidateInvalid(f"pool envelope member {member_id!r} is listed twice")
        seen.add(member_id)
        if member_id not in parents:
            raise PaperCandidateInvalid(f"pool envelope lineage omits member {member_id!r}")
        if is_portfolio_artifact(pinned.get("artifact") or {}):
            raise PaperCandidateInvalid("pool envelope member must not be another envelope")
        member = _read_approved_bundle(
            member_id, "pool member", expectation=expectation, read_entry_view=read_entry_view
        )
        if member.get("version") != pinned.get("version"):
            raise PaperCandidateInvalid(f"pool member {member_id!r} version does not match the envelope")
        if member.get("checksum") != pinned.get("checksum"):
            raise PaperCandidateInvalid(f"pool member {member_id!r} checksum does not match the envelope")
        member_meta = _require_paper_metadata(member, "pool member", expectation=expectation)
        member_report = _verify_paper_bundle_payload(
            member, member_meta, expectation=expectation, read_entry_view=read_entry_view
        )
        reports.append({
            "artifact_id": member_id,
            "version": member["version"],
            "checksum": member_report["checksum"],
            "source_strategy_spec_id": member_report.get("source_strategy_spec_id"),
        })
    if not members:
        # A flat (all-removed) envelope pins no members; it must continue from
        # the approved paper envelope it replaces.
        previous = metadata.get("previous_envelope_id")
        if not isinstance(previous, str) or previous not in parents:
            raise PaperCandidateInvalid("empty pool envelope must name its previous envelope in lineage")
        parent = _read_approved_bundle(
            previous, "previous envelope", expectation=expectation, read_entry_view=read_entry_view
        )
        _require_paper_metadata(parent, "previous envelope", expectation=expectation)
    return {
        "kind": "execution_bundle",
        "checksum": computed,
        "pool_version": pool_version,
        "pool_digest": pool_digest,
        "pool_members": reports,
    }


def verify_paper_registry_candidate(
    view: Mapping[str, Any],
    *,
    expectation: PaperCandidateExpectation,
    read_entry_view: Callable[[str], Mapping[str, Any]],
) -> dict[str, Any]:
    """Bind a Registry entry view to the immutable paper candidate contract.

    Only immutable content is consulted: identity, checksum, embedded spec or
    artifact payload, lineage and the zero-capital paper metadata.  The
    mutable ``evaluation_summary`` is never read.
    """
    entry = view.get("entry") if isinstance(view, Mapping) else None
    if not isinstance(entry, Mapping):
        raise PaperCandidateInvalid("Registry entry view is malformed")
    identity = {
        "registry_id": expectation.target_id,
        "version": expectation.target_version,
        "owner_tenant": expectation.tenant_id,
        "checksum": expectation.candidate_digest,
        "artifact_state": "candidate",
    }
    for key, expected in identity.items():
        if not isinstance(expected, str) or not expected.strip() or entry.get(key) != expected:
            raise PaperCandidateInvalid(f"Registry candidate {key} does not match the approval target")
    metadata = _require_paper_metadata(entry, "candidate", expectation=expectation)
    artifact_type = entry.get("artifact_type")
    if artifact_type == "strategy_spec":
        report = _verify_paper_spec_payload(entry, metadata, "candidate", expectation=expectation)
    elif artifact_type == "execution_bundle":
        report = _verify_paper_bundle_payload(entry, metadata, expectation=expectation, read_entry_view=read_entry_view)
    else:
        raise PaperCandidateInvalid("Registry candidate artifact_type is not a paper spec or bundle")
    report.update({
        "registry_id": expectation.target_id,
        "version": expectation.target_version,
        "tenant_id": expectation.tenant_id,
        "persona_id": expectation.persona_id,
        "capital_pool_id": expectation.capital_pool_id,
        "execution_context": "paper",
        "capital_scale_pct": 0,
    })
    return report


def expectation_from_decision(decision: Mapping[str, Any]) -> PaperCandidateExpectation:
    values = {
        "tenant_id": decision.get("tenant_id"),
        "persona_id": decision.get("persona_id"),
        "capital_pool_id": decision.get("capital_pool_id"),
        "target_id": decision.get("target_id"),
        "target_version": decision.get("target_version"),
        "candidate_digest": decision.get("candidate_digest"),
    }
    for key, value in values.items():
        if not isinstance(value, str) or not value.strip():
            raise PaperCandidateInvalid(f"Approval decision {key} is required for paper candidate verification")
    return PaperCandidateExpectation(**values)  # type: ignore[arg-type]


def _verify_card_spec_entry(entry: Mapping[str, Any], *, tenant_id: Any, state: str) -> dict[str, Any]:
    """The immutable content of a gate-ready hypothesis-card StrategySpec entry (no capital pool)."""
    from services.registry.paper_strategy_spec import validate_strategy_spec

    if entry.get("artifact_type") != "strategy_spec":
        raise PaperCandidateInvalid("Card admission candidate must be a strategy_spec entry")
    if entry.get("artifact_state") != state or entry.get("owner_tenant") != tenant_id:
        raise PaperCandidateInvalid("card strategy_spec entry state or tenant does not match the approval")
    metadata = entry.get("metadata")
    spec = metadata.get("strategy_spec") if isinstance(metadata, Mapping) else None
    if not isinstance(spec, Mapping):
        raise PaperCandidateInvalid("candidate metadata.strategy_spec is required")
    if validate_strategy_spec(dict(spec)):
        raise PaperCandidateInvalid("candidate strategy_spec failed schema validation")
    if spec.get("strategy_id") != entry.get("strategy_id"):
        raise PaperCandidateInvalid("candidate strategy_spec.strategy_id does not match the entry")
    profile = spec.get("execution_profile")
    # A card spec is compiled as a research spec; canary / live (or no) hint is never admitted here.
    if not isinstance(profile, Mapping) or profile.get("execution_mode_hint") not in ("research", "paper"):
        raise PaperCandidateInvalid(
            "candidate strategy_spec.execution_profile.execution_mode_hint must be 'research' or 'paper'")
    embedded = spec.get("metadata")
    embedded = embedded if isinstance(embedded, Mapping) else {}
    wiring = embedded.get("card_wiring")
    if not isinstance(wiring, Mapping) or wiring.get("status") != "gate_ready":
        raise PaperCandidateInvalid("candidate strategy_spec is not a gate-ready hypothesis card spec")
    # entry.owner_tenant is already exact; any tenant the spec or the entry metadata names must agree.
    for label, bound in (("strategy_spec.metadata", embedded), ("metadata", metadata)):
        if bound.get("tenant_id") not in (None, tenant_id):
            raise PaperCandidateInvalid(f"candidate {label}.tenant_id does not match the approval tenant")
    capital = embedded.get("capital_scale_pct", 0)
    if not _is_number(capital) or capital != 0:
        raise PaperCandidateInvalid("candidate strategy_spec.metadata.capital_scale_pct must be 0")
    if _canonical_sha256(spec) != _text(entry, "checksum"):
        raise PaperCandidateInvalid("candidate strategy_spec checksum does not match the entry checksum")
    return {"kind": "card_strategy_spec", "checksum": _text(entry, "checksum"),
            "registry_id": entry.get("registry_id"), "tenant_id": tenant_id}


def _verify_card_bundle_entry(entry: Mapping[str, Any], *, decision: Mapping[str, Any]) -> dict[str, Any]:
    """The pool candidate a card-derived spec produced: a checksummed paper bundle bound to the demand.

    The artifact's ``lineage.source_strategy_spec_id`` names the spec by strategy id, which Registry cannot
    resolve as an entry id, so the parent spec is not re-read here; the bundle is bound to the open demand
    and persona the research proposer named, to paper / zero capital, and to the gate's verified proof.
    """
    from services.registry.strategy_artifact import (
        StrategyArtifactValidationError, strategy_artifact_checksum, validate_strategy_artifact)

    tenant_id = decision.get("tenant_id")
    if entry.get("owner_tenant") != tenant_id or entry.get("artifact_state") != "candidate":
        raise PaperCandidateInvalid("card bundle entry state or tenant does not match the approval")
    metadata = entry.get("metadata")
    if not isinstance(metadata, Mapping):
        raise PaperCandidateInvalid("bundle metadata is required")
    artifact = metadata.get("strategy_artifact")
    if not isinstance(artifact, Mapping):
        raise PaperCandidateInvalid("bundle metadata.strategy_artifact is required")
    try:
        validate_strategy_artifact(artifact)
        computed = strategy_artifact_checksum(artifact)
    except StrategyArtifactValidationError as exc:
        raise PaperCandidateInvalid(f"bundle strategy_artifact validation failed: {exc}") from exc
    if computed != _text(entry, "checksum"):
        raise PaperCandidateInvalid("bundle strategy_artifact checksum does not match the entry checksum")
    for artifact_key, entry_key in (("artifact_id", "registry_id"), ("version", "version"), ("strategy_id", "strategy_id")):
        if artifact.get(artifact_key) != entry.get(entry_key):
            raise PaperCandidateInvalid(f"bundle strategy_artifact.{artifact_key} does not match the entry")
    algorithm_ref = artifact.get("algorithm_ref")
    if not isinstance(algorithm_ref, Mapping) or algorithm_ref.get("signal_interface") != PAPER_SIGNAL_INTERFACE:
        raise PaperCandidateInvalid("bundle algorithm_ref.signal_interface is not the paper signal interface")
    lineage = artifact.get("lineage")
    source_id = lineage.get("source_strategy_spec_id") if isinstance(lineage, Mapping) else None
    entry_lineage = entry.get("lineage")
    if not isinstance(source_id, str) or not source_id.strip() or not isinstance(entry_lineage, Mapping) \
            or entry_lineage.get("source_strategy_spec_id") != source_id:
        raise PaperCandidateInvalid("bundle lineage.source_strategy_spec_id is required and must match the entry")
    if metadata.get("execution_context") != "paper" or not _is_number(metadata.get("capital_scale_pct")) \
            or metadata.get("capital_scale_pct") != 0:
        raise PaperCandidateInvalid("bundle metadata must be paper execution context with zero capital")
    if metadata.get("persona_id") != decision.get("persona_id") or metadata.get("demand_ref") != decision.get("proposal_id"):
        raise PaperCandidateInvalid("bundle metadata persona/demand does not match the approval")
    if metadata.get("tenant_id") not in (None, tenant_id):
        raise PaperCandidateInvalid("bundle metadata.tenant_id does not match the approval tenant")
    if entry.get("replication_success") is not True or not isinstance(entry.get("replication_proof"), Mapping):
        raise PaperCandidateInvalid("bundle has no verified replication proof")
    return {"kind": "card_execution_bundle", "checksum": computed, "tenant_id": tenant_id,
            "source_strategy_spec_id": source_id}


def verify_card_admission_candidate(view: Mapping[str, Any], decision: Mapping[str, Any]) -> dict[str, Any]:
    """Immutable checks of what a research card admission may approve (paper research, no capital pool).

    Either the gate-ready hypothesis-card StrategySpec itself or the pool candidate (execution bundle) a
    card-derived spec produced after passing the gate.
    """
    entry = view.get("entry") if isinstance(view, Mapping) else None
    if not isinstance(entry, Mapping):
        raise PaperCandidateInvalid("Registry entry view is malformed")
    for key, expected in (("registry_id", decision.get("target_id")), ("version", decision.get("target_version")),
                          ("owner_tenant", decision.get("tenant_id")), ("checksum", decision.get("candidate_digest")),
                          ("artifact_state", "candidate")):
        if not isinstance(expected, str) or not expected.strip() or entry.get(key) != expected:
            raise PaperCandidateInvalid(f"Registry candidate {key} does not match the approval target")
    tenant_id = decision.get("tenant_id")
    if entry.get("artifact_type") == "execution_bundle":
        return _verify_card_bundle_entry(entry, decision=decision)
    return _verify_card_spec_entry(entry, tenant_id=tenant_id, state="candidate")


def verify_paper_candidate_for_decision(
    decision: Mapping[str, Any], *, reader: PaperRegistryReader
) -> dict[str, Any]:
    """Governance-side recheck of the actual Registry candidate at review/decide."""
    if decision.get("owner_user_id") != DEV_PAPER_PROVISIONER_SUBJECT:
        # Only a research card admission reaches here with another owner (require_paper_owned_decision
        # admits no other); it has no capital pool, so it is checked as a card-derived paper spec.
        return verify_card_admission_candidate(reader.get_entry_view(str(decision.get("target_id") or "")), decision)
    expectation = expectation_from_decision(decision)
    view = reader.get_entry_view(expectation.target_id)
    return verify_paper_registry_candidate(
        view, expectation=expectation, read_entry_view=reader.get_entry_view
    )


def paper_candidate_usage_context(
    entry: Mapping[str, Any],
    *,
    evidence: Mapping[str, Any],
    read_entry: Callable[[str], Mapping[str, Any] | None],
    environment: str,
) -> ApprovalUsageContext:
    """Registry-side admission: only a validated paper candidate earns a context.

    ``entry`` is the durable base entry (RegistryEntry.to_dict()), ``evidence``
    the ApprovalEvidence dump, ``read_entry`` an exact local owner read.
    """
    expectation = PaperCandidateExpectation(
        tenant_id=str(entry.get("owner_tenant") or ""),
        persona_id=str(evidence.get("persona_id") or ""),
        capital_pool_id=str(evidence.get("capital_pool_id") or ""),
        target_id=str(entry.get("registry_id") or ""),
        target_version=str(entry.get("version") or ""),
        candidate_digest=str(entry.get("checksum") or ""),
    )

    def _view(registry_id: str) -> Mapping[str, Any]:
        record = read_entry(registry_id)
        if record is None:
            raise PaperCandidateInvalid("source strategy spec entry not found")
        return {"entry": record}

    owner = evidence.get("owner_user_id")
    if owner not in (None, "", DEV_PAPER_PROVISIONER_SUBJECT):
        # A scoped approval owned by another principal exists only through the research card admission
        # widening (Governance stamps the scope on nothing else): the entry must be that gate-ready,
        # zero-capital card spec, or the pool candidate it produced, at exactly the approved version/digest
        # (persona and proposal_id are the ones the approval names).
        verify_card_admission_candidate({"entry": entry}, {
            "tenant_id": evidence.get("tenant_id"), "target_id": evidence.get("target_id"),
            "target_version": evidence.get("target_version"), "candidate_digest": evidence.get("candidate_digest"),
            "persona_id": evidence.get("persona_id"), "proposal_id": evidence.get("proposal_id")})
        return ApprovalUsageContext(environment=environment, target_stage="paper", capital_scale_pct=0)
    verify_paper_registry_candidate({"entry": entry}, expectation=expectation, read_entry_view=_view)
    return ApprovalUsageContext(environment=environment, target_stage="paper", capital_scale_pct=0)


__all__ = [
    "PERSONA_LIFECYCLE_TARGET",
    "PAPER_LADDER_TRANSITIONS",
    "PersonaLifecycleGrant",
    "PersonaPaperBindingVerifier",
    "configured_persona_lifecycle_grant",
    "configured_persona_paper_binding_verifier",
    "is_persona_lifecycle_decision",
    "require_paper_lifecycle_decision",
    "DEV_PAPER_PROVISIONER_SUBJECT",
    "DEV_PAPER_APPROVAL_SCOPE",
    "DEV_PAPER_TENANT_ID",
    "DEV_PAPER_ROLES",
    "DEV_PAPER_AUTHORIZATION_SCOPE",
    "PAPER_SIGNAL_INTERFACE",
    "PAPER_APPROVAL_MAX_TTL",
    "AuthorizationScopeError",
    "UsageContextViolation",
    "PaperApprovalDenied",
    "PaperCandidateInvalid",
    "PaperCandidateUnavailable",
    "ApprovalUsageContext",
    "DevPaperGrant",
    "PaperRegistryReader",
    "PaperCandidateExpectation",
    "normalize_authorization_scope",
    "enforce_authorization_scope",
    "authorization_scope_errors",
    "require_dedicated_subject_scope",
    "current_environment",
    "dev_paper_feature_enabled",
    "resolve_dev_paper_grant",
    "validate_paper_expiry",
    "validate_paper_proposal",
    "require_paper_owned_decision",
    "require_card_admission_decision",
    "RiskPolicyGrant",
    "configured_risk_policy_grant",
    "is_risk_policy_decision",
    "require_paper_risk_policy_decision",
    "risk_policy_candidate_failure",
    "verify_card_admission_candidate",
    "CardAdmissionGrant",
    "configured_card_admission_grant",
    "CARD_ADMISSION_GRANT_VALUE",
    "validate_paper_decide_body",
    "configured_paper_registry_reader",
    "verify_paper_registry_candidate",
    "verify_paper_candidate_for_decision",
    "expectation_from_decision",
    "paper_candidate_usage_context",
]

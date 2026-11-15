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
_PROPOSER_SUBJECT_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:@-")
_ACTIVE_RUNTIME_STATES = frozenset({"active", "running", "ok"})
PAPER_SIGNAL_INTERFACE = "services.execution.lean_runtime.paper_signal_producer:Strategy"
PAPER_APPROVAL_MAX_TTL = timedelta(hours=24)

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
class DevPaperGrant:
    subject: str
    tenant_id: str
    persona_lifecycle: PersonaLifecycleGrant | None = None

    @property
    def authorization_scope(self) -> dict[str, Any]:
        return normalize_authorization_scope(DEV_PAPER_AUTHORIZATION_SCOPE)


def configured_persona_lifecycle_grant(env: Mapping[str, str] | None = None) -> PersonaLifecycleGrant | None:
    """The explicit lifecycle widening carried by the mounted grant file, else None.

    Only a mounted grant file whose whole content is
    ``enabled;persona_lifecycle=paper_ladder;proposer=<subject>`` widens the
    grant; the plain ``enabled`` content and an unmounted grant behave exactly
    as before.  A malformed or unreadable widening is never a widening.
    """
    from services.service_token_file import configured_service_token
    source = env if env is not None else os.environ
    if not str(source.get("GOVERNANCE_DEV_PAPER_GRANT_FILE", "") or "").strip():
        return None
    try:
        content = configured_service_token("GOVERNANCE_DEV_PAPER_GRANT", source)
    except RuntimeError:
        return None
    if not content.startswith(PERSONA_LIFECYCLE_GRANT_PREFIX):
        return None
    proposer = content[len(PERSONA_LIFECYCLE_GRANT_PREFIX):]
    if not proposer or proposer == DEV_PAPER_PROVISIONER_SUBJECT or not set(proposer) <= _PROPOSER_SUBJECT_CHARS:
        return None
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
    return PersonaLifecycleGrant(proposer_subject=proposer, grant_id=PERSONA_LIFECYCLE_GRANT_ID_PREFIX + digest)


def dev_paper_feature_enabled(env: Mapping[str, str] | None = None) -> bool:
    from services.service_token_file import configured_dev_paper_grant_enabled
    source = env if env is not None else os.environ
    enabled = str(source.get("GOVERNANCE_DEV_PAPER_APPROVAL_ENABLED", "") or "").strip().lower()
    return (enabled == "true" and current_environment(source) == DEV_PAPER_ENVIRONMENT
            and (configured_dev_paper_grant_enabled(source)
                 or configured_persona_lifecycle_grant(source) is not None))


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
                         persona_lifecycle=configured_persona_lifecycle_grant(env))


# ---------------------------------------------------------------------------
# Proposal / transition admission for the grant
# ---------------------------------------------------------------------------

def validate_paper_expiry(expires_at: Any, *, created_at: Any, now: datetime) -> datetime:
    """expires_at must be finite, in the future and <= created_at + 24h."""
    try:
        expiry = parse_rfc3339(expires_at, "expires_at")
        created = parse_rfc3339(created_at, "created_at")
    except ValueError as exc:
        raise PaperApprovalDenied(str(exc), 422) from exc
    if expiry <= now:
        raise PaperApprovalDenied("Paper approval expires_at must be in the future", 422)
    if expiry > created + PAPER_APPROVAL_MAX_TTL:
        raise PaperApprovalDenied("Paper approval expires_at must be at most 24h after created_at", 422)
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
    """The dedicated subject may only act on its own exact-scope proposals."""
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
        supplied = validate_paper_expiry(body.get("expires_at"), created_at=decision.get("created_at"), now=now)
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


def verify_paper_candidate_for_decision(
    decision: Mapping[str, Any], *, reader: PaperRegistryReader
) -> dict[str, Any]:
    """Governance-side recheck of the actual Registry candidate at review/decide."""
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
    "validate_paper_decide_body",
    "configured_paper_registry_reader",
    "verify_paper_registry_candidate",
    "verify_paper_candidate_for_decision",
    "expectation_from_decision",
    "paper_candidate_usage_context",
]

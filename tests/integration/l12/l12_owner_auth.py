"""Human owner identities and governed approval shared by the deployed L12 suites.

The isolated harness mints ``PANTHEON_L12_OPERATOR_TOKEN`` (operator) and
``PANTHEON_L12_REVIEWER_TOKEN`` (governance_reviewer) with the signer that
Registry and Governance verify, for the tenant the stack runs as.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

# post(path, payload, headers, expected_statuses) -> decoded JSON object
GovernancePost = Callable[[str, Mapping[str, Any], Mapping[str, str], Sequence[int]], Mapping[str, Any]]


def compose_file_args() -> list[str]:
    """``-f`` arguments for the compose file list the isolated harness exports.

    The harness exports ``PANTHEON_L12_COMPOSE_FILES`` (os.pathsep separated);
    the singular legacy name is honoured only when the plural one is unset.
    """
    raw = os.getenv("PANTHEON_L12_COMPOSE_FILES", "").strip() or os.getenv(
        "PANTHEON_L12_COMPOSE_FILE", ""
    ).strip()
    args: list[str] = []
    for part in raw.split(os.pathsep):
        if part.strip():
            args.extend(["-f", part.strip()])
    return args


def human_token(role: str) -> str:
    if hosted_chain_enabled():
        return hosted_token(role)
    token = os.getenv(f"PANTHEON_L12_{role}_TOKEN", "").strip()
    if not token:
        raise RuntimeError(f"PANTHEON_L12_{role}_TOKEN is not set by the isolated harness")
    return token


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_subject(token: str) -> str:
    payload = token.split(".")[1]
    return str(json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"])


def approve_registry_entry(
    post: GovernancePost,
    *,
    decision_id: str,
    entry: Mapping[str, Any],
    tenant_id: str,
    rationale: str,
    risk_level: str = "low",
    proposal: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Record a decided Governance approval bound to one exact Registry entry.

    An operator proposes; a distinct governance_reviewer reviews and decides,
    with the expiry and candidate digest Registry requires before approving.
    """
    if hosted_chain_enabled():
        raise HostedDecisionNotCallerOwned(
            "hosted mode never proposes, reviews or decides an approval; "
            "read back a decided record with require_decided_approval"
        )
    operator, reviewer = human_token("OPERATOR"), human_token("REVIEWER")
    reviewer_id = token_subject(reviewer)
    path = f"/api/governance/approvals/{decision_id}"

    def command(token: str, step: str, payload: Mapping[str, Any], expected: Sequence[int]) -> Mapping[str, Any]:
        headers = {**bearer(token), "Idempotency-Key": f"{decision_id}-{step}"}
        return post(path + f"/{step}" if step != "propose" else "/api/governance/approvals", payload, headers, expected)

    proposed = command(operator, "propose", {
        **dict(proposal or {}),
        "decision_id": decision_id,
        "expected_version": 0,
        "target_type": "registry_entry",
        "target_id": entry.get("registry_id"),
        "target_version": entry.get("version"),
        "candidate_digest": entry.get("checksum"),
        "risk_level": risk_level,
        "tenant_id": tenant_id,
        "owner_user_id": token_subject(operator),
    }, (201,))
    reviewed = command(reviewer, "review", {
        "expected_version": proposed.get("version"),
        "actor_id": reviewer_id,
        "actor_role": "governance_reviewer",
    }, (200,))
    decided = command(reviewer, "decide", {
        "expected_version": reviewed.get("version"),
        "actor_id": reviewer_id,
        "actor_role": "governance_reviewer",
        "outcome": "approved",
        "rationale": rationale,
        "candidate_digest": entry.get("checksum"),
        "expires_at": (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, (200,))
    if decided.get("decision_state") != "decided" or decided.get("decision") != "approved":
        raise RuntimeError(f"Governance did not record an approved decision: {decided!r}")
    return decided


def registry_advance_body(
    entry: Mapping[str, Any],
    target_state: str,
    *,
    command_key: str,
    approval_decision_id: str | None = None,
) -> dict[str, Any]:
    """Registry advance is a caller-bound CAS on the entry the caller observed."""
    body: dict[str, Any] = {
        "target_state": target_state,
        "command_key": command_key,
        "expected_artifact_state": entry.get("artifact_state"),
        "expected_version": entry.get("version"),
        "expected_updated_at": entry.get("updated_at"),
    }
    if approval_decision_id:
        body["approval_decision_id"] = approval_decision_id
    return body


# ---------------------------------------------------------------------------
# Hosted chain mode (PANTHEON_L12_HOSTED_CHAIN=1)
#
# The hosted dev stack is shared and the humans behind H1-H4 decide on their own
# schedule. Hosted callers therefore (a) run one phase per process, (b) take the
# stable chain id and the predecessor owner IDs from the input the parent hands
# over, (c) read each human decision back from its owner and pause *before* the
# action it covers when it is absent, and (d) never mint a token, propose or
# decide an approval, or assert ``mfa_verified``.
# ---------------------------------------------------------------------------

HOSTED_FLAG = "PANTHEON_L12_HOSTED_CHAIN"
HOSTED_CHAIN_ID_ENV = "PANTHEON_L12_CROSS_LOOP_RUN_ID"
HOSTED_PHASE_ENV = "PANTHEON_L12_HOSTED_CHAIN_PHASE"
HOSTED_INPUT_ENV = "PANTHEON_L12_HOSTED_CHAIN_INPUT"
HOSTED_PHASES = ("A_research", "B_learning", "C_promote", "D_observe", "E_close")
HOSTED_ID_PREFIX = "hosted-all12-"
HOSTED_TASK_ID = "HOSTED-ALL12-CHAIN-DESIGN-20261023"
HOSTED_LABELS = {
    "task_id": HOSTED_TASK_ID,
    "paper_only": True,
    "execution_context": "paper",
    "fixture": "hosted_all12",
}
_FORBIDDEN_INPUT_KEY = re.compile(r"token|secret|password|credential|authorization|mfa", re.IGNORECASE)
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class HostedDecisionNotCallerOwned(RuntimeError):
    """A hosted caller attempted to decide on a human's behalf."""


class HostedInputError(RuntimeError):
    """The parent-supplied chain input is missing, malformed, or carries credentials."""


class HostedPause(Exception):
    """Normal incomplete progress: stop before the action a human decision covers."""

    def __init__(
        self, blocked_before: str, detail: str, resource_ids: Mapping[str, Any] | None = None
    ) -> None:
        super().__init__(detail)
        self.blocked_before = blocked_before
        self.detail = detail
        self.resource_ids = dict(resource_ids or {})


@dataclass(frozen=True)
class HostedChainInput:
    chain_id: str
    phase: str
    predecessors: Mapping[str, Any] = field(default_factory=dict)
    decisions: Mapping[str, Any] = field(default_factory=dict)

    def predecessor(self, key: str) -> str:
        return str(self.predecessors.get(key) or "").strip()

    def decision(self, point: str) -> Mapping[str, Any] | None:
        ref = self.decisions.get(point)
        return ref if isinstance(ref, Mapping) and ref else None

    def id(self, *parts: str) -> str:
        return hosted_id(self.chain_id, *parts)

    def labels(self) -> dict[str, Any]:
        return {"chain_id": self.chain_id, **HOSTED_LABELS}


def hosted_chain_enabled() -> bool:
    return os.getenv(HOSTED_FLAG, "").strip() == "1"


def hosted_id(chain_id: str, *parts: str) -> str:
    """Deterministic owner ID: every process of one chain derives the same value."""
    return HOSTED_ID_PREFIX + "-".join([chain_id, *parts])


def hosted_token(role: str) -> str:
    """Real executing identity supplied by the workflow; the minted harness tokens are refused."""
    if role.upper() != "OPERATOR":
        raise HostedDecisionNotCallerOwned(
            f"hosted mode has no {role} identity: a human decision is read back, never made by the caller"
        )
    token = os.getenv("PANTHEON_L12_HOSTED_EXECUTOR_TOKEN", "").strip()
    if not token:
        raise RuntimeError("PANTHEON_L12_HOSTED_EXECUTOR_TOKEN is not set for the hosted chain")
    return token


def persona_writer_identity() -> tuple[str, str] | None:
    """(Authorization value, actor_id) of the Persona service writer, hosted mode only.

    The workflow stages the persona container's own opaque service token and actor id.
    The token is never decoded; ``None`` means use the caller's human identity (isolated).
    """
    if not hosted_chain_enabled():
        return None
    token = os.getenv("PANTHEON_L12_PERSONA_SERVICE_TOKEN", "").strip()
    if not token:
        return None
    actor = os.getenv("PANTHEON_L12_PERSONA_SERVICE_ACTOR_ID", "").strip() or "operator-bff"
    return f"Bearer {token.removeprefix('Bearer ').strip()}", actor


def persona_create_identity() -> dict[str, Any]:
    """Create-request identity fields: service actor mutates, the executing human stays owner.

    ``actor_id``/``created_by`` is the Persona service actor; ``owner`` and
    ``metadata.requested_by`` keep the genuine human subject so the logical owner does not
    silently become the service actor. Isolated mode keeps the human as the actor.
    """
    writer = persona_writer_identity()
    human = token_subject(human_token("OPERATOR"))
    if writer is None:
        return {"actor_id": human}
    return {
        "actor_id": writer[1],
        "owner": human,
        "metadata": {"requested_by": {"actor_type": "operator", "actor_id": human}},
    }


_CAPITAL_TOKEN_SNIPPET = (
    "import sys;"
    "from services.control_plane.bff.personas.service import _PersonaOwnerHttpTransport as T;"
    "sys.stdout.write(T(tenant_id=sys.argv[1])._service_jwt('capital'))"
)


def fresh_capital_authorization(compose_command: Callable[..., list[str]], tenant_id: str) -> str:
    """A genuine, just-issued Capital service ``Authorization`` value.

    The configured operator-bff signs it with its own production transport (the token lives
    ~120s, so it is requested per call and never held for a phase). It is captured privately:
    never logged, written to evidence, or placed in an error message.
    """
    import subprocess

    result = subprocess.run(
        compose_command("exec", "-T", "operator-bff", "python", "-c", _CAPITAL_TOKEN_SNIPPET, tenant_id),
        capture_output=True, text=True, timeout=60, check=False,
    )
    token = result.stdout.strip()
    if result.returncode != 0 or token.count(".") != 2:
        raise RuntimeError(f"operator-bff did not issue a Capital service token (exit {result.returncode})")
    return f"Bearer {token}"


def capital_identity(authorization: str) -> tuple[str, str]:
    """(actor_id, service) Capital will compare a mutation body against, read from the real token.

    Capital trusts ``delegated_actor_id``/``operator_id``/``user_id`` and falls back to the
    token subject; the calling service must be one of the token's service claims. The claims
    are only decoded (never verified or minted) so the body can name the genuine principal.
    """

    token = authorization.removeprefix("Bearer ").strip()
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        raise RuntimeError("hosted Capital token is not a decodable JWT; its actor cannot be named") from None
    if not isinstance(claims, Mapping):
        raise RuntimeError("hosted Capital token claims are not an object")

    def first(keys: Sequence[str]) -> str:
        return next((str(claims[k]).strip() for k in keys if str(claims.get(k) or "").strip()), "")

    actor = first(("delegated_actor_id", "operator_id", "user_id", "sub"))
    service = first(("service", "service_id", "serviceId", "client_id", "azp", "sub"))
    if not actor or not service:
        raise RuntimeError("hosted Capital token carries no actor or service claim")
    return actor, service


def _reject_credentials(node: Any, path: str = "") -> None:
    if isinstance(node, Mapping):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if _FORBIDDEN_INPUT_KEY.search(str(key)) or str(key).lower() == "approved":
                raise HostedInputError(f"hosted chain input carries a credential or approval flag: {here}")
            _reject_credentials(value, here)
    elif isinstance(node, (list, tuple)):
        for index, value in enumerate(node):
            _reject_credentials(value, f"{path}[{index}]")


def load_hosted_input(allowed_phases: Sequence[str]) -> HostedChainInput:
    chain_id = os.getenv(HOSTED_CHAIN_ID_ENV, "").strip()
    if not _SAFE_ID.match(chain_id):
        raise HostedInputError(f"{HOSTED_CHAIN_ID_ENV} must be a stable nonsecret chain identifier")
    phase = os.getenv(HOSTED_PHASE_ENV, "").strip()
    if phase not in HOSTED_PHASES:
        raise HostedInputError(f"{HOSTED_PHASE_ENV} must be one of {list(HOSTED_PHASES)}")
    if phase not in allowed_phases:
        raise HostedInputError(f"phase {phase} is not run by this suite (allowed: {list(allowed_phases)})")
    document: Mapping[str, Any] = {}
    raw_path = os.getenv(HOSTED_INPUT_ENV, "").strip()
    if raw_path:
        try:
            loaded = json.loads(Path(raw_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HostedInputError(f"{HOSTED_INPUT_ENV} is unreadable: {exc}") from exc
        if not isinstance(loaded, Mapping):
            raise HostedInputError("hosted chain input must be a JSON object")
        document = loaded
    _reject_credentials(document)
    if document.get("chain_id", chain_id) != chain_id or document.get("phase", phase) != phase:
        raise HostedInputError("hosted chain input chain_id/phase disagree with the environment")
    predecessors, decisions = document.get("predecessors") or {}, document.get("decisions") or {}
    if not isinstance(predecessors, Mapping) or not isinstance(decisions, Mapping):
        raise HostedInputError("predecessors and decisions must be JSON objects")
    return HostedChainInput(chain_id, phase, dict(predecessors), dict(decisions))


def hosted_receipt_fields(
    chain: HostedChainInput,
    *,
    phase_status: str,
    blocked_before: str | None = None,
    resource_ids: Mapping[str, Any] | None = None,
    detail: str | None = None,
) -> dict[str, Any]:
    """Fields hosted adds to the existing per-suite receipt; ``paused`` is never ``passed``."""
    if phase_status not in {"completed", "paused", "failed"}:
        raise ValueError(f"unknown hosted phase_status {phase_status!r}")
    fields: dict[str, Any] = {
        "hosted_chain": True,
        "chain_id": chain.chain_id,
        "phase": chain.phase,
        "phase_status": phase_status,
        "blocked_before": blocked_before,
        "resource_ids": {k: v for k, v in dict(resource_ids or {}).items() if v not in (None, "")},
    }
    if detail:
        fields["detail"] = detail
    return fields


def require_decided_approval(
    read: Callable[[str], Mapping[str, Any] | None],
    ref: Mapping[str, Any] | None,
    *,
    point: str,
    blocked_before: str,
    tenant_id: str,
    target_ids: Sequence[str] = (),
    candidate_digest: str | None = None,
    expected: Mapping[str, Any] | None = None,
    resource_ids: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Read one human Governance decision back; pause before ``blocked_before`` unless it is decided.

    ``read`` GETs ``/api/governance/approvals/{id}`` and returns None on 404. The caller
    only ever reads: an absent, undecided, revoked, expired, cross-tenant or differently
    bound record pauses the phase with the concrete reason.
    """

    def pause(reason: str) -> HostedPause:
        return HostedPause(blocked_before, f"{point}: {reason}", resource_ids)

    decision_id = str((ref or {}).get("decision_id") or "").strip()
    if not decision_id:
        raise pause("no decision record referenced in the chain input")
    record = read(decision_id)
    if not isinstance(record, Mapping):
        raise pause(f"decision {decision_id} not found at the owner")
    if record.get("tenant_id") != tenant_id:
        raise pause(f"decision {decision_id} belongs to tenant {record.get('tenant_id')!r}")
    if record.get("decision_state") != "decided" or record.get("decision") != "approved":
        raise pause(
            f"decision {decision_id} is {record.get('decision_state')!r}/{record.get('decision')!r}, not decided/approved"
        )
    if record.get("revoked_at") or record.get("superseded_by"):
        raise pause(f"decision {decision_id} is revoked or superseded")
    expires_at = str(record.get("expires_at") or "")
    if expires_at and expires_at <= datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"):
        raise pause(f"decision {decision_id} expired at {expires_at}")
    if target_ids and record.get("target_id") not in set(target_ids):
        raise pause(f"decision {decision_id} is bound to {record.get('target_id')!r}, not {list(target_ids)}")
    if candidate_digest and record.get("candidate_digest") != candidate_digest:
        raise pause(f"decision {decision_id} candidate digest does not match {candidate_digest}")
    for name, value in (expected or {}).items():
        # Same exact-field scope the Deployment/Runtime owners enforce; a missing field never matches.
        if value in (None, "") or record.get(name) != value:
            raise pause(f"decision {decision_id} {name} is {record.get(name)!r}, expected {value!r}")
    return {
        "decision_id": decision_id,
        "version": record.get("version"),
        "target_id": record.get("target_id"),
        "decided_at": record.get("decided_at"),
        "decision_maker_actor_id": record.get("actor_id"),
        "owner_user_id": record.get("owner_user_id"),
        "tenant_id": record.get("tenant_id"),
    }

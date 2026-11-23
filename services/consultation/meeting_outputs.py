"""Route deliberative meeting proposals to governed owner entry points (C2 / M5).

A meeting executes nothing. Each proposal is dispatched once and idempotently
to its existing owner entry point under the sponsor persona's own identity
(except ``agora_candidate_decision``, which has ``sponsor_persona_id=None`` and
routes to the Agora candidate-decision path). Advisors and private servants
never sponsor. Downstream gates remain unchanged: every dispatched proposal
enters at its entry point's initial state, and live status is read back from
the owner.
"""
from __future__ import annotations

import contextlib
import copy
import fcntl
import hashlib
import json
import os
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import jsonschema

from services.consultation.sponsor_decision_bridge import (
    ApprovalDecisionProposal,
    EvolutionDecisionProposal,
    FORBIDDEN_SPONSOR_ORIGINS,
    SponsorDecisionBridgeError,
    bridge,
    validate_sponsor_persona,
)
from services.runtime_auth_inbound import encode_jwt_hs256
from services.service_token_file import configured_service_token

SUPPORTED_PROPOSAL_KINDS: frozenset[str] = frozenset(
    {
        "research_demand",
        "evolution_proposal",
        "harness_patch",
        "lesson_candidate",
        "lesson_rule",
        "pool_decision",
        "governance_approval",
        "agora_candidate_decision",
    }
)

FORBIDDEN_APPROVED_OR_APPLIED_STATES: frozenset[str] = frozenset(
    {
        "approved",
        "approved_with_conditions",
        "applied",
        "committed",
        "executed",
        "endorsed",
        "merged",
        "canary_approved",
        "live_approved",
    }
)

HttpTransport = Callable[[str, str, Mapping[str, str], dict[str, Any] | None], tuple[int, dict[str, Any]]]

_LOCK = threading.RLock()
_CONFIGURED_TRANSPORT: HttpTransport | None = None
_CONFIGURED_STORE_PATH: Path | None = None
_LIVE_PROPOSAL_REFS: dict[str, list[dict[str, Any]]] = {}
_LIVE_MEETING_REFS: dict[str, list[dict[str, Any]]] = {}


class MeetingOutputRoutingError(RuntimeError):
    """Raised when a proposal status lookup or transport operation fails."""

    def __init__(self, message: str, *, status_code: int = 500) -> None:
        super().__init__(message)
        self.status_code = status_code


def _utc_now(now: Callable[[], datetime] | None = None) -> datetime:
    current = now() if now is not None else datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_store_path() -> Path:
    if _CONFIGURED_STORE_PATH is not None:
        return _CONFIGURED_STORE_PATH
    explicit = os.getenv("CONSULTATION_MEETING_OUTPUTS_PATH", "").strip()
    if explicit:
        return Path(explicit)
    data_dir = Path(os.getenv("CONSULTATION_DATA_DIR", "/tmp/pantheon/consultation"))
    return data_dir / "meeting_output_dispatches.json"


def configure_output_routing(
    *,
    transport: HttpTransport | None = None,
    store_path: str | Path | None = None,
    clear_live_refs: bool = False,
) -> None:
    """Configure optional in-process transport or ledger path for tests/runtime."""
    global _CONFIGURED_TRANSPORT, _CONFIGURED_STORE_PATH
    with _LOCK:
        _CONFIGURED_TRANSPORT = transport
        _CONFIGURED_STORE_PATH = Path(store_path) if store_path is not None else None
        if clear_live_refs:
            _LIVE_PROPOSAL_REFS.clear()
            _LIVE_MEETING_REFS.clear()


@contextlib.contextmanager
def output_routing_context(
    *,
    transport: HttpTransport | None = None,
    store_path: str | Path | None = None,
) -> Iterator[None]:
    """Scoped configuration context for output routing transport and store path."""
    global _CONFIGURED_TRANSPORT, _CONFIGURED_STORE_PATH
    prev_transport = _CONFIGURED_TRANSPORT
    prev_store = _CONFIGURED_STORE_PATH
    configure_output_routing(transport=transport, store_path=store_path)
    try:
        yield
    finally:
        with _LOCK:
            _CONFIGURED_TRANSPORT = prev_transport
            _CONFIGURED_STORE_PATH = prev_store


class _DispatchLedger:
    """Process- and thread-safe JSON ledger of meeting proposal dispatches."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @contextlib.contextmanager
    def _locked_file(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = self.path.with_suffix(self.path.suffix + ".lock").open("a", encoding="utf-8")
        try:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                lock_file.close()

    def _read_unlocked(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        raw = self.path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        loaded = json.loads(raw)
        if isinstance(loaded, Mapping):
            proposals = loaded.get("proposals")
            if isinstance(proposals, Mapping):
                return {str(k): dict(v) for k, v in proposals.items() if isinstance(v, Mapping)}
            return {str(k): dict(v) for k, v in loaded.items() if isinstance(v, Mapping)}
        return {}

    def _write_unlocked(self, records: Mapping[str, Mapping[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"proposals": dict(records)}, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        except Exception:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
            raise

    def get(self, proposal_id: str) -> dict[str, Any] | None:
        with _LOCK, self._locked_file():
            records = self._read_unlocked()
            row = records.get(proposal_id)
            return copy.deepcopy(row) if row is not None else None

    def put(self, proposal_id: str, record: Mapping[str, Any]) -> dict[str, Any]:
        with _LOCK, self._locked_file():
            records = self._read_unlocked()
            stored = copy.deepcopy(dict(record))
            records[proposal_id] = stored
            self._write_unlocked(records)
            return copy.deepcopy(stored)

    def list_all(self) -> list[dict[str, Any]]:
        with _LOCK, self._locked_file():
            records = self._read_unlocked()
            return [copy.deepcopy(v) for v in records.values()]


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: dict[str, Any] | None,
    *, max_reply_bytes: int | None = None,
) -> tuple[int, dict[str, Any]]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    req_headers = {"Accept": "application/json", **dict(headers)}
    if data is not None:
        req_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=req_headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=10.0) as resp:
            status_code = int(getattr(resp, "status", None) or getattr(resp, "code", None) or 200)
            raw_bytes = resp.read(max_reply_bytes + 1) if max_reply_bytes is not None else resp.read()
            if max_reply_bytes is not None and len(raw_bytes) > max_reply_bytes:
                raise MeetingOutputRoutingError("owner_reply_exceeds_byte_budget", status_code=502)
            text = raw_bytes.decode("utf-8", "replace") if isinstance(raw_bytes, (bytes, bytearray)) else str(raw_bytes or "")
            parsed = json.loads(text) if text.strip() else {}
            return status_code, parsed if isinstance(parsed, dict) else {"items": parsed}
    except urllib.error.HTTPError as exc:
        raw_bytes = (exc.read(max_reply_bytes + 1) if max_reply_bytes is not None else exc.read()) if hasattr(exc, "read") else b""
        text = raw_bytes.decode("utf-8", "replace") if isinstance(raw_bytes, (bytes, bytearray)) else str(raw_bytes or "")
        try:
            parsed = json.loads(text) if text.strip() else {"detail": str(exc)}
        except Exception:
            parsed = {"detail": text or str(exc)}
        return int(exc.code), parsed if isinstance(parsed, dict) else {"detail": parsed}


def _active_transport(transport: HttpTransport | None) -> HttpTransport:
    if transport is not None:
        return transport
    if _CONFIGURED_TRANSPORT is not None:
        return _CONFIGURED_TRANSPORT
    return _urllib_transport


def _service_base_url(kind: str) -> str:
    if kind == "research_demand":
        return (
            os.getenv("PANTHEON_RESEARCH_API_URL")
            or os.getenv("PANTHEON_RESEARCH_SERVICE_URL")
            or os.getenv("RESEARCH_URL")
            or "http://127.0.0.1:8085"
        ).rstrip("/")
    if kind == "evolution_proposal":
        return (
            os.getenv("PANTHEON_EVOLUTION_API_URL")
            or os.getenv("PANTHEON_EVOLUTION_SERVICE_URL")
            or os.getenv("EVOLUTION_URL")
            or "http://127.0.0.1:8093"
        ).rstrip("/")
    if kind in {"harness_patch", "lesson_rule", "pool_decision"}:
        return (
            os.getenv("PANTHEON_PERSONA_API_URL")
            or os.getenv("PANTHEON_PERSONA_SERVICE_URL")
            or os.getenv("PERSONA_URL")
            or "http://127.0.0.1:8089"
        ).rstrip("/")
    if kind == "lesson_candidate":
        return (
            os.getenv("PANTHEON_MEMORY_API_URL")
            or os.getenv("PANTHEON_MEMORY_SERVICE_URL")
            or os.getenv("MEMORY_API_URL")
            or os.getenv("MEMORY_URL")
            or "http://127.0.0.1:8084"
        ).rstrip("/")
    if kind == "governance_approval":
        return (
            os.getenv("PANTHEON_GOVERNANCE_APPROVAL_API_URL")
            or os.getenv("PANTHEON_GOVERNANCE_API_URL")
            or os.getenv("PANTHEON_GOVERNANCE_SERVICE_URL")
            or os.getenv("GOVERNANCE_URL")
            or "http://127.0.0.1:8082"
        ).rstrip("/")
    if kind == "agora_candidate_decision":
        return (
            os.getenv("PANTHEON_AGORA_BFF_URL")
            or os.getenv("PANTHEON_BFF_API_URL")
            or os.getenv("PANTHEON_BFF_URL")
            or os.getenv("BFF_URL")
            or "http://127.0.0.1:8080"
        ).rstrip("/")
    raise ValueError(f"Unsupported proposal kind: {kind!r}")


def _target_path(kind: str, sponsor_persona_id: str | None, payload: Mapping[str, Any], meeting_id: str, proposal_id: str) -> str:
    if kind == "research_demand":
        return "/api/research/persona-demands"
    if kind == "evolution_proposal":
        return "/api/evolution/proposals"
    if kind == "harness_patch":
        persona_segment = urllib.parse.quote(str(sponsor_persona_id or "unknown"), safe="")
        return f"/api/personas/{persona_segment}/harness/patches"
    if kind == "lesson_candidate":
        return "/api/memory/trade-lessons"
    if kind == "lesson_rule":
        persona_segment = urllib.parse.quote(str(sponsor_persona_id or "unknown"), safe="")
        return f"/api/personas/{persona_segment}/lesson-rule-proposals"
    if kind == "pool_decision":
        persona_segment = urllib.parse.quote(str(sponsor_persona_id or "unknown"), safe="")
        return f"/api/personas/{persona_segment}/strategy-pool/decisions"
    if kind == "governance_approval":
        return "/api/governance/approvals"
    if kind == "agora_candidate_decision":
        interaction_id = urllib.parse.quote(str(payload.get("interaction_id") or meeting_id or "meeting"), safe="")
        measure_id = urllib.parse.quote(str(payload.get("measure_id") or f"measure-{proposal_id}"), safe="")
        return f"/bff/agora/interactions/{interaction_id}/recommended-measures/{measure_id}/candidates"
    return f"/api/unknown/{kind}"


def _mint_jwt(
    *,
    subject: str,
    tenant_id: str,
    roles: list[str],
    secret: str,
    issuer: str | None = None,
    audience: str | None = None,
    extra_claims: Mapping[str, Any] | None = None,
) -> str:
    now_ts = int(time.time())
    claims: dict[str, Any] = {
        "sub": subject,
        "actor_id": subject,
        "tenant_id": tenant_id,
        "allowed_tenants": [tenant_id],
        "roles": list(roles),
        "iat": now_ts,
        "exp": now_ts + 300,
    }
    if issuer:
        claims["iss"] = issuer
    if audience:
        claims["aud"] = audience
    if extra_claims:
        claims.update(dict(extra_claims))
    return encode_jwt_hs256(claims, secret=secret)


def _resolve_configured_token(*variables: str) -> str:
    for variable in variables:
        token = configured_service_token(variable)
        if token:
            return token
    return ""


def _auth_headers(
    *,
    kind: str,
    tenant_id: str,
    sponsor_persona_id: str | None,
    idempotency_key: str | None = None,
    owner_user_id: str | None = None,
    read_only: bool = False,
) -> dict[str, str]:
    headers: dict[str, str] = {"X-Tenant-Id": tenant_id}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if sponsor_persona_id:
        headers["X-Sponsor-Persona-Id"] = sponsor_persona_id
        headers["X-Actor-Id"] = sponsor_persona_id

    if kind == "research_demand":
        static_token = _resolve_configured_token(
            "PERSONA_RESEARCH_SERVICE_TOKEN",
            "PANTHEON_RESEARCH_SERVICE_TOKEN",
        )
        if static_token:
            headers["Authorization"] = f"Bearer {static_token}"
            return headers
        secret = (
            os.getenv("PANTHEON_RESEARCH_JWT_SECRET")
            or os.getenv("PANTHEON_DEV_BFF_JWT_SECRET")
            or os.getenv("PANTHEON_BFF_JWT_SECRET")
            or os.getenv("PERSONA_JWT_SECRET")
            or "pantheon-meeting-outputs-local-secret"
        ).strip()
        issuer = (
            os.getenv("PANTHEON_RESEARCH_JWT_ISSUER")
            or os.getenv("PANTHEON_DEV_BFF_JWT_ISSUER")
            or os.getenv("PANTHEON_BFF_JWT_ISSUER")
            or None
        )
        audience = (
            os.getenv("PANTHEON_RESEARCH_JWT_AUDIENCE")
            or os.getenv("PANTHEON_DEV_BFF_JWT_AUDIENCE")
            or os.getenv("PANTHEON_BFF_JWT_AUDIENCE")
            or None
        )
        token = _mint_jwt(
            subject="persona-pool-cycle",
            tenant_id=tenant_id,
            roles=["viewer", "service"],
            secret=secret,
            issuer=issuer,
            audience=audience,
            extra_claims={"sponsor_persona_id": sponsor_persona_id},
        )
        headers["Authorization"] = f"Bearer {token}"
        return headers

    if kind == "evolution_proposal":
        static_token = (
            os.getenv("EVOLUTION_AUTH_TOKEN")
            or os.getenv("PANTHEON_EVOLUTION_SERVICE_TOKEN")
            or ""
        ).strip()
        if static_token:
            headers["Authorization"] = f"Bearer {static_token}"
            return headers
        secret = (
            os.getenv("PERSONA_JWT_SECRET")
            or os.getenv("PANTHEON_BFF_JWT_SECRET")
            or "pantheon-meeting-outputs-local-secret"
        ).strip()
        token = _mint_jwt(
            subject=str(sponsor_persona_id or "operator"),
            tenant_id=tenant_id,
            roles=["operator", "service"],
            secret=secret,
        )
        headers["Authorization"] = f"Bearer {token}"
        return headers

    if kind in {"harness_patch", "lesson_rule", "pool_decision"}:
        secret = (
            os.getenv("PERSONA_JWT_SECRET")
            or os.getenv("PANTHEON_RUNTIME_JWT_SECRET")
            or os.getenv("PANTHEON_BFF_JWT_SECRET")
            or ""
        ).strip()
        if secret:
            issuer = os.getenv("PERSONA_JWT_ISSUER") or os.getenv("PANTHEON_RUNTIME_JWT_ISSUER") or None
            audience = os.getenv("PERSONA_JWT_AUDIENCE") or os.getenv("PANTHEON_RUNTIME_JWT_AUDIENCE") or None
            token = _mint_jwt(
                subject=str(sponsor_persona_id),
                tenant_id=tenant_id,
                roles=["persona.admin", "operator", "service", "viewer"],
                secret=secret,
                issuer=issuer,
                audience=audience,
            )
            headers["Authorization"] = f"Bearer {token}"
            return headers
        static_token = (
            os.getenv("PANTHEON_PERSONA_SERVICE_TOKEN")
            or os.getenv("PERSONA_SERVICE_TOKEN")
            or ""
        ).strip()
        if static_token:
            headers["Authorization"] = f"Bearer {static_token}"
            return headers
        fallback_token = _mint_jwt(
            subject=str(sponsor_persona_id),
            tenant_id=tenant_id,
            roles=["persona.admin", "operator", "service", "viewer"],
            secret="pantheon-meeting-outputs-local-secret",
        )
        headers["Authorization"] = f"Bearer {fallback_token}"
        return headers

    if kind == "lesson_candidate":
        static_token = (
            _resolve_configured_token(
                "CONSULTATION_MEMORY_READ_TOKEN",
                "CONSULTATION_MEMORY_WRITE_TOKEN",
                "PANTHEON_MEMORY_AUTH_TOKEN",
            )
            if read_only
            else _resolve_configured_token(
                "CONSULTATION_MEMORY_WRITE_TOKEN",
                "PANTHEON_MEMORY_AUTH_TOKEN",
            )
        )
        if static_token:
            headers["Authorization"] = f"Bearer {static_token}"
            return headers
        secret = (
            os.getenv("PANTHEON_MEMORY_JWT_SECRET")
            or os.getenv("PERSONA_JWT_SECRET")
            or os.getenv("PANTHEON_BFF_JWT_SECRET")
            or os.getenv("PANTHEON_RUNTIME_JWT_SECRET")
            or "pantheon-meeting-outputs-local-secret"
        ).strip()
        issuer = (
            os.getenv("PANTHEON_MEMORY_JWT_ISSUER")
            or os.getenv("PERSONA_JWT_ISSUER")
            or os.getenv("PANTHEON_BFF_JWT_ISSUER")
            or None
        )
        audience = (
            os.getenv("PANTHEON_MEMORY_JWT_AUDIENCE")
            or os.getenv("PERSONA_JWT_AUDIENCE")
            or os.getenv("PANTHEON_BFF_JWT_AUDIENCE")
            or None
        )
        token = _mint_jwt(
            subject=str(sponsor_persona_id),
            tenant_id=tenant_id,
            roles=["memory-writer", "memory-reader", "persona-svc", "operator"],
            secret=secret,
            issuer=issuer,
            audience=audience,
            extra_claims={"service": "consultation-meeting-outputs"},
        )
        headers["Authorization"] = f"Bearer {token}"
        return headers

    if kind == "governance_approval":
        secret = (
            os.getenv("PANTHEON_GOVERNANCE_JWT_SECRET")
            or os.getenv("PERSONA_JWT_SECRET")
            or os.getenv("PANTHEON_BFF_JWT_SECRET")
            or "pantheon-meeting-outputs-local-secret"
        ).strip()
        issuer = (
            os.getenv("PANTHEON_GOVERNANCE_OIDC_ISSUER")
            or os.getenv("PANTHEON_GOVERNANCE_JWT_ISSUER")
            or "pantheon-governance"
        ).strip()
        audience = (
            os.getenv("PANTHEON_GOVERNANCE_OIDC_AUDIENCE")
            or os.getenv("PANTHEON_GOVERNANCE_JWT_AUDIENCE")
            or "pantheon-governance"
        ).strip()
        token = _mint_jwt(
            subject=str(sponsor_persona_id),
            tenant_id=tenant_id,
            roles=["approval_proposer", "approval_reader", "operator"],
            secret=secret,
            issuer=issuer,
            audience=audience,
        )
        headers["Authorization"] = f"Bearer {token}"
        return headers

    if kind == "agora_candidate_decision":
        static_token = os.getenv("PANTHEON_AGORA_SERVICE_TOKEN", "").strip()
        if static_token:
            headers["Authorization"] = f"Bearer {static_token}"
            return headers
        secret = (
            os.getenv("PANTHEON_BFF_JWT_SECRET")
            or os.getenv("PERSONA_JWT_SECRET")
            or "pantheon-meeting-outputs-local-secret"
        ).strip()
        actor = str(owner_user_id or "agora-operator").strip()
        token = _mint_jwt(
            subject=actor,
            tenant_id=tenant_id,
            roles=["operator", "viewer"],
            secret=secret,
        )
        headers["Authorization"] = f"Bearer {token}"
        return headers

    return headers


def _check_sponsor_eligibility(
    *,
    kind: str,
    sponsor_persona_id: Any,
    proposal: Mapping[str, Any],
    payload: Mapping[str, Any],
    meeting: Mapping[str, Any],
) -> str | None:
    """Validate sponsor identity and enforce that advisors and private servants never sponsor."""
    if kind == "agora_candidate_decision":
        if sponsor_persona_id is not None and str(sponsor_persona_id).strip() != "":
            raise SponsorDecisionBridgeError(
                "agora_candidate_decision proposals must have sponsor_persona_id=None"
            )
        return None

    # Check meeting participants roster for advisor / private_servant origin or role
    participants = meeting.get("participants")
    participant_entry: Mapping[str, Any] = {}
    if isinstance(participants, list) and sponsor_persona_id is not None:
        target = str(sponsor_persona_id).strip()
        for item in participants:
            if not isinstance(item, Mapping):
                continue
            pid = str(item.get("persona_id") or item.get("id") or item.get("participant_ref") or "").strip()
            if pid == target:
                participant_entry = item
                break

    origin = (
        proposal.get("sponsor_origin")
        or proposal.get("origin")
        or payload.get("sponsor_origin")
        or payload.get("origin")
        or participant_entry.get("origin")
    )
    role = (
        proposal.get("sponsor_role")
        or proposal.get("role")
        or payload.get("sponsor_role")
        or payload.get("role")
        or participant_entry.get("role")
    )
    persona_class = (
        proposal.get("persona_class")
        or payload.get("persona_class")
        or participant_entry.get("persona_class")
    )
    archetype = (
        proposal.get("archetype")
        or payload.get("archetype")
        or participant_entry.get("archetype")
    )
    is_servant = bool(
        proposal.get("is_servant")
        or payload.get("is_servant")
        or participant_entry.get("is_servant")
        or proposal.get("servant_flag")
        or participant_entry.get("servant_flag")
    )
    agora_user_id = (
        proposal.get("agora_user_id")
        or payload.get("agora_user_id")
        or participant_entry.get("agora_user_id")
    )

    validated = validate_sponsor_persona(
        sponsor_persona_id,
        origin=str(origin) if origin is not None else None,
        role=str(role) if role is not None else None,
        persona_class=str(persona_class) if persona_class is not None else None,
        archetype=str(archetype) if archetype is not None else None,
        is_servant=is_servant,
        agora_user_id=str(agora_user_id) if agora_user_id is not None else None,
        allow_none=False,
    )
    assert validated is not None

    # Ensure payload does not impersonate another persona
    for identity_field in ("persona_id", "sponsor_persona_id", "created_by_id", "owner_user_id"):
        if identity_field in payload and payload[identity_field] is not None:
            declared = str(payload[identity_field]).strip()
            if declared and declared != validated:
                raise SponsorDecisionBridgeError(
                    f"proposal payload {identity_field}={declared!r} does not match sponsor_persona_id={validated!r}"
                )
    return validated


_TRADE_EPISODE_SCHEMA_PATH = (
    Path(__file__).resolve().parents[1] / "telemetry" / "trade_episode_projection.schema.json"
)
_TRADE_EPISODE_VALIDATOR = jsonschema.Draft7Validator(
    json.loads(_TRADE_EPISODE_SCHEMA_PATH.read_text(encoding="utf-8"))
)


def _as_uuid_str(value: Any, namespace_tag: str) -> str:
    text = str(value or "").strip()
    if text:
        try:
            return str(uuid.UUID(text))
        except ValueError:
            return str(uuid.uuid5(uuid.NAMESPACE_OID, f"pantheon:{namespace_tag}:{text}"))
    return str(uuid.uuid5(uuid.NAMESPACE_OID, f"pantheon:{namespace_tag}:default"))


def _telemetry_base_url() -> str:
    return (
        os.getenv("PANTHEON_TELEMETRY_API_URL")
        or os.getenv("PANTHEON_TELEMETRY_SERVICE_URL")
        or os.getenv("TELEMETRY_URL")
        or "http://127.0.0.1:8083"
    ).rstrip("/")


def _telemetry_auth_headers(
    *,
    tenant_id: str,
    sponsor_persona_id: str | None,
) -> dict[str, str] | None:
    static_token = _resolve_configured_token(
        "CONSULTATION_TELEMETRY_READ_TOKEN",
        "PANTHEON_TELEMETRY_SERVICE_TOKEN",
    )
    if not static_token:
        return None
    headers: dict[str, str] = {
        "X-Tenant-Id": tenant_id,
        "Authorization": f"Bearer {static_token}",
    }
    if sponsor_persona_id:
        headers["X-Sponsor-Persona-Id"] = sponsor_persona_id
        headers["X-Actor-Id"] = sponsor_persona_id
    return headers


def _is_verified_trade_episode(
    record: Any,
    *,
    tenant_id: str,
    sponsor_persona_id: str | None,
    expected_episode_id: str | None = None,
) -> bool:
    if not isinstance(record, Mapping) or not sponsor_persona_id:
        return False
    episode_id = str(record.get("trade_episode_id") or "").strip()
    if not episode_id:
        return False
    if expected_episode_id is not None and episode_id != expected_episode_id:
        return False
    if str(record.get("tenant_id") or "").strip() != tenant_id:
        return False
    if str(record.get("persona_id") or "").strip() != sponsor_persona_id:
        return False
    return _TRADE_EPISODE_VALIDATOR.is_valid(dict(record))


def _resolve_lesson_candidate_episode_ids(
    *,
    payload: Mapping[str, Any],
    citations: Sequence[Any],
    meeting: Mapping[str, Any] | None,
    sponsor_persona_id: str | None,
    tenant_id: str,
    send: HttpTransport | None = None,
) -> list[str]:
    """Resolve distinct trade_episode_ids verified via Telemetry owner GET for tenant_id and sponsor_persona_id."""
    del meeting
    verified_by_id: dict[str, Mapping[str, Any]] = {}
    rejected_ids: set[str] = set()
    episode_ids: list[str] = []
    active_send = _active_transport(send)
    telemetry_base: str | None = None
    telemetry_headers: dict[str, str] | None = None
    telemetry_headers_resolved = False

    def _verify_episode_id(raw_id: str) -> str | None:
        ep_id = raw_id.strip()
        if not ep_id:
            return None
        if ep_id in verified_by_id:
            return ep_id
        if ep_id in rejected_ids:
            return None
        nonlocal telemetry_base, telemetry_headers, telemetry_headers_resolved
        if not telemetry_headers_resolved:
            telemetry_headers = _telemetry_auth_headers(
                tenant_id=tenant_id,
                sponsor_persona_id=sponsor_persona_id,
            )
            telemetry_headers_resolved = True
        if telemetry_headers is None:
            rejected_ids.add(ep_id)
            return None
        if telemetry_base is None:
            telemetry_base = _telemetry_base_url()
        encoded_id = urllib.parse.quote(ep_id, safe="")
        try:
            status_code, body = active_send(
                "GET",
                f"{telemetry_base}/api/telemetry/trade-episodes/{encoded_id}",
                telemetry_headers,
                None,
            )
        except Exception:
            rejected_ids.add(ep_id)
            return None
        if status_code == 200 and _is_verified_trade_episode(
            body,
            tenant_id=tenant_id,
            sponsor_persona_id=sponsor_persona_id,
            expected_episode_id=ep_id,
        ):
            verified_by_id[ep_id] = body
            return ep_id
        rejected_ids.add(ep_id)
        return None

    def _collect(seq: Iterable[Any]) -> None:
        for entry in seq:
            if isinstance(entry, Mapping):
                if not _is_verified_trade_episode(
                    entry,
                    tenant_id=tenant_id,
                    sponsor_persona_id=sponsor_persona_id,
                ):
                    continue
                verified_id = _verify_episode_id(str(entry["trade_episode_id"]))
                if verified_id and verified_id not in episode_ids:
                    episode_ids.append(verified_id)
                continue
            if isinstance(entry, str):
                verified_id = _verify_episode_id(entry)
                if verified_id and verified_id not in episode_ids:
                    episode_ids.append(verified_id)

    raw_payload_episodes = payload.get("trade_episode_ids")
    if isinstance(raw_payload_episodes, (list, tuple)):
        _collect(raw_payload_episodes)
    _collect(citations or ())
    return episode_ids


def _build_outbound_request(
    *,
    kind: str,
    proposal_id: str,
    meeting_id: str,
    tenant_id: str,
    sponsor_persona_id: str | None,
    payload: Mapping[str, Any],
    citations: list[Any],
    now: datetime,
    meeting: Mapping[str, Any] | None = None,
    proposal: Mapping[str, Any] | None = None,
    send: HttpTransport | None = None,
) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Return (path, request_json, request_meta) for the given proposal kind."""
    now_iso = _iso(now)
    path = _target_path(kind, sponsor_persona_id, payload, meeting_id, proposal_id)

    if kind == "research_demand":
        assert sponsor_persona_id is not None
        cite_str = None
        if citations:
            first_cite = citations[0]
            cite_str = str(first_cite.get("ref_id") or first_cite.get("ref") or first_cite.get("id") if isinstance(first_cite, Mapping) else first_cite)
        body = {
            "persona_id": sponsor_persona_id,
            "mandate": str(payload.get("mandate") or "meeting_research_demand"),
            "missing_datasets": list(payload.get("missing_datasets") or []),
            "tried_families": list(payload.get("tried_families") or ["momentum"]),
            "short_measure": str(payload.get("short_measure") or "effective_bets"),
            "pool_version": int(payload.get("pool_version", 1)),
            "evidence_ref": str(payload.get("evidence_ref") or cite_str or f"meeting:{meeting_id}:{proposal_id}"),
        }
        return path, body, {"persona_id": sponsor_persona_id}

    if kind == "evolution_proposal":
        assert sponsor_persona_id is not None
        evidence_refs = list(payload.get("evidence_refs") or [])
        for cite in citations:
            if isinstance(cite, Mapping):
                ref_type = str(cite.get("ref_type") or cite.get("type") or "committee_memo")
                ref_id = str(cite.get("ref_id") or cite.get("ref") or cite.get("id") or "")
                if ref_id:
                    evidence_refs.append({"ref_type": ref_type, "ref_id": ref_id})
            elif str(cite or "").strip():
                evidence_refs.append({"ref_type": "committee_memo", "ref_id": str(cite).strip()})
        if not evidence_refs:
            evidence_refs.append({"ref_type": "committee_memo", "ref_id": f"meeting-{meeting_id}"})

        bridge_input: dict[str, Any] = {
            "decision_id": str(payload.get("decision_id") or proposal_id),
            "proposal_id": str(payload.get("decision_id") or f"evolution-proposal-from-{proposal_id}"),
            "type": "evolution",
            "sponsor_persona_id": sponsor_persona_id,
            "target_type": str(payload.get("target_type") or "persona"),
            "target_id": str(payload.get("target_id") or sponsor_persona_id),
            "target_version": str(payload.get("target_version") or "1.0.0"),
            "action_type": str(payload.get("action_type") or "retrain"),
            "rationale": str(
                payload.get("rationale")
                or payload.get("summary")
                or f"Deliberative meeting {meeting_id} proposal {proposal_id} sponsored by {sponsor_persona_id}."
            ),
            "committee_id": meeting_id,
            "persona_id": sponsor_persona_id,
            "evidence_refs": evidence_refs,
            "threshold_snapshots": list(payload.get("threshold_snapshots") or []),
            "metadata": {**(dict(payload.get("metadata")) if isinstance(payload.get("metadata"), Mapping) else {}), "meeting_id": meeting_id, "proposal_id": proposal_id},
        }
        for opt_key in ("target_stage", "risk_level", "capital_pool_id", "linked_incident_id", "linked_postmortem_id"):
            if payload.get(opt_key) is not None:
                bridge_input[opt_key] = payload[opt_key]
        bridged = bridge(bridge_input)
        assert isinstance(bridged, EvolutionDecisionProposal)
        evo_body = bridged.to_evolution_propose_request(tenant_id=tenant_id)
        return path, evo_body, {"decision_id": evo_body["decision_id"]}

    if kind == "harness_patch":
        assert sponsor_persona_id is not None
        raw_refs = list(payload.get("evidence_refs") or [])
        if not raw_refs:
            for cite in citations:
                if isinstance(cite, Mapping) and cite.get("kind") and cite.get("ref") and cite.get("moment"):
                    raw_refs.append(
                        {
                            "kind": str(cite["kind"]),
                            "ref": str(cite["ref"]),
                            "moment": str(cite["moment"]),
                        }
                    )
                elif str(cite or "").strip():
                    raw_refs.append(
                        {
                            "kind": "memo",
                            "ref": str(cite).strip(),
                            "moment": meeting_id,
                        }
                    )
        if not raw_refs:
            raw_refs.append({"kind": "memo", "ref": f"memo-{meeting_id}", "moment": meeting_id})

        base_version = int(payload.get("base_version", 1))
        patch_id = str(payload.get("patch_id") or f"patch-{proposal_id}")
        proposed_change = dict(
            payload.get("proposed_change")
            or {"cycle_thresholds": {"minimum_evidence_count": 3, "minimum_confidence": 0.8}}
        )
        patch_body = {
            "schema_version": "persona_harness_patch.v1",
            "patch_id": patch_id,
            "persona_id": sponsor_persona_id,
            "session_id": str(payload.get("session_id") or meeting_id),
            "base_version": base_version,
            "patch_type": str(payload.get("patch_type") or "threshold"),
            "proposed_change": proposed_change,
            "evidence_refs": raw_refs,
            "expected_effect": str(
                payload.get("expected_effect")
                or payload.get("rationale")
                or f"Meeting {meeting_id} harness proposal {proposal_id}"
            ),
            "rollback_target": int(payload.get("rollback_target", base_version)),
        }
        return path, patch_body, {"persona_id": sponsor_persona_id, "patch_id": patch_id}

    if kind == "lesson_candidate":
        assert sponsor_persona_id is not None
        lc_id = _as_uuid_str(payload.get("lesson_candidate_id") or proposal_id, "lesson_candidate")
        ref_id = _as_uuid_str(payload.get("reflection_id") or f"{meeting_id}:{proposal_id}", "reflection")
        episode_ids = _resolve_lesson_candidate_episode_ids(
            payload=payload,
            citations=citations,
            meeting=meeting,
            sponsor_persona_id=sponsor_persona_id,
            tenant_id=tenant_id,
            send=send,
        )
        if len(episode_ids) < 3:
            raise MeetingOutputRoutingError(
                "lesson_candidate requires at least 3 distinct supporting trade_episode_ids "
                f"from input or verified evidence (got {len(episode_ids)})",
                status_code=422,
            )
        expiry = str(payload.get("expiry") or _iso(now + timedelta(days=30)))
        created_at = str(payload.get("created_at") or now_iso)
        lesson_body = {
            "lesson_candidate_id": lc_id,
            "reflection_id": ref_id,
            "trade_episode_ids": episode_ids,
            "persona_id": sponsor_persona_id,
            "tenant_id": tenant_id,
            "scope": str(payload.get("scope") or "strategy"),
            "proposed_change": str(
                payload.get("proposed_change")
                or payload.get("summary")
                or f"Meeting {meeting_id} lesson candidate {proposal_id}"
            ),
            "confidence": float(payload.get("confidence", 0.8)),
            "review_state": "proposed",
            "created_at": created_at,
            "updated_at": str(payload.get("updated_at") or created_at),
            "expiry": expiry,
            "reflection_version": str(payload.get("reflection_version") or "v1"),
            "target_env": "paper",
            "promotion_stage": "proposed",
        }
        return path, lesson_body, {"lesson_candidate_id": lc_id}

    if kind == "lesson_rule":
        assert sponsor_persona_id is not None
        rule_kind = str(payload.get("kind") or "harness_patch")
        rule_body: dict[str, Any] = {
            "kind": rule_kind,
            "lesson_ids": list(payload.get("lesson_ids") or []),
            "case_file": dict(payload.get("case_file") or {}),
            "expected_effect": str(
                payload.get("expected_effect")
                or payload.get("rationale")
                or f"Meeting {meeting_id} lesson-rule proposal {proposal_id}"
            ),
        }
        if rule_kind in payload and isinstance(payload[rule_kind], Mapping):
            rule_body[rule_kind] = dict(payload[rule_kind])
        return path, rule_body, {"persona_id": sponsor_persona_id}

    if kind == "pool_decision":
        assert sponsor_persona_id is not None
        pool_body: dict[str, Any] = {
            "expected_version": int(payload.get("expected_version", 1)),
            "execution_context": "paper",
            "action": str(payload.get("action") or "reweight"),
            "books": dict(payload.get("books") or {}),
            "observations": list(payload.get("observations") or []),
            "dataset": dict(payload.get("dataset") or {}),
            "initiated_by": str(payload.get("initiated_by") or "operator"),
        }
        if payload.get("candidate") is not None:
            pool_body["candidate"] = dict(payload["candidate"])
        if payload.get("consultation_memo_id") is not None:
            pool_body["consultation_memo_id"] = str(payload["consultation_memo_id"])
        if payload.get("consultation_pool_version") is not None:
            pool_body["consultation_pool_version"] = int(payload["consultation_pool_version"])
        return path, pool_body, {"persona_id": sponsor_persona_id}

    if kind == "governance_approval":
        assert sponsor_persona_id is not None
        evidence_refs = list(payload.get("evidence_refs") or [])
        for cite in citations:
            if isinstance(cite, Mapping):
                evidence_refs.append(
                    {
                        "ref_type": str(cite.get("ref_type") or "committee_memo"),
                        "ref_id": str(cite.get("ref_id") or cite.get("ref") or cite.get("id") or meeting_id),
                    }
                )
            elif str(cite or "").strip():
                evidence_refs.append({"ref_type": "committee_memo", "ref_id": str(cite).strip()})
        bridge_input = {
            "decision_id": str(payload.get("decision_id") or proposal_id),
            "proposal_id": str(payload.get("decision_id") or f"approval-proposal-from-{proposal_id}"),
            "type": "approval",
            "sponsor_persona_id": sponsor_persona_id,
            "target_type": str(payload.get("target_type") or "strategy_spec"),
            "target_id": str(payload.get("target_id") or f"target-{proposal_id}"),
            "target_version": str(payload.get("target_version") or "1.0.0"),
            "risk_level": str(payload.get("risk_level") or "medium"),
            "rationale": str(
                payload.get("rationale")
                or f"Meeting {meeting_id} governance proposal {proposal_id} sponsored by {sponsor_persona_id}."
            ),
            "committee_id": str(payload.get("committee_id") or meeting_id),
            "handoff_id": str(payload.get("handoff_id") or f"gh-{proposal_id}"),
            "persona_id": sponsor_persona_id,
            "capital_pool_id": payload.get("capital_pool_id"),
            "evidence_refs": evidence_refs,
        }
        bridged = bridge(bridge_input)
        assert isinstance(bridged, ApprovalDecisionProposal)
        subject = dict(payload["subject"]) if isinstance(payload.get("subject"), Mapping) else None
        gov_body = bridged.to_governance_propose_request(
            tenant_id=tenant_id,
            owner_user_id=sponsor_persona_id,
            expected_version=0,
            proposal_id=str(payload.get("proposal_id") or proposal_id),
            proposal_content_digest=payload.get("proposal_content_digest"),
            subject=subject,
        )
        return path, gov_body, {"decision_id": gov_body["decision_id"]}

    if kind == "agora_candidate_decision":
        interaction_id = str(payload.get("interaction_id") or meeting_id)
        opinion_id = str(payload.get("opinion_id") or f"opinion-{proposal_id}")
        measure_id = str(payload.get("measure_id") or f"measure-{proposal_id}")
        agora_body = {
            "interaction_id": interaction_id,
            "opinion_id": opinion_id,
            "measure_id": measure_id,
        }
        owner_user_id = str(payload.get("owner_user_id") or "user-a")
        return path, agora_body, {"interaction_id": interaction_id, "measure_id": measure_id, "owner_user_id": owner_user_id}

    raise ValueError(f"Unsupported proposal kind: {kind!r}")


def _extract_dispatch_outcome(
    *,
    kind: str,
    sponsor_persona_id: str | None,
    response_body: Mapping[str, Any],
    request_meta: dict[str, Any],
    now_iso: str,
) -> tuple[str, str, str]:
    """Extract (downstream_id, initial_status, updated_at) from the owner's response."""
    if kind == "research_demand":
        downstream_id = str(response_body.get("demand_id") or "")
        status = str(response_body.get("status") or "open")
        updated_at = str(response_body.get("updated_at") or response_body.get("created_at") or now_iso)
        return downstream_id, status, updated_at

    if kind == "evolution_proposal":
        downstream_id = str(response_body.get("decision_id") or request_meta.get("decision_id") or "")
        status = str(response_body.get("decision_state") or "proposed")
        updated_at = str(response_body.get("updated_at") or response_body.get("created_at") or now_iso)
        return downstream_id, status, updated_at

    if kind == "harness_patch":
        proposal_obj = response_body.get("proposal") if isinstance(response_body.get("proposal"), Mapping) else {}
        downstream_id = str(proposal_obj.get("patch_id") or response_body.get("patch_id") or request_meta.get("patch_id") or "")
        status = str(response_body.get("status") or "draft")
        updated_at = str(response_body.get("updated_at") or response_body.get("created_at") or now_iso)
        return downstream_id, status, updated_at

    if kind == "lesson_candidate":
        downstream_id = str(response_body.get("lesson_candidate_id") or request_meta.get("lesson_candidate_id") or "")
        status = str(response_body.get("review_state") or "proposed")
        updated_at = str(response_body.get("updated_at") or response_body.get("created_at") or now_iso)
        return downstream_id, status, updated_at

    if kind == "lesson_rule":
        downstream_id = str(response_body.get("proposal_id") or "")
        status = str(response_body.get("status") or "recorded")
        updated_at = str(response_body.get("updated_at") or response_body.get("created_at") or now_iso)
        return downstream_id, status, updated_at

    if kind == "pool_decision":
        pool_version = int(response_body.get("pool_version", 1))
        request_meta["pool_version"] = pool_version
        downstream_id = f"{sponsor_persona_id}:v{pool_version}"
        decision = response_body.get("decision") if isinstance(response_body.get("decision"), Mapping) else {}
        if decision.get("status") == "refused":
            status = "refused"
        else:
            status = str(decision.get("execution_status") or "requires_approved_envelope")
        updated_at = str(response_body.get("updated_at") or response_body.get("created_at") or now_iso)
        return downstream_id, status, updated_at

    if kind == "governance_approval":
        downstream_id = str(response_body.get("decision_id") or request_meta.get("decision_id") or "")
        status = str(response_body.get("decision_state") or "proposed")
        updated_at = str(response_body.get("recorded_at") or response_body.get("created_at") or now_iso)
        return downstream_id, status, updated_at

    if kind == "agora_candidate_decision":
        data = response_body.get("data") if isinstance(response_body.get("data"), Mapping) else response_body
        candidate = data.get("candidate") if isinstance(data.get("candidate"), Mapping) else data
        meta = response_body.get("meta") if isinstance(response_body.get("meta"), Mapping) else {}
        downstream_id = str(candidate.get("proposal_id") or "")
        status = str(candidate.get("state") or candidate.get("status") or "draft")
        updated_at = str(candidate.get("updated_at") or candidate.get("created_at") or meta.get("snapshot_at") or now_iso)
        return downstream_id, status, updated_at

    raise ValueError(f"Unsupported proposal kind: {kind!r}")


def _extract_proposals_list(meeting: dict[str, Any]) -> list[dict[str, Any]]:
    seen_ids: set[str] = set()
    collected: list[dict[str, Any]] = []
    for container in (
        meeting.get("proposals"),
        (meeting.get("synthesis") or {}).get("proposals") if isinstance(meeting.get("synthesis"), Mapping) else None,
    ):
        if not isinstance(container, list):
            continue
        for item in container:
            if not isinstance(item, dict):
                continue
            pid = str(item.get("proposal_id") or "").strip()
            if pid and pid not in seen_ids:
                seen_ids.add(pid)
                collected.append(item)
    return collected


def _register_live_refs(meeting: dict[str, Any], proposal: dict[str, Any], proposal_id: str) -> None:
    with _LOCK:
        refs = _LIVE_PROPOSAL_REFS.setdefault(proposal_id, [])
        if all(existing is not proposal for existing in refs):
            refs.append(proposal)
        # Also register any matching proposal dict inside meeting["synthesis"]["proposals"] or meeting["proposals"]
        for container in (
            meeting.get("proposals"),
            (meeting.get("synthesis") or {}).get("proposals") if isinstance(meeting.get("synthesis"), Mapping) else None,
        ):
            if isinstance(container, list):
                for item in container:
                    if isinstance(item, dict) and str(item.get("proposal_id") or "") == proposal_id:
                        if all(existing is not item for existing in refs):
                            refs.append(item)
        m_refs = _LIVE_MEETING_REFS.setdefault(proposal_id, [])
        if all(existing is not meeting for existing in m_refs):
            m_refs.append(meeting)


def _apply_to_live_refs(
    proposal_id: str,
    *,
    downstream_target: str,
    downstream_id: str | None,
    status: str,
    error: str | None,
    updated_at: str,
) -> None:
    downstream_summary = {
        "target": downstream_target,
        "id": downstream_id,
        "status": status,
        "error": error,
        "updated_at": updated_at,
    }
    with _LOCK:
        for prop in _LIVE_PROPOSAL_REFS.get(proposal_id, []):
            prop["downstream_target"] = downstream_target
            prop["downstream_id"] = downstream_id
            prop["status"] = status
            prop["error"] = error
            prop["updated_at"] = updated_at
            prop["downstream"] = dict(downstream_summary)
        for meeting in _LIVE_MEETING_REFS.get(proposal_id, []):
            dispatches = meeting.get("proposal_dispatches")
            if isinstance(dispatches, list):
                for item in dispatches:
                    if isinstance(item, dict) and str(item.get("proposal_id") or "") == proposal_id:
                        item["downstream_target"] = downstream_target
                        item["downstream_id"] = downstream_id
                        item["status"] = status
                        item["error"] = error
                        item["updated_at"] = updated_at


def _format_http_error(status_code: int, body: Mapping[str, Any]) -> str:
    detail = body.get("detail") or body.get("error") or body.get("reason") or body
    if isinstance(detail, Mapping):
        msg = detail.get("message") or detail.get("reason") or detail.get("error") or json.dumps(detail, sort_keys=True)
    else:
        msg = str(detail)
    return f"HTTP {status_code}: {msg}"


ACTION_PAGE_SIZE = 8
ACTION_SNAPSHOT_MAX_BYTES = 8192
ACTION_STATES = frozenset({"open", "in_progress", "waiting_for_input", "submitted", "returned", "accepted"})


def _action_headers(tenant_id: str, key: str | None = None) -> dict[str, str]:
    """The meeting owner acts as itself, never as the assignee or an operator."""
    token = _resolve_configured_token("CONSULTATION_RESEARCH_SERVICE_TOKEN")
    if not token:
        secret = (os.getenv("PANTHEON_RESEARCH_JWT_SECRET") or
                  os.getenv("PANTHEON_BFF_JWT_SECRET") or
                  os.getenv("PANTHEON_DEV_BFF_JWT_SECRET") or "").strip()
        if not secret:
            raise MeetingOutputRoutingError("research_owner_credential_unavailable", status_code=503)
        token = _mint_jwt(subject="consultation-svc", tenant_id=tenant_id,
                          roles=["service", "viewer"], secret=secret,
                          issuer=os.getenv("PANTHEON_RESEARCH_JWT_ISSUER") or os.getenv("PANTHEON_BFF_JWT_ISSUER"),
                          audience=os.getenv("PANTHEON_RESEARCH_JWT_AUDIENCE") or os.getenv("PANTHEON_BFF_JWT_AUDIENCE"))
    headers = {"X-Tenant-Id": tenant_id, "Authorization": f"Bearer {token}"}
    if key:
        headers["Idempotency-Key"] = key
    return headers


def _action_request(method: str, path: str, tenant: str, body: dict | None = None,
                    *, transport: HttpTransport | None = None, key: str | None = None) -> dict:
    send = _active_transport(transport)
    args = (method, _service_base_url("research_demand") + path, _action_headers(tenant, key), body)
    code, reply = send(*args, max_reply_bytes=262144) if send is _urllib_transport else send(*args)
    if code >= 400:
        # Do not copy owner response bodies (which can contain credentials/private data).
        raise MeetingOutputRoutingError(f"research_owner_http_{code}", status_code=code)
    if not isinstance(reply, dict) or len(json.dumps(reply).encode()) > 262144:
        raise MeetingOutputRoutingError("research_owner_malformed_reply", status_code=502)
    return reply


def _ticket_receipt(reply: Mapping[str, Any], tenant: str, *, origin: Mapping[str, Any] | None = None,
                    ticket_id: str | None = None) -> dict[str, Any]:
    """Require a real owner receipt before freezing an id or suppressing a retry."""
    tid = reply.get("ticket_id")
    action = reply.get("meeting_action")
    if (not isinstance(tid, str) or not tid.strip() or len(tid) > 256
            or reply.get("tenant_id") != tenant or not isinstance(action, Mapping)
            or reply.get("action_state") not in ACTION_STATES
            or type(reply.get("revision")) is not int or reply["revision"] < 0
            or (ticket_id is not None and tid != ticket_id)
            or (origin is not None and any(action.get(k) != v for k, v in origin.items()))):
        raise MeetingOutputRoutingError("research_owner_missing_or_invalid_ticket_receipt", status_code=502)
    return dict(reply)


def action_snapshot(ticket: Mapping[str, Any]) -> dict[str, Any]:
    """Bound the minutes projection; full history and evidence stay at Research."""
    origin = dict(ticket.get("meeting_action") or {})
    tid = str(ticket["ticket_id"])
    row = {"action_id": tid, "ticket_id": tid, "meeting_id": origin.get("meeting_id"),
           "owner": ticket.get("owner"), "assignee": ticket.get("owner"),
           "assignee_kind": origin.get("assignee_kind"), "deliverable": origin.get("deliverable"),
           "acceptance_criterion": origin.get("acceptance_criterion"), "due_at": origin.get("due_at"),
           "next_review": origin.get("next_review"), "state": ticket.get("action_state"),
           "revision": ticket.get("revision"), "required_actor": ticket.get("required_actor"),
           "updated_at": ticket.get("updated_at"), "submitted_at": ticket.get("submitted_at"),
           "reviewed_at": ticket.get("reviewed_at"), "evidence_refs": list(ticket.get("evidence_refs") or [])[:8],
           "review_decision": ticket.get("review_decision"), "input_request": ticket.get("input_request"),
           "downstream": {"target": "research_tickets", "id": tid,
                          "href": "/api/v1/research/tickets/" + urllib.parse.quote(tid, safe="")}}
    due = origin.get("due_at")
    row["overdue"] = bool(due and str(due) < _iso(_utc_now()) and row["state"] != "accepted")
    if len(json.dumps(row, ensure_ascii=True).encode()) > ACTION_SNAPSHOT_MAX_BYTES:
        # Preserve identity, criteria and continuation link; never pretend omitted evidence was read.
        row.update(evidence_refs=[], review_decision=None, input_request=None, evidence_omitted=True)
    if len(json.dumps(row, ensure_ascii=True).encode()) > ACTION_SNAPSHOT_MAX_BYTES:
        raise MeetingOutputRoutingError("research_ticket_projection_exceeds_budget", status_code=502)
    return row


def read_meeting_actions(meeting: Mapping[str, Any], *, transport: HttpTransport | None = None) -> dict:
    """Three bounded owner queries; no meeting archive or semantic-memory scan.

    Each lane retains its own continuation, including submitted and accepted work,
    so a large open backlog cannot conceal new submissions or dispositions.
    """
    series = meeting.get("meeting_series")
    if not isinstance(series, Mapping):
        return {"status": "unavailable", "reason": "stable_series_unavailable", "items": [], "pages": {}}
    tenant = str(meeting["tenant_id"])
    pages, items, failures = {}, [], []
    tokens = (meeting.get("metadata") or {}).get("prior_actions_page_tokens") or {}
    for lane, states in (("outstanding", "open,in_progress,waiting_for_input,returned"),
                         ("submitted", "submitted"), ("reviewed", "accepted")):
        query = {"meeting_linked": "true", "meeting_type": series["meeting_type"],
                 "subject_ref": series["subject_ref"], "action_state": states, "page_size": ACTION_PAGE_SIZE}
        if isinstance(tokens, Mapping) and tokens.get(lane):
            query["page_token"] = str(tokens[lane])
        try:
            reply = _action_request("GET", "/api/research/tickets?" + urllib.parse.urlencode(query), tenant,
                                    transport=transport)
            rows, info = reply.get("data"), reply.get("page_info")
            if not isinstance(rows, list) or len(rows) > ACTION_PAGE_SIZE or not isinstance(info, Mapping):
                raise MeetingOutputRoutingError("research_owner_malformed_page", status_code=502)
            page_items = []
            for raw in rows:
                ticket = _ticket_receipt(raw, tenant, origin=series)
                if ticket["meeting_action"].get("meeting_id") != meeting.get("meeting_id"):
                    page_items.append(action_snapshot(ticket))
            items.extend(page_items)
            pages[lane] = {"status": "ok", "next_page_token": info.get("next_page_token"),
                           "has_more": bool(info.get("next_page_token")), "returned": len(page_items),
                           "owner_query": query}
        except Exception as exc:
            reason = str(exc) if isinstance(exc, MeetingOutputRoutingError) else type(exc).__name__
            failures.append(reason)
            pages[lane] = {"status": "denied" if getattr(exc, "status_code", None) in {401, 403} else "unavailable",
                           "reason": reason, "owner_query": query}
    # Resolve actual submitted deliverables through their existing authorized owner.
    # Eight reads total, with full continuation refs retained when there are more.
    evidence, reads = [], 0
    for item in items:
        refs = item.get("evidence_refs") or []
        item["evidence_available"] = bool(refs)
        if item["state"] not in {"submitted", "accepted", "returned"}:
            continue
        for ref in refs:
            status = "unavailable"
            record = {"action_id": item["action_id"], "artifact_id": ref.get("artifact_id"), "status": status}
            try:
                if reads >= 8:
                    raise MeetingOutputRoutingError("evidence_read_budget_exhausted", status_code=503)
                reads += 1
                kind, aid = ref.get("kind"), str(ref.get("artifact_id") or "")
                if kind not in {"research_note", "research_artifact"}:
                    raise MeetingOutputRoutingError("unsupported_evidence_owner", status_code=502)
                path = "/api/research/" + ("notes/" if kind == "research_note" else "artifacts/")
                saved = _action_request("GET", path + urllib.parse.quote(aid, safe=""), tenant, transport=transport)
                if kind == "research_note":
                    binding = saved.get("_meeting_binding") or {}
                    if binding.get("tenant_id") != tenant or binding.get("ticket_id") != item["action_id"]:
                        raise MeetingOutputRoutingError("evidence_binding_mismatch", status_code=502)
                    content = {"title": saved.get("title"), "body": saved.get("body"), "source_refs": saved.get("source_refs")}
                    checksum = saved.get("_meeting_digest")
                else:
                    if saved.get("tenant_id") != tenant:
                        raise MeetingOutputRoutingError("evidence_tenant_mismatch", status_code=502)
                    content = {k: saved[k] for k in ("artifact_id", "title", "summary", "result", "metrics") if k in saved}
                    checksum = saved.get("checksum")
                if not content or checksum != ref.get("checksum") or not checksum:
                    raise MeetingOutputRoutingError("evidence_changed_or_missing", status_code=502)
                if len(json.dumps(content).encode()) > 16384:
                    raise MeetingOutputRoutingError("evidence_content_exceeds_budget", status_code=502)
                record.update(status="ok", content=content, checksum=checksum)
            except Exception as exc:
                item["evidence_available"] = False
                record["reason"] = str(exc) if isinstance(exc, MeetingOutputRoutingError) else type(exc).__name__
            evidence.append(record)
    return {"status": "degraded" if failures and items else "unavailable" if failures else "ok",
            "items": items, "pages": pages, "evidence": evidence}


def dispatch_meeting_actions(meeting: dict[str, Any], *, store_path: str | Path | None = None,
                             transport: HttpTransport | None = None) -> list[dict[str, Any]]:
    """Reserve work at its existing owner, with an unchanged source on ambiguous retries."""
    ledger = _DispatchLedger(Path(store_path) if store_path is not None else _default_store_path())
    tenant, mid = str(meeting["tenant_id"]), str(meeting["meeting_id"])
    actions = (meeting.get("synthesis") or {}).get("actions") or []
    if len(actions) > ACTION_PAGE_SIZE:
        raise ValueError("at most 8 adopted actions per meeting")
    results = []
    for action in actions:
        source = str(action["source_action_id"])
        key = f"meeting-action:{tenant}:{mid}:{source}"
        saved = ledger.get(key) or {}
        if saved.get("receipt"):
            action.update(copy.deepcopy(saved["receipt"]))
            results.append(action)
            continue
        origin = {"meeting_id": mid, "source_action_id": source, **meeting["meeting_series"],
                  "assignee_kind": action["assignee_kind"], "deliverable": action["deliverable"],
                  "acceptance_criterion": action["acceptance_criterion"], "reviewer_id": "consultation-svc",
                  "source_refs": action["source_refs"]}
        for field in ("due_at", "next_review"):
            if action.get(field):
                origin[field] = action[field]
        body = {"title": action["deliverable"][:256], "description": action["acceptance_criterion"],
                "priority": "normal", "owner": action["assignee"], "meeting_action": origin}
        # Keep the original request even after a process restart; divergent replay must fail at the owner.
        if saved.get("request") is not None and saved["request"] != body:
            raise ValueError("adopted action changed after dispatch attempt")
        ledger.put(key, {"request": body})
        try:
            ticket = _ticket_receipt(_action_request("POST", "/api/research/tickets", tenant, body,
                                      transport=transport, key=key), tenant, origin=origin)
            if ticket.get("owner") != action["assignee"]:
                raise MeetingOutputRoutingError("research_owner_assignment_mismatch", status_code=502)
            receipt = {"action_id": ticket["ticket_id"], "ticket_id": ticket["ticket_id"],
                       "state": ticket["action_state"], "downstream": action_snapshot(ticket)["downstream"],
                       "dispatch_error": None}
            ledger.put(key, {"request": body, "receipt": receipt})
            action.update(receipt)
        except Exception as exc:
            action.update(dispatch_error=str(exc) if isinstance(exc, MeetingOutputRoutingError) else type(exc).__name__,
                          state="dispatch_pending")
        results.append(action)
    return results


def review_meeting_actions(meeting: dict[str, Any], *, store_path: str | Path | None = None,
                           transport: HttpTransport | None = None) -> list[dict[str, Any]]:
    """Apply a chair's explicit criterion comparison to the same submitted owner revision."""
    ledger = _DispatchLedger(Path(store_path) if store_path is not None else _default_store_path())
    tenant, mid = str(meeting["tenant_id"]), str(meeting["meeting_id"])
    prior = {r["action_id"]: r for r in meeting.get("prior_actions") or []}
    results = []
    for review in (meeting.get("synthesis") or {}).get("action_reviews") or []:
        tid = review["action_id"]
        key = f"meeting-action-review:{tenant}:{mid}:{tid}"
        saved = ledger.get(key) or {}
        if saved.get("receipt"):
            results.append(copy.deepcopy(saved["receipt"]))
            continue
        snapshot = prior[tid]
        patch = {"action": review["decision"], "expected_revision": snapshot["revision"],
                 "idempotency_key": key, "reason": review["reason"], "review_source": mid}
        if saved.get("request") is not None and saved["request"] != patch:
            raise ValueError("chair review changed after dispatch attempt")
        ledger.put(key, {"request": patch})
        try:
            reply = _action_request("PATCH", "/api/research/tickets/" + urllib.parse.quote(tid, safe=""), tenant,
                                    {"patch": patch}, transport=transport, key=key)
            ticket = _ticket_receipt(reply, tenant, ticket_id=tid)
            decision = ticket.get("review_decision") or {}
            if decision.get("review_source") != mid or decision.get("decision") != review["decision"]:
                raise MeetingOutputRoutingError("research_owner_missing_review_receipt", status_code=502)
            receipt = {**review, "status": "recorded", "owner_receipt": action_snapshot(ticket)}
            ledger.put(key, {"request": patch, "receipt": receipt})
        except Exception as exc:
            receipt = {**review, "status": "retry_pending", "error": str(exc) if isinstance(exc, MeetingOutputRoutingError) else type(exc).__name__}
        results.append(receipt)
    return results


def read_action_status(tenant_id: str, ticket_id: str, *, transport: HttpTransport | None = None) -> dict:
    try:
        reply = _action_request("GET", "/api/research/tickets/" + urllib.parse.quote(ticket_id, safe=""),
                                tenant_id, transport=transport)
        return {"status": "ok", "ticket": action_snapshot(_ticket_receipt(reply, tenant_id, ticket_id=ticket_id))}
    except Exception as exc:
        return {"status": "denied" if getattr(exc, "status_code", None) in {401, 403} else "unavailable",
                "reason": str(exc) if isinstance(exc, MeetingOutputRoutingError) else type(exc).__name__}


def dispatch_proposals(
    meeting: dict[str, Any],
    *,
    store_path: str | Path | None = None,
    transport: HttpTransport | None = None,
    now: Callable[[], datetime] | None = None,
) -> list[dict[str, Any]]:
    """Dispatch each proposal in ``meeting`` once and idempotently to its owner entry point.

    Returns a list of result dicts:
      ``{proposal_id, downstream_target, downstream_id, status, error}``
    and updates the proposals on ``meeting`` in place with their downstream id
    and current status/error.
    """
    if not isinstance(meeting, dict):
        raise TypeError("meeting must be a dict")

    ledger = _DispatchLedger(Path(store_path) if store_path is not None else _default_store_path())
    send = _active_transport(transport)
    current_dt = _utc_now(now)
    now_iso = _iso(current_dt)
    meeting_id = str(meeting.get("meeting_id") or meeting.get("id") or meeting.get("request_id") or "meeting-unknown").strip()
    default_tenant = str(meeting.get("tenant_id") or os.getenv("PANTHEON_TENANT_ID") or "tenant-a").strip()

    proposals = _extract_proposals_list(meeting)
    results: list[dict[str, Any]] = []

    for proposal in proposals:
        proposal_id = str(proposal.get("proposal_id") or "").strip()
        if not proposal_id:
            proposal_id = "prop-" + hashlib.sha256(json.dumps(proposal, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
            proposal["proposal_id"] = proposal_id

        prop_meeting_id = str(proposal.get("meeting_id") or meeting_id).strip()
        proposal["meeting_id"] = prop_meeting_id
        kind = str(proposal.get("kind") or "").strip()
        raw_sponsor = proposal.get("sponsor_persona_id")
        payload = dict(proposal.get("payload") or {}) if isinstance(proposal.get("payload"), Mapping) else {}
        citations = list(proposal.get("citations") or []) if isinstance(proposal.get("citations"), list) else []
        tenant_id = str(proposal.get("tenant_id") or payload.get("tenant_id") or default_tenant).strip()

        _register_live_refs(meeting, proposal, proposal_id)

        existing = ledger.get(proposal_id)
        if (
            existing is not None
            and existing.get("dispatched_at") is not None
            and existing.get("downstream_id") is not None
            and existing.get("error") is None
            and existing.get("status") != "failed"
        ):
            target = str(existing["downstream_target"])
            d_id = str(existing["downstream_id"])
            status = str(existing["status"])
            updated_at = str(existing.get("updated_at") or existing["dispatched_at"])
            _apply_to_live_refs(
                proposal_id,
                downstream_target=target,
                downstream_id=d_id,
                status=status,
                error=None,
                updated_at=updated_at,
            )
            results.append(
                {
                    "proposal_id": proposal_id,
                    "downstream_target": target,
                    "downstream_id": d_id,
                    "status": status,
                    "error": None,
                }
            )
            continue

        attempts = int((existing or {}).get("attempt_count", 0)) + 1
        idempotency_key = f"meeting-output:{prop_meeting_id}:{proposal_id}"

        if kind not in SUPPORTED_PROPOSAL_KINDS:
            target = f"/api/unknown/{kind or 'missing'}"
            err = f"unsupported_proposal_kind:{kind!r}"
            ledger.put(
                proposal_id,
                {
                    "proposal_id": proposal_id,
                    "meeting_id": prop_meeting_id,
                    "tenant_id": tenant_id,
                    "kind": kind,
                    "sponsor_persona_id": raw_sponsor,
                    "downstream_target": target,
                    "downstream_id": None,
                    "status": "failed",
                    "error": err,
                    "updated_at": now_iso,
                    "dispatched_at": None,
                    "attempt_count": attempts,
                    "idempotency_key": idempotency_key,
                    "request_meta": {},
                },
            )
            _apply_to_live_refs(
                proposal_id,
                downstream_target=target,
                downstream_id=None,
                status="failed",
                error=err,
                updated_at=now_iso,
            )
            results.append(
                {
                    "proposal_id": proposal_id,
                    "downstream_target": target,
                    "downstream_id": None,
                    "status": "failed",
                    "error": err,
                }
            )
            continue

        try:
            sponsor_persona_id = _check_sponsor_eligibility(
                kind=kind,
                sponsor_persona_id=raw_sponsor,
                proposal=proposal,
                payload=payload,
                meeting=meeting,
            )
            headers = _auth_headers(
                kind=kind,
                tenant_id=tenant_id,
                sponsor_persona_id=sponsor_persona_id,
                idempotency_key=idempotency_key,
                owner_user_id=str(payload.get("owner_user_id") or "user-a")
                if kind == "agora_candidate_decision"
                else None,
            )
            target_path, request_body, request_meta = _build_outbound_request(
                kind=kind,
                proposal_id=proposal_id,
                meeting_id=prop_meeting_id,
                tenant_id=tenant_id,
                sponsor_persona_id=sponsor_persona_id,
                payload=payload,
                citations=citations,
                now=current_dt,
                meeting=meeting,
                proposal=proposal,
                send=send,
            )
        except Exception as exc:
            target_path = _target_path(
                kind,
                str(raw_sponsor).strip() if raw_sponsor else None,
                payload,
                prop_meeting_id,
                proposal_id,
            )
            err = str(exc)
            ledger.put(
                proposal_id,
                {
                    "proposal_id": proposal_id,
                    "meeting_id": prop_meeting_id,
                    "tenant_id": tenant_id,
                    "kind": kind,
                    "sponsor_persona_id": raw_sponsor,
                    "downstream_target": target_path,
                    "downstream_id": None,
                    "status": "failed",
                    "error": err,
                    "updated_at": now_iso,
                    "dispatched_at": None,
                    "attempt_count": attempts,
                    "idempotency_key": idempotency_key,
                    "request_meta": {},
                },
            )
            _apply_to_live_refs(
                proposal_id,
                downstream_target=target_path,
                downstream_id=None,
                status="failed",
                error=err,
                updated_at=now_iso,
            )
            results.append(
                {
                    "proposal_id": proposal_id,
                    "downstream_target": target_path,
                    "downstream_id": None,
                    "status": "failed",
                    "error": err,
                }
            )
            continue

        base_url = _service_base_url(kind)
        full_url = f"{base_url}{target_path}"

        try:
            status_code, resp_body = send("POST", full_url, headers, request_body)
            # Handle idempotent replay when an earlier attempt succeeded on the server before response loss
            if status_code == 409 and kind == "evolution_proposal":
                dec_id = urllib.parse.quote(str(request_meta["decision_id"]), safe="")
                status_code, resp_body = send("GET", f"{base_url}/api/evolution/proposals/{dec_id}", headers, None)
            elif status_code == 409 and kind == "governance_approval":
                dec_id = urllib.parse.quote(str(request_meta["decision_id"]), safe="")
                status_code, resp_body = send("GET", f"{base_url}/api/governance/approvals/{dec_id}", headers, None)
            elif status_code == 422 and kind == "lesson_candidate" and "already exists" in json.dumps(resp_body):
                lc_id = urllib.parse.quote(str(request_meta["lesson_candidate_id"]), safe="")
                read_headers = _auth_headers(
                    kind=kind,
                    tenant_id=tenant_id,
                    sponsor_persona_id=sponsor_persona_id,
                    owner_user_id=request_meta.get("owner_user_id"),
                    read_only=True,
                )
                status_code, resp_body = send("GET", f"{base_url}/api/memory/trade-lessons/{lc_id}", read_headers, None)

            if status_code >= 400:
                raise MeetingOutputRoutingError(
                    _format_http_error(status_code, resp_body),
                    status_code=status_code,
                )

            downstream_id, initial_status, updated_at = _extract_dispatch_outcome(
                kind=kind,
                sponsor_persona_id=sponsor_persona_id,
                response_body=resp_body,
                request_meta=request_meta,
                now_iso=now_iso,
            )
            if not downstream_id:
                raise MeetingOutputRoutingError("owner response did not include a downstream identifier")
            if initial_status.lower() in FORBIDDEN_APPROVED_OR_APPLIED_STATES:
                raise MeetingOutputRoutingError(
                    f"downstream gate violation: entry point returned pre-approved/applied status {initial_status!r}"
                )
        except Exception as exc:
            err = str(exc)
            ledger.put(
                proposal_id,
                {
                    "proposal_id": proposal_id,
                    "meeting_id": prop_meeting_id,
                    "tenant_id": tenant_id,
                    "kind": kind,
                    "sponsor_persona_id": sponsor_persona_id,
                    "downstream_target": target_path,
                    "downstream_id": None,
                    "status": "failed",
                    "error": err,
                    "updated_at": now_iso,
                    "dispatched_at": None,
                    "attempt_count": attempts,
                    "idempotency_key": idempotency_key,
                    "request_meta": request_meta,
                },
            )
            _apply_to_live_refs(
                proposal_id,
                downstream_target=target_path,
                downstream_id=None,
                status="failed",
                error=err,
                updated_at=now_iso,
            )
            results.append(
                {
                    "proposal_id": proposal_id,
                    "downstream_target": target_path,
                    "downstream_id": None,
                    "status": "failed",
                    "error": err,
                }
            )
            continue

        ledger.put(
            proposal_id,
            {
                "proposal_id": proposal_id,
                "meeting_id": prop_meeting_id,
                "tenant_id": tenant_id,
                "kind": kind,
                "sponsor_persona_id": sponsor_persona_id,
                "downstream_target": target_path,
                "downstream_id": downstream_id,
                "status": initial_status,
                "error": None,
                "updated_at": updated_at,
                "dispatched_at": now_iso,
                "attempt_count": attempts,
                "idempotency_key": idempotency_key,
                "request_meta": request_meta,
            },
        )
        _apply_to_live_refs(
            proposal_id,
            downstream_target=target_path,
            downstream_id=downstream_id,
            status=initial_status,
            error=None,
            updated_at=updated_at,
        )
        results.append(
            {
                "proposal_id": proposal_id,
                "downstream_target": target_path,
                "downstream_id": downstream_id,
                "status": initial_status,
                "error": None,
            }
        )

    meeting["proposal_dispatches"] = copy.deepcopy(results)
    return results


def read_downstream_status(
    proposal_id: str,
    *,
    meeting: dict[str, Any] | None = None,
    store_path: str | Path | None = None,
    transport: HttpTransport | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Read live downstream status for ``proposal_id`` from its authoritative owner route.

    Returns ``{status, downstream_id, updated_at}`` and updates both the durable
    ledger and any tracked meeting/proposal record in place.
    """
    clean_id = str(proposal_id or "").strip()
    if not clean_id:
        raise KeyError("proposal_id is required")

    ledger = _DispatchLedger(Path(store_path) if store_path is not None else _default_store_path())
    entry = ledger.get(clean_id)
    if entry is None:
        raise KeyError(f"Unknown proposal_id: {clean_id!r}")

    if meeting is not None and isinstance(meeting, dict):
        for prop in _extract_proposals_list(meeting):
            if str(prop.get("proposal_id") or "") == clean_id:
                _register_live_refs(meeting, prop, clean_id)

    downstream_id = entry.get("downstream_id")
    if not downstream_id or entry.get("status") == "failed":
        return {
            "status": str(entry.get("status") or "failed"),
            "downstream_id": None,
            "updated_at": str(entry.get("updated_at") or _iso(_utc_now(now))),
        }

    kind = str(entry["kind"])
    tenant_id = str(entry.get("tenant_id") or "tenant-a")
    sponsor_persona_id = entry.get("sponsor_persona_id")
    request_meta = dict(entry.get("request_meta") or {})
    base_url = _service_base_url(kind)
    send = _active_transport(transport)
    try:
        headers = _auth_headers(
            kind=kind,
            tenant_id=tenant_id,
            sponsor_persona_id=sponsor_persona_id,
            owner_user_id=request_meta.get("owner_user_id"),
            read_only=True,
        )
    except RuntimeError as exc:
        raise MeetingOutputRoutingError(str(exc), status_code=503) from exc
    now_iso = _iso(_utc_now(now))

    if kind == "research_demand":
        query = urllib.parse.urlencode({"persona_id": str(sponsor_persona_id or "")})
        code, body = send("GET", f"{base_url}/api/research/persona-demands?{query}", headers, None)
        if code >= 400:
            raise MeetingOutputRoutingError(_format_http_error(code, body), status_code=code)
        demands = body.get("demands") if isinstance(body.get("demands"), list) else []
        matched = next((row for row in demands if isinstance(row, Mapping) and row.get("demand_id") == downstream_id), None)
        if matched is None:
            raise MeetingOutputRoutingError(f"Research demand {downstream_id!r} not found", status_code=404)
        live_status = str(matched.get("status") or "open")
        updated_at = str(matched.get("satisfied_at") or matched.get("updated_at") or matched.get("created_at") or now_iso)

    elif kind == "evolution_proposal":
        encoded = urllib.parse.quote(str(downstream_id), safe="")
        code, body = send("GET", f"{base_url}/api/evolution/proposals/{encoded}", headers, None)
        if code >= 400:
            raise MeetingOutputRoutingError(_format_http_error(code, body), status_code=code)
        live_status = str(body.get("decision_state") or "proposed")
        exec_res = body.get("execution_result") if isinstance(body.get("execution_result"), Mapping) else {}
        chain = body.get("review_chain") if isinstance(body.get("review_chain"), list) else []
        last_chain_at = chain[-1].get("at") if chain and isinstance(chain[-1], Mapping) else None
        updated_at = str(exec_res.get("executed_at") or last_chain_at or body.get("updated_at") or body.get("created_at") or now_iso)

    elif kind == "harness_patch":
        persona_seg = urllib.parse.quote(str(sponsor_persona_id or ""), safe="")
        code, body = send("GET", f"{base_url}/api/personas/{persona_seg}/harness", headers, None)
        if code >= 400:
            raise MeetingOutputRoutingError(_format_http_error(code, body), status_code=code)
        patches = body.get("patches") if isinstance(body.get("patches"), Mapping) else {}
        patch_row = patches.get(str(downstream_id))
        if not isinstance(patch_row, Mapping):
            raise MeetingOutputRoutingError(f"Harness patch {downstream_id!r} not found", status_code=404)
        live_status = str(patch_row.get("status") or "draft")
        events = [
            ev for ev in (body.get("events") if isinstance(body.get("events"), list) else [])
            if isinstance(ev, Mapping) and ev.get("patch_id") == downstream_id
        ]
        last_event_at = events[-1].get("at") if events else None
        updated_at = str(patch_row.get("adopted_at") or last_event_at or now_iso)

    elif kind == "lesson_candidate":
        encoded = urllib.parse.quote(str(downstream_id), safe="")
        code, body = send("GET", f"{base_url}/api/memory/trade-lessons/{encoded}", headers, None)
        if code >= 400:
            raise MeetingOutputRoutingError(_format_http_error(code, body), status_code=code)
        live_status = str(body.get("review_state") or "proposed")
        updated_at = str(body.get("updated_at") or body.get("created_at") or now_iso)

    elif kind == "lesson_rule":
        persona_seg = urllib.parse.quote(str(sponsor_persona_id or ""), safe="")
        code, body = send("GET", f"{base_url}/api/personas/{persona_seg}/lesson-rule-proposals", headers, None)
        if code >= 400:
            raise MeetingOutputRoutingError(_format_http_error(code, body), status_code=code)
        proposals = body.get("proposals") if isinstance(body.get("proposals"), list) else []
        matched = next((row for row in proposals if isinstance(row, Mapping) and row.get("proposal_id") == downstream_id), None)
        if matched is None:
            raise MeetingOutputRoutingError(f"Lesson-rule proposal {downstream_id!r} not found", status_code=404)
        live_status = str(matched.get("status") or "recorded")
        updated_at = str(matched.get("updated_at") or matched.get("created_at") or now_iso)

    elif kind == "pool_decision":
        persona_seg = urllib.parse.quote(str(sponsor_persona_id or ""), safe="")
        pool_version = int(request_meta.get("pool_version") or str(downstream_id).rsplit(":v", 1)[-1])
        code, body = send("GET", f"{base_url}/api/personas/{persona_seg}/strategy-pool/versions/{pool_version}", headers, None)
        if code >= 400:
            raise MeetingOutputRoutingError(_format_http_error(code, body), status_code=code)
        decision = body.get("decision") if isinstance(body.get("decision"), Mapping) else {}
        if decision.get("status") == "refused":
            live_status = "refused"
        else:
            live_status = str(decision.get("execution_status") or decision.get("status") or "requires_approved_envelope")
        updated_at = str(body.get("updated_at") or body.get("created_at") or now_iso)

    elif kind == "governance_approval":
        encoded = urllib.parse.quote(str(downstream_id), safe="")
        code, body = send("GET", f"{base_url}/api/governance/approvals/{encoded}", headers, None)
        if code >= 400:
            raise MeetingOutputRoutingError(_format_http_error(code, body), status_code=code)
        state = str(body.get("decision_state") or "proposed")
        outcome = body.get("decision")
        live_status = str(outcome) if state == "decided" and outcome else state
        updated_at = str(body.get("decided_at") or body.get("recorded_at") or body.get("created_at") or now_iso)

    elif kind == "agora_candidate_decision":
        encoded = urllib.parse.quote(str(downstream_id), safe="")
        code, body = send("GET", f"{base_url}/bff/agora/proposals/{encoded}/candidate", headers, None)
        if code >= 400:
            raise MeetingOutputRoutingError(_format_http_error(code, body), status_code=code)
        data = body.get("data") if isinstance(body.get("data"), Mapping) else body
        cand = data.get("candidate") if isinstance(data.get("candidate"), Mapping) else data
        meta = body.get("meta") if isinstance(body.get("meta"), Mapping) else {}
        live_status = str(cand.get("state") or cand.get("status") or "draft")
        updated_at = str(cand.get("updated_at") or cand.get("created_at") or meta.get("snapshot_at") or now_iso)

    else:
        raise MeetingOutputRoutingError(f"Unsupported proposal kind: {kind!r}", status_code=400)

    entry["status"] = live_status
    entry["updated_at"] = updated_at
    ledger.put(clean_id, entry)
    _apply_to_live_refs(
        clean_id,
        downstream_target=str(entry["downstream_target"]),
        downstream_id=str(downstream_id),
        status=live_status,
        error=None,
        updated_at=updated_at,
    )
    return {
        "status": live_status,
        "downstream_id": str(downstream_id),
        "updated_at": updated_at,
    }


def list_dispatched_proposals(
    *,
    meeting_id: str | None = None,
    sponsor_persona_id: str | None = None,
    status: str | None = None,
    store_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return proposal records formatted for ``GET /api/consult/meetings/proposals``."""
    ledger = _DispatchLedger(Path(store_path) if store_path is not None else _default_store_path())
    items: list[dict[str, Any]] = []
    for row in ledger.list_all():
        if not row.get("proposal_id"):
            continue  # Action receipts share the existing ledger, not the legacy proposal projection.
        if meeting_id and row.get("meeting_id") != meeting_id:
            continue
        if sponsor_persona_id and row.get("sponsor_persona_id") != sponsor_persona_id:
            continue
        if status and row.get("status") != status:
            continue
        items.append(
            {
                "proposal_id": row["proposal_id"],
                "meeting_id": row["meeting_id"],
                "kind": row["kind"],
                "sponsor_persona_id": row.get("sponsor_persona_id"),
                "downstream": {
                    "target": row.get("downstream_target"),
                    "id": row.get("downstream_id"),
                    "status": row.get("status"),
                },
            }
        )
    return items

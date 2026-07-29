"""Fail-closed authority boundary for committing a persona teaching target.

Caller-provided approval assertions are intentionally absent from the public
API.  A commit reads the configured persona and approval authorities, writes a
digest-bound target with an idempotency key, and then verifies a terminal
authoritative readback.  No write response is treated as proof by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


class PersonaTargetError(RuntimeError):
    """Raised when persona-target authority cannot be proven exactly."""


@dataclass(frozen=True)
class PersonaTargetResponse:
    """Transport-neutral HTTP response used by injectable test transports."""

    status_code: int
    body: Any


class PersonaTargetTransport(Protocol):
    """Minimal transport contract for persona target authority calls."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> PersonaTargetResponse:
        ...


class UrllibPersonaTargetTransport:
    """Standard-library HTTP transport with strict JSON decoding."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Mapping[str, Any] | None,
        timeout_seconds: float,
    ) -> PersonaTargetResponse:
        data = None
        if json_body is not None:
            try:
                data = json.dumps(
                    dict(json_body),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            except (TypeError, ValueError) as exc:
                raise PersonaTargetError("persona target write body is not canonical JSON") from exc
        request = Request(url, data=data, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
                status_code = int(response.status)
                raw = response.read()
        except HTTPError as exc:
            raise PersonaTargetError(
                f"persona target authority returned HTTP {exc.code}"
            ) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise PersonaTargetError("persona target authority transport failed") from exc

        return PersonaTargetResponse(status_code=status_code, body=raw)


_FORBIDDEN_MARKERS = frozenset(
    {"submitted", "queued", "pending", "seed", "snapshot", "fixture"}
)
_MARKER_FIELDS = (
    "status",
    "state",
    "phase",
    "source",
    "source_type",
    "record_type",
    "kind",
    "mode",
)
_MARKER_FIELD_SUFFIXES = tuple(f"_{field}" for field in _MARKER_FIELDS)
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def canonical_digest(payload: Any) -> str:
    """Return a stable SHA-256 identity for finite JSON data."""

    try:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PersonaTargetError("persona target input is not finite canonical JSON") from exc
    return hashlib.sha256(encoded).hexdigest()


def read_persona_target_precondition(
    *,
    persona_id: str,
    tenant_id: str,
    persona_readback_url: str,
    authorization_token: str,
    trusted_now: datetime,
    transport: PersonaTargetTransport | None = None,
    timeout_seconds: float = 5.0,
    max_future_skew_seconds: float = 60.0,
) -> dict[str, Any]:
    """Read the authoritative current persona generation without mutating it."""

    normalized_persona_id = _required_text(persona_id, "persona_id")
    normalized_tenant_id = _required_text(tenant_id, "tenant_id")
    url = _configured_url(persona_readback_url, "persona_readback_url")
    authorization_headers = {
        **_authorization_headers(authorization_token),
        "X-Tenant-Id": normalized_tenant_id,
    }
    now = _trusted_utc(trusted_now)
    timeout = _positive_finite_number(timeout_seconds, "timeout_seconds")
    future_skew = _nonnegative_finite_number(
        max_future_skew_seconds, "max_future_skew_seconds"
    )
    payload = _request_json_object(
        transport or UrllibPersonaTargetTransport(),
        "GET",
        url,
        label="persona pre-readback",
        expected_statuses=(200,),
        timeout_seconds=timeout,
        headers=authorization_headers,
    )
    record = _unwrap(payload, ("persona", "target"))
    status, generation, record_ref, recorded_at = _validate_persona_authority(
        record,
        persona_id=normalized_persona_id,
        tenant_id=normalized_tenant_id,
        trusted_now=now,
        max_future_skew_seconds=future_skew,
    )
    return {
        "persona_id": normalized_persona_id,
        "tenant_id": normalized_tenant_id,
        "status": status,
        "current_generation": generation,
        "expected_previous_generation": generation,
        "target_generation": generation + 1,
        "precondition_digest": canonical_digest(record),
        "controller_record_ref": record_ref,
        "recorded_at": _isoformat(recorded_at),
    }


def commit_persona_target(
    *,
    persona_id: str,
    tenant_id: str,
    session_id: str,
    candidate: Any,
    control_state: Any,
    evaluation_proof: Any,
    generation: int,
    idempotency_key: str,
    expected_precondition_digest: str,
    approval_decision_ref: str,
    persona_readback_url: str,
    approval_readback_url: str,
    target_write_url: str,
    target_readback_url: str,
    authorization_token: str,
    trusted_now: datetime,
    transport: PersonaTargetTransport | None = None,
    timeout_seconds: float = 5.0,
    max_future_skew_seconds: float = 60.0,
) -> dict[str, Any]:
    """Commit one approved target and return its verified digest receipt.

    ``generation`` and ``expected_precondition_digest`` come from the durable
    evaluation proof populated by :func:`read_persona_target_precondition`.
    A fresh commit requires the persona pre-readback at ``generation - 1`` to
    match that digest exactly.  A same-generation readback is accepted only as
    an idempotent replay of the same digest-bound target.
    """

    normalized_persona_id = _required_text(persona_id, "persona_id")
    normalized_tenant_id = _required_text(tenant_id, "tenant_id")
    normalized_session_id = _required_text(session_id, "session_id")
    normalized_key = _required_text(idempotency_key, "idempotency_key")
    normalized_generation = _strict_int(generation, "generation", minimum=1)
    normalized_precondition_digest = _required_digest(
        expected_precondition_digest, "expected_precondition_digest"
    )
    normalized_approval_ref = _required_text(
        approval_decision_ref, "approval_decision_ref"
    )
    authorization_headers = {
        **_authorization_headers(authorization_token),
        "X-Tenant-Id": normalized_tenant_id,
    }
    now = _trusted_utc(trusted_now)
    normalized_timeout = _positive_finite_number(timeout_seconds, "timeout_seconds")
    future_skew = _nonnegative_finite_number(
        max_future_skew_seconds, "max_future_skew_seconds"
    )

    urls = {
        "persona pre-readback": _configured_url(
            persona_readback_url, "persona_readback_url"
        ),
        "approval readback": _configured_url(
            approval_readback_url, "approval_readback_url"
        ),
        "target write": _configured_url(target_write_url, "target_write_url"),
        "target terminal readback": _configured_url(
            target_readback_url, "target_readback_url"
        ),
    }
    client = transport or UrllibPersonaTargetTransport()

    candidate_digest = canonical_digest(candidate)
    control_digest = canonical_digest(control_state)
    proof_digest, proof_precondition_ref = _validated_proof_digest(
        evaluation_proof,
        candidate_digest=candidate_digest,
        control_digest=control_digest,
        persona_id=normalized_persona_id,
        tenant_id=normalized_tenant_id,
        generation=normalized_generation,
        expected_precondition_digest=normalized_precondition_digest,
        approval_decision_ref=normalized_approval_ref,
    )

    persona_payload = _request_json_object(
        client,
        "GET",
        urls["persona pre-readback"],
        label="persona pre-readback",
        expected_statuses=(200,),
        timeout_seconds=normalized_timeout,
        headers=authorization_headers,
    )
    persona = _unwrap(persona_payload, ("persona", "target"))
    _, observed_generation, persona_record_ref, _ = _validate_persona_authority(
        persona,
        persona_id=normalized_persona_id,
        tenant_id=normalized_tenant_id,
        trusted_now=now,
        max_future_skew_seconds=future_skew,
    )
    pre_generation = _validate_persona_pre_readback(
        persona,
        persona_id=normalized_persona_id,
        tenant_id=normalized_tenant_id,
        session_id=normalized_session_id,
        generation=normalized_generation,
        candidate_digest=candidate_digest,
        control_digest=control_digest,
        proof_digest=proof_digest,
    )
    if observed_generation == normalized_generation - 1:
        observed_precondition_digest = canonical_digest(persona)
        if observed_precondition_digest != normalized_precondition_digest:
            raise PersonaTargetError("persona pre-readback digest mismatch")
        if persona_record_ref != proof_precondition_ref:
            raise PersonaTargetError("persona pre-readback controller_record_ref mismatch")

    approval_payload = _request_json_object(
        client,
        "GET",
        urls["approval readback"],
        label="approval readback",
        expected_statuses=(200,),
        timeout_seconds=normalized_timeout,
        headers=authorization_headers,
    )
    approval = _unwrap(approval_payload, ("approval", "approval_decision"))
    approval_record_ref, _ = _validate_authoritative_record(
        approval,
        label="approval readback",
        trusted_now=now,
        max_future_skew_seconds=future_skew,
    )
    approval_decision_id = _validate_approval(
        approval,
        expected_approval_decision_ref=normalized_approval_ref,
        persona_id=normalized_persona_id,
        tenant_id=normalized_tenant_id,
        session_id=normalized_session_id,
        candidate_digest=candidate_digest,
        proof_digest=proof_digest,
        trusted_now=now,
    )
    approval_digest = canonical_digest(approval)

    binding = {
        "persona_id": normalized_persona_id,
        "tenant_id": normalized_tenant_id,
        "session_id": normalized_session_id,
        "candidate_digest": candidate_digest,
        "control_digest": control_digest,
        "proof_digest": proof_digest,
        "approval_digest": approval_digest,
        "generation": normalized_generation,
    }
    write_body = {
        **binding,
        "expected_previous_generation": normalized_generation - 1,
        "expected_precondition_digest": normalized_precondition_digest,
        "expected_precondition_record_ref": proof_precondition_ref,
        "approval_decision_id": approval_decision_id,
        "approval_decision_ref": normalized_approval_ref,
        "candidate": candidate,
        "control_state": control_state,
        "evaluation_proof": evaluation_proof,
    }
    write_payload = _request_json_object(
        client,
        "POST",
        urls["target write"],
        label="persona target write",
        expected_statuses=(200, 201),
        timeout_seconds=normalized_timeout,
        headers={
            **authorization_headers,
            "Content-Type": "application/json",
            "Idempotency-Key": normalized_key,
        },
        json_body=write_body,
    )
    write_record = _unwrap(write_payload, ("target", "persona_target"))
    write_record_ref, _ = _validate_authoritative_record(
        write_record,
        label="persona target write response",
        trusted_now=now,
        max_future_skew_seconds=future_skew,
    )
    _validate_committed_target(write_record, binding, "persona target write response")

    terminal_payload = _request_json_object(
        client,
        "GET",
        urls["target terminal readback"],
        label="persona target terminal readback",
        expected_statuses=(200,),
        timeout_seconds=normalized_timeout,
        headers=authorization_headers,
    )
    terminal = _unwrap(terminal_payload, ("target", "persona_target"))
    terminal_record_ref, terminal_recorded_at = _validate_authoritative_record(
        terminal,
        label="persona target terminal readback",
        trusted_now=now,
        max_future_skew_seconds=future_skew,
    )
    _validate_committed_target(terminal, binding, "persona target terminal readback")
    if terminal_record_ref != write_record_ref:
        raise PersonaTargetError("persona target controller_record_ref changed before readback")

    return {
        "status": "committed",
        **binding,
        "approval_decision_id": approval_decision_id,
        "approval_decision_ref": normalized_approval_ref,
        "approval_controller_record_ref": approval_record_ref,
        "target_controller_record_ref": terminal_record_ref,
        "target_recorded_at": _isoformat(terminal_recorded_at),
        "expected_precondition_digest": normalized_precondition_digest,
        "expected_precondition_record_ref": proof_precondition_ref,
        "idempotency_key": normalized_key,
        "pre_generation": pre_generation,
        "replayed": bool(
            pre_generation == normalized_generation
            or write_record.get("replayed") is True
            or terminal.get("replayed") is True
        ),
    }


def _configured_url(value: Any, label: str) -> str:
    url = _required_text(value, label)
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PersonaTargetError(f"{label} must be an explicit HTTP(S) URL")
    if parsed.username is not None or parsed.password is not None:
        raise PersonaTargetError(f"{label} must not contain credentials")
    return url


def _request_json_object(
    transport: PersonaTargetTransport,
    method: str,
    url: str,
    *,
    label: str,
    expected_statuses: tuple[int, ...],
    timeout_seconds: float,
    headers: Mapping[str, str] | None = None,
    json_body: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    request_headers = {"Accept": "application/json", **dict(headers or {})}
    try:
        response = transport.request(
            method,
            url,
            headers=request_headers,
            json_body=json_body,
            timeout_seconds=timeout_seconds,
        )
    except Exception:  # noqa: BLE001 - every authority transport failure is closed.
        # A transport exception can contain request headers.  Suppress its
        # context so a service token can never escape through this boundary.
        raise PersonaTargetError(f"{label} transport failed") from None

    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise PersonaTargetError(f"{label} returned an invalid HTTP status")
    if status_code not in expected_statuses:
        raise PersonaTargetError(f"{label} returned HTTP {status_code}")
    body = getattr(response, "body", None)
    if body is None and hasattr(response, "json"):
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            raise PersonaTargetError(f"{label} did not return JSON") from None
    if isinstance(body, (bytes, bytearray)):
        try:
            body = bytes(body).decode("utf-8")
        except UnicodeError as exc:
            raise PersonaTargetError(f"{label} did not return UTF-8 JSON") from exc
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError as exc:
            raise PersonaTargetError(f"{label} did not return JSON") from exc
    if not isinstance(body, Mapping):
        raise PersonaTargetError(f"{label} must return a JSON object")
    return dict(body)


def _unwrap(payload: Mapping[str, Any], envelope_keys: tuple[str, ...]) -> dict[str, Any]:
    for key in envelope_keys:
        value = payload.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return dict(payload)


def _validate_authoritative_record(
    record: Mapping[str, Any],
    *,
    label: str,
    trusted_now: datetime,
    max_future_skew_seconds: float,
) -> tuple[str, datetime]:
    _reject_forbidden_markers(record, label)
    _require_exact(record, "authority_status", "authoritative", label)
    record_ref = _required_text(
        record.get("controller_record_ref"), f"{label}.controller_record_ref"
    )
    recorded_at = _required_timestamp_alias(record, ("recorded_at",), f"{label}.recorded_at")
    if recorded_at > trusted_now + timedelta(seconds=max_future_skew_seconds):
        raise PersonaTargetError(f"{label}.recorded_at is in the future")
    return record_ref, recorded_at


def _validate_persona_authority(
    record: Mapping[str, Any],
    *,
    persona_id: str,
    tenant_id: str,
    trusted_now: datetime,
    max_future_skew_seconds: float,
) -> tuple[str, int, str, datetime]:
    record_ref, recorded_at = _validate_authoritative_record(
        record,
        label="persona pre-readback",
        trusted_now=trusted_now,
        max_future_skew_seconds=max_future_skew_seconds,
    )
    _require_exact(record, "persona_id", persona_id, "persona pre-readback")
    _require_exact(record, "tenant_id", tenant_id, "persona pre-readback")
    status = _required_alias_text(
        record,
        ("target_status", "status", "state"),
        "persona pre-readback status",
    )
    if status not in {"active", "committed"}:
        raise PersonaTargetError("persona pre-readback must be active or committed")
    generation = _strict_int(
        record.get("generation"), "persona pre-readback generation", minimum=0
    )
    return status, generation, record_ref, recorded_at


def _validate_persona_pre_readback(
    record: Mapping[str, Any],
    *,
    persona_id: str,
    tenant_id: str,
    session_id: str,
    generation: int,
    candidate_digest: str,
    control_digest: str,
    proof_digest: str,
) -> int:
    _reject_forbidden_markers(record, "persona pre-readback")
    _require_exact(record, "persona_id", persona_id, "persona pre-readback")
    _require_exact(record, "tenant_id", tenant_id, "persona pre-readback")
    status = _required_alias_text(
        record,
        ("target_status", "status", "state"),
        "persona pre-readback status",
    )
    if status not in {"active", "committed"}:
        raise PersonaTargetError("persona pre-readback must be active or committed")
    observed_generation = _strict_int(
        record.get("generation"), "persona pre-readback generation", minimum=0
    )
    if observed_generation == generation - 1:
        return observed_generation
    if observed_generation != generation:
        raise PersonaTargetError(
            "persona pre-readback generation is neither the expected predecessor "
            "nor an exact idempotent replay"
        )
    if status != "committed":
        raise PersonaTargetError("same-generation persona pre-readback is not committed")
    expected = {
        "persona_id": persona_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "candidate_digest": candidate_digest,
        "control_digest": control_digest,
        "proof_digest": proof_digest,
        "generation": generation,
    }
    _require_binding(record, expected, "same-generation persona pre-readback")
    return observed_generation


def _validated_proof_digest(
    proof: Any,
    *,
    candidate_digest: str,
    control_digest: str,
    persona_id: str,
    tenant_id: str,
    generation: int,
    expected_precondition_digest: str,
    approval_decision_ref: str,
) -> tuple[str, str]:
    if not isinstance(proof, Mapping):
        raise PersonaTargetError("evaluation proof must be an authoritative JSON object")
    _require_exact(proof, "tenant_id", tenant_id, "evaluation proof")

    authority = proof.get("authority")
    policy = authority.get("policy") if isinstance(authority, Mapping) else None
    if not isinstance(policy, Mapping):
        raise PersonaTargetError("evaluation proof policy authority is missing")
    _require_exact(
        policy,
        "approval_decision_ref",
        approval_decision_ref,
        "evaluation proof policy authority",
    )

    precondition = proof.get("target_precondition")
    if not isinstance(precondition, Mapping):
        raise PersonaTargetError("evaluation proof target_precondition is missing")
    _require_exact(
        precondition, "persona_id", persona_id, "evaluation proof target_precondition"
    )
    _require_exact(
        precondition, "tenant_id", tenant_id, "evaluation proof target_precondition"
    )
    if (
        _strict_int(
            precondition.get("expected_previous_generation"),
            "evaluation proof expected_previous_generation",
            minimum=0,
        )
        != generation - 1
    ):
        raise PersonaTargetError("evaluation proof expected_previous_generation mismatch")
    if (
        _strict_int(
            precondition.get("target_generation"),
            "evaluation proof target_generation",
            minimum=1,
        )
        != generation
    ):
        raise PersonaTargetError("evaluation proof target_generation mismatch")
    _require_exact(
        precondition,
        "precondition_digest",
        expected_precondition_digest,
        "evaluation proof target_precondition",
    )
    precondition_record_ref = _required_text(
        precondition.get("controller_record_ref"),
        "evaluation proof target_precondition.controller_record_ref",
    )

    _require_exact(
        proof, "candidate_digest", candidate_digest, "evaluation proof candidate binding"
    )
    _require_exact(
        proof, "controls_digest", control_digest, "evaluation proof control binding"
    )
    candidate_binding = proof.get("candidate_binding")
    if canonical_digest(candidate_binding) != candidate_digest:
        raise PersonaTargetError("evaluation proof candidate_binding digest mismatch")
    controls = proof.get("controls")
    if canonical_digest(controls) != control_digest:
        raise PersonaTargetError("evaluation proof controls digest mismatch")

    embedded_proof_digest = proof.get("proof_digest")
    if embedded_proof_digest is None:
        raise PersonaTargetError("evaluation proof proof_digest is required")
    if not isinstance(embedded_proof_digest, str) or not _DIGEST_PATTERN.fullmatch(
        embedded_proof_digest
    ):
        raise PersonaTargetError("evaluation proof proof_digest is not canonical SHA-256")
    unsigned = dict(proof)
    unsigned.pop("proof_digest", None)
    # Runtime evidence is appended only after the signed evaluation proof is
    # durably recorded, so it cannot be part of the proof's own digest.
    unsigned.pop("runtime_evidence", None)
    if canonical_digest(unsigned) != embedded_proof_digest:
        raise PersonaTargetError("evaluation proof proof_digest mismatch")
    return embedded_proof_digest, precondition_record_ref


def _validate_approval(
    decision: Mapping[str, Any],
    *,
    expected_approval_decision_ref: str,
    persona_id: str,
    tenant_id: str,
    session_id: str,
    candidate_digest: str,
    proof_digest: str,
    trusted_now: datetime,
) -> str:
    _reject_forbidden_markers(decision, "approval readback")
    lifecycle_tokens = [
        str(decision.get(key) or "").strip().lower()
        for key in ("decision_state", "status", "state")
        if str(decision.get(key) or "").strip()
    ]
    outcome_tokens = [
        str(decision.get(key) or "").strip().lower()
        for key in ("decision", "outcome")
        if str(decision.get(key) or "").strip()
    ]
    if not lifecycle_tokens:
        raise PersonaTargetError("approval readback is missing a terminal decision state")
    if any(token not in {"decided", "approved"} for token in lifecycle_tokens):
        raise PersonaTargetError("approval readback is not an approved terminal decision")
    if outcome_tokens and any(token != "approved" for token in outcome_tokens):
        raise PersonaTargetError("approval readback outcome is not approved")
    if "approved" not in lifecycle_tokens and "approved" not in outcome_tokens:
        raise PersonaTargetError("approval readback does not prove approval")

    _require_exact(decision, "persona_id", persona_id, "approval readback")
    _require_exact(decision, "tenant_id", tenant_id, "approval readback")
    _require_exact(decision, "session_id", session_id, "approval readback")
    _require_exact(
        decision, "candidate_digest", candidate_digest, "approval readback"
    )
    _require_exact(decision, "proof_digest", proof_digest, "approval readback")
    recorded_ref = decision.get("approval_decision_ref")
    if recorded_ref is not None:
        _require_exact(
            decision,
            "approval_decision_ref",
            expected_approval_decision_ref,
            "approval readback",
        )
    else:
        approval_identity = _required_alias_text(
            decision, ("decision_id", "approval_id"), "approval decision id"
        )
        if approval_identity != expected_approval_decision_ref:
            raise PersonaTargetError("approval readback decision identity mismatch")
    decision_id_values = [
        decision.get(key) for key in ("decision_id", "approval_id") if decision.get(key)
    ]
    approval_id = (
        _required_alias_text(decision, ("decision_id", "approval_id"), "approval decision id")
        if decision_id_values
        else expected_approval_decision_ref
    )

    approved_at = _required_timestamp_alias(
        decision,
        ("approved_at", "decided_at", "decision_at"),
        "approval decision time",
    )
    expires_at = _required_timestamp_alias(
        decision, ("expires_at",), "approval expiry"
    )
    if approved_at > trusted_now:
        raise PersonaTargetError("approval decision time is in the future")
    if expires_at <= trusted_now:
        raise PersonaTargetError("approval decision is expired")
    if approved_at >= expires_at:
        raise PersonaTargetError("approval decision time must be before expiry")
    return approval_id


def _validate_committed_target(
    record: Mapping[str, Any], expected: Mapping[str, Any], label: str
) -> None:
    _reject_forbidden_markers(record, label)
    status = _required_alias_text(
        record, ("target_status", "status", "state"), f"{label} status"
    )
    if status != "committed":
        raise PersonaTargetError(f"{label} is not terminal committed")
    _require_binding(record, expected, label)


def _require_binding(
    record: Mapping[str, Any], expected: Mapping[str, Any], label: str
) -> None:
    for key, value in expected.items():
        observed = record.get(key)
        if type(observed) is not type(value) or observed != value:  # noqa: E721
            raise PersonaTargetError(f"{label} {key} mismatch")
        if key.endswith("_digest") and not _DIGEST_PATTERN.fullmatch(str(observed)):
            raise PersonaTargetError(f"{label} {key} is not a canonical SHA-256 digest")


def _reject_forbidden_markers(record: Mapping[str, Any], label: str) -> None:
    seen: set[int] = set()

    def visit(value: Any, field_name: str | None = None) -> None:
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in seen:
                raise PersonaTargetError(f"{label} contains a recursive record")
            seen.add(identity)
            for key, nested in value.items():
                normalized_key = str(key).strip().lower()
                visit(nested, normalized_key)
            seen.remove(identity)
            return
        if isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen:
                raise PersonaTargetError(f"{label} contains a recursive record")
            seen.add(identity)
            for nested in value:
                visit(nested, field_name)
            seen.remove(identity)
            return
        is_marker_field = bool(
            field_name
            and (
                field_name in _MARKER_FIELDS
                or field_name.endswith(_MARKER_FIELD_SUFFIXES)
            )
        )
        if not is_marker_field or not isinstance(value, str):
            return
        tokens = {
            token
            for token in re.split(r"[^a-z0-9]+", value.strip().lower())
            if token
        }
        forbidden = tokens.intersection(_FORBIDDEN_MARKERS)
        if forbidden:
            raise PersonaTargetError(f"{label} contains non-authoritative marker data")

    visit(record)


def _required_alias_text(
    record: Mapping[str, Any], keys: tuple[str, ...], label: str
) -> str:
    populated: list[str] = []
    for key in keys:
        value = record.get(key)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise PersonaTargetError(f"{label} must be text")
        normalized = value.strip()
        if normalized:
            populated.append(normalized)
    if not populated:
        raise PersonaTargetError(f"{label} is required")
    if len(set(populated)) != 1:
        raise PersonaTargetError(f"{label} aliases disagree")
    return populated[0]


def _required_timestamp_alias(
    record: Mapping[str, Any], keys: tuple[str, ...], label: str
) -> datetime:
    text = _required_alias_text(record, keys, label)
    return _parse_timestamp(text, label)


def _parse_timestamp(value: str, label: str) -> datetime:
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise PersonaTargetError(f"{label} must be an RFC3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PersonaTargetError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _trusted_utc(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise PersonaTargetError("trusted_now must be a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _isoformat(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _authorization_headers(value: Any) -> dict[str, str]:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(character.isspace() for character in value)
        or "\r" in value
        or "\n" in value
    ):
        raise PersonaTargetError("authorization_token must be a non-empty bearer token")
    return {"Authorization": f"Bearer {value}"}


def _positive_finite_number(value: Any, label: str) -> float:
    normalized = _nonnegative_finite_number(value, label)
    if normalized <= 0:
        raise PersonaTargetError(f"{label} must be a positive finite number")
    return normalized


def _nonnegative_finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PersonaTargetError(f"{label} must be a non-negative finite number")
    normalized = float(value)
    if normalized < 0 or not math.isfinite(normalized):
        raise PersonaTargetError(f"{label} must be a non-negative finite number")
    return normalized


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PersonaTargetError(f"{label} is required")
    return value.strip()


def _strict_int(value: Any, label: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PersonaTargetError(f"{label} must be an integer >= {minimum}")
    return value


def _required_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _DIGEST_PATTERN.fullmatch(value):
        raise PersonaTargetError(f"{label} must be a canonical SHA-256 digest")
    return value


def _require_exact(
    record: Mapping[str, Any], key: str, expected: str, label: str
) -> None:
    observed = record.get(key)
    if not isinstance(observed, str) or observed != expected:
        raise PersonaTargetError(f"{label} {key} mismatch")


__all__ = [
    "PersonaTargetError",
    "PersonaTargetResponse",
    "PersonaTargetTransport",
    "UrllibPersonaTargetTransport",
    "canonical_digest",
    "commit_persona_target",
    "read_persona_target_precondition",
]

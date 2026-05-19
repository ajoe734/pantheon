"""Broker credential vault readiness schema and validator.

Implements the 2026-05-19 broker live activation supplement Part B5. The
validator checks secret-reference, stage-isolation, rotation, and VM-2 injection
evidence only; it never reads a vault, starts a broker session, or enables live
execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Sequence


SCHEMA_VERSION = "BrokerCredentialReadiness.v1"
READINESS_SOURCE = "2026-05-19 broker live activation supplement Part B5"
MAX_ROTATION_INTERVAL_DAYS = 90

ALLOWED_STAGES = ("paper", "canary", "live")
READY_STATUSES = {"active", "ready", "verified"}
ALLOWED_INJECTION_TARGETS = (
    "execution_plane_vm2",
    "runtime_manager_vm2",
    "vm2_execution_env",
    "pantheon_exec_vm2",
    "pantheon_exec_vm2_20260424",
)
FORBIDDEN_SECRET_LOCATION_TOKENS = (
    "artifact",
    "bff",
    "browser",
    "control_plane",
    "frontend",
    "launch_manifest",
    "lovable",
    "manifest",
    "openclaw",
    "telemetry",
    "vm1",
)
SECRET_REF_PREFIXES = (
    "secret://",
    "gcp-secret://",
    "gcp-secret-manager://",
    "sm://",
)
REQUIRED_LIVE_PERMISSION_SCOPE = (
    "account_read",
    "market_data_read",
    "order_cancel",
    "order_submit",
)
FORBIDDEN_PERMISSION_SCOPES = {
    "*",
    "admin",
    "all",
    "full_access",
    "root",
    "superuser",
}
REQUIRED_ISOLATION_BY_STAGE = {
    "paper": ("canary", "live"),
    "canary": ("paper", "live"),
    "live": ("paper", "canary"),
}
REQUIRED_SCHEMA_FIELDS = (
    "schema_version",
    "source",
    "broker",
    "stage",
    "account_ref",
    "venue_ref",
    "vault_secret_refs",
    "injection_target",
    "permission_scope",
    "not_shared_with_stages",
    "rotation_interval_days",
    "last_rotated_at",
    "next_rotation_due_at",
    "rotation_policy_ref",
    "revocation_procedure_ref",
    "operator_verification_ref",
    "entitlement_evidence_ref",
    "sandbox_smoke_ref",
    "status",
)

_RAW_SECRET_KEYS = {
    "api_key",
    "api_secret",
    "broker_api_key",
    "broker_api_secret",
    "broker_secret",
    "password",
    "raw_broker_secret",
    "raw_secret",
    "raw_secret_value",
    "secret_key",
    "secret_value",
    "shioaji_api_key",
    "shioaji_secret_key",
    "token",
}
_RAW_SECRET_FLAGS = {"raw_broker_secret_present", "raw_secret_present"}
_SECRET_REF_KEYS = {
    "secret_ref",
    "secret_refs",
    "secret_name_ref",
    "vault_secret_ref",
    "vault_secret_refs",
}


class BrokerCredentialReadinessError(ValueError):
    """Raised when credential readiness fails closed."""


@dataclass(frozen=True)
class CredentialReadinessIssue:
    code: str
    path: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "path": self.path, "message": self.message}


@dataclass(frozen=True)
class CredentialReadinessResult:
    passed: bool
    ready_for_stage_activation: bool
    errors: tuple[CredentialReadinessIssue, ...] = ()
    blocking_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "ready_for_stage_activation": self.ready_for_stage_activation,
            "blocking_reasons": list(self.blocking_reasons),
            "errors": [issue.to_dict() for issue in self.errors],
        }


@dataclass(frozen=True)
class BrokerCredentialReadiness:
    schema_version: str
    source: str
    broker: str
    stage: str
    account_ref: str
    venue_ref: str
    vault_secret_refs: tuple[str, ...]
    injection_target: str
    permission_scope: tuple[str, ...]
    not_shared_with_stages: tuple[str, ...]
    rotation_interval_days: int
    last_rotated_at: str
    next_rotation_due_at: str
    rotation_policy_ref: str
    revocation_procedure_ref: str
    operator_verification_ref: str
    entitlement_evidence_ref: str
    sandbox_smoke_ref: str
    status: str
    evidence_refs: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "BrokerCredentialReadiness":
        payload = _mapping(value)
        return cls(
            schema_version=_text(payload.get("schema_version") or payload.get("version")),
            source=_text(payload.get("source") or READINESS_SOURCE),
            broker=_normalized_token(payload.get("broker") or payload.get("provider")),
            stage=_normalized_token(payload.get("stage") or payload.get("deployment_stage")),
            account_ref=_text(payload.get("account_ref") or payload.get("broker_account_ref")),
            venue_ref=_text(payload.get("venue_ref") or payload.get("routing_ref")),
            vault_secret_refs=tuple(_strings(payload.get("vault_secret_refs"))),
            injection_target=_normalized_token(payload.get("injection_target")),
            permission_scope=tuple(
                _normalized_token(item)
                for item in _strings(payload.get("permission_scope"))
            ),
            not_shared_with_stages=tuple(
                _normalized_token(item) for item in _strings(payload.get("not_shared_with_stages"))
            ),
            rotation_interval_days=_int_or_zero(
                payload.get("rotation_interval_days") or payload.get("max_rotation_interval_days")
            ),
            last_rotated_at=_text(payload.get("last_rotated_at")),
            next_rotation_due_at=_text(payload.get("next_rotation_due_at")),
            rotation_policy_ref=_text(payload.get("rotation_policy_ref")),
            revocation_procedure_ref=_text(payload.get("revocation_procedure_ref")),
            operator_verification_ref=_text(payload.get("operator_verification_ref")),
            entitlement_evidence_ref=_text(payload.get("entitlement_evidence_ref")),
            sandbox_smoke_ref=_text(payload.get("sandbox_smoke_ref")),
            status=_normalized_token(payload.get("status") or payload.get("readiness_status") or "missing"),
            evidence_refs=tuple(_strings(payload.get("evidence_refs"))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "broker": self.broker,
            "stage": self.stage,
            "account_ref": self.account_ref,
            "venue_ref": self.venue_ref,
            "vault_secret_refs": list(self.vault_secret_refs),
            "injection_target": self.injection_target,
            "permission_scope": list(self.permission_scope),
            "not_shared_with_stages": list(self.not_shared_with_stages),
            "rotation_interval_days": self.rotation_interval_days,
            "last_rotated_at": self.last_rotated_at,
            "next_rotation_due_at": self.next_rotation_due_at,
            "rotation_policy_ref": self.rotation_policy_ref,
            "revocation_procedure_ref": self.revocation_procedure_ref,
            "operator_verification_ref": self.operator_verification_ref,
            "entitlement_evidence_ref": self.entitlement_evidence_ref,
            "sandbox_smoke_ref": self.sandbox_smoke_ref,
            "status": self.status,
            "evidence_refs": list(self.evidence_refs),
        }


def validate_credential_readiness(
    readiness: BrokerCredentialReadiness | Mapping[str, Any],
    *,
    expected_stage: str | None = None,
) -> CredentialReadinessResult:
    """Validate credential vault readiness without dereferencing secrets."""

    payload = (
        readiness.to_dict()
        if isinstance(readiness, BrokerCredentialReadiness)
        else _mapping(readiness)
    )
    if not payload:
        return _result(
            (
                _issue(
                    "invalid_credential_readiness",
                    "$",
                    "credential readiness must be an object",
                ),
            )
        )

    errors: list[CredentialReadinessIssue] = []
    errors.extend(_raw_secret_issues(payload))
    model = (
        readiness
        if isinstance(readiness, BrokerCredentialReadiness)
        else BrokerCredentialReadiness.from_mapping(payload)
    )

    errors.extend(_missing_field_issues(model))
    if model.schema_version != SCHEMA_VERSION:
        errors.append(
            _issue(
                "invalid_schema_version",
                "schema_version",
                f"schema_version must be {SCHEMA_VERSION!r}",
            )
        )

    if model.stage not in ALLOWED_STAGES:
        errors.append(
            _issue(
                "invalid_stage",
                "stage",
                f"stage must be one of: {', '.join(ALLOWED_STAGES)}",
            )
        )
    if expected_stage and model.stage != _normalized_token(expected_stage):
        errors.append(
            _issue(
                "stage_mismatch",
                "stage",
                f"credential readiness is for {model.stage!r}, expected {expected_stage!r}",
            )
        )

    errors.extend(_vault_ref_issues(model))
    errors.extend(_injection_target_issues(model))
    errors.extend(_permission_scope_issues(model))
    errors.extend(_stage_isolation_issues(model))
    errors.extend(_rotation_issues(model))

    if model.status not in READY_STATUSES:
        errors.append(
            _issue(
                "credential_readiness_not_verified",
                "status",
                f"status must be one of: {', '.join(sorted(READY_STATUSES))}",
            )
        )

    for field in (
        "account_ref",
        "venue_ref",
        "rotation_policy_ref",
        "revocation_procedure_ref",
        "operator_verification_ref",
        "entitlement_evidence_ref",
        "sandbox_smoke_ref",
    ):
        value = getattr(model, field)
        if _is_secret_ref(value):
            errors.append(
                _issue(
                    "secret_ref_in_public_evidence_field",
                    field,
                    f"{field} must not point to secret material",
                )
            )

    return _result(errors)


def validate_credential_readiness_or_raise(
    readiness: BrokerCredentialReadiness | Mapping[str, Any],
    *,
    expected_stage: str | None = None,
) -> CredentialReadinessResult:
    result = validate_credential_readiness(readiness, expected_stage=expected_stage)
    if not result.passed:
        raise BrokerCredentialReadinessError("; ".join(result.blocking_reasons))
    return result


def _missing_field_issues(model: BrokerCredentialReadiness) -> list[CredentialReadinessIssue]:
    errors: list[CredentialReadinessIssue] = []
    for field in REQUIRED_SCHEMA_FIELDS:
        value = getattr(model, field)
        if isinstance(value, tuple):
            present = bool(value)
        elif isinstance(value, int):
            present = value > 0
        else:
            present = _present(value)
        if not present:
            errors.append(_issue("missing_required_field", field, f"{field} is required"))
    return errors


def _vault_ref_issues(model: BrokerCredentialReadiness) -> list[CredentialReadinessIssue]:
    errors: list[CredentialReadinessIssue] = []
    seen: set[str] = set()
    for index, ref in enumerate(model.vault_secret_refs):
        path = f"vault_secret_refs[{index}]"
        if not _is_secret_ref(ref):
            errors.append(
                _issue(
                    "invalid_vault_secret_ref",
                    path,
                    "vault_secret_refs must use secret reference URIs, never raw values",
                )
            )
        if ref in seen:
            errors.append(
                _issue(
                    "duplicate_vault_secret_ref",
                    path,
                    "vault secret refs must be unique",
                )
            )
        seen.add(ref)
    return errors


def _injection_target_issues(model: BrokerCredentialReadiness) -> list[CredentialReadinessIssue]:
    target = _normalized_token(model.injection_target)
    errors: list[CredentialReadinessIssue] = []
    if target not in ALLOWED_INJECTION_TARGETS:
        errors.append(
            _issue(
                "invalid_injection_target",
                "injection_target",
                "credential injection target must be the VM-2 execution environment",
            )
        )
    if any(token in target for token in FORBIDDEN_SECRET_LOCATION_TOKENS):
        errors.append(
            _issue(
                "forbidden_credential_location",
                "injection_target",
                    "broker credentials must not be injected into VM-1, BFF, "
                    "frontend, telemetry, artifacts, manifests, or OpenClaw",
            )
        )
    return errors


def _permission_scope_issues(model: BrokerCredentialReadiness) -> list[CredentialReadinessIssue]:
    scope = set(model.permission_scope)
    errors: list[CredentialReadinessIssue] = []
    broad = sorted(scope & FORBIDDEN_PERMISSION_SCOPES)
    if broad:
        errors.append(
            _issue(
                "overbroad_permission_scope",
                "permission_scope",
                f"permission_scope must not include broad grants: {', '.join(broad)}",
            )
        )
    if model.stage in {"canary", "live"}:
        missing = tuple(item for item in REQUIRED_LIVE_PERMISSION_SCOPE if item not in scope)
        if missing:
            errors.append(
                _issue(
                    "missing_required_permission_scope",
                    "permission_scope",
                    f"{model.stage} credentials require: {', '.join(missing)}",
                )
            )
    return errors


def _stage_isolation_issues(model: BrokerCredentialReadiness) -> list[CredentialReadinessIssue]:
    required = REQUIRED_ISOLATION_BY_STAGE.get(model.stage, ())
    declared = set(model.not_shared_with_stages)
    missing = tuple(stage for stage in required if stage not in declared)
    if not missing:
        return []
    return [
        _issue(
            "missing_stage_isolation",
            "not_shared_with_stages",
            f"{model.stage} credentials must be explicitly isolated from: {', '.join(missing)}",
        )
    ]


def _rotation_issues(model: BrokerCredentialReadiness) -> list[CredentialReadinessIssue]:
    errors: list[CredentialReadinessIssue] = []
    if model.rotation_interval_days > MAX_ROTATION_INTERVAL_DAYS:
        errors.append(
            _issue(
                "rotation_cadence_too_long",
                "rotation_interval_days",
                f"rotation_interval_days must be <= {MAX_ROTATION_INTERVAL_DAYS}",
            )
        )

    last_rotated = _parse_timestamp(model.last_rotated_at, "last_rotated_at", errors)
    next_due = _parse_timestamp(model.next_rotation_due_at, "next_rotation_due_at", errors)
    if last_rotated is None or next_due is None or model.rotation_interval_days <= 0:
        return errors

    delta_days = (next_due - last_rotated).total_seconds() / 86400
    if delta_days <= 0:
        errors.append(
            _issue(
                "invalid_rotation_window",
                "next_rotation_due_at",
                "next_rotation_due_at must be after last_rotated_at",
            )
        )
    elif delta_days > model.rotation_interval_days:
        errors.append(
            _issue(
                "rotation_window_exceeds_policy",
                "next_rotation_due_at",
                "next_rotation_due_at must be within rotation_interval_days",
            )
        )
    return errors


def _raw_secret_issues(value: Any, path: str = "$") -> list[CredentialReadinessIssue]:
    errors: list[CredentialReadinessIssue] = []
    if isinstance(value, Mapping):
        for raw_key, raw_value in value.items():
            key = _normalized_key(raw_key)
            child_path = f"{path}.{raw_key}" if path != "$" else str(raw_key)
            if key in _RAW_SECRET_FLAGS and raw_value is True:
                errors.append(
                    _issue(
                        "raw_secret_material_present",
                        child_path,
                        "raw broker secret material must not be present",
                    )
                )
            elif key in _RAW_SECRET_KEYS and _present(raw_value):
                errors.append(
                    _issue(
                        "raw_secret_material_present",
                        child_path,
                        "raw broker secret material must not be present",
                    )
                )
            elif key in _SECRET_REF_KEYS:
                errors.extend(_secret_ref_field_issues(raw_value, child_path))
            else:
                errors.extend(_raw_secret_issues(raw_value, child_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            errors.extend(_raw_secret_issues(item, f"{path}[{index}]"))
    return errors


def _secret_ref_field_issues(value: Any, path: str) -> list[CredentialReadinessIssue]:
    refs = _strings(value)
    if not refs and _present(value):
        return [
            _issue(
                "invalid_vault_secret_ref",
                path,
                "secret reference fields must contain secret reference URIs",
            )
        ]
    return [
        _issue(
            "invalid_vault_secret_ref",
            f"{path}[{index}]",
            "secret reference fields must contain secret reference URIs",
        )
        for index, ref in enumerate(refs)
        if not _is_secret_ref(ref)
    ]


def _parse_timestamp(
    value: str,
    path: str,
    errors: list[CredentialReadinessIssue],
) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(_issue("invalid_timestamp", path, f"{path} must be an ISO-8601 timestamp"))
        return None
    if parsed.tzinfo is None:
        errors.append(_issue("invalid_timestamp", path, f"{path} must include a timezone"))
        return None
    return parsed


def _result(errors: Sequence[CredentialReadinessIssue]) -> CredentialReadinessResult:
    issues = tuple(errors)
    return CredentialReadinessResult(
        passed=not issues,
        ready_for_stage_activation=not issues,
        errors=issues,
        blocking_reasons=tuple(issue.message for issue in issues),
    )


def _issue(code: str, path: str, message: str) -> CredentialReadinessIssue:
    return CredentialReadinessIssue(code=code, path=path, message=message)


def _mapping(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    return {}


def _strings(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value.strip(),) if value.strip() else ()
    if isinstance(value, Sequence):
        return tuple(str(item).strip() for item in value if str(item).strip())
    return ()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_token(value: Any) -> str:
    return _text(value).lower().replace("-", "_").replace(" ", "_")


def _normalized_key(value: Any) -> str:
    normalized = _normalized_token(value)
    for char in (".", "/"):
        normalized = normalized.replace(char, "_")
    return normalized


def _int_or_zero(value: Any) -> int:
    if isinstance(value, bool) or value is None:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (Sequence, Mapping)):
        return bool(value)
    return True


def _is_secret_ref(value: Any) -> bool:
    text = _text(value).lower()
    return any(text.startswith(prefix) for prefix in SECRET_REF_PREFIXES)


__all__ = [
    "ALLOWED_INJECTION_TARGETS",
    "ALLOWED_STAGES",
    "BrokerCredentialReadiness",
    "BrokerCredentialReadinessError",
    "CredentialReadinessIssue",
    "CredentialReadinessResult",
    "MAX_ROTATION_INTERVAL_DAYS",
    "READINESS_SOURCE",
    "REQUIRED_SCHEMA_FIELDS",
    "SCHEMA_VERSION",
    "validate_credential_readiness",
    "validate_credential_readiness_or_raise",
]

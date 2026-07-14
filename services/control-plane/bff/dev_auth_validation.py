"""Canonical validation for Pantheon's governed dev authentication boundary.

This module intentionally has no BFF or third-party dependencies.  The BFF
imports it directly, while deployment scripts and CI invoke its CLI before any
cloud authentication or remote mutation.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


class DevAuthValidationError(ValueError):
    """Raised when a governed dev-auth value fails closed."""


DEV_LOGIN_PROFILES_ENV = "PANTHEON_BFF_DEV_LOGIN_CLIENT_PROFILES_JSON"
DEV_LOGIN_ALLOWED_ENVIRONMENTS = frozenset({"dev", "local", "test", "testing"})
DEV_LOGIN_ALLOWED_ROLES = frozenset(
    {"viewer", "operator", "approver", "admin", "reviewer", "risk_owner"}
)

_PROFILE_REQUIRED_FIELDS = frozenset(
    {
        "secret",
        "subject",
        "roles",
        "tenant_id",
        "allowed_tenants",
        "capabilities",
        "mfa_verified",
    }
)
_CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9._-]{3,128}$")
_SUBJECT_RE = re.compile(r"^[A-Za-z0-9._:@/+-]{3,128}$")
_TENANT_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_CAPABILITY_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_JWT_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")
_WORKSHOP_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def _reject_duplicate_keys(pairs: Iterable[tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DevAuthValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json_object(raw: str, *, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except DevAuthValidationError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise DevAuthValidationError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise DevAuthValidationError(f"{label} must be a JSON object")
    return value


def _utf8_size(value: str) -> int:
    return len(value.encode("utf-8"))


def _has_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def _clean_string(
    value: Any,
    *,
    label: str,
    pattern: re.Pattern[str],
) -> str:
    if not isinstance(value, str):
        raise DevAuthValidationError(f"{label} must be a string")
    if value != value.strip() or _has_control(value) or not pattern.fullmatch(value):
        raise DevAuthValidationError(f"{label} is invalid")
    return value


def validate_secret(value: Any, *, label: str) -> str:
    """Validate an exact raw secret without trimming or normalization."""

    if not isinstance(value, str):
        raise DevAuthValidationError(f"{label} must be a string")
    size = _utf8_size(value)
    if size < 32 or size > 4096:
        raise DevAuthValidationError(f"{label} must be between 32 and 4096 UTF-8 bytes")
    if value != value.strip() or any(char.isspace() for char in value) or _has_control(value):
        raise DevAuthValidationError(f"{label} contains whitespace or control characters")
    return value


def _unique_clean_list(
    value: Any,
    *,
    label: str,
    pattern: re.Pattern[str],
    minimum: int = 0,
    maximum: int = 64,
) -> List[str]:
    if not isinstance(value, list):
        raise DevAuthValidationError(f"{label} must be a list")
    if not minimum <= len(value) <= maximum:
        raise DevAuthValidationError(f"{label} has an invalid item count")
    result: List[str] = []
    seen = set()
    for index, raw_item in enumerate(value):
        item = _clean_string(raw_item, label=f"{label}[{index}]", pattern=pattern)
        if item in seen:
            raise DevAuthValidationError(f"{label} must not contain duplicates")
        seen.add(item)
        result.append(item)
    return result


def _constant_time_digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def validate_dev_login_configuration(
    raw_profiles: str,
    jwt_secret: str,
    *,
    require_ci_profile: bool = False,
    ci_client_id: Optional[str] = None,
    ci_client_secret: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """Validate the complete profile map and JWT signing secret.

    Every profile is validated before any credential lookup.  This prevents a
    valid selected actor from hiding an invalid or ambiguous extra profile.
    """

    validate_secret(jwt_secret, label="dev-login JWT secret")
    if not isinstance(raw_profiles, str) or not raw_profiles:
        raise DevAuthValidationError("dev-login client profiles are required")
    if raw_profiles != raw_profiles.strip():
        raise DevAuthValidationError("dev-login client profiles contain outer whitespace")
    if _utf8_size(raw_profiles) > 65536:
        raise DevAuthValidationError("dev-login client profiles exceed 65536 UTF-8 bytes")

    decoded = load_json_object(raw_profiles, label="dev-login client profiles")
    if not 1 <= len(decoded) <= 32:
        raise DevAuthValidationError("dev-login client profiles must contain 1 to 32 clients")

    profiles: Dict[str, Dict[str, Any]] = {}
    subject_keys: set[str] = set()
    # A client credential must never double as the HS256 signing key.  Anyone
    # holding a low-privilege profile secret can mint arbitrary JWT claims when
    # those values are equal, bypassing the governed role/MFA/capability map.
    secret_digests: List[bytes] = [_constant_time_digest(jwt_secret)]
    for raw_client_id, raw_profile in decoded.items():
        client_id = _clean_string(
            raw_client_id,
            label="dev-login client id",
            pattern=_CLIENT_ID_RE,
        )
        if not isinstance(raw_profile, dict):
            raise DevAuthValidationError(f"profile {client_id} must be an object")
        fields = frozenset(raw_profile)
        if fields != _PROFILE_REQUIRED_FIELDS:
            missing = sorted(_PROFILE_REQUIRED_FIELDS - fields)
            extra = sorted(fields - _PROFILE_REQUIRED_FIELDS)
            raise DevAuthValidationError(
                f"profile {client_id} fields are invalid (missing={missing}, extra={extra})"
            )

        secret = validate_secret(raw_profile["secret"], label=f"profile {client_id} secret")
        secret_digest = _constant_time_digest(secret)
        if any(hmac.compare_digest(secret_digest, other) for other in secret_digests):
            raise DevAuthValidationError(
                "dev-login signing and client profile secrets must be distinct"
            )
        secret_digests.append(secret_digest)

        subject = _clean_string(
            raw_profile["subject"],
            label=f"profile {client_id} subject",
            pattern=_SUBJECT_RE,
        )
        subject_key = subject.casefold()
        if subject_key in subject_keys:
            raise DevAuthValidationError("dev-login client profile subjects must be distinct")
        subject_keys.add(subject_key)

        roles = _unique_clean_list(
            raw_profile["roles"],
            label=f"profile {client_id} roles",
            pattern=re.compile(r"^[a-z][a-z0-9_]{1,63}$"),
            minimum=1,
            maximum=16,
        )
        if any(role not in DEV_LOGIN_ALLOWED_ROLES for role in roles):
            raise DevAuthValidationError(f"profile {client_id} contains an unsupported role")

        tenant_id = _clean_string(
            raw_profile["tenant_id"],
            label=f"profile {client_id} tenant_id",
            pattern=_TENANT_RE,
        )
        allowed_tenants = _unique_clean_list(
            raw_profile["allowed_tenants"],
            label=f"profile {client_id} allowed_tenants",
            pattern=_TENANT_RE,
            minimum=1,
            maximum=32,
        )
        if tenant_id not in allowed_tenants:
            raise DevAuthValidationError(f"profile {client_id} tenant_id is not allowed")

        capabilities = _unique_clean_list(
            raw_profile["capabilities"],
            label=f"profile {client_id} capabilities",
            pattern=_CAPABILITY_RE,
            maximum=64,
        )
        mfa_verified = raw_profile["mfa_verified"]
        if not isinstance(mfa_verified, bool):
            raise DevAuthValidationError(f"profile {client_id} mfa_verified must be boolean")

        profiles[client_id] = {
            "secret": secret,
            "subject": subject,
            "roles": roles,
            "tenant_id": tenant_id,
            "allowed_tenants": allowed_tenants,
            "capabilities": capabilities,
            "mfa_verified": mfa_verified,
        }

    if require_ci_profile:
        if ci_client_id is None or ci_client_secret is None:
            raise DevAuthValidationError("CI client id and secret are required")
        exact_ci_client_id = _clean_string(
            ci_client_id,
            label="CI client id",
            pattern=_CLIENT_ID_RE,
        )
        validate_secret(ci_client_secret, label="CI client secret")
        ci_profile = profiles.get(exact_ci_client_id)
        if ci_profile is None or not hmac.compare_digest(
            _constant_time_digest(ci_client_secret),
            _constant_time_digest(str(ci_profile["secret"])),
        ):
            raise DevAuthValidationError("CI credential does not match a governed profile")
        if (
            ci_profile["subject"] != "pantheon-dev-ci-agora"
            or ci_profile["roles"] != ["operator"]
            or ci_profile["tenant_id"] != "tenant-dev"
            or ci_profile["allowed_tenants"] != ["tenant-dev"]
            or ci_profile["capabilities"] != []
            or ci_profile["mfa_verified"] is not False
        ):
            raise DevAuthValidationError("CI profile does not match the least-role contract")

    return profiles


def dev_login_environment_allowed(environment: str, deployment_stage: str) -> bool:
    """Allow only explicit dev/local-test markers and reject unknown peers."""

    configured = []
    for raw in (environment, deployment_stage):
        value = str(raw or "")
        if value:
            configured.append(value)
    return bool(configured) and all(value in DEV_LOGIN_ALLOWED_ENVIRONMENTS for value in configured)


def validate_login_response(raw: str) -> str:
    response = load_json_object(raw, label="dev-login response")
    token_type = response.get("token_type")
    if (
        not isinstance(token_type, str)
        or token_type != "bearer"
        or token_type != token_type.strip()
        or _has_control(token_type)
    ):
        raise DevAuthValidationError("dev-login token_type must be the exact value bearer")
    token = response.get("access_token")
    if not isinstance(token, str) or token != token.strip() or _has_control(token):
        raise DevAuthValidationError("dev-login access_token contains whitespace or control characters")
    if not 32 <= len(token) <= 8192 or not _JWT_RE.fullmatch(token):
        raise DevAuthValidationError("dev-login access_token is not a compact JWT")
    return token


def validate_workshop_response(raw: str) -> str:
    response = load_json_object(raw, label="workshop response")
    data = response.get("data")
    if not isinstance(data, dict):
        raise DevAuthValidationError("workshop response data must be an object")
    workshop_id = data.get("workshop_id")
    if (
        not isinstance(workshop_id, str)
        or workshop_id != workshop_id.strip()
        or _has_control(workshop_id)
        or not _WORKSHOP_ID_RE.fullmatch(workshop_id)
    ):
        raise DevAuthValidationError("workshop_id is not a canonical UUID")
    return workshop_id


def _env_snapshot(raw: str, *, label: str) -> Dict[str, str]:
    try:
        entries = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except DevAuthValidationError:
        raise
    except (json.JSONDecodeError, TypeError) as exc:
        raise DevAuthValidationError(f"{label} is not valid JSON") from exc
    if not isinstance(entries, list) or any(not isinstance(entry, str) for entry in entries):
        raise DevAuthValidationError(f"{label} must be a Docker environment array")
    result: Dict[str, str] = {}
    for entry in entries:
        if "=" not in entry:
            raise DevAuthValidationError(f"{label} contains an invalid entry")
        name, value = entry.split("=", 1)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) or name in result:
            raise DevAuthValidationError(f"{label} contains an invalid or duplicate name")
        result[name] = value
    return result


def validate_rollback_environment(expected_raw: str, actual_raw: str, names_raw: str) -> None:
    expected = _env_snapshot(expected_raw, label="captured environment")
    actual = _env_snapshot(actual_raw, label="rollback environment")
    names = names_raw.splitlines()
    if not names or any(not re.fullmatch(r"[A-Z][A-Z0-9_]*", name) for name in names):
        raise DevAuthValidationError("rollback policy names are invalid")
    if len(names) != len(set(names)):
        raise DevAuthValidationError("rollback policy names contain duplicates")
    for name in names:
        expected_present = name in expected
        actual_present = name in actual
        if expected_present != actual_present:
            raise DevAuthValidationError(f"rollback changed presence of {name}")
        if expected_present and not hmac.compare_digest(
            _constant_time_digest(expected[name]), _constant_time_digest(actual[name])
        ):
            raise DevAuthValidationError(f"rollback changed value of {name}")


def _read_json_path(value: Mapping[str, Any], path: str) -> Any:
    current: Any = value
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def validate_rollback_http_proof(
    expected_identity_raw: str,
    actual_identity_raw: str,
    expected_mode_raw: str,
    actual_mode_raw: str,
) -> None:
    expected_identity = load_json_object(expected_identity_raw, label="captured /bff/me response")
    actual_identity = load_json_object(actual_identity_raw, label="rollback /bff/me response")
    expected_mode = load_json_object(expected_mode_raw, label="captured mode response")
    actual_mode = load_json_object(actual_mode_raw, label="rollback mode response")

    identity_paths = (
        "data.currentUser.id",
        "data.roles",
        "data.capabilities",
        "data.session.mfa_verified",
        "data.tenant.id",
        "data.tenant.allowed_ids",
    )
    if not _read_json_path(expected_identity, "data.currentUser.id"):
        raise DevAuthValidationError("captured identity has no actor id")
    for path in identity_paths:
        if _read_json_path(actual_identity, path) != _read_json_path(expected_identity, path):
            raise DevAuthValidationError(f"rollback /bff/me changed {path}")

    mode_paths = (
        "data.kernel_enabled",
        "data.control_mode.configured",
        "data.control_mode.active",
        "data.control_mode.state",
    )
    for path in mode_paths:
        if _read_json_path(actual_mode, path) != _read_json_path(expected_mode, path):
            raise DevAuthValidationError(f"rollback mode changed {path}")
    if (
        _read_json_path(actual_mode, "data.control_mode.active") is not False
        or _read_json_path(actual_mode, "data.control_mode.state") != "inactive"
    ):
        raise DevAuthValidationError("rollback did not restore inactive control mode")


def _read_protected(path: Path, *, label: str) -> str:
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
    except OSError as exc:
        raise DevAuthValidationError(f"cannot read {label}") from exc
    if mode & 0o077:
        raise DevAuthValidationError(f"{label} file permissions must be 0600 or stricter")
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DevAuthValidationError(f"cannot read {label}") from exc


def _write_protected(path: Path, value: str) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8", closefd=False) as handle:
            handle.write(value)
            handle.flush()
    finally:
        os.close(descriptor)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    profiles = subparsers.add_parser("profiles", help="validate the complete governed profile map")
    profiles.add_argument("--profiles-file", type=Path, required=True)
    profiles.add_argument("--jwt-secret-file", type=Path, required=True)
    profiles.add_argument("--require-ci-profile", action="store_true")
    profiles.add_argument("--ci-client-id")
    profiles.add_argument("--ci-client-secret-file", type=Path)

    login = subparsers.add_parser("login-response", help="validate and extract a compact JWT")
    login.add_argument("--input-file", type=Path, required=True)
    login.add_argument("--token-file", type=Path, required=True)

    workshop = subparsers.add_parser("workshop-response", help="validate and extract workshop_id")
    workshop.add_argument("--input-file", type=Path, required=True)
    workshop.add_argument("--workshop-id-file", type=Path, required=True)

    compare_env = subparsers.add_parser(
        "compare-env", help="prove every named container environment value is unchanged"
    )
    compare_env.add_argument("--expected-file", type=Path, required=True)
    compare_env.add_argument("--actual-file", type=Path, required=True)
    compare_env.add_argument("--names-file", type=Path, required=True)

    rollback_http = subparsers.add_parser(
        "rollback-http", help="prove the old credential and mode contract after rollback"
    )
    rollback_http.add_argument("--expected-identity-file", type=Path, required=True)
    rollback_http.add_argument("--actual-identity-file", type=Path, required=True)
    rollback_http.add_argument("--expected-mode-file", type=Path, required=True)
    rollback_http.add_argument("--actual-mode-file", type=Path, required=True)

    environment = subparsers.add_parser(
        "environment", help="validate the explicit dev/local-test environment allowlist"
    )
    environment.add_argument("--environment", required=True)
    environment.add_argument("--deployment-stage", required=True)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "profiles":
            profiles_raw = _read_protected(args.profiles_file, label="profiles")
            jwt_secret = _read_protected(args.jwt_secret_file, label="JWT secret")
            ci_secret = None
            if args.ci_client_secret_file is not None:
                ci_secret = _read_protected(args.ci_client_secret_file, label="CI client secret")
            validate_dev_login_configuration(
                profiles_raw,
                jwt_secret,
                require_ci_profile=args.require_ci_profile,
                ci_client_id=args.ci_client_id,
                ci_client_secret=ci_secret,
            )
        elif args.command == "login-response":
            raw = _read_protected(args.input_file, label="dev-login response")
            _write_protected(args.token_file, validate_login_response(raw))
        elif args.command == "workshop-response":
            raw = _read_protected(args.input_file, label="workshop response")
            _write_protected(args.workshop_id_file, validate_workshop_response(raw))
        elif args.command == "compare-env":
            validate_rollback_environment(
                _read_protected(args.expected_file, label="captured environment"),
                _read_protected(args.actual_file, label="rollback environment"),
                _read_protected(args.names_file, label="policy names"),
            )
        elif args.command == "rollback-http":
            validate_rollback_http_proof(
                _read_protected(args.expected_identity_file, label="captured identity"),
                _read_protected(args.actual_identity_file, label="rollback identity"),
                _read_protected(args.expected_mode_file, label="captured mode"),
                _read_protected(args.actual_mode_file, label="rollback mode"),
            )
        elif args.command == "environment":
            if not dev_login_environment_allowed(args.environment, args.deployment_stage):
                raise DevAuthValidationError("environment is not explicitly dev/local/test")
        else:  # pragma: no cover - argparse guarantees the command.
            raise DevAuthValidationError("unknown validation command")
    except DevAuthValidationError as exc:
        print(f"dev auth validation failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

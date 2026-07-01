"""Assistant redaction helpers.

The assistant redactor runs before provider invocation and before transcript
persistence.  It is intentionally conservative: known credential-bearing fields
and strings are replaced with stable markers while the surrounding diagnostic
shape is preserved.
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


JsonValue = Any

USER_MODES = frozenset({"user"})
KERNEL_MODES = frozenset({"kernel_observe", "kernel_debug", "kernel_repair"})


class RedactionError(RuntimeError):
    """Raised when redaction cannot produce a safe payload."""


@dataclass(frozen=True)
class RedactionSummary:
    """Telemetry describing a redaction pass."""

    enabled: bool
    redacted_fields: int = 0
    redacted_values: int = 0
    categories: dict[str, int] = field(default_factory=dict)
    failed: bool = False
    failure_stage: str | None = None
    failure_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "redacted_fields": self.redacted_fields,
            "redacted_values": self.redacted_values,
            "categories": dict(sorted(self.categories.items())),
            "failed": self.failed,
            "failure_stage": self.failure_stage,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class RedactionResult:
    """Redacted value plus summary metadata."""

    value: JsonValue
    summary: RedactionSummary


@dataclass
class _RedactionStats:
    fields: int = 0
    values: int = 0
    categories: dict[str, int] = field(default_factory=dict)

    def record(self, category: str, *, field: bool = False, count: int = 1) -> None:
        self.values += count
        if field:
            self.fields += 1
        self.categories[category] = self.categories.get(category, 0) + count


_MARKERS = {
    "account_number": "[REDACTED_ACCOUNT]",
    "api_key": "[REDACTED_API_KEY]",
    "auth_header": "[REDACTED_AUTH_HEADER]",
    "broker_credential": "[REDACTED_BROKER_CREDENTIAL]",
    "cookie": "[REDACTED_COOKIE]",
    "database_credentials": "[REDACTED_CREDENTIALS]",
    "env_value": "[REDACTED_ENV_VALUE]",
    "private_key": "[REDACTED_PRIVATE_KEY]",
    "provider_session_path": "[REDACTED_PROVIDER_SESSION_PATH]",
    "session_id": "[REDACTED_SESSION]",
    "token": "[REDACTED_TOKEN]",
}

_SENSITIVE_KEY_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"^(authorization|proxy-authorization)$", re.IGNORECASE), "auth_header"),
    (re.compile(r"^(cookie|set-cookie)$", re.IGNORECASE), "cookie"),
    (re.compile(r"(broker.*(credential|secret|token|key)|shioaji|broker_account)$", re.IGNORECASE), "broker_credential"),
    (re.compile(r"(account[_-]?(number|id)|broker[_-]?account)$", re.IGNORECASE), "account_number"),
    (re.compile(r"(api[_-]?key|x-api-key)$", re.IGNORECASE), "api_key"),
    (re.compile(r"(authorization[_-]?code|auth[_-]?code)$", re.IGNORECASE), "token"),
    (re.compile(r"(access[_-]?token|refresh[_-]?token|id[_-]?token|bearer|token)$", re.IGNORECASE), "token"),
    (re.compile(r"(secret|password|passwd|pwd)$", re.IGNORECASE), "env_value"),
    (re.compile(r"(private[_-]?key|pem)$", re.IGNORECASE), "private_key"),
    (re.compile(r"(database[_-]?url|db[_-]?url|dsn)$", re.IGNORECASE), "database_credentials"),
    (
        re.compile(
            r"(codex[_-]?home|claude[_-]?(config|home)|provider[_-]?session|credential[_-]?mount|oauth[_-]?dir)",
            re.IGNORECASE,
        ),
        "provider_session_path",
    ),
    (re.compile(r"(session[_-]?id|sid|csrf)", re.IGNORECASE), "session_id"),
)

_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.DOTALL,
)
_AUTH_HEADER_LINE_RE = re.compile(r"(?im)^((?:authorization|proxy-authorization)\s*:\s*)(.+)$")
_BEARER_RE = re.compile(r"(?i)\b(Bearer\s+)([A-Za-z0-9._~+/=-]{8,})")
_API_KEY_LINE_RE = re.compile(
    r"(?i)\b((?:api[_-]?key|x-api-key|openai_api_key|anthropic_api_key)\s*[:=]\s*)([\"']?)([^\"'\s,;]+)([\"']?)"
)
_SECRET_LINE_RE = re.compile(
    r"(?i)\b((?:access[_-]?token|refresh[_-]?token|id[_-]?token|token|secret|password|passwd|pwd)\s*[:=]\s*)"
    r"([\"']?)([^\"'\s,;]+)([\"']?)"
)
_BROKER_LINE_RE = re.compile(
    r"(?i)\b((?:broker[_-]?(?:credential|secret|token|key|password)|shioaji[_-]?(?:key|secret|account))\s*[:=]\s*)"
    r"([\"']?)([^\"'\s,;]+)([\"']?)"
)
_ENV_SECRET_LINE_RE = re.compile(
    r"(?m)^([A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|BROKER_KEY|BROKER_SECRET)\s*=\s*)(.*)$"
)
_DATABASE_URL_RE = re.compile(
    r"\b((?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|redis|amqp|kafka|clickhouse)://)"
    r"([^:@/\s]+):([^@\s]+)@",
    re.IGNORECASE,
)
_PROVIDER_SESSION_PATH_RE = re.compile(
    r"(?<![\w.-])(?:~|/[A-Za-z0-9._-][^\s:'\",;]*)?/\.((?:codex|claude))(?:/[^\s:'\",;]*)?",
    re.IGNORECASE,
)
_COOKIE_VALUE_RE = re.compile(r"(?i)\b([A-Za-z0-9_.-]*(?:session|sid|csrf|token|auth)[A-Za-z0-9_.-]*=)([^;\s,]+)")
_RAW_COOKIE_HEADER_RE = re.compile(r"(?im)^((?:cookie|set-cookie)\s*:\s*)(.+)$")
_SESSION_ID_RE = re.compile(r"(?i)\b((?:session[_-]?id|sid|csrf[_-]?token)\s*[:=]\s*)([\"']?)([^\"'\s,;]+)([\"']?)")
_ACCOUNT_NUMBER_RE = re.compile(r"(?i)\b((?:account[_-]?(?:number|id)|broker[_-]?account)\s*[:=]\s*)([\"']?)([0-9A-Za-z-]{6,})([\"']?)")


def redact_text(text: str, *, stats: _RedactionStats | None = None) -> str:
    """Redact known secret patterns from a diagnostic text blob."""

    local_stats = stats or _RedactionStats()
    redacted = text

    def sub(pattern: re.Pattern[str], category: str, repl: Any) -> None:
        nonlocal redacted
        redacted, count = pattern.subn(repl, redacted)
        if count:
            local_stats.record(category, count=count)

    sub(_PRIVATE_KEY_RE, "private_key", _MARKERS["private_key"])
    sub(_DATABASE_URL_RE, "database_credentials", lambda match: f"{match.group(1)}{_MARKERS['database_credentials']}@")
    sub(_AUTH_HEADER_LINE_RE, "auth_header", _redact_auth_header_line)
    sub(_BEARER_RE, "token", lambda match: f"{match.group(1)}{_MARKERS['token']}")
    sub(_RAW_COOKIE_HEADER_RE, "cookie", _redact_cookie_header_line)
    sub(_COOKIE_VALUE_RE, "cookie", lambda match: f"{match.group(1)}{_MARKERS['cookie']}")
    sub(_PROVIDER_SESSION_PATH_RE, "provider_session_path", _MARKERS["provider_session_path"])
    sub(_BROKER_LINE_RE, "broker_credential", lambda match: _replace_assignment(match, _MARKERS["broker_credential"]))
    sub(_API_KEY_LINE_RE, "api_key", lambda match: _replace_assignment(match, _MARKERS["api_key"]))
    sub(_SECRET_LINE_RE, "env_value", lambda match: _replace_assignment(match, _MARKERS["env_value"]))
    sub(_ENV_SECRET_LINE_RE, "env_value", _redact_env_line_once)
    sub(_SESSION_ID_RE, "session_id", lambda match: _replace_assignment_once(match, _MARKERS["session_id"]))
    sub(_ACCOUNT_NUMBER_RE, "account_number", lambda match: _replace_assignment(match, _MARKERS["account_number"]))
    return redacted


def redact_payload(payload: JsonValue, *, enabled: bool = True) -> RedactionResult:
    """Redact a JSON-like payload while preserving mapping/list shape."""

    if not enabled:
        return RedactionResult(payload, RedactionSummary(enabled=False))
    stats = _RedactionStats()
    value = _redact_value(payload, stats=stats, path=())
    return RedactionResult(
        value=value,
        summary=RedactionSummary(
            enabled=True,
            redacted_fields=stats.fields,
            redacted_values=stats.values,
            categories=dict(stats.categories),
        ),
    )


def redact_assistant_payload(
    payload: JsonValue,
    *,
    mode: str,
    stage: str,
    enabled: bool = True,
    allow_kernel_override: bool = False,
) -> RedactionResult:
    """Redact an assistant payload with mode-aware failure semantics.

    User mode fails closed on any redaction exception.  Kernel modes also fail
    closed unless the caller has provided an explicit override, in which case a
    diagnostic failure envelope is returned instead of the original payload.
    """

    try:
        return redact_payload(payload, enabled=enabled)
    except Exception as exc:  # noqa: BLE001 - fail-closed boundary
        mode_label = str(mode or "user")
        if mode_label in USER_MODES or not allow_kernel_override:
            raise RedactionError(f"assistant redaction failed closed at {stage}: {exc}") from exc
        failure_summary = RedactionSummary(
            enabled=enabled,
            failed=True,
            failure_stage=stage,
            failure_reason=type(exc).__name__,
            categories={"redaction_failure": 1},
            redacted_values=1,
        )
        return RedactionResult(
            value={
                "redaction_failed": True,
                "stage": stage,
                "mode": mode_label,
                "payload": "[REDACTION_FAILED_PAYLOAD_SUPPRESSED]",
            },
            summary=failure_summary,
        )


def _redact_value(value: JsonValue, *, stats: _RedactionStats, path: tuple[str, ...]) -> JsonValue:
    if isinstance(value, Mapping):
        result: dict[Any, Any] = {}
        for key, child in value.items():
            key_label = str(key)
            if _is_public_reauth_tracking_key(key_label):
                result[key] = _redact_value(child, stats=stats, path=(*path, key_label))
                continue
            category = _category_for_key(key_label)
            if category:
                result[key] = _redact_sensitive_field(key_label, child, category=category, stats=stats)
            else:
                result[key] = _redact_value(child, stats=stats, path=(*path, key_label))
        return result
    if isinstance(value, str):
        return redact_text(value, stats=stats)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return [_redact_value(child, stats=stats, path=path) for child in value]
    return value


def _redact_sensitive_field(key: str, value: JsonValue, *, category: str, stats: _RedactionStats) -> JsonValue:
    stats.record(category, field=True)
    if isinstance(value, str):
        if category == "auth_header":
            return _redact_auth_value(value)
        if category == "cookie":
            return _redact_cookie_value(value)
        if category == "database_credentials":
            redacted = redact_text(value, stats=stats)
            return redacted if redacted != value else _MARKERS[category]
    return _MARKERS[category]


def _category_for_key(key: str) -> str | None:
    normalized = key.strip().replace("-", "_").lower()
    for pattern, category in _SENSITIVE_KEY_PATTERNS:
        if pattern.search(normalized):
            return category
    return None


def _is_public_reauth_tracking_key(key: str) -> bool:
    normalized = key.strip().replace("-", "_").lower()
    return normalized in {"reauth_session_id", "reauthsessionid"}


def _redact_auth_header_line(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_redact_auth_value(match.group(2))}"


def _redact_auth_value(value: str) -> str:
    stripped = value.strip()
    bearer = re.match(r"(?i)^Bearer\s+", stripped)
    if bearer:
        return f"{stripped[:bearer.end()]}{_MARKERS['token']}"
    return _MARKERS["auth_header"]


def _redact_cookie_header_line(match: re.Match[str]) -> str:
    return f"{match.group(1)}{_redact_cookie_value(match.group(2))}"


def _redact_cookie_value(value: str) -> str:
    parts = []
    for part in value.split(";"):
        segment = part.strip()
        if "=" in segment:
            name, _raw = segment.split("=", 1)
            parts.append(f"{name}={_MARKERS['cookie']}")
        else:
            parts.append(segment)
    return "; ".join(parts)


def _replace_assignment(match: re.Match[str], marker: str) -> str:
    quote = match.group(2) or ""
    close_quote = match.group(4) if match.lastindex and match.lastindex >= 4 else quote
    if quote and close_quote != quote:
        close_quote = quote
    return f"{match.group(1)}{quote}{marker}{close_quote}"


def _replace_assignment_once(match: re.Match[str], marker: str) -> str:
    value = match.group(3) if match.lastindex and match.lastindex >= 3 else ""
    if value.startswith("[REDACTED_"):
        return match.group(0)
    return _replace_assignment(match, marker)


def _redact_env_line_once(match: re.Match[str]) -> str:
    value = match.group(2).strip()
    if value.startswith("[REDACTED_") or value.startswith("\"[REDACTED_") or value.startswith("'[REDACTED_"):
        return match.group(0)
    return f"{match.group(1)}{_MARKERS['env_value']}"

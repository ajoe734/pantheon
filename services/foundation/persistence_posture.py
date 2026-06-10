"""Shared staging/prod persistence posture checks.

The check is intentionally configuration-only: it validates that a service is
declared to use Postgres and that shared object-store env is present before the
service accepts traffic. It does not open database or object-store connections.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Sequence


ENFORCED_MODES = {"stage", "staging", "staging-live", "prod", "production"}
OBJECT_STORE_KEYS = (
    "PANTHEON_S3_ENDPOINT",
    "PANTHEON_ARTIFACT_BUCKET",
    "PANTHEON_S3_ACCESS_KEY",
    "PANTHEON_S3_SECRET_KEY",
)


SERVICE_BACKEND_DEFAULTS: Mapping[str, Mapping[str, str]] = {
    "capital": {
        "CAPITAL_STORE_BACKEND": "json",
        "CAPITAL_AUDIT_BACKEND": "jsonl",
    },
    "capital-pool": {
        "CAPITAL_STORE_BACKEND": "json",
        "CAPITAL_AUDIT_BACKEND": "jsonl",
    },
    "capital-pool-svc": {
        "CAPITAL_STORE_BACKEND": "json",
        "CAPITAL_AUDIT_BACKEND": "jsonl",
    },
    "consultation": {"CONSULTATION_STORE_BACKEND": "jsonl"},
    "consultation-svc": {"CONSULTATION_STORE_BACKEND": "jsonl"},
    "governance": {
        "GOVERNANCE_STORE_BACKEND": "json",
        "GOVERNANCE_AUDIT_BACKEND": "jsonl",
    },
    "governance-svc": {
        "GOVERNANCE_STORE_BACKEND": "json",
        "GOVERNANCE_AUDIT_BACKEND": "jsonl",
    },
    "incidents": {"INCIDENT_STORE_BACKEND": "json"},
    "incident-svc": {"INCIDENT_STORE_BACKEND": "json"},
    "memory": {"PANTHEON_MEMORY_STORE_BACKEND": "json"},
    "memory-svc": {"PANTHEON_MEMORY_STORE_BACKEND": "json"},
    "operator-bff": {"MANAGEMENT_AI_STORE_BACKEND": "json"},
    "bff": {"MANAGEMENT_AI_STORE_BACKEND": "json"},
    "control-plane-bff": {"MANAGEMENT_AI_STORE_BACKEND": "json"},
    "policy-learning": {"POLICY_LEARNING_STORE_BACKEND": "json"},
    "policy-learning-svc": {"POLICY_LEARNING_STORE_BACKEND": "json"},
    "postmortems": {"POSTMORTEM_STORE_BACKEND": "json"},
    "postmortem-svc": {"POSTMORTEM_STORE_BACKEND": "json"},
    "promotion": {"PROMOTION_STORE_BACKEND": "json"},
    "promotion-svc": {"PROMOTION_STORE_BACKEND": "json"},
    "reconciliation-drift": {"RECONCILIATION_DRIFT_STORE_BACKEND": "json"},
    "reconciliation-drift-svc": {"RECONCILIATION_DRIFT_STORE_BACKEND": "json"},
    "research-orchestrator": {"RESEARCH_ORCHESTRATOR_EVENT_STORE_BACKEND": "jsonl"},
    "research-orchestrator-svc": {"RESEARCH_ORCHESTRATOR_EVENT_STORE_BACKEND": "jsonl"},
    "research-worker-gateway": {"RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND": "jsonl"},
    "research-worker-gateway-svc": {"RESEARCH_WORKER_GATEWAY_EVENT_STORE_BACKEND": "jsonl"},
    "training-session": {"TRAINING_SESSION_EVENT_STORE_BACKEND": "jsonl"},
    "training-session-svc": {"TRAINING_SESSION_EVENT_STORE_BACKEND": "jsonl"},
}


@dataclass(frozen=True)
class PersistencePostureCheck:
    mode: str
    service: str
    enforced: bool
    errors: tuple[str, ...]
    backends: Mapping[str, str]
    object_store_configured: bool
    database_url_configured: bool
    database_backend: str

    @property
    def status(self) -> str:
        return "error" if self.errors else "ok"

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "mode": self.mode,
            "service": self.service,
            "enforced": self.enforced,
            "errors": list(self.errors),
            "backends": dict(self.backends),
            "object_store_configured": self.object_store_configured,
            "database_url_configured": self.database_url_configured,
            "database_backend": self.database_backend,
            "dev_fallback_allowed": not self.enforced,
        }

    def alert_count(self) -> int:
        return len(self.errors)


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def _clean(value: object) -> str:
    return str(value or "").strip()


def _mode(env_map: Mapping[str, str]) -> str:
    return (
        _clean(env_map.get("PANTHEON_PERSISTENCE_POSTURE"))
        or _clean(env_map.get("PANTHEON_ENV"))
        or "dev"
    ).lower()


def _is_enforced_mode(mode: str) -> bool:
    return mode in ENFORCED_MODES or mode.startswith("staging") or mode.startswith("prod")


def _database_backend(database_url: str) -> str:
    if database_url.startswith(("postgresql://", "postgres://")):
        return "postgres"
    if not database_url:
        return "missing"
    scheme = database_url.split(":", 1)[0].strip().lower()
    return scheme or "unknown"


def _backend_defaults(
    service: str,
    backend_env_vars: Sequence[str] | Mapping[str, str] | None,
) -> Mapping[str, str]:
    if backend_env_vars is None:
        defaults = SERVICE_BACKEND_DEFAULTS.get(service)
        if defaults is None:
            raise ValueError(f"unknown persistence posture service: {service}")
        return defaults
    if isinstance(backend_env_vars, Mapping):
        return dict(backend_env_vars)
    return {key: "json" for key in backend_env_vars}


def validate_persistence_posture(
    service: str,
    *,
    env: Mapping[str, str] | None = None,
    backend_env_vars: Sequence[str] | Mapping[str, str] | None = None,
    require_object_store: bool = True,
) -> PersistencePostureCheck:
    env_map = _env(env)
    normalized_service = _clean(service).lower()
    mode = _mode(env_map)
    enforced = _is_enforced_mode(mode)
    errors: list[str] = []
    defaults = _backend_defaults(normalized_service, backend_env_vars)
    backends = {
        key: _clean(env_map.get(key)).lower() or default
        for key, default in defaults.items()
    }

    database_url = _clean(env_map.get("DATABASE_URL"))
    database_backend = _database_backend(database_url)
    object_store_configured = all(_clean(env_map.get(key)) for key in OBJECT_STORE_KEYS)

    if enforced:
        if database_backend != "postgres":
            errors.append("DATABASE_URL must be a Postgres DSN in staging/prod persistence posture")
        for key, backend in backends.items():
            if backend != "postgres":
                errors.append(f"{key} must be postgres in staging/prod persistence posture; {backend} is dev-only")
        if require_object_store:
            for key in OBJECT_STORE_KEYS:
                if not _clean(env_map.get(key)):
                    errors.append(f"{key} is required for staging/prod object-store posture")

    return PersistencePostureCheck(
        mode=mode,
        service=normalized_service,
        enforced=enforced,
        errors=tuple(errors),
        backends=backends,
        object_store_configured=object_store_configured,
        database_url_configured=bool(database_url),
        database_backend=database_backend,
    )


def require_persistence_posture(
    service: str,
    *,
    env: Mapping[str, str] | None = None,
    backend_env_vars: Sequence[str] | Mapping[str, str] | None = None,
    require_object_store: bool = True,
) -> PersistencePostureCheck:
    check = validate_persistence_posture(
        service,
        env=env,
        backend_env_vars=backend_env_vars,
        require_object_store=require_object_store,
    )
    if check.errors:
        raise RuntimeError("; ".join(check.errors))
    return check

"""Persona daily-cycle owner-port configuration and reachability health surface.

Reports the status of every required owner port (`research`, `memory`,
`registry`, `evolution`, `consultation`, `governance`, `search`) as
`configured`, `unconfigured`, or `unreachable` without ever exposing
credential values.
"""
from __future__ import annotations

import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, MutableMapping

from services.service_token_file import configured_service_token

REQUIRED_OWNER_PORTS: tuple[str, ...] = (
    "research",
    "memory",
    "registry",
    "evolution",
    "consultation",
    "governance",
    "search",
)

# Legacy non-existent compose service hostname that must never be treated as reachable.
_UNRESOLVABLE_LEGACY_HOSTS: frozenset[str] = frozenset({"research-orchestrator"})


@dataclass(frozen=True)
class OwnerPortSpec:
    name: str
    url_vars: tuple[str, ...]
    token_vars: tuple[str, ...]
    health_path: str = "/readyz"


OWNER_PORT_SPECS: dict[str, OwnerPortSpec] = {
    "research": OwnerPortSpec(
        name="research",
        url_vars=("RESEARCH_API_URL", "PANTHEON_RESEARCH_API_URL"),
        token_vars=("PERSONA_RESEARCH_SERVICE_TOKEN", "PANTHEON_RESEARCH_SERVICE_TOKEN"),
    ),
    "memory": OwnerPortSpec(
        name="memory",
        url_vars=("MEMORY_API_URL", "PANTHEON_MEMORY_API_URL"),
        token_vars=(
            "PERSONA_MEMORY_SERVICE_TOKEN",
            "PANTHEON_MEMORY_AUTH_TOKEN",
            "PANTHEON_MEMORY_SERVICE_TOKEN",
        ),
    ),
    "registry": OwnerPortSpec(
        name="registry",
        url_vars=(
            "REGISTRY_URL",
            "PERSONA_REGISTRY_BASE_URL",
            "PANTHEON_REGISTRY_URL",
            "PANTHEON_REGISTRY_API_URL",
        ),
        token_vars=("PERSONA_REGISTRY_SERVICE_TOKEN", "PANTHEON_REGISTRY_SERVICE_TOKEN"),
    ),
    "evolution": OwnerPortSpec(
        name="evolution",
        url_vars=("EVOLUTION_API_URL", "PANTHEON_EVOLUTION_API_URL"),
        token_vars=("EVOLUTION_AUTH_TOKEN", "PERSONA_EVOLUTION_SERVICE_TOKEN"),
    ),
    "consultation": OwnerPortSpec(
        name="consultation",
        url_vars=("PANTHEON_CONSULTATION_API_URL", "PANTHEON_CONSULTATION_SERVICE_URL"),
        token_vars=(
            "CONSULTATION_SERVICE_TOKEN",
            "CONSULTATION_API_TOKEN",
            "PERSONA_CONSULTATION_SERVICE_TOKEN",
        ),
    ),
    "governance": OwnerPortSpec(
        name="governance",
        url_vars=(
            "PANTHEON_GOVERNANCE_API_URL",
            "PANTHEON_GOVERNANCE_SERVICE_URL",
            "PERSONA_TRAINING_TARGET_GOVERNANCE_BASE_URL",
        ),
        token_vars=("PERSONA_GOVERNANCE_SERVICE_TOKEN", "LESSON_RULE_PROPOSER_TOKEN"),
    ),
    "search": OwnerPortSpec(
        name="search",
        url_vars=("PANTHEON_SEARCH_API_URL",),
        token_vars=("PERSONA_SEARCH_SERVICE_TOKEN", "PANTHEON_SEARCH_SERVICE_TOKEN"),
    ),
}


def _resolve_token_present(token_vars: tuple[str, ...], env: Mapping[str, str]) -> tuple[bool, str | None, str | None]:
    """Return (has_token, source_var, error_reason) without returning the token value."""
    last_error: str | None = None
    for var in token_vars:
        file_var = f"{var}_FILE"
        if str(env.get(file_var) or "").strip():
            try:
                value = configured_service_token(var, env)
            except RuntimeError:
                last_error = f"{file_var}_unreadable"
                continue
            if value:
                return True, file_var, None
        raw = str(env.get(var) or "").strip()
        if raw:
            return True, var, None
    return False, None, last_error or "credential_missing"


def _resolve_token_value(token_vars: tuple[str, ...], env: Mapping[str, str]) -> str:
    """Internal helper for syncing file-backed tokens into process env aliases."""
    for var in token_vars:
        file_var = f"{var}_FILE"
        if str(env.get(file_var) or "").strip():
            try:
                value = configured_service_token(var, env)
                if value:
                    return value
            except RuntimeError:
                continue
        raw = str(env.get(var) or "").strip()
        if raw:
            return raw
    return ""


def _resolve_url(url_vars: tuple[str, ...], env: Mapping[str, str]) -> tuple[str, str | None]:
    for var in url_vars:
        raw = str(env.get(var) or "").strip()
        if raw:
            return raw.rstrip("/"), var
    return "", None


def _valid_http_url(url: str) -> tuple[bool, str | None]:
    if not url:
        return False, "address_missing"
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False, "address_invalid"
    if parsed.hostname in _UNRESOLVABLE_LEGACY_HOSTS:
        return False, f"unresolvable_host:{parsed.hostname}"
    return True, None


def _default_probe(url: str, timeout: float = 1.5) -> bool:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 500
    except urllib.error.HTTPError as exc:
        # An HTTP response from the service proves network reachability.
        return 200 <= int(exc.code) < 500
    except Exception:
        return False


def evaluate_owner_ports(
    env: Mapping[str, str] | None = None,
    *,
    probe: bool | None = None,
    probe_fn: Callable[[str], bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """Evaluate all 7 required persona cycle owner ports without exposing secrets."""
    source_env: Mapping[str, str] = os.environ if env is None else env
    should_probe = (
        probe
        if probe is not None
        else (
            probe_fn is not None
            or str(source_env.get("PERSONA_OWNER_PORT_HEALTH_PROBE") or "").strip().lower()
            in {"1", "true", "yes"}
        )
    )
    active_probe = probe_fn or _default_probe

    report: dict[str, dict[str, Any]] = {}
    for port_name in REQUIRED_OWNER_PORTS:
        spec = OWNER_PORT_SPECS[port_name]
        url, url_var = _resolve_url(spec.url_vars, source_env)
        url_ok, url_reason = _valid_http_url(url)
        token_ok, token_source, token_reason = _resolve_token_present(spec.token_vars, source_env)

        if not url:
            report[port_name] = {
                "status": "unconfigured",
                "address": None,
                "address_configured": False,
                "credential_configured": bool(token_ok),
                "reason": url_reason or "address_missing",
            }
            continue

        if not url_ok:
            status = "unreachable" if (url_reason or "").startswith("unresolvable_host:") else "unconfigured"
            report[port_name] = {
                "status": status,
                "address": url,
                "address_source": url_var,
                "address_configured": False,
                "credential_configured": bool(token_ok),
                "reason": url_reason or "address_invalid",
            }
            continue

        if not token_ok:
            report[port_name] = {
                "status": "unconfigured",
                "address": url,
                "address_source": url_var,
                "address_configured": True,
                "credential_configured": False,
                "reason": token_reason or "credential_missing",
            }
            continue

        if should_probe:
            probe_url = f"{url}{spec.health_path}"
            if not active_probe(probe_url):
                report[port_name] = {
                    "status": "unreachable",
                    "address": url,
                    "address_source": url_var,
                    "address_configured": True,
                    "credential_configured": True,
                    "credential_source": token_source,
                    "reason": "endpoint_unreachable",
                }
                continue

        report[port_name] = {
            "status": "configured",
            "address": url,
            "address_source": url_var,
            "address_configured": True,
            "credential_configured": True,
            "credential_source": token_source,
        }
    return report


def non_healthy_owner_ports(
    owner_ports: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    probe: bool | None = None,
    probe_fn: Callable[[str], bool] | None = None,
) -> dict[str, dict[str, Any]]:
    """Return owner ports whose status is not `configured`."""
    evaluated = (
        dict(owner_ports)
        if owner_ports is not None
        else evaluate_owner_ports(env, probe=probe, probe_fn=probe_fn)
    )
    return {
        name: dict(details)
        for name, details in evaluated.items()
        if details.get("status") != "configured"
    }


def has_owner_port_env_configured(env: Mapping[str, str] | None = None) -> bool:
    """Return True when daily cycle or any owner-port address/credential env var is set."""
    source_env: Mapping[str, str] = os.environ if env is None else env
    if str(source_env.get("PERSONA_DAILY_CYCLE_ENABLED") or "").strip().lower() in {"1", "true", "yes"}:
        return True
    for spec in OWNER_PORT_SPECS.values():
        for url_var in spec.url_vars:
            if str(source_env.get(url_var) or "").strip():
                return True
        for token_var in spec.token_vars:
            if str(source_env.get(token_var) or "").strip() or str(source_env.get(f"{token_var}_FILE") or "").strip():
                return True
    return False


def apply_owner_port_health_to_cycle_report(
    report: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    probe: bool | None = None,
    probe_fn: Callable[[str], bool] | None = None,
) -> dict[str, Any]:
    """Ensure cycle heartbeat/report records degraded status naming every missing owner port."""
    out = dict(report)
    owner_ports = evaluate_owner_ports(env, probe=probe, probe_fn=probe_fn)
    missing = non_healthy_owner_ports(owner_ports)
    out["owner_ports"] = owner_ports
    failures = [dict(item) for item in (out.get("failures") or []) if isinstance(item, Mapping)]

    persona_ids: list[str] = []
    seen_pids: set[str] = set()
    tenant_scopes = out.get("tenant_scopes")
    if isinstance(tenant_scopes, Mapping):
        for pids in tenant_scopes.values():
            if isinstance(pids, (list, tuple, set)):
                for pid in pids:
                    pid_str = str(pid or "").strip()
                    if pid_str and pid_str not in seen_pids:
                        seen_pids.add(pid_str)
                        persona_ids.append(pid_str)
    elif isinstance(tenant_scopes, (list, tuple)):
        for scope in tenant_scopes:
            if isinstance(scope, Mapping):
                pids = scope.get("personas") or scope.get("persona_ids") or ()
                if isinstance(pids, (list, tuple, set)):
                    for pid in pids:
                        pid_str = str(pid or "").strip()
                        if pid_str and pid_str not in seen_pids:
                            seen_pids.add(pid_str)
                            persona_ids.append(pid_str)
    for key in ("personas", "jobs"):
        mapping = out.get(key)
        if isinstance(mapping, Mapping):
            for pid in mapping.keys():
                pid_str = str(pid or "").strip()
                if pid_str and pid_str not in seen_pids:
                    seen_pids.add(pid_str)
                    persona_ids.append(pid_str)

    existing_keys = {
        (str(item.get("persona_id") or "") or None, str(item.get("owner_port") or ""))
        for item in failures
        if item.get("owner_port")
    }
    target_pids: list[str | None] = persona_ids if persona_ids else [None]
    for port_name, details in missing.items():
        status = str(details.get("status") or "unconfigured")
        reason = str(details.get("reason") or status)
        for pid in target_pids:
            if (pid, port_name) in existing_keys or (None, port_name) in existing_keys:
                continue
            entry: dict[str, Any] = {
                "owner_port": port_name,
                "status": status,
                "reason": f"owner_port_{status}:{port_name}:{reason}",
            }
            if pid is not None:
                entry["persona_id"] = pid
            failures.append(entry)
            existing_keys.add((pid, port_name))

    out["failures"] = failures
    out["status"] = "degraded" if failures else str(out.get("status") or "ok")
    return out


def sync_owner_port_env(target_env: MutableMapping[str, str] | None = None) -> dict[str, str]:
    """Propagate resolved owner URLs and file-backed credentials into a mapping without mutating os.environ."""
    env: MutableMapping[str, str] = dict(os.environ) if target_env is None else target_env
    for spec in OWNER_PORT_SPECS.values():
        url, _ = _resolve_url(spec.url_vars, env)
        if url:
            for url_var in spec.url_vars:
                if not str(env.get(url_var) or "").strip():
                    env[url_var] = url
        token = _resolve_token_value(spec.token_vars, env)
        if token:
            for token_var in spec.token_vars:
                env[token_var] = token
    # Also sync source-ingest read token if delivered via PERSONA_POOL_SOURCE_READ_TOKEN_FILE.
    source_token = _resolve_token_value(
        ("PERSONA_SOURCE_INGEST_SERVICE_TOKEN", "PERSONA_POOL_SOURCE_READ_TOKEN"),
        env,
    )
    if source_token:
        env["PERSONA_SOURCE_INGEST_SERVICE_TOKEN"] = source_token
    return dict(env)


class OwnerPortEnvView(Mapping[str, str]):
    """Read-through view over an environment mapping that resolves owner port aliases and _FILE tokens without mutating os.environ."""

    def __init__(self, base_env: Mapping[str, str] | None = None) -> None:
        self._base = os.environ if base_env is None else base_env

    def _snapshot(self) -> dict[str, str]:
        data = dict(self._base)
        sync_owner_port_env(data)
        return data

    def __getitem__(self, key: str) -> str:
        snap = self._snapshot()
        if key in snap:
            return snap[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        return self._snapshot().get(key, default)

    def __iter__(self):
        return iter(self._snapshot())

    def __len__(self) -> int:
        return len(self._snapshot())

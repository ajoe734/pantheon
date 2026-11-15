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
        token_vars=("PERSONA_EVOLUTION_SERVICE_TOKEN", "EVOLUTION_AUTH_TOKEN"),
    ),
    "consultation": OwnerPortSpec(
        name="consultation",
        url_vars=("PANTHEON_CONSULTATION_API_URL", "PANTHEON_CONSULTATION_SERVICE_URL"),
        token_vars=(
            "PERSONA_CONSULTATION_SERVICE_TOKEN",
            "CONSULTATION_SERVICE_TOKEN",
            "CONSULTATION_API_TOKEN",
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
    existing_ports = {
        str(item.get("owner_port") or "")
        for item in failures
        if item.get("owner_port")
    }
    for port_name, details in missing.items():
        if port_name in existing_ports:
            continue
        status = str(details.get("status") or "unconfigured")
        reason = str(details.get("reason") or status)
        failures.append(
            {
                "owner_port": port_name,
                "status": status,
                "reason": f"owner_port_{status}:{port_name}:{reason}",
            }
        )
    out["failures"] = failures
    out["status"] = "degraded" if failures else str(out.get("status") or "ok")
    return out


def sync_owner_port_env(target_env: MutableMapping[str, str] | None = None) -> None:
    """Propagate resolved owner URLs and file-backed credentials to alias env vars in-process."""
    env = os.environ if target_env is None else target_env
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

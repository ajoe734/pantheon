"""
Canonical reference validation for incident-facing services.

BP5-SVC-011 requires incident evidence services to enforce that runtime
binding, telemetry evidence, and lineage pointers are canonical references,
not opaque strings.  This module centralizes those checks so both deployable
services can reuse one validation policy.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .incident import IncidentCase

_SERVICES_DIR = Path(__file__).resolve().parents[1]
_RUNTIME_MANAGER_DIR = _SERVICES_DIR / "runtime-manager"
if str(_RUNTIME_MANAGER_DIR) not in sys.path:
    sys.path.insert(0, str(_RUNTIME_MANAGER_DIR))

try:
    from runtime_manager_client import RuntimeManagerClient  # type: ignore
except ImportError:  # pragma: no cover - defensive fallback
    RuntimeManagerClient = None  # type: ignore[assignment]

_TELEMETRY_DIR = _SERVICES_DIR / "telemetry"
if str(_TELEMETRY_DIR) not in sys.path:
    sys.path.insert(0, str(_TELEMETRY_DIR))

try:
    from lineage_read import LineageReadService  # type: ignore
except ImportError:  # pragma: no cover - defensive fallback
    LineageReadService = None  # type: ignore[assignment]

_DEFAULT_LINEAGE_CORPUS_PATH = (
    _SERVICES_DIR / "registry" / "lineage" / "lin001a_benchmark_corpus.json"
)


class CanonicalReferenceError(ValueError):
    """Raised when an incident references non-canonical evidence."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(" ; ".join(errors))


class _RuntimeBindingLookup:
    """Lookup RuntimeBinding records through the canonical runtime-manager path."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client or (RuntimeManagerClient() if RuntimeManagerClient else None)

    def get_binding(self, binding_id: str) -> Optional[dict[str, Any]]:
        if not self._client or not binding_id:
            return None
        binding = self._client.get(binding_id)
        if binding is None:
            return None
        if isinstance(binding, dict):
            return dict(binding)
        if hasattr(binding, "to_dict"):
            return dict(binding.to_dict())
        fields = (
            "binding_id",
            "runtime_id",
            "capital_pool_id",
            "artifact_id",
            "artifact_version",
            "deployment_mode",
            "effective_at",
            "retired_at",
            "plan_id",
            "persona_capital_binding_id",
        )
        return {field: getattr(binding, field, None) for field in fields}


class _TelemetryLineageLookup:
    """Resolve telemetry and lineage refs via HTTP or a local lineage corpus."""

    def __init__(
        self,
        *,
        base_url: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        corpus_path: Optional[str | Path] = None,
    ) -> None:
        env_base_url = (
            base_url
            or os.getenv("PANTHEON_TELEMETRY_URL", "")
            or os.getenv("PANTHEON_TELEMETRY_BASE_URL", "")
        ).strip().rstrip("/")
        self._base_url = env_base_url
        self._timeout_seconds = int(
            timeout_seconds
            if timeout_seconds is not None
            else os.getenv("PANTHEON_TELEMETRY_TIMEOUT_SECONDS", "5")
        )
        self._corpus_path = Path(
            corpus_path
            or os.getenv("LINEAGE_READ_CORPUS_PATH", str(_DEFAULT_LINEAGE_CORPUS_PATH))
        )
        self._local_service: Any | None = None

    def telemetry_event_trace(self, event_id: str) -> Optional[dict[str, Any]]:
        return self._query("telemetry_event_trace", event_id=event_id)

    def runtime_binding_projection(self, binding_id: str) -> Optional[dict[str, Any]]:
        return self._query("runtime_binding_projection", binding_id=binding_id)

    def forensic_plan_trace(self, plan_id: str) -> Optional[dict[str, Any]]:
        return self._query("forensic_plan_trace", plan_id=plan_id)

    def _query(self, query_family: str, **params: str) -> Optional[dict[str, Any]]:
        if self._base_url:
            return self._query_http(query_family, **params)
        return self._query_local(query_family, **params)

    def _query_http(self, query_family: str, **params: str) -> Optional[dict[str, Any]]:
        route = self._route_for(query_family, **params)
        req = urllib.request.Request(
            f"{self._base_url}{route}",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_seconds) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise CanonicalReferenceError(
                [f"telemetry lineage lookup failed for {query_family}: HTTP {exc.code}"]
            ) from exc
        except urllib.error.URLError as exc:
            raise CanonicalReferenceError(
                [f"telemetry lineage lookup unavailable for {query_family}: {getattr(exc, 'reason', exc)}"]
            ) from exc

    def _query_local(self, query_family: str, **params: str) -> Optional[dict[str, Any]]:
        service = self._get_local_service()
        if service is None:
            return None
        result = service.query(query_family, **params)
        markers = result.get("conflict_markers", [])
        if any(marker.get("code") == "node_not_found" for marker in markers):
            return None
        return result

    def _get_local_service(self) -> Any | None:
        if self._local_service is not None:
            return self._local_service
        if LineageReadService is None or not self._corpus_path.exists():
            return None
        service = LineageReadService()
        service.load_corpus(json.loads(self._corpus_path.read_text()))
        self._local_service = service
        return self._local_service

    @staticmethod
    def _route_for(query_family: str, **params: str) -> str:
        if query_family == "telemetry_event_trace":
            return f"/api/telemetry/lineage/events/{params['event_id']}/trace"
        if query_family == "runtime_binding_projection":
            return f"/api/telemetry/lineage/runtime-bindings/{params['binding_id']}/projection"
        if query_family == "forensic_plan_trace":
            return f"/api/telemetry/lineage/plans/{params['plan_id']}/forensic-trace"
        raise ValueError(f"Unsupported query family: {query_family}")


@dataclass
class CanonicalReferenceValidator:
    """Validate authoritative foreign references for incident evidence."""

    binding_lookup: Any | None = None
    telemetry_lookup: Any | None = None

    def __post_init__(self) -> None:
        if self.binding_lookup is None:
            self.binding_lookup = _RuntimeBindingLookup()
        if self.telemetry_lookup is None:
            self.telemetry_lookup = _TelemetryLineageLookup()

    def validate_incident(self, incident: IncidentCase) -> None:
        errors: list[str] = []
        binding = self.binding_lookup.get_binding(incident.binding_id) if self.binding_lookup else None
        if binding is None:
            errors.append(
                f"binding_id {incident.binding_id!r} does not resolve through the canonical RuntimeBinding path"
            )
        else:
            errors.extend(_binding_mismatch_errors(incident, binding))
            errors.extend(_binding_window_errors(incident, binding))

        artifact_ref = f"{incident.artifact_id}@{incident.artifact_version}"
        if incident.lineage_ref:
            if incident.lineage_ref != artifact_ref:
                errors.append(
                    f"lineage_ref {incident.lineage_ref!r} must match artifact snapshot {artifact_ref!r}"
                )
            projection = self.telemetry_lookup.runtime_binding_projection(incident.binding_id) if self.telemetry_lookup else None
            if projection is None:
                errors.append(
                    f"lineage_ref {incident.lineage_ref!r} cannot be verified because no runtime binding lineage projection is available"
                )
            else:
                refs = projection.get("refs", {})
                if incident.lineage_ref not in refs.get("artifact_refs", []):
                    errors.append(
                        f"lineage_ref {incident.lineage_ref!r} is absent from the canonical lineage projection for binding {incident.binding_id!r}"
                    )

        for event_id in incident.telemetry_event_ids:
            trace = self.telemetry_lookup.telemetry_event_trace(event_id) if self.telemetry_lookup else None
            if trace is None:
                errors.append(
                    f"telemetry_event_id {event_id!r} does not resolve through the canonical telemetry lineage path"
                )
                continue
            errors.extend(_telemetry_trace_mismatch_errors(incident, trace, event_id))

        if errors:
            raise CanonicalReferenceError(errors)


def _binding_mismatch_errors(incident: IncidentCase, binding: Mapping[str, Any]) -> list[str]:
    comparisons = (
        ("deployment_stage", incident.deployment_stage, binding.get("deployment_mode")),
        ("deployment_plan_id", incident.deployment_plan_id, binding.get("plan_id")),
        ("capital_pool_id", incident.capital_pool_id, binding.get("capital_pool_id")),
        (
            "persona_capital_binding_id",
            incident.persona_capital_binding_id,
            binding.get("persona_capital_binding_id"),
        ),
        ("artifact_id", incident.artifact_id, binding.get("artifact_id")),
        ("artifact_version", incident.artifact_version, binding.get("artifact_version")),
        ("runtime_id", incident.runtime_id, binding.get("runtime_id")),
    )
    errors: list[str] = []
    for field_name, expected, observed in comparisons:
        if observed is None:
            errors.append(
                f"RuntimeBinding {incident.binding_id!r} did not expose {field_name} for incident validation"
            )
            continue
        if expected != observed:
            errors.append(
                f"{field_name} mismatch for binding {incident.binding_id!r}: incident={expected!r}, binding={observed!r}"
            )
    return errors


def _binding_window_errors(incident: IncidentCase, binding: Mapping[str, Any]) -> list[str]:
    created_at = _parse_rfc3339(incident.created_at)
    if created_at is None:
        return [f"incident created_at {incident.created_at!r} is not valid RFC3339 UTC"]

    effective_at = _parse_rfc3339(binding.get("effective_at"))
    retired_at = _parse_rfc3339(binding.get("retired_at"))
    errors: list[str] = []
    if effective_at and created_at < effective_at:
        errors.append(
            f"incident created_at {incident.created_at!r} is before RuntimeBinding {incident.binding_id!r} effective_at {binding.get('effective_at')!r}"
        )
    if retired_at and created_at > retired_at:
        errors.append(
            f"incident created_at {incident.created_at!r} is after RuntimeBinding {incident.binding_id!r} retired_at {binding.get('retired_at')!r}"
        )
    return errors


def _telemetry_trace_mismatch_errors(
    incident: IncidentCase,
    trace: Mapping[str, Any],
    event_id: str,
) -> list[str]:
    refs = trace.get("refs", {})
    artifact_ref = f"{incident.artifact_id}@{incident.artifact_version}"
    runtime_refs = {
        item.get("id")
        for item in list(trace.get("upstream_chain", [])) + list(trace.get("downstream_chain", []))
        if item.get("type") == "runtime_ref"
    }
    errors: list[str] = []

    expected_membership = (
        ("runtime_binding_ids", incident.binding_id),
        ("deployment_plan_ids", incident.deployment_plan_id),
        ("capital_pool_ids", incident.capital_pool_id),
        ("persona_capital_binding_ids", incident.persona_capital_binding_id),
        ("artifact_refs", artifact_ref),
    )
    for ref_key, expected in expected_membership:
        values = refs.get(ref_key, [])
        if expected not in values:
            errors.append(
                f"telemetry_event_id {event_id!r} does not carry {ref_key}={expected!r} in the canonical lineage trace"
            )

    if incident.runtime_id not in runtime_refs:
        errors.append(
            f"telemetry_event_id {event_id!r} does not carry runtime_ref {incident.runtime_id!r} in the canonical lineage trace"
        )

    trace_ids = refs.get("trace_ids", [])
    if trace_ids and incident.trace_id not in trace_ids:
        errors.append(
            f"telemetry_event_id {event_id!r} does not carry trace_id {incident.trace_id!r} in the canonical lineage trace"
        )
    return errors


def _parse_rfc3339(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None

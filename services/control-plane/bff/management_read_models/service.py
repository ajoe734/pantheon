"""Management domain service and read model operations.

Consolidates all business logic, aggregations, data adapters, and read model projections
for the Management System domain:
- Shell summary & session counts
- Operator home & overview cards
- Operator health status & secondary control path guidance
- Cockpit aggregate & KPIs
- Trading pulse & rankings
- Sentinel pulse
- Loop throughput metrics
- Risk radar indicators
- Incident timeline
- Human inbox & item details
- HIQ backlog
- Intervention stream
- Evidence explorer
- Operations read model
- Degraded control guidance
- Composed read models (formula jobs, activity audit, paper telemetry, postmortems)
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterator, List, Mapping, Optional, Sequence, Set, Tuple, Union

try:
    from fastapi import HTTPException
except ImportError:
    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: Any = None) -> None:
            super().__init__(f"HTTP {status_code}: {detail}")
            self.status_code = status_code
            self.detail = detail

try:
    from operations_read_model import (
        DataConfidence,
        SourceState,
        SourceStatus,
        SourceDiagnostic,
        OperationsIdentity,
        OperationsPerformance,
        OperationsReadModelEntry,
        OperationsReadModelEnvelope,
        sanitize_metric as ops_read_model_sanitize_metric,
        build_operations_identity,
        classify_confidence,
        dedupe_ids,
        diagnostic as ops_read_model_diagnostic,
    )
except ImportError:
    from services.control_plane.bff.operations_read_model import (  # type: ignore[no-redef]
        DataConfidence,
        SourceState,
        SourceStatus,
        SourceDiagnostic,
        OperationsIdentity,
        OperationsPerformance,
        OperationsReadModelEntry,
        OperationsReadModelEnvelope,
        sanitize_metric as ops_read_model_sanitize_metric,
        build_operations_identity,
        classify_confidence,
        dedupe_ids,
        diagnostic as ops_read_model_diagnostic,
    )

from pathlib import Path

try:
    from read_store import (
        redact_evidence_refs,
        EVIDENCE_CAPABILITY_MAP,
        SOURCE_TYPE_TO_EVIDENCE_KIND,
    )
except ImportError:
    try:
        from services.control_plane.bff.read_store import (  # type: ignore[no-redef]
            redact_evidence_refs,
            EVIDENCE_CAPABILITY_MAP,
            SOURCE_TYPE_TO_EVIDENCE_KIND,
        )
    except ImportError:
        redact_evidence_refs = None  # type: ignore[assignment]
        EVIDENCE_CAPABILITY_MAP = {}
        SOURCE_TYPE_TO_EVIDENCE_KIND = {}

try:
    from models import (
        ErrorCode,
        OperatorIdentity,
        EvidenceKind,
        RedactedEvidenceRef,
    )
except ImportError:
    try:
        from services.control_plane.bff.models import (  # type: ignore[no-redef]
            ErrorCode,
            OperatorIdentity,
            EvidenceKind,
            RedactedEvidenceRef,
        )
    except ImportError:
        class ErrorCode:  # type: ignore[no-redef]
            VALIDATION_FAILED = "VALIDATION_FAILED"
            AUTH_REQUIRED = "AUTH_REQUIRED"
            FORBIDDEN = "FORBIDDEN"
            OPERATION_NOT_ALLOWED = "OPERATION_NOT_ALLOWED"
            RESOURCE_NOT_FOUND = "RESOURCE_NOT_FOUND"
            RESOURCE_CONFLICT = "RESOURCE_CONFLICT"
            DEPENDENCY_UNAVAILABLE = "DEPENDENCY_UNAVAILABLE"
            INTERNAL_ERROR = "INTERNAL_ERROR"
        OperatorIdentity = None  # type: ignore[assignment]
        EvidenceKind = None  # type: ignore[assignment]
        RedactedEvidenceRef = None  # type: ignore[assignment]


def _default_bff_error(
    status_code: int,
    code: str,
    message: str,
    reason: Optional[str] = None,
    precondition_failed: Optional[str] = None,
    suggestion: Optional[str] = None,
    details_extra: Optional[Dict[str, Any]] = None,
) -> HTTPException:
    detail: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "reason": reason or message,
            "status_code": status_code,
        }
    }
    if precondition_failed:
        detail["error"]["details"] = {"precondition_failed": precondition_failed}
    if suggestion:
        detail["error"]["suggestion"] = suggestion
    if details_extra:
        detail["error"].setdefault("details", {}).update(details_extra)
    return HTTPException(status_code=status_code, detail=detail)


class ManagementValidationError(ValueError):
    def __init__(
        self,
        message: str,
        reason: Optional[str] = None,
        field: Optional[str] = None,
        status_code: int = 400,
        code: Optional[str] = None,
        details_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason or message
        self.field = field
        self.status_code = status_code
        self.code = code or ("VALIDATION_FAILED" if status_code in (400, 422) else ("FORBIDDEN" if status_code == 403 else ("IDEMPOTENCY_CONFLICT" if status_code == 409 else "INTERNAL_ERROR")))
        self.details_extra = details_extra

log = logging.getLogger(__name__)


def _utc_now_rfc3339() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _snapshot_meta(snapshot_at: Optional[str] = None) -> Dict[str, Any]:
    now = snapshot_at or _utc_now_rfc3339()
    return {
        "snapshot_at": now,
        "version": "v1",
    }


def _page_slice(
    items: Sequence[Any],
    page_token: Optional[str],
    page_size: int,
) -> Tuple[List[Any], Optional[str]]:
    start = 0
    if page_token:
        try:
            start = int(page_token)
        except (TypeError, ValueError):
            start = 0
    page_items = list(items[start: start + page_size])
    next_token = str(start + page_size) if start + page_size < len(items) else None
    return page_items, next_token


def _as_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (TypeError, ValueError):
        return None


def _parse_time(val: Any) -> datetime:
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
    if isinstance(val, str) and val.strip():
        try:
            clean = val.strip().replace("Z", "+00:00")
            dt = datetime.fromisoformat(clean)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.min.replace(tzinfo=timezone.utc)


def _aggregate_group_surface(
    group_name: str,
    surfaces: List[Dict[str, Any]],
    *,
    snapshot_at: Optional[str] = None,
    unavailable_message: Optional[str] = None,
    degraded_message: Optional[str] = None,
) -> Dict[str, Any]:
    now = snapshot_at or _utc_now_rfc3339()
    valid_surfaces = [s for s in surfaces if isinstance(s, dict)]
    if not valid_surfaces:
        return {
            "status": "unavailable",
            "source": "missing",
            "message": unavailable_message or f"{group_name} aggregate unavailable.",
            "snapshot_at": now,
        }

    statuses = [s.get("status", "ok") for s in valid_surfaces]
    if all(st == "unavailable" for st in statuses):
        return {
            "status": "unavailable",
            "source": "missing",
            "message": unavailable_message or f"{group_name} aggregate unavailable.",
            "snapshot_at": now,
        }
    if any(st in ("degraded", "unavailable") for st in statuses):
        return {
            "status": "degraded",
            "source": "bff_composed",
            "message": degraded_message or f"One or more contributing surfaces for {group_name} are degraded.",
            "snapshot_at": now,
        }
    return {
        "status": "ok",
        "source": "bff_composed",
        "snapshot_at": now,
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_HEALTH_GROUP_LABELS: Dict[str, str] = {
    "runtime": "Strategy Runtime Bindings",
    "telemetry": "Paper Execution Telemetry",
    "incident": "Active Incident Alerts",
    "governance": "Governance & Approvals",
    "kill_switch": "Kill Switch & Safe Mode",
}

_SECONDARY_CONTROL_PATH_ADVISORY_TARGETS: List[Dict[str, Any]] = [
    {
        "name": "admin_cli",
        "description": "Pantheon CLI for runtime status & diagnostics",
        "target": "pantheon-admin status --all",
    },
    {
        "name": "protected_internal_api",
        "description": "Control-plane internal health probe",
        "target": "/api/internal/v1/health",
    },
]

_SECONDARY_CONTROL_PATH_RECOMMENDED_TARGETS: List[Dict[str, Any]] = [
    {
        "name": "admin_cli",
        "description": "Pantheon CLI for emergency containment",
        "target": "pantheon-admin kill-switch activate --scope global",
    },
    {
        "name": "protected_internal_api",
        "description": "Control-plane direct secondary command path",
        "target": "/api/internal/v1/commands",
    },
]

_MANAGEMENT_SENTINEL_ACTIVE_STATUSES: Set[str] = {
    "active", "open", "triggered", "elevated", "warning", "critical"
}
_MANAGEMENT_SENTINEL_PENDING_INTERVENTION_STATUSES: Set[str] = {
    "pending_intervention", "action_required", "needs_review", "open", "pending"
}
_HUMAN_INBOX_PRIORITY_RANK: Dict[str, int] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

_SHELL_SUMMARY_COUNT_CACHE: Dict[str, Any] = {}
_SHELL_SUMMARY_COUNT_CACHE_LOCK = threading.Lock()

_ROLE_CAPABILITY_MAP: Dict[str, List[str]] = {
    "admin": list(EVIDENCE_CAPABILITY_MAP.values()) if EVIDENCE_CAPABILITY_MAP else [
        "metric.read", "job.read", "audit.read", "strategy.view", "persona.view",
        "runtime.read", "risk.incident.read", "risk.alert.read", "artifact.read",
        "approval.read", "postmortem.read", "policy.read",
    ],
    "approver": [
        "approval.read",
        "postmortem.read",
        "policy.read",
    ],
    "operator": [
        "runtime.read",
        "risk.incident.read",
        "risk.alert.read",
        "artifact.read",
    ],
    "reviewer": [
        "approval.read",
        "strategy.view",
        "persona.view",
    ],
    "analyst": [
        "metric.read",
        "job.read",
        "audit.read",
    ],
    "viewer": [
        "metric.read",
        "strategy.view",
        "persona.view",
    ],
}


def _capabilities_for_identity(identity: Any) -> Optional[List[str]]:
    if identity is None:
        return None
    explicit = getattr(identity, "capabilities", None)
    if isinstance(explicit, list):
        return explicit
    roles = getattr(identity, "roles", None)
    if isinstance(roles, (list, set, tuple)):
        caps: List[str] = []
        for role in roles:
            mapped = _ROLE_CAPABILITY_MAP.get(str(role))
            if mapped:
                caps.extend(mapped)
        seen = set()
        deduped = []
        for c in caps:
            if c not in seen:
                seen.add(c)
                deduped.append(c)
        return deduped
    return None


_KW03_LINKED_ENTITY_TYPES: Set[str] = {
    "memory_entry",
    "research_note",
    "insight_card",
    "strategy_spec",
    "experiment",
    "artifact",
}
_KW03_LINK_TYPES: Set[str] = {
    "supporting_evidence",
    "counter_evidence",
    "citation",
    "provenance",
    "corroboration",
}
_KW03_CREDIBILITY_TIERS: Set[str] = {"primary", "secondary", "tertiary", "unverified"}

_BFF_LIVE_EVIDENCE_VERIFY_JSON_ENV = "PANTHEON_BFF_LIVE_EVIDENCE_VERIFY_JSON"
_BFF_LIVE_EVIDENCE_VERIFY_JSON_NAME = "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY.json"
_BFF_LIVE_EVIDENCE_REF_ID = "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY"
_REPO_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)


def _kw03_validate_linked_entity_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW03_LINKED_ENTITY_TYPES:
        raise ManagementValidationError(
            "Invalid linked_entity_type",
            f"linked_entity_type must be one of {sorted(_KW03_LINKED_ENTITY_TYPES)}",
            field="linked_entity_type",
            status_code=400,
        )
    return normalized


def _kw03_validate_link_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW03_LINK_TYPES:
        raise ManagementValidationError(
            "Invalid link_type",
            f"link_type must be one of {sorted(_KW03_LINK_TYPES)}",
            field="link_type",
            status_code=400,
        )
    return normalized


def _kw03_validate_credibility_tier(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _KW03_CREDIBILITY_TIERS:
        raise ManagementValidationError(
            "Invalid credibility_tier",
            f"credibility_tier must be one of {sorted(_KW03_CREDIBILITY_TIERS)}",
            field="credibility_tier",
            status_code=400,
        )
    return normalized


def _management_live_evidence_verify_candidates() -> List[Path]:
    candidates: List[Path] = []
    explicit = str(os.getenv(_BFF_LIVE_EVIDENCE_VERIFY_JSON_ENV) or "").strip()
    if explicit:
        candidates.append(Path(explicit))
    audit_dir = str(os.getenv("PANTHEON_AUDIT_OUT_DIR") or "").strip()
    if audit_dir:
        candidates.append(Path(audit_dir) / _BFF_LIVE_EVIDENCE_VERIFY_JSON_NAME)
    candidates.append(
        Path(_REPO_ROOT)
        / ".lovable"
        / "audits"
        / "current-run"
        / _BFF_LIVE_EVIDENCE_VERIFY_JSON_NAME
    )
    deduped: List[Path] = []
    seen: Set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _management_optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _management_string_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    strings: List[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            strings.append(text)
    return strings


def _management_remediation_invalid_inputs(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    invalid_inputs: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if name or reason:
            invalid_inputs.append({"name": name, "reason": reason})
    return invalid_inputs


def _management_live_evidence_preflight_remediation(artifact_dir: Path) -> Optional[Dict[str, Any]]:
    preflight_path = artifact_dir / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
    try:
        payload = json.loads(preflight_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    remediation = payload.get("operator_remediation")
    if not isinstance(remediation, dict):
        return None
    workflow_dispatch = remediation.get("workflow_dispatch")
    if not isinstance(workflow_dispatch, dict):
        workflow_dispatch = {}
    safe_remediation = {
        "github_environment": _management_optional_text(remediation.get("github_environment")),
        "repository": _management_optional_text(remediation.get("repository")),
        "required_secret_names": _management_string_list(remediation.get("required_secret_names")),
        "missing_secret_names": _management_string_list(remediation.get("missing_secret_names")),
        "missing_workflow_inputs": _management_string_list(remediation.get("missing_workflow_inputs")),
        "invalid_inputs": _management_remediation_invalid_inputs(remediation.get("invalid_inputs")),
        "secret_set_commands": _management_string_list(remediation.get("secret_set_commands")),
        "workflow_dispatch": {
            "recommended_workflow": _management_optional_text(workflow_dispatch.get("recommended_workflow")),
            "mode": _management_optional_text(workflow_dispatch.get("mode")),
            "environment": _management_optional_text(workflow_dispatch.get("environment")),
            "run_command_template": _management_optional_text(workflow_dispatch.get("run_command_template")),
        },
        "notes": _management_string_list(remediation.get("notes")),
    }
    return safe_remediation


def _management_live_evidence_release_gate_summary(artifact_dir: Path) -> Optional[Dict[str, Any]]:
    summary_path = artifact_dir / "release-gate-summary.json"
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None

    checks: List[Dict[str, Any]] = []
    gates = payload.get("gates")
    if isinstance(gates, dict):
        for gate, gate_checks in sorted(gates.items(), key=lambda item: str(item[0])):
            if not isinstance(gate_checks, list):
                continue
            for index, check in enumerate(gate_checks):
                if not isinstance(check, dict):
                    continue
                status = _management_optional_text(check.get("status")) or "missing"
                label = _management_optional_text(check.get("label"))
                if not label and not status:
                    continue
                checks.append(
                    {
                        "gate": str(gate),
                        "index": index,
                        "label": label,
                        "status": status,
                        "note": _management_optional_text(check.get("note")),
                        "owner": _management_optional_text(check.get("owner")),
                        "evidence": _management_optional_text(check.get("evidence")),
                        "blocking": status != "pass",
                    }
                )

    return {
        "overall": _management_optional_text(payload.get("overall")),
        "generated_at": _management_optional_text(payload.get("generatedAt") or payload.get("generated_at")),
        "audit_dir": _management_optional_text(payload.get("auditDir") or payload.get("audit_dir")),
        "run_url": _management_optional_text(payload.get("runUrl") or payload.get("run_url")),
        "checklist_out": _management_optional_text(payload.get("checklistOut") or payload.get("checklist_out")),
        "open_check_count": sum(1 for check in checks if bool(check.get("blocking"))),
        "checks": checks,
    }


def _management_current_run_live_evidence_refs() -> List[Dict[str, Any]]:
    for candidate in _management_live_evidence_verify_candidates():
        try:
            if not candidate.is_file():
                continue
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        manifest = payload.get("artifact_manifest")
        if not isinstance(manifest, dict):
            continue
        criteria = payload.get("criteria")
        if not isinstance(criteria, dict):
            criteria = {}
        try:
            mtime_captured_at = datetime.fromtimestamp(
                candidate.stat().st_mtime,
                timezone.utc,
            ).isoformat().replace("+00:00", "Z")
        except OSError:
            mtime_captured_at = _utc_now_rfc3339()
        captured_at = payload.get("generated_at") or payload.get("created_at") or mtime_captured_at
        artifact_dir_path = Path(str(payload.get("artifact_dir") or candidate.parent))
        artifact_dir = str(artifact_dir_path)
        operator_remediation = _management_live_evidence_preflight_remediation(artifact_dir_path)
        release_gate_summary = _management_live_evidence_release_gate_summary(artifact_dir_path)
        evidence_ref = {
            "ref_id": _BFF_LIVE_EVIDENCE_REF_ID,
            "evidence_type": "workflow_artifact",
            "link_type": "provenance",
            "display_label": "BFF live evidence artifact verifier",
            "title": "Strict BFF live evidence artifact verifier",
            "source_type": "workflow_artifact",
            "source_ref": str(candidate),
            "captured_at": captured_at,
            "credibility": {
                "tier": "primary",
                "verified": payload.get("overall") == "pass",
            },
            "linked_object_summary": {
                "entity_type": "artifact",
                "entity_ref": _BFF_LIVE_EVIDENCE_REF_ID,
                "display_label": "Current-run BFF live evidence",
            },
            "resolved_link": {
                "availability": "available",
                "route_href": artifact_dir,
            },
            "route_href": str(candidate),
            "overall": payload.get("overall"),
            "artifact_manifest": json.loads(json.dumps(manifest)),
            "criteria": json.loads(json.dumps(criteria)),
            "created_at": captured_at,
        }
        if operator_remediation is not None:
            evidence_ref["operator_remediation"] = operator_remediation
        if release_gate_summary is not None:
            evidence_ref["release_gate_summary"] = release_gate_summary
        return [evidence_ref]
    return []


def _management_evidence_public_item(item: Dict[str, Any]) -> Dict[str, Any]:
    ref_id = str(item.get("ref_id") or item.get("id") or "").strip()
    display_label = item.get("display_label") or ref_id
    if item.get("redacted"):
        required_capability = item.get("required_capability")
        return {
            "id": ref_id,
            "ref_id": ref_id,
            "display_label": display_label,
            "kind": item.get("kind"),
            "required_capability": required_capability,
            "reason": item.get("reason"),
            "redacted": True,
        }

    source_document = item.get("source_document") if isinstance(item.get("source_document"), dict) else {}
    linked_summary = (
        item.get("linked_object_summary")
        if isinstance(item.get("linked_object_summary"), dict)
        else {}
    )
    resolved_link = item.get("resolved_link") if isinstance(item.get("resolved_link"), dict) else {}
    credibility = item.get("credibility") if isinstance(item.get("credibility"), dict) else {}
    source_type = source_document.get("source_type") or item.get("source_type") or item.get("sourceType") or "unknown"
    source_ref = source_document.get("source_ref") or item.get("source_ref") or item.get("sourceRef")
    captured_at = source_document.get("captured_at") or item.get("captured_at") or item.get("capturedAt")
    link_type = item.get("link_type")
    route_href = item.get("route_href") or (f"/knowledge/evidence/{ref_id}" if ref_id else None)
    title = source_document.get("title") or item.get("title") or display_label
    public_item: Dict[str, Any] = {
        "id": ref_id,
        "ref_id": ref_id,
        "title": title,
        "display_label": display_label,
        "source_type": source_type,
        "source_ref": source_ref,
        "captured_at": captured_at,
        "link_type": link_type,
        "credibility": json.loads(json.dumps(credibility)),
        "linked_object_summary": json.loads(json.dumps(linked_summary)),
        "resolved_link": json.loads(json.dumps(resolved_link)),
        "route_href": route_href,
        "management_href": f"/management/evidence?ref_id={ref_id}" if ref_id else None,
        "redacted": False,
    }
    artifact_manifest = item.get("artifact_manifest")
    if isinstance(artifact_manifest, dict):
        public_item["artifact_manifest"] = json.loads(json.dumps(artifact_manifest))
    criteria = item.get("criteria")
    if isinstance(criteria, dict):
        public_item["criteria"] = json.loads(json.dumps(criteria))
    operator_remediation = item.get("operator_remediation")
    if isinstance(operator_remediation, dict):
        public_item["operator_remediation"] = json.loads(json.dumps(operator_remediation))
    release_gate_summary = item.get("release_gate_summary")
    if isinstance(release_gate_summary, dict):
        public_item["release_gate_summary"] = json.loads(json.dumps(release_gate_summary))
    if "overall" in item:
        public_item["overall"] = item.get("overall")
    return public_item


def _management_evidence_summary(
    *,
    filtered_total: int,
    page_items: List[Dict[str, Any]],
    redacted_count: int,
) -> Dict[str, Any]:
    visible_items = [item for item in page_items if not item.get("redacted")]
    verified_count = len(
        [
            item
            for item in visible_items
            if bool((item.get("credibility") or {}).get("verified") if (item.get("credibility") or {}).get("verified") is not None else item.get("verified"))
        ]
    )
    by_source_type: Dict[str, int] = {}
    for item in visible_items:
        source_document = item.get("source_document") if isinstance(item.get("source_document"), dict) else {}
        source_type = (
            source_document.get("source_type")
            or item.get("source_type")
            or item.get("sourceType")
            or "unknown"
        )
        key = str(source_type or "unknown").strip() or "unknown"
        by_source_type[key] = by_source_type.get(key, 0) + 1

    by_link_type: Dict[str, int] = {}
    for item in visible_items:
        key = str(item.get("link_type") or "unknown").strip() or "unknown"
        by_link_type[key] = by_link_type.get(key, 0) + 1

    by_credibility_tier: Dict[str, int] = {}
    for item in visible_items:
        cred = item.get("credibility") if isinstance(item.get("credibility"), dict) else {}
        key = str(cred.get("tier") or item.get("credibility_tier") or "unknown").strip() or "unknown"
        by_credibility_tier[key] = by_credibility_tier.get(key, 0) + 1

    return {
        "total_evidence": filtered_total,
        "total_items": filtered_total,
        "returned_evidence": len(page_items),
        "visible_evidence": len(visible_items),
        "redacted_evidence": redacted_count,
        "verified_evidence": verified_count,
        "verified_count": verified_count,
        "by_source_type": by_source_type,
        "by_link_type": by_link_type,
        "by_credibility_tier": by_credibility_tier,
    }


def _management_evidence_degraded_payload(*, page_size: int = 20, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
    now = snapshot_at or _utc_now_rfc3339()
    summary = _management_evidence_summary(filtered_total=0, page_items=[], redacted_count=0)
    facets = {
        "source_types": summary["by_source_type"],
        "link_types": summary["by_link_type"],
        "credibility_tiers": summary["by_credibility_tier"],
    }
    meta = _snapshot_meta(now)
    meta["surfaces"] = {
        "management_evidence": {
            "status": "degraded",
            "source": "bff_composed",
            "message": "Evidence aggregation timed out under concurrent read fanout; degraded empty response returned.",
            "snapshot_at": now,
        }
    }
    meta["redacted_evidence_count"] = 0
    return {
        "data": {
            "id": "management-evidence",
            "items": [],
            "summary": summary,
            "facets": facets,
        },
        "page_info": {"next_page_token": None, "total": 0, "page_size": page_size},
        "meta": meta,
    }


def _human_inbox_detail_match(item: Dict[str, Any], item_id: str) -> bool:
    clean = str(item_id or "").strip()
    candidates = {
        str(item.get("id") or ""),
        str(item.get("inbox_id") or ""),
        str(item.get("source_id") or ""),
        str(item.get("item_id") or ""),
        str(item.get("approval_decision_id") or ""),
        str(item.get("decision_id") or ""),
        str(item.get("intervention_id") or ""),
        str(item.get("review_item_id") or ""),
        str(item.get("finding_id") or ""),
        str(item.get("persona_id") or ""),
    }
    return clean in candidates


_TRADING_PULSE_RANKING_METRIC_FIELDS: Dict[str, Tuple[str, ...]] = {
    "pnl": ("pnl",),
    "drawdown": ("drawdown",),
    "sharpe_ratio": ("sharpeRatio", "sharpe_ratio"),
    "fill_rate": ("fillRate", "fill_rate"),
    "avg_slippage_bps": ("avgSlippageBps", "avg_slippage_bps"),
    "total_trades": ("totalTrades", "total_trades"),
}
_TRADING_PULSE_DRIFT_BREACH_STATUSES = {"breached", "blocked", "critical", "fail", "failed"}
_TRADING_PULSE_DRIFT_WATCH_STATUSES = {"degraded", "warn", "warning", "watch"}
_TRADING_PULSE_METRICS = (
    "pnl",
    "drawdown",
    "sharpe_ratio",
    "fill_rate",
    "avg_slippage_bps",
    "total_trades",
)
_TRADING_PULSE_BASELINE_OPERATOR_ORDER = {
    "breached": 0,
    "blocked": 0,
    "critical": 0,
    "failed": 0,
    "watch": 1,
    "degraded": 1,
    "warning": 1,
    "warn": 1,
    "unavailable": 2,
    "unknown": 3,
    "ok": 4,
}


def _management_number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _management_avg(values: List[float]) -> Optional[float]:
    return round(sum(values) / len(values), 6) if values else None


def _management_count_by(records: List[Dict[str, Any]], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        value = str(record.get(field) or "unknown").strip() or "unknown"
        counts[value] = counts.get(value, 0) + 1
    return counts


def _management_json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value))


def _runtime_state_row_health_check(
    status: str,
    *,
    source: str,
    message: Optional[str] = None,
    applies: bool = True,
) -> Dict[str, Any]:
    check: Dict[str, Any] = {
        "status": status,
        "source": source,
        "applies": applies,
    }
    if message:
        check["message"] = message
    return check


def _runtime_state_monitoring_terminal_reason(
    session: Optional[Dict[str, Any]],
) -> Optional[str]:
    if not session:
        return None
    for key in ("terminal_reason", "ended_reason"):
        value = str(session.get(key) or "").strip()
        if value:
            return value
    staleness = session.get("staleness")
    if isinstance(staleness, dict):
        reason = str(staleness.get("reason") or "").strip()
        if reason:
            return reason
        status = str(staleness.get("status") or "").strip().lower()
        if status == "stale":
            return "stale_monitoring_session"
    status = str(session.get("status") or "").strip().lower()
    if status in {"ended", "stale", "failed"}:
        return status
    return None


def _runtime_state_monitoring_health_check(
    monitoring_session: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if monitoring_session is None:
        return _runtime_state_row_health_check(
            "unavailable",
            source="paper_runtime_monitoring_sessions",
            message="Paper runtime monitoring session is unavailable for this runtime.",
        )
    terminal_reason = _runtime_state_monitoring_terminal_reason(monitoring_session)
    inactive = monitoring_session.get("active") is False
    ended = monitoring_session.get("ended_at") not in (None, "")
    if terminal_reason or inactive or ended:
        reason = terminal_reason or "inactive_monitoring_session"
        return _runtime_state_row_health_check(
            "degraded",
            source="paper_runtime_monitoring_sessions",
            message=f"Paper runtime monitoring session is terminal: {reason}.",
        )
    return _runtime_state_row_health_check(
        "ok",
        source="paper_runtime_monitoring_sessions",
    )


def _derive_runtime_state_row_health(
    *,
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
    monitoring_session: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    deployment_stage = str(
        binding.get("deployment_stage") or binding.get("deployment_mode") or ""
    ).lower()
    checks: Dict[str, Dict[str, Any]] = {
        "runtime_binding": _runtime_state_row_health_check(
            "ok",
            source="runtime_bindings",
        ),
        "telemetry_summary": (
            _runtime_state_row_health_check("ok", source="telemetry_summaries")
            if telemetry_summary is not None
            else _runtime_state_row_health_check(
                "unavailable",
                source="telemetry_summaries",
                message="Telemetry summary row is unavailable for this runtime.",
            )
        ),
    }
    if deployment_stage == "paper":
        checks["paper_runtime_monitoring"] = _runtime_state_monitoring_health_check(
            monitoring_session
        )
    else:
        checks["paper_runtime_monitoring"] = _runtime_state_row_health_check(
            "ok",
            source="not_applicable",
            applies=False,
            message="Paper runtime monitoring applies only to paper runtimes.",
        )

    degraded_checks = [
        key
        for key, check in checks.items()
        if check.get("applies", True) and check.get("status") != "ok"
    ]
    return {
        "status": "degraded" if degraded_checks else "ok",
        "checks": checks,
        "degraded_checks": degraded_checks,
    }


def _derive_runtime_state_last_updated_at(
    binding: Dict[str, Any],
    telemetry_summary: Optional[Dict[str, Any]],
    latest_rollback: Optional[Dict[str, Any]],
    monitoring_session: Optional[Dict[str, Any]],
) -> Optional[str]:
    candidates = [
        binding.get("last_updated_at"),
        binding.get("updated_at"),
        binding.get("started_at"),
        binding.get("created_at"),
        (telemetry_summary or {}).get("last_heartbeat_at"),
        (telemetry_summary or {}).get("last_event_at"),
        (telemetry_summary or {}).get("collected_at"),
        (latest_rollback or {}).get("completed_at"),
        (latest_rollback or {}).get("initiated_at"),
        (monitoring_session or {}).get("last_heartbeat_at"),
        (monitoring_session or {}).get("ended_at"),
        (monitoring_session or {}).get("started_at"),
    ]
    values = [candidate for candidate in candidates if candidate]
    if not values:
        return None
    return max(values)


def _project_runtime_state_telemetry_summary(summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not summary:
        return None
    projected = {
        "window": summary.get("window"),
        "collected_at": summary.get("collected_at"),
        "metrics": {
            "pnl": summary.get("pnl"),
            "drawdown": summary.get("drawdown"),
            "sharpe_ratio": summary.get("sharpe_ratio"),
            "fill_rate": summary.get("fill_rate"),
            "avg_slippage_bps": summary.get("avg_slippage_bps") or summary.get("avg_slippage"),
            "total_trades": summary.get("total_trades"),
        },
    }
    for key in (
        "runtime_binding_id",
        "binding_id",
        "deployment_stage",
        "state",
        "last_heartbeat_at",
        "last_event_at",
        "last_event_type",
        "engine_bridge_repo",
        "engine_bridge_commit",
        "engine_bridge_path",
        "runtime_adapter_version",
        "health_summary",
        "projection_source",
        "projection_updated_at",
        "staleness",
        "executed_trade_count",
        "position_count",
        "positions",
        "last_fill",
    ):
        if key in summary:
            projected[key] = summary.get(key)
    return projected


def _project_runtime_state_monitoring_session(session: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not session:
        return None
    projected: Dict[str, Any] = {}
    for key in (
        "session_id",
        "session_type",
        "binding_id",
        "runtime_binding_id",
        "runtime_id",
        "deployment_stage",
        "status",
        "active",
        "started_at",
        "ended_at",
        "ended_reason",
        "terminal_reason",
        "last_heartbeat_at",
        "heartbeat_status",
        "stale_after_seconds",
        "restart_count",
        "staleness",
        "last_error",
    ):
        if key in session:
            projected[key] = session.get(key)
    terminal_reason = _runtime_state_monitoring_terminal_reason(session)
    if terminal_reason and "terminal_reason" not in projected:
        projected["terminal_reason"] = terminal_reason
    return projected


def _project_runtime_state_latest_rollback(rollbacks: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not rollbacks:
        return None
    latest = max(
        rollbacks,
        key=lambda rollback: (
            rollback.get("completed_at")
            or rollback.get("executed_at")
            or rollback.get("initiated_at")
            or ""
        ),
    )
    return {
        "rollback_id": latest.get("rollback_id") or latest.get("id"),
        "action_type": latest.get("action_type"),
        "status": latest.get("status"),
        "from_version": latest.get("from_version"),
        "to_version": latest.get("to_version"),
        "initiated_at": latest.get("initiated_at"),
        "completed_at": latest.get("completed_at") or latest.get("executed_at"),
    }


def _project_operator_runtime_state_row(
    store: Optional[Any],
    binding: Dict[str, Any],
    *,
    telemetry_summary_record: Optional[Dict[str, Any]] = None,
    monitoring_session_record: Optional[Dict[str, Any]] = None,
    prefetched: bool = False,
) -> Dict[str, Any]:
    runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
    runtime_binding_id = (
        binding.get("runtime_binding_id")
        or binding.get("binding_id")
        or binding.get("id")
    )
    raw_telemetry_summary = (
        telemetry_summary_record
        if prefetched
        else (store.get_telemetry_summary(runtime_id) if store and hasattr(store, "get_telemetry_summary") else None)
    )
    telemetry_summary = _project_runtime_state_telemetry_summary(raw_telemetry_summary)
    raw_monitoring_session = (
        monitoring_session_record
        if prefetched
        else (
            store.get_paper_runtime_monitoring_session(
                runtime_id=runtime_id,
                binding_id=str(runtime_binding_id or ""),
            )
            if store and hasattr(store, "get_paper_runtime_monitoring_session")
            else None
        )
    )
    monitoring_session = _project_runtime_state_monitoring_session(raw_monitoring_session)
    rollbacks: List[Dict[str, Any]] = []
    if store and hasattr(store, "get_rollbacks"):
        try:
            rollbacks = store.get_rollbacks(runtime_id) or []
        except Exception:
            rollbacks = []
    latest_rollback = _project_runtime_state_latest_rollback(rollbacks)
    artifact_id = binding.get("artifact_id")
    artifact_version = binding.get("artifact_version") or binding.get("version")
    plan_id = binding.get("plan_id")

    return {
        "runtime_id": runtime_id,
        "runtime_binding_id": runtime_binding_id,
        "deployment_stage": binding.get("deployment_stage") or binding.get("deployment_mode"),
        "status": binding.get("status"),
        "capital_pool_id": binding.get("capital_pool_id"),
        "plan_ref": (
            {
                "plan_id": plan_id,
                "href": f"/deployment/review?plan_id={plan_id}",
            }
            if plan_id
            else None
        ),
        "artifact_ref": (
            {
                "artifact_id": artifact_id,
                "artifact_version": artifact_version,
            }
            if artifact_id or artifact_version
            else None
        ),
        "telemetry_summary": telemetry_summary,
        "executed_trade_count": (telemetry_summary or {}).get("executed_trade_count"),
        "total_trades": ((telemetry_summary or {}).get("metrics") or {}).get("total_trades"),
        "position_count": (telemetry_summary or {}).get("position_count"),
        "positions": (telemetry_summary or {}).get("positions"),
        "last_fill": (telemetry_summary or {}).get("last_fill"),
        "paper_runtime_monitoring": monitoring_session,
        "row_health": _derive_runtime_state_row_health(
            binding=binding,
            telemetry_summary=telemetry_summary,
            monitoring_session=monitoring_session,
        ),
        "rollback_summary": {
            "count": len(rollbacks),
            "latest": latest_rollback,
            "href": f"/api/v1/runtimes/{runtime_id}/rollbacks",
        },
        "last_updated_at": _derive_runtime_state_last_updated_at(
            binding,
            telemetry_summary,
            latest_rollback,
            monitoring_session,
        ),
    }


def _trading_pulse_baseline_status(report: Optional[Dict[str, Any]]) -> str:
    if not report:
        return "unavailable"
    threshold = report.get("threshold_evaluation")
    if not isinstance(threshold, dict):
        return "unknown"
    status = str(threshold.get("overall_status") or threshold.get("status") or "").lower()
    if status in _TRADING_PULSE_DRIFT_BREACH_STATUSES:
        return "breached"
    if status in _TRADING_PULSE_DRIFT_WATCH_STATUSES:
        return "watch"
    if status in {"ok", "pass", "passed", "within_threshold", "within-threshold"}:
        return "ok"
    return status or "unknown"


def _trading_pulse_drift_metric_counts(drift_groups: Any) -> Dict[str, int]:
    metric_count = 0
    breached_metric_count = 0
    watch_metric_count = 0
    groups = drift_groups if isinstance(drift_groups, list) else []
    for group in groups:
        metrics = group.get("metrics") if isinstance(group, dict) else None
        if not isinstance(metrics, list):
            continue
        for metric in metrics:
            if not isinstance(metric, dict):
                continue
            metric_count += 1
            status = str(metric.get("status") or "").lower()
            if status in _TRADING_PULSE_DRIFT_BREACH_STATUSES:
                breached_metric_count += 1
            elif status in _TRADING_PULSE_DRIFT_WATCH_STATUSES:
                watch_metric_count += 1
    return {
        "metricCount": metric_count,
        "metric_count": metric_count,
        "breachedMetricCount": breached_metric_count,
        "breached_metric_count": breached_metric_count,
        "watchMetricCount": watch_metric_count,
        "watch_metric_count": watch_metric_count,
    }


def _build_trading_pulse_baseline_comparison(
    store: Optional[Any],
    row: Dict[str, Any],
    *,
    drift_report: Optional[Dict[str, Any]] = None,
    prefetched: bool = False,
) -> Dict[str, Any]:
    runtime_id = str(row.get("runtime_id") or "").strip()
    report = (
        drift_report
        if prefetched
        else (
            store.get_paper_live_drift_report(runtime_id)
            if store and hasattr(store, "get_paper_live_drift_report")
            else None
        )
    )
    status = _trading_pulse_baseline_status(report)
    drift_groups = (report or {}).get("drift_groups") or []
    metric_counts = _trading_pulse_drift_metric_counts(drift_groups)
    threshold_evaluation = (
        _management_json_clone((report or {}).get("threshold_evaluation"))
        if report
        else {
            "overall_status": "unavailable",
            "summary": "Paper/live baseline comparison unavailable for this runtime.",
            "breached_metric_ids": [],
        }
    )
    paper_live_drift = {
        "available": report is not None,
        "status": status,
        "href": f"/api/v1/operator/paper-live-drift/{runtime_id}" if runtime_id else None,
    }
    return {
        "runtimeId": runtime_id or None,
        "runtime_id": runtime_id or None,
        "runtimeBindingId": row.get("runtime_binding_id"),
        "runtime_binding_id": row.get("runtime_binding_id"),
        "deploymentStage": row.get("deployment_stage"),
        "deployment_stage": row.get("deployment_stage"),
        "status": status,
        "paperLiveDrift": paper_live_drift,
        "paper_live_drift": paper_live_drift,
        "paperBaseline": _management_json_clone((report or {}).get("paper_baseline"))
        if report
        else None,
        "paper_baseline": _management_json_clone((report or {}).get("paper_baseline"))
        if report
        else None,
        "observedState": _management_json_clone((report or {}).get("observed_state"))
        if report
        else None,
        "observed_state": _management_json_clone((report or {}).get("observed_state"))
        if report
        else None,
        "driftGroups": _management_json_clone(drift_groups),
        "drift_groups": _management_json_clone(drift_groups),
        "thresholdEvaluation": threshold_evaluation,
        "threshold_evaluation": threshold_evaluation,
        **metric_counts,
    }


def _trading_pulse_runtime_id(record: Dict[str, Any]) -> str:
    return str(record.get("runtime_id") or record.get("runtimeId") or record.get("id") or "").strip()


def _trading_pulse_binding_id(record: Dict[str, Any]) -> str:
    return str(
        record.get("runtime_binding_id")
        or record.get("runtimeBindingId")
        or record.get("binding_id")
        or record.get("bindingId")
        or ""
    ).strip()


def _trading_pulse_index_by_runtime(records: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    by_runtime: Dict[str, Dict[str, Any]] = {}
    for record in records:
        runtime_id = _trading_pulse_runtime_id(record)
        if runtime_id and runtime_id not in by_runtime:
            by_runtime[runtime_id] = record
    return by_runtime


def _trading_pulse_monitoring_indexes(
    sessions: List[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    by_runtime: Dict[str, Dict[str, Any]] = {}
    by_binding: Dict[str, Dict[str, Any]] = {}
    for session in sessions:
        runtime_id = _trading_pulse_runtime_id(session)
        binding_id = _trading_pulse_binding_id(session)
        if runtime_id and runtime_id not in by_runtime:
            by_runtime[runtime_id] = session
        if binding_id and binding_id not in by_binding:
            by_binding[binding_id] = session
    return by_runtime, by_binding


def _trading_pulse_stage(row: Dict[str, Any]) -> str:
    return str(row.get("deployment_stage") or row.get("deploymentStage") or "").strip().lower()


def _trading_pulse_metric_map(row: Dict[str, Any]) -> Dict[str, Any]:
    telemetry = row.get("telemetry_summary") if isinstance(row.get("telemetry_summary"), dict) else {}
    metrics = telemetry.get("metrics") if isinstance(telemetry.get("metrics"), dict) else {}
    return metrics if isinstance(metrics, dict) else {}


def _trading_pulse_row_health(row: Dict[str, Any]) -> Dict[str, Any]:
    health = row.get("row_health") if isinstance(row.get("row_health"), dict) else {}
    return health if isinstance(health, dict) else {}


def _trading_pulse_row_health_status(row: Dict[str, Any]) -> str:
    return str(_trading_pulse_row_health(row).get("status") or "unknown").strip().lower() or "unknown"


def _trading_pulse_row_degraded_checks(row: Dict[str, Any]) -> List[str]:
    checks = _trading_pulse_row_health(row).get("degraded_checks")
    if not isinstance(checks, list):
        return []
    return [str(check) for check in checks if str(check or "").strip()]


def _trading_pulse_status_counts(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        status = _trading_pulse_row_health_status(row)
        counts[status] = counts.get(status, 0) + 1
    return counts


def _trading_pulse_missing_metric_runtime_ids(
    rows: List[Dict[str, Any]],
    metric: str,
) -> List[str]:
    missing: List[str] = []
    for row in rows:
        metrics = _trading_pulse_metric_map(row)
        if _management_number(metrics.get(metric)) is not None:
            continue
        runtime_id = str(row.get("runtime_id") or "").strip()
        if runtime_id:
            missing.append(runtime_id)
    return missing


def _trading_pulse_metric_coverage(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    coverage: Dict[str, Dict[str, Any]] = {}
    total = len(rows)
    for metric in _TRADING_PULSE_METRICS:
        missing_runtime_ids = _trading_pulse_missing_metric_runtime_ids(rows, metric)
        available_count = total - len(missing_runtime_ids)
        coverage[metric] = {
            "available_count": available_count,
            "missing_count": len(missing_runtime_ids),
            "missing_runtime_ids": missing_runtime_ids,
        }
    return coverage


def _trading_pulse_coverage_summary(
    rows: List[Dict[str, Any]],
    baseline_comparisons: List[Dict[str, Any]],
) -> Dict[str, Any]:
    runtime_ids = [str(row.get("runtime_id") or "").strip() for row in rows if row.get("runtime_id")]
    telemetry_runtime_ids = [
        str(row.get("runtime_id") or "").strip()
        for row in rows
        if isinstance(row.get("telemetry_summary"), dict)
    ]
    paper_rows = [row for row in rows if _trading_pulse_stage(row) == "paper"]
    monitoring_runtime_ids = [
        str(row.get("runtime_id") or "").strip()
        for row in paper_rows
        if isinstance(row.get("paper_runtime_monitoring"), dict)
    ]
    baseline_runtime_ids = [
        str(comparison.get("runtime_id") or comparison.get("runtimeId") or "").strip()
        for comparison in baseline_comparisons
        if ((comparison.get("paper_live_drift") or {}).get("available") is True)
    ]
    degraded_runtime_ids = [
        str(row.get("runtime_id") or "").strip()
        for row in rows
        if _trading_pulse_row_health_status(row) != "ok"
    ]
    missing_telemetry_runtime_ids = sorted(set(runtime_ids) - set(telemetry_runtime_ids))
    missing_monitoring_runtime_ids = sorted(
        {
            str(row.get("runtime_id") or "").strip()
            for row in paper_rows
            if not isinstance(row.get("paper_runtime_monitoring"), dict)
        }
    )
    missing_baseline_runtime_ids = sorted(set(runtime_ids) - set(baseline_runtime_ids))
    row_health_status_counts = _trading_pulse_status_counts(rows)
    metric_coverage = _trading_pulse_metric_coverage(rows)
    return {
        "runtime_count": len(rows),
        "paper_runtime_count": len(paper_rows),
        "telemetry_coverage_count": len(telemetry_runtime_ids),
        "monitoring_coverage_count": len(monitoring_runtime_ids),
        "baseline_comparison_count": len(baseline_runtime_ids),
        "missing_telemetry_runtime_ids": missing_telemetry_runtime_ids,
        "missing_monitoring_runtime_ids": missing_monitoring_runtime_ids,
        "missing_baseline_runtime_ids": missing_baseline_runtime_ids,
        "row_health_status_counts": row_health_status_counts,
        "row_health_degraded_count": len(degraded_runtime_ids),
        "degraded_runtime_ids": degraded_runtime_ids,
        "metric_coverage": metric_coverage,
    }


def _trading_pulse_row_health_surface(
    rows: List[Dict[str, Any]],
    *,
    snapshot_at: str,
) -> Dict[str, Any]:
    surface: Dict[str, Any] = {
        "status": "ok",
        "source": "bff_composed",
        "snapshot_at": snapshot_at,
    }
    degraded_count = sum(1 for row in rows if _trading_pulse_row_health_status(row) != "ok")
    if degraded_count:
        surface["status"] = "degraded"
        surface["message"] = (
            f"Trading pulse row health is degraded for {degraded_count} runtime(s)."
        )
    return surface


def _trading_pulse_operator_row_sort_key(row: Dict[str, Any]) -> Tuple[int, int, float, float, str]:
    health_status = _trading_pulse_row_health_status(row)
    health_priority = 1 if health_status == "ok" else 0
    baseline = row.get("baseline_comparison") if isinstance(row.get("baseline_comparison"), dict) else {}
    baseline_status = str(baseline.get("status") or "unknown").strip().lower() or "unknown"
    baseline_priority = _TRADING_PULSE_BASELINE_OPERATOR_ORDER.get(baseline_status, 3)
    pnl = _management_number(_trading_pulse_metric_map(row).get("pnl"))
    pnl_priority = -(abs(pnl) if pnl is not None else -1.0)
    last_updated_dt = _parse_time(row.get("last_updated_at"))
    last_updated_priority = -(last_updated_dt.timestamp()) if last_updated_dt != datetime.min.replace(tzinfo=timezone.utc) else float("inf")
    runtime_id = str(row.get("runtime_id") or "")
    return (health_priority, baseline_priority, pnl_priority, last_updated_priority, runtime_id)


def _trading_pulse_sort_operator_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=_trading_pulse_operator_row_sort_key)


def _trading_pulse_metric_value(
    item: Dict[str, Any],
    metric: str,
) -> Optional[float]:
    for field in _TRADING_PULSE_RANKING_METRIC_FIELDS.get(metric, (metric,)):
        value = _management_number(item.get(field))
        if value is not None:
            return value
    return None


def _trading_pulse_ranked_items(
    rankings: List[Dict[str, Any]],
    *,
    metric: str,
    descending: bool,
    limit: int,
    block_id: str,
) -> List[Dict[str, Any]]:
    present = [
        item for item in rankings
        if _trading_pulse_metric_value(item, metric) is not None
    ]
    ordered_present = sorted(
        present,
        key=lambda item: (
            _trading_pulse_metric_value(item, metric) or 0.0,
            str(item.get("runtimeId") or item.get("runtime_id") or ""),
        ),
        reverse=descending,
    )

    ranked: List[Dict[str, Any]] = []
    for index, item in enumerate(ordered_present[:limit], start=1):
        projected = dict(item)
        projected["rank"] = index
        projected["ranking_block_id"] = block_id
        projected["ranking_metric"] = metric
        projected["ranking_metric_value"] = _trading_pulse_metric_value(item, metric)
        projected["ranking_eligible"] = True
        ranked.append(projected)
    return ranked


def _trading_pulse_ranking_missing_runtime_ids(
    rankings: List[Dict[str, Any]],
    metric: str,
) -> List[str]:
    return [
        str(item.get("runtimeId") or item.get("runtime_id") or "")
        for item in rankings
        if _trading_pulse_metric_value(item, metric) is None
        and str(item.get("runtimeId") or item.get("runtime_id") or "").strip()
    ]


def _build_management_trading_pulse_ranking_block(
    rankings: List[Dict[str, Any]],
    *,
    block_id: str,
    label: str,
    metric: str,
    descending: bool,
    limit: int,
    secondary_metric: Optional[str] = None,
) -> Dict[str, Any]:
    items = _trading_pulse_ranked_items(
        rankings,
        metric=metric,
        descending=descending,
        limit=limit,
        block_id=block_id,
    )
    missing_runtime_ids = _trading_pulse_ranking_missing_runtime_ids(rankings, metric)
    block: Dict[str, Any] = {
        "block_id": block_id,
        "label": label,
        "metric": metric,
        "sort_order": "desc" if descending else "asc",
        "items": items,
        "eligible_item_count": len(rankings) - len(missing_runtime_ids),
        "missing_metric_count": len(missing_runtime_ids),
        "missing_metric_runtime_ids": missing_runtime_ids,
    }
    if secondary_metric:
        block["secondary_metric"] = secondary_metric
    return block


def _build_management_trading_pulse_ranking_blocks(
    rankings: List[Dict[str, Any]],
    *,
    limit: int,
) -> List[Dict[str, Any]]:
    return [
        _build_management_trading_pulse_ranking_block(
            rankings,
            block_id="pnl-leaders",
            label="P&L Leaders",
            metric="pnl",
            descending=True,
            limit=limit,
        ),
        _build_management_trading_pulse_ranking_block(
            rankings,
            block_id="drawdown-control",
            label="Drawdown Control",
            metric="drawdown",
            descending=False,
            limit=limit,
        ),
        _build_management_trading_pulse_ranking_block(
            rankings,
            block_id="execution-quality",
            label="Execution Quality",
            metric="fill_rate",
            descending=True,
            limit=limit,
            secondary_metric="avg_slippage_bps",
        ),
        _build_management_trading_pulse_ranking_block(
            rankings,
            block_id="sharpe-leaders",
            label="Sharpe Leaders",
            metric="sharpe_ratio",
            descending=True,
            limit=limit,
        ),
    ]


# ---------------------------------------------------------------------------
# Management Domain Service Class
# ---------------------------------------------------------------------------

class ManagementService:
    """Consolidated management business domain operations and aggregators."""

    def __init__(
        self,
        get_read_store: Optional[Callable[[], Any]] = None,
        utc_now: Optional[Callable[[], str]] = None,
        ops_read_model_entry_fn: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._get_read_store = get_read_store
        self._utc_now = utc_now or _utc_now_rfc3339
        self._ops_read_model_entry_fn = ops_read_model_entry_fn

    def _resolve_store(self) -> Optional[Any]:
        if self._get_read_store is not None:
            try:
                return self._get_read_store()
            except Exception:
                return None
        return None

    # -----------------------------------------------------------------------
    # 1. Shell Summary
    # -----------------------------------------------------------------------
    def build_shell_summary_counts(self, ttl_seconds: float = 5.0) -> Dict[str, Any]:
        now_str = self._utc_now()
        now_mono = time.monotonic()
        store = self._resolve_store()

        with _SHELL_SUMMARY_COUNT_CACHE_LOCK:
            cached = _SHELL_SUMMARY_COUNT_CACHE.get("latest")
            if cached and (now_mono - cached.get("cached_at", 0.0) < ttl_seconds):
                if cached.get("store_id") == id(store):
                    return cached["payload"]

        pending_approvals = 0
        running_jobs = 0
        open_alerts = 0
        surfaces: Dict[str, Any] = {}

        if store is not None:
            # 1. Approvals
            try:
                if hasattr(store, "list_approval_queue_items"):
                    items = store.list_approval_queue_items() or []
                    pending_approvals += sum(1 for r in items if isinstance(r, dict) and (r.get("decision_state") in ("pending", "open", "in_review") or r.get("status") in ("pending", "open", "in_review")))
                if hasattr(store, "list_governance_review_queue_items"):
                    items = store.list_governance_review_queue_items() or []
                    pending_approvals += sum(1 for r in items if isinstance(r, dict) and r.get("status") in ("pending", "open", "in_review"))
                if not hasattr(store, "list_approval_queue_items") and not hasattr(store, "list_governance_review_queue_items"):
                    if hasattr(store, "list_approval_records"):
                        records = store.list_approval_records() or []
                        pending_approvals += sum(1 for r in records if isinstance(r, dict) and r.get("status") in ("pending", "in_review", "open"))
                surfaces["governance_approvals"] = {"status": "ok", "source": "store"}
            except Exception:
                surfaces["governance_approvals"] = {"status": "unavailable", "source": "error"}

            # 2. Jobs
            try:
                if hasattr(store, "list_jobs_bff"):
                    jobs = store.list_jobs_bff() or []
                    running_jobs = sum(1 for j in jobs if isinstance(j, dict) and j.get("status") in ("running", "admitted", "pending"))
                    surfaces["jobs_read_model"] = {"status": "ok", "source": "store"}
                elif hasattr(store, "list_records"):
                    res = store.list_records("jobs")
                    avail, jobs = res if isinstance(res, tuple) else (True, res)
                    if avail:
                        running_jobs = sum(1 for j in (jobs or []) if isinstance(j, dict) and j.get("status") in ("running", "admitted", "pending"))
                        surfaces["jobs_read_model"] = {"status": "ok", "source": "store"}
                    else:
                        surfaces["jobs_read_model"] = {"status": "degraded", "source": "missing"}
                else:
                    surfaces["jobs_read_model"] = {"status": "ok", "source": "store"}
            except Exception:
                surfaces["jobs_read_model"] = {"status": "unavailable", "source": "error"}

            # 3. Alerts / Incidents
            try:
                if hasattr(store, "list_incidents"):
                    incidents = store.list_incidents() or []
                    open_alerts += sum(1 for a in incidents if isinstance(a, dict) and a.get("status") in ("open", "active", "triggered", "elevated"))
                elif hasattr(store, "list_incident_alerts"):
                    alerts = store.list_incident_alerts() or []
                    open_alerts += sum(1 for a in alerts if isinstance(a, dict) and a.get("status") in ("open", "active", "triggered", "elevated"))
                elif hasattr(store, "list_records"):
                    res = store.list_records("incident_alerts")
                    avail, alerts = res if isinstance(res, tuple) else (True, res)
                    if avail:
                        open_alerts += sum(1 for a in (alerts or []) if isinstance(a, dict) and a.get("status") in ("open", "active", "triggered", "elevated"))
                surfaces["incident_alerts"] = {"status": "ok", "source": "store"}
            except Exception:
                surfaces["incident_alerts"] = {"status": "unavailable", "source": "error"}
        else:
            surfaces["governance_approvals"] = {"status": "unavailable", "source": "missing"}
            surfaces["jobs_read_model"] = {"status": "unavailable", "source": "missing"}
            surfaces["incident_alerts"] = {"status": "unavailable", "source": "missing"}

        shell_surface = _aggregate_group_surface("shell_summary", list(surfaces.values()), snapshot_at=now_str)
        payload = {
            "counts": {
                "pending_approvals": pending_approvals,
                "running_jobs": running_jobs,
                "open_alerts": open_alerts,
            },
            "snapshot_at": now_str,
            "surfaces": {
                "shell_summary": shell_surface,
                **surfaces,
            },
        }

        with _SHELL_SUMMARY_COUNT_CACHE_LOCK:
            _SHELL_SUMMARY_COUNT_CACHE["latest"] = {
                "cached_at": now_mono,
                "store_id": id(store),
                "payload": payload,
            }
        return payload

    def get_shell_summary(self, identity: Any) -> Dict[str, Any]:
        count_payload = self.build_shell_summary_counts()
        checked_at = self._utc_now()
        op_id = getattr(identity, "operator_id", None) or getattr(identity, "user_id", "op-user")
        roles = list(getattr(identity, "roles", ["operator"]))
        session_kind = getattr(identity, "session_kind", "bearer")

        session_info = {
            "operator_id": str(op_id),
            "operatorId": str(op_id),
            "display_name": getattr(identity, "display_name", str(op_id)),
            "displayLabel": getattr(identity, "display_name", str(op_id)),
            "roles": roles,
            "session_kind": session_kind,
            "sessionKind": session_kind,
            "state": "active",
            "fresh": True,
            "mfa_verified": bool(getattr(identity, "mfa_verified", False)),
            "checked_at": checked_at,
        }

        return {
            "data": {
                "counts": count_payload["counts"],
                "session": session_info,
                "transport": {
                    "bff_status": "ok",
                    "service": "operator-bff",
                    "api_version": "0.2.0",
                },
            },
            "meta": {
                "snapshot_at": count_payload["snapshot_at"],
                "surfaces": count_payload["surfaces"],
            },
        }

    # -----------------------------------------------------------------------
    # 2. Operator Health Status
    # -----------------------------------------------------------------------
    def get_operator_health_status(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        store = self._resolve_store()
        group_surfaces: Dict[str, Any] = {}

        # 1. Runtime group
        runtime_bindings: List[Dict[str, Any]] = []
        if store is not None and hasattr(store, "list_runtime_bindings"):
            try:
                runtime_bindings = store.list_runtime_bindings() or []
                group_surfaces["runtime"] = {"status": "ok", "source": "store"}
            except Exception:
                group_surfaces["runtime"] = {"status": "unavailable", "source": "error"}
        else:
            group_surfaces["runtime"] = {"status": "unavailable", "source": "missing" if store is None else "unsupported"}

        active_runtimes = sum(1 for b in runtime_bindings if isinstance(b, dict) and b.get("status") == "running")
        runtime_group = {
            "group_id": "runtime",
            "label": _HEALTH_GROUP_LABELS["runtime"],
            "status": group_surfaces["runtime"]["status"],
            "summary": f"{active_runtimes} active runtime(s) running." if runtime_bindings else "No active runtime bindings.",
            "details": {
                "total_runtimes": len(runtime_bindings),
                "active_runtimes": active_runtimes,
            },
            "target_refs": [{"label": "Runtime State", "href": "/api/v1/operator/runtime-state"}],
        }

        # 2. Telemetry group
        telemetry_records: List[Dict[str, Any]] = []
        if store is not None and hasattr(store, "list_telemetry_summaries"):
            try:
                res = store.list_telemetry_summaries()
                telemetry_records = list(res.values()) if isinstance(res, dict) else (res or [])
                group_surfaces["telemetry"] = {"status": "ok", "source": "store"}
            except Exception:
                group_surfaces["telemetry"] = {"status": "unavailable", "source": "error"}
        else:
            group_surfaces["telemetry"] = {"status": "unavailable", "source": "missing" if store is None else "unsupported"}

        telemetry_group = {
            "group_id": "telemetry",
            "label": _HEALTH_GROUP_LABELS["telemetry"],
            "status": group_surfaces["telemetry"]["status"],
            "summary": f"{len(telemetry_records)} telemetry summaries observed." if telemetry_records else "No active telemetry feeds.",
            "details": {
                "telemetry_count": len(telemetry_records),
            },
            "target_refs": [{"label": "Telemetry", "href": "/bff/management/paper-telemetry"}],
        }

        # 3. Incident group
        incidents: List[Dict[str, Any]] = []
        if store is not None and (hasattr(store, "list_incidents") or hasattr(store, "list_incident_alerts")):
            try:
                if hasattr(store, "list_incidents"):
                    incidents = store.list_incidents() or []
                elif hasattr(store, "list_incident_alerts"):
                    incidents = store.list_incident_alerts() or []
                group_surfaces["incident"] = {"status": "ok", "source": "store"}
            except Exception:
                group_surfaces["incident"] = {"status": "unavailable", "source": "error"}
        else:
            group_surfaces["incident"] = {"status": "unavailable", "source": "missing" if store is None else "unsupported"}

        open_incidents = [i for i in incidents if isinstance(i, dict) and i.get("status") in ("open", "active", "triggered")]
        incident_group = {
            "group_id": "incident",
            "label": _HEALTH_GROUP_LABELS["incident"],
            "status": "degraded" if open_incidents else group_surfaces["incident"]["status"],
            "summary": f"{len(open_incidents)} active incident(s)." if open_incidents else "No active incidents.",
            "details": {
                "total_incidents": len(incidents),
                "open_incidents": len(open_incidents),
            },
            "target_refs": [{"label": "Incidents", "href": "/bff/alerts"}],
        }

        # 4. Governance group
        approvals: List[Dict[str, Any]] = []
        if store is not None and (
            hasattr(store, "list_approval_queue_items")
            or hasattr(store, "list_governance_review_queue_items")
            or hasattr(store, "list_approval_records")
        ):
            try:
                if hasattr(store, "list_approval_queue_items"):
                    approvals.extend(store.list_approval_queue_items() or [])
                if hasattr(store, "list_governance_review_queue_items"):
                    approvals.extend(store.list_governance_review_queue_items() or [])
                if not approvals and hasattr(store, "list_approval_records"):
                    approvals.extend(store.list_approval_records() or [])
                group_surfaces["governance"] = {"status": "ok", "source": "store"}
            except Exception:
                group_surfaces["governance"] = {"status": "unavailable", "source": "error"}
        else:
            group_surfaces["governance"] = {"status": "unavailable", "source": "missing" if store is None else "unsupported"}

        pending_gov = [a for a in approvals if isinstance(a, dict) and (a.get("status") in ("pending", "open", "in_review") or a.get("decision_state") in ("pending", "open", "in_review"))]
        governance_group = {
            "group_id": "governance",
            "label": _HEALTH_GROUP_LABELS["governance"],
            "status": "degraded" if pending_gov else group_surfaces["governance"]["status"],
            "summary": f"{len(pending_gov)} pending governance decision(s)." if pending_gov else "Governance queues clear.",
            "details": {
                "total_items": len(approvals),
                "pending_items": len(pending_gov),
            },
            "target_refs": [{"label": "Approval Queue", "href": "/api/v1/operator/governance/approval-queue"}],
        }

        # 5. Kill switch group
        kill_switch_state = {"status": "armed", "safe_mode_status": "off", "active": False}
        if store is not None and hasattr(store, "get_kill_switch_status"):
            try:
                kill_switch_state = store.get_kill_switch_status() or kill_switch_state
                group_surfaces["kill_switch"] = {"status": "ok", "source": "store"}
            except Exception:
                group_surfaces["kill_switch"] = {"status": "unavailable", "source": "error"}
        else:
            group_surfaces["kill_switch"] = {"status": "unavailable", "source": "missing" if store is None else "unsupported"}

        kill_switch_group = {
            "group_id": "kill_switch",
            "label": _HEALTH_GROUP_LABELS["kill_switch"],
            "status": group_surfaces["kill_switch"]["status"],
            "summary": f"Kill switch {kill_switch_state.get('status', 'armed')}, Safe mode {kill_switch_state.get('safe_mode_status', 'off')}.",
            "details": kill_switch_state,
            "target_refs": [{"label": "Kill Switch", "href": "/api/v1/operator/kill-switch"}],
        }

        groups = [runtime_group, telemetry_group, incident_group, governance_group, kill_switch_group]
        overall_surface = _aggregate_group_surface("health_status", list(group_surfaces.values()), snapshot_at=snap)
        overall_status = overall_surface.get("status", "ok")

        group_counts = {
            "ok": sum(1 for s in group_surfaces.values() if s.get("status") == "ok"),
            "degraded": sum(1 for s in group_surfaces.values() if s.get("status") == "degraded"),
            "unavailable": sum(1 for s in group_surfaces.values() if s.get("status") == "unavailable"),
        }

        safe_mode_state = {
            "status": kill_switch_state.get("safe_mode_status", "off"),
            "kill_switch_status": kill_switch_state.get("status", "inactive"),
            "active": bool(kill_switch_state.get("active", False)),
            "last_confirmed_at": snap,
            "last_triggered_at": None,
            "secondary_path_available": True,
        }

        secondary_control_path = {
            "mode": "hidden" if overall_status == "ok" else "advisory",
            "reason": None if overall_status == "ok" else "One or more health groups are degraded.",
            "targets": _SECONDARY_CONTROL_PATH_ADVISORY_TARGETS if overall_status != "ok" else [],
        }

        headline = "Control plane healthy" if overall_status == "ok" else "Control plane degraded"
        message = (
            "All health groups are responding normally."
            if overall_status == "ok"
            else f"{group_counts['degraded'] + group_counts['unavailable']} of {len(group_surfaces)} health groups need attention."
        )

        return {
            "overall_status": overall_status,
            "headline": headline,
            "message": message,
            "group_counts": group_counts,
            "safe_mode_state": safe_mode_state,
            "secondary_control_path": secondary_control_path,
            "groups": groups,
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "health_status": overall_surface,
                    **group_surfaces,
                },
            },
        }

    # -----------------------------------------------------------------------
    # 3. Operator Home
    # -----------------------------------------------------------------------
    def get_operator_home(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        health_payload = self.get_operator_health_status(snapshot_at=snap)
        groups_by_id = {g["group_id"]: g for g in health_payload.get("groups", [])}

        incident_grp = groups_by_id.get("incident", {})
        gov_grp = groups_by_id.get("governance", {})
        runtime_grp = groups_by_id.get("runtime", {})
        telemetry_grp = groups_by_id.get("telemetry", {})

        cards: List[Dict[str, Any]] = [
            {
                "card_id": "alerts",
                "label": "Alerts",
                "status": incident_grp.get("status", "ok"),
                "summary": incident_grp.get("summary", "No active alerts."),
                "details": incident_grp.get("details", {}),
                "target_refs": [{"surface_id": "OC-02", "label": "Open alerts rail", "href": "/bff/alerts"}],
            },
            {
                "card_id": "incidents",
                "label": "Incidents",
                "status": incident_grp.get("status", "ok"),
                "summary": incident_grp.get("summary", "Incident summary."),
                "details": incident_grp.get("details", {}),
                "target_refs": [{"surface_id": "OC-02", "label": "Incidents", "href": "/bff/alerts"}],
            },
            {
                "card_id": "governance",
                "label": "Governance",
                "status": gov_grp.get("status", "ok"),
                "summary": gov_grp.get("summary", "Governance queues clear."),
                "details": gov_grp.get("details", {}),
                "target_refs": [{"surface_id": "OC-01", "label": "Approval Queue", "href": "/api/v1/operator/governance/approval-queue"}],
            },
            {
                "card_id": "runtime",
                "label": "Runtime",
                "status": runtime_grp.get("status", "ok"),
                "summary": runtime_grp.get("summary", "Runtime overview."),
                "details": {
                    "runtime": runtime_grp.get("details", {}),
                    "telemetry": telemetry_grp.get("details", {}),
                },
                "target_refs": [{"surface_id": "OC-04", "label": "Runtime State", "href": "/api/v1/operator/runtime-state"}],
            },
            {
                "card_id": "health",
                "label": "Health",
                "status": health_payload.get("overall_status", "ok"),
                "summary": health_payload.get("message", "Health status."),
                "details": {
                    "headline": health_payload.get("headline"),
                    "group_counts": health_payload.get("group_counts"),
                    "safe_mode_state": health_payload.get("safe_mode_state"),
                },
                "target_refs": [{"surface_id": "OC-03", "label": "Health Status", "href": "/api/v1/operator/health-status"}],
            },
        ]

        home_surface = _aggregate_group_surface("operator_home", [
            health_payload.get("meta", {}).get("surfaces", {}).get("health_status", {"status": "ok"})
        ], snapshot_at=snap)

        return {
            "cards": cards,
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "operator_home": home_surface,
                    **health_payload.get("meta", {}).get("surfaces", {}),
                },
            },
        }

    # -----------------------------------------------------------------------
    # 4. Trading Pulse & Rankings
    # -----------------------------------------------------------------------
    def get_trading_pulse(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        store = self._resolve_store()

        runtime_bindings: List[Dict[str, Any]] = []
        telemetry_summaries: List[Dict[str, Any]] = []
        drift_reports: List[Dict[str, Any]] = []
        monitoring_sessions: List[Dict[str, Any]] = []

        if store is not None:
            if hasattr(store, "list_runtime_bindings"):
                try:
                    runtime_bindings = store.list_runtime_bindings() or []
                except Exception:
                    runtime_bindings = []
            if hasattr(store, "list_telemetry_summaries"):
                try:
                    t_res = store.list_telemetry_summaries() or []
                    if isinstance(t_res, dict):
                        telemetry_summaries = list(t_res.values())
                    elif isinstance(t_res, list):
                        telemetry_summaries = t_res
                except Exception:
                    telemetry_summaries = []
            if hasattr(store, "list_paper_live_drift_reports"):
                try:
                    d_res = store.list_paper_live_drift_reports() or []
                    if isinstance(d_res, dict):
                        drift_reports = list(d_res.values())
                    elif isinstance(d_res, list):
                        drift_reports = d_res
                except Exception:
                    drift_reports = []
            if hasattr(store, "list_paper_runtime_monitoring_sessions"):
                try:
                    m_res = store.list_paper_runtime_monitoring_sessions() or []
                    if isinstance(m_res, dict):
                        monitoring_sessions = list(m_res.values())
                    elif isinstance(m_res, list):
                        monitoring_sessions = m_res
                except Exception:
                    monitoring_sessions = []

        telemetry_by_runtime_id = _trading_pulse_index_by_runtime(telemetry_summaries)
        drift_by_runtime_id = _trading_pulse_index_by_runtime(drift_reports)
        monitoring_by_runtime_id, monitoring_by_binding_id = _trading_pulse_monitoring_indexes(monitoring_sessions)

        runtime_rows = []
        for binding in runtime_bindings:
            if not isinstance(binding, dict):
                continue
            runtime_id = str(binding.get("runtime_id") or binding.get("id") or "")
            binding_id = str(
                binding.get("runtime_binding_id")
                or binding.get("binding_id")
                or binding.get("id")
                or ""
            )
            runtime_rows.append(
                _project_operator_runtime_state_row(
                    store,
                    binding,
                    telemetry_summary_record=telemetry_by_runtime_id.get(runtime_id),
                    monitoring_session_record=(
                        monitoring_by_binding_id.get(binding_id)
                        or monitoring_by_runtime_id.get(runtime_id)
                    ),
                    prefetched=True,
                )
            )

        baseline_comparisons = [
            _build_trading_pulse_baseline_comparison(
                store,
                row,
                drift_report=drift_by_runtime_id.get(str(row.get("runtime_id") or "")),
                prefetched=True,
            )
            for row in runtime_rows
        ]
        baseline_by_runtime_id = {
            str(comparison.get("runtimeId") or comparison.get("runtime_id") or ""): comparison
            for comparison in baseline_comparisons
        }
        for row in runtime_rows:
            comparison = baseline_by_runtime_id.get(str(row.get("runtime_id") or ""))
            row["baseline_comparison"] = comparison
        runtime_rows = _trading_pulse_sort_operator_rows(runtime_rows)
        row_order = {
            str(row.get("runtime_id") or ""): index
            for index, row in enumerate(runtime_rows)
        }
        baseline_comparisons = sorted(
            baseline_comparisons,
            key=lambda comparison: row_order.get(
                str(comparison.get("runtimeId") or comparison.get("runtime_id") or ""),
                len(row_order),
            ),
        )
        coverage = _trading_pulse_coverage_summary(runtime_rows, baseline_comparisons)
        telemetry_rows = [
            row.get("telemetry_summary")
            for row in runtime_rows
            if isinstance(row.get("telemetry_summary"), dict)
        ]
        pnl_values = [
            value
            for value in (_management_number((row.get("metrics") or {}).get("pnl")) for row in telemetry_rows)
            if value is not None
        ]
        drawdown_values = [
            value
            for value in (_management_number((row.get("metrics") or {}).get("drawdown")) for row in telemetry_rows)
            if value is not None
        ]
        fill_rate_values = [
            value
            for value in (_management_number((row.get("metrics") or {}).get("fill_rate")) for row in telemetry_rows)
            if value is not None
        ]
        slippage_values = [
            value
            for value in (_management_number((row.get("metrics") or {}).get("avg_slippage_bps")) for row in telemetry_rows)
            if value is not None
        ]
        trade_values = [
            value
            for value in (_management_number((row.get("metrics") or {}).get("total_trades")) for row in telemetry_rows)
            if value is not None
        ]

        rankings: List[Dict[str, Any]] = []
        for row in runtime_rows:
            telemetry = row.get("telemetry_summary") if isinstance(row.get("telemetry_summary"), dict) else {}
            metrics = telemetry.get("metrics") if isinstance(telemetry.get("metrics"), dict) else {}
            baseline_comparison = baseline_by_runtime_id.get(str(row.get("runtime_id") or "")) or {}
            rankings.append(
                {
                    "runtime_id": row.get("runtime_id"),
                    "runtime_binding_id": row.get("runtime_binding_id"),
                    "deployment_stage": row.get("deployment_stage"),
                    "status": row.get("status"),
                    "pnl": metrics.get("pnl"),
                    "drawdown": metrics.get("drawdown"),
                    "sharpe_ratio": metrics.get("sharpe_ratio"),
                    "fill_rate": metrics.get("fill_rate"),
                    "avg_slippage_bps": metrics.get("avg_slippage_bps"),
                    "total_trades": metrics.get("total_trades"),
                    "last_updated_at": row.get("last_updated_at"),
                    "baseline_comparison_status": baseline_comparison.get("status"),
                    "breached_metric_count": baseline_comparison.get("breached_metric_count"),
                    "row_health_status": _trading_pulse_row_health_status(row),
                    "row_health_degraded_checks": _trading_pulse_row_degraded_checks(row),
                }
            )
        rankings.sort(
            key=lambda item: (
                _management_number(item.get("pnl")) is not None,
                _management_number(item.get("pnl")) or 0.0,
                str(item.get("runtime_id") or ""),
            ),
            reverse=True,
        )
        for index, item in enumerate(rankings, start=1):
            item["rank"] = index

        by_status = _management_count_by(runtime_rows, "status")
        by_stage = _management_count_by(runtime_rows, "deployment_stage")
        by_baseline_status = _management_count_by(baseline_comparisons, "status")
        baseline_available_count = sum(
            1
            for comparison in baseline_comparisons
            if (comparison.get("paper_live_drift") or {}).get("available")
        )
        baseline_breached_count = sum(
            1 for comparison in baseline_comparisons
            if comparison.get("status") == "breached"
        )
        baseline_watch_count = sum(
            1 for comparison in baseline_comparisons
            if comparison.get("status") == "watch"
        )

        def _get_dataset_source(ds: str) -> str:
            if store is not None and hasattr(store, "dataset_source"):
                try:
                    return str(store.dataset_source(ds))
                except Exception:
                    return "service_store"
            return "service_store" if store is not None else "missing"

        def _make_dataset_surface(ds: str) -> Dict[str, Any]:
            src = _get_dataset_source(ds)
            surf: Dict[str, Any] = {"status": "ok" if src != "missing" else "unavailable", "source": src}
            if src == "missing":
                surf.setdefault("staleness", {"served_from": "unverifiable", "last_known_at": snap})
            elif src == "local_snapshot":
                surf["status"] = "degraded"
                surf["note"] = "Served from local BFF snapshot fallback instead of a backend-owned read store."
                surf["staleness"] = {"served_from": "local_snapshot", "last_known_at": snap}
            return surf

        runtime_surface = _make_dataset_surface("runtime_bindings")
        telemetry_surface = _make_dataset_surface("telemetry_summaries")
        if runtime_rows and len(telemetry_rows) < len(runtime_rows) and telemetry_surface.get("status") == "ok":
            telemetry_surface["status"] = "degraded"
            telemetry_surface["message"] = "Telemetry summary missing for one or more cockpit runtimes."
            telemetry_surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snap},
            )

        monitoring_surface = _make_dataset_surface("paper_runtime_monitoring_sessions")
        paper_runtime_count = int(coverage.get("paper_runtime_count") or 0)
        monitoring_coverage_count = int(coverage.get("monitoring_coverage_count") or 0)
        if paper_runtime_count == 0:
            monitoring_surface = {
                "status": "ok",
                "source": "not_applicable",
                "message": "No paper runtimes require paper runtime monitoring.",
            }
        elif (
            monitoring_coverage_count < paper_runtime_count
            and monitoring_surface.get("status") == "ok"
        ):
            monitoring_surface["status"] = "degraded"
            monitoring_surface["message"] = (
                "Paper runtime monitoring session missing for one or more paper runtimes."
            )
            monitoring_surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snap},
            )

        paper_live_drift_surface = _make_dataset_surface("paper_live_drift_reports")
        if (
            runtime_rows
            and baseline_available_count < len(runtime_rows)
            and paper_live_drift_surface.get("status") == "ok"
        ):
            paper_live_drift_surface["status"] = "degraded"
            paper_live_drift_surface["message"] = (
                "Paper/live baseline comparison missing for one or more cockpit runtimes."
            )
            paper_live_drift_surface.setdefault(
                "staleness",
                {"served_from": "unverifiable", "last_known_at": snap},
            )

        baseline_surface = _aggregate_group_surface(
            "baseline_comparison",
            [runtime_surface, paper_live_drift_surface],
            snapshot_at=snap,
            unavailable_message="Trading pulse baseline comparison unavailable.",
            degraded_message="Trading pulse baseline comparison is degraded because one or more paper/live drift reports are missing.",
        )
        row_health_surface = _trading_pulse_row_health_surface(
            runtime_rows,
            snapshot_at=snap,
        )
        trading_surface = _aggregate_group_surface(
            "management_trading_pulse",
            [
                runtime_surface,
                telemetry_surface,
                monitoring_surface,
                paper_live_drift_surface,
                baseline_surface,
                row_health_surface,
            ],
            snapshot_at=snap,
            unavailable_message="Trading pulse aggregate unavailable.",
            degraded_message="Trading pulse aggregate is available, but runtime, telemetry, monitoring, row health, or baseline coverage is degraded.",
        )

        summary = {
            "runtime_count": len(runtime_rows),
            "telemetry_coverage_count": len(telemetry_rows),
            "by_status": by_status,
            "by_stage": by_stage,
            "total_pnl": round(sum(pnl_values), 6) if pnl_values else None,
            "worst_drawdown": max(drawdown_values) if drawdown_values else None,
            "average_fill_rate": _management_avg(fill_rate_values),
            "worst_slippage_bps": max(slippage_values) if slippage_values else None,
            "total_trades": int(sum(trade_values)) if trade_values else 0,
            "baseline_comparison_count": baseline_available_count,
            "baseline_breached_count": baseline_breached_count,
            "baseline_watch_count": baseline_watch_count,
            "by_baseline_status": by_baseline_status,
            "row_health_degraded_count": coverage["row_health_degraded_count"],
            "row_health_status_counts": coverage["row_health_status_counts"],
            "monitoring_coverage_count": coverage["monitoring_coverage_count"],
            "missing_telemetry_runtime_ids": coverage["missing_telemetry_runtime_ids"],
            "missing_monitoring_runtime_ids": coverage["missing_monitoring_runtime_ids"],
            "missing_baseline_runtime_ids": coverage["missing_baseline_runtime_ids"],
            "metric_coverage": coverage["metric_coverage"],
            "coverage": coverage,
        }
        cards = [
            {
                "card_id": "runtime-status",
                "label": "Runtime Status",
                "value": len(runtime_rows),
                "details": {
                    "by_status": by_status,
                    "by_stage": by_stage,
                    "row_health_status_counts": coverage["row_health_status_counts"],
                },
            },
            {
                "card_id": "row-health",
                "label": "Row Health",
                "value": summary["row_health_degraded_count"],
                "details": {
                    "row_health_status_counts": coverage["row_health_status_counts"],
                    "degraded_runtime_ids": coverage["degraded_runtime_ids"],
                },
            },
            {
                "card_id": "pnl",
                "label": "P&L",
                "value": summary["total_pnl"],
                "details": {
                    "telemetry_coverage_count": len(telemetry_rows),
                    "metric_coverage": (coverage["metric_coverage"] or {}).get("pnl"),
                },
            },
            {
                "card_id": "drawdown",
                "label": "Worst Drawdown",
                "value": summary["worst_drawdown"],
                "details": {
                    "source": "telemetry_summaries",
                    "metric_coverage": (coverage["metric_coverage"] or {}).get("drawdown"),
                },
            },
            {
                "card_id": "execution-quality",
                "label": "Execution Quality",
                "value": summary["average_fill_rate"],
                "details": {
                    "worst_slippage_bps": summary["worst_slippage_bps"],
                    "metric_coverage": (coverage["metric_coverage"] or {}).get("fill_rate"),
                },
            },
            {
                "card_id": "baseline-comparison",
                "label": "Baseline Comparison",
                "value": summary["baseline_breached_count"],
                "details": {
                    "baseline_comparison_count": summary["baseline_comparison_count"],
                    "by_baseline_status": by_baseline_status,
                    "missing_baseline_runtime_ids": coverage["missing_baseline_runtime_ids"],
                },
            },
        ]
        meta = _snapshot_meta(snap)
        meta["surfaces"] = {
            "management_trading_pulse": trading_surface,
            "runtime_roster": runtime_surface,
            "telemetry_summary": telemetry_surface,
            "paper_runtime_monitoring": monitoring_surface,
            "paper_live_drift": paper_live_drift_surface,
            "baseline_comparison": baseline_surface,
            "runtime_row_health": row_health_surface,
        }
        meta["coverage"] = coverage
        return {
            "data": {
                "id": "management-trading-pulse",
                "summary": summary,
                "cards": cards,
                "rankings": rankings,
                "runtime_rows": runtime_rows,
                "baseline_comparisons": baseline_comparisons,
            },
            "page_info": {
                "next_page_token": None,
                "total": len(cards),
                "page_size": len(cards),
            },
            "meta": meta,
        }

    def get_trading_pulse_rankings(self, limit: int = 20, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        trading_pulse = self.get_trading_pulse(snapshot_at=snap)
        trading_pulse_payload = trading_pulse.get("data") or {}
        blocks = _build_management_trading_pulse_ranking_blocks(
            list(trading_pulse_payload.get("rankings") or []),
            limit=limit,
        )
        surfaces = dict((trading_pulse.get("meta") or {}).get("surfaces") or {})
        source_surfaces = [
            surface for surface in (
                surfaces.get("management_trading_pulse"),
                surfaces.get("runtime_roster"),
                surfaces.get("telemetry_summary"),
                surfaces.get("paper_runtime_monitoring"),
                surfaces.get("paper_live_drift"),
                surfaces.get("baseline_comparison"),
                surfaces.get("runtime_row_health"),
            )
            if isinstance(surface, dict)
        ]
        surfaces["management_trading_pulse_rankings"] = _aggregate_group_surface(
            "management_trading_pulse_rankings",
            source_surfaces,
            snapshot_at=snap,
            unavailable_message="Trading pulse rankings aggregate unavailable.",
            degraded_message="Trading pulse rankings are degraded because runtime or telemetry coverage is degraded.",
        )
        top_item = (blocks[0].get("items") or [None])[0] if blocks else None
        ranked_item_count = sum(len(block.get("items") or []) for block in blocks)
        missing_metric_item_count = sum(
            int(block.get("missing_metric_count") or 0)
            for block in blocks
        )
        eligible_item_count = sum(
            int(block.get("eligible_item_count") or 0)
            for block in blocks
        )
        if missing_metric_item_count and surfaces["management_trading_pulse_rankings"].get("status") == "ok":
            surfaces["management_trading_pulse_rankings"]["status"] = "degraded"
            surfaces["management_trading_pulse_rankings"]["message"] = (
                "Trading pulse rankings are degraded because one or more ranking metrics are missing."
            )
        summary = {
            "runtime_count": int((trading_pulse_payload.get("summary") or {}).get("runtime_count") or 0),
            "ranking_block_count": len(blocks),
            "ranked_item_count": ranked_item_count,
            "eligible_item_count": eligible_item_count,
            "missing_metric_item_count": missing_metric_item_count,
            "criteria": [str(block.get("metric") or "") for block in blocks],
            "limit": limit,
            "top_runtime_id": (top_item or {}).get("runtime_id") if isinstance(top_item, dict) else None,
        }
        return {
            "data": {
                "id": "management-trading-pulse-rankings",
                "items": blocks,
                "summary": summary,
            },
            "page_info": {
                "next_page_token": None,
                "total": len(blocks),
                "page_size": len(blocks),
            },
            "meta": {
                **_snapshot_meta(snap),
                "surfaces": surfaces,
                "composition_sources": [
                    "GET /bff/management/trading-pulse",
                    "GET /bff/runtimes",
                    "telemetry_summaries",
                    "paper_runtime_monitoring_sessions",
                    "paper_live_drift_reports",
                ],
            },
        }


    # -----------------------------------------------------------------------
    # 5. Sentinel Pulse
    # -----------------------------------------------------------------------
    def get_sentinel_pulse(
        self,
        kind: Optional[str] = None,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        q: str = "",
        page_token: Optional[str] = None,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_findings: List[Dict[str, Any]] = []
        sentinel_avail = True

        if store is not None and hasattr(store, "list_sentinel_findings"):
            try:
                # Production port returns Tuple[bool, List[Dict[str, Any]]] or List[Dict[str, Any]]
                res = store.list_sentinel_findings()
                if isinstance(res, tuple):
                    sentinel_avail, raw_findings = res
                elif isinstance(res, list):
                    sentinel_avail, raw_findings = True, res
                else:
                    sentinel_avail, raw_findings = False, []
            except Exception:
                sentinel_avail, raw_findings = False, []

        raw_interventions: List[Dict[str, Any]] = []
        if store is not None:
            try:
                if hasattr(store, "list_v5_interventions"):
                    intv_res = store.list_v5_interventions()
                    raw_interventions = intv_res if isinstance(intv_res, list) else []
                elif hasattr(store, "list_interventions"):
                    intv_res = store.list_interventions()
                    raw_interventions = intv_res if isinstance(intv_res, list) else []
                elif hasattr(store, "list_intervention_records"):
                    intv_res = store.list_intervention_records()
                    raw_interventions = intv_res if isinstance(intv_res, list) else []
            except Exception:
                raw_interventions = []

        filtered_findings = []
        for item in raw_findings:
            if not isinstance(item, dict):
                continue
            if kind and str(item.get("kind", "")).lower() != kind.lower():
                continue
            if status and str(item.get("status", "")).lower() != status.lower():
                continue
            if severity and str(item.get("severity", "")).lower() != severity.lower():
                continue
            if q and q.lower() not in json.dumps(item).lower():
                continue
            filtered_findings.append(item)

        filtered_findings.sort(key=lambda x: _parse_time(x.get("created_at") or x.get("timestamp")), reverse=True)
        page_findings, next_token = _page_slice(filtered_findings, page_token, page_size)

        active_findings = [x for x in filtered_findings if str(x.get("status", "")).lower() in _MANAGEMENT_SENTINEL_ACTIVE_STATUSES]
        critical_findings = [x for x in filtered_findings if str(x.get("severity", "")).lower() in ("critical", "sev1", "sev0", "high")]
        pending_interventions = [x for x in raw_interventions if isinstance(x, dict) and str(x.get("status", "")).lower() in _MANAGEMENT_SENTINEL_PENDING_INTERVENTION_STATUSES]

        summary = {
            "finding_count": len(filtered_findings),
            "returned_finding_count": len(page_findings),
            "active_finding_count": len(active_findings),
            "active_findings": len(active_findings),
            "critical_finding_count": len(critical_findings),
            "critical_findings": len(critical_findings),
            "intervention_count": len(raw_interventions),
            "pending_intervention_count": len(pending_interventions),
            "highest_severity": "critical" if critical_findings else "normal",
            "total_items": len(filtered_findings),
            "returned_items": len(page_findings),
            "policy": "read_only_sentinel_pulse",
            "basis": "composed_from_v5_sentinel_findings_and_interventions",
        }

        cards = [
            {"card_id": "active-findings", "label": "Active Findings", "value": summary["active_finding_count"]},
            {"card_id": "critical-findings", "label": "Critical Findings", "value": summary["critical_finding_count"]},
            {"card_id": "pending-interventions", "label": "Pending Interventions", "value": summary["pending_intervention_count"]},
        ]

        sentinel_surface = {"status": "ok" if sentinel_avail else "unavailable", "source": "store" if store else "missing"}
        return {
            "data": {
                "id": "management-sentinel-pulse",
                "items": page_findings,
                "findings": page_findings,
                "interventions": raw_interventions,
                "summary": summary,
                "cards": cards,
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered_findings),
                "page_size": page_size,
            },
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "sentinel_pulse": sentinel_surface,
                    "sentinel_findings": sentinel_surface,
                },
            },
        }

    # -----------------------------------------------------------------------
    # 6. Human Inbox & HIQ Backlog
    # -----------------------------------------------------------------------
    def get_human_inbox(
        self,
        source_type: Optional[str] = None,
        status: Optional[str] = None,
        priority: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        identity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        all_items: List[Dict[str, Any]] = []
        failures: Dict[str, Dict[str, Any]] = {}
        surfaces: Dict[str, Dict[str, Any]] = {}

        if store is None:
            failures["store"] = {
                "status": "unavailable",
                "source": "missing",
                "dataset": "human_inbox",
                "snapshot_at": snap,
                "message": "Store unavailable",
            }
            surfaces["human_inbox"] = {
                "status": "unavailable",
                "source": "missing",
                "snapshot_at": snap,
            }
        else:
            # 1. Approvals
            if hasattr(store, "list_approval_queue_items") or hasattr(store, "list_approval_records"):
                try:
                    records = (
                        store.list_approval_queue_items()
                        if hasattr(store, "list_approval_queue_items")
                        else store.list_approval_records()
                    ) or []
                    surfaces["approval_queue"] = {
                        "status": "ok" if records else "unavailable",
                        "source": "read_store",
                        "snapshot_at": snap,
                        **({"message": "Approval queue has no readable source records."} if not records else {}),
                    }
                    for r in records:
                        if isinstance(r, dict):
                            dec_id = str(r.get("decision_id") or r.get("id") or r.get("approval_decision_id") or "")
                            if dec_id:
                                dec_state = str(r.get("decision_state") or r.get("status") or "pending")
                                all_items.append({
                                    "id": f"approval:{dec_id}",
                                    "item_id": dec_id,
                                    "inbox_id": f"approval:{dec_id}",
                                    "source_id": dec_id,
                                    "decision_id": dec_id,
                                    "approval_decision_id": dec_id,
                                    "source_type": "approval",
                                    "inboxType": "approval",
                                    "status": dec_state,
                                    "action_state": "pending" if dec_state in ("pending", "in_review", "open") else "resolved",
                                    "priority": str(r.get("priority") or "medium"),
                                    "title": str(r.get("title") or "Deployment Approval"),
                                    "summary": str(r.get("summary") or r.get("description") or "Governance action required"),
                                    "created_at": str(r.get("submitted_at") or r.get("created_at") or snap),
                                    "updated_at": str(r.get("updated_at") or r.get("created_at") or snap),
                                    "details": r,
                                })
                except Exception as exc:
                    failures["approval_queue"] = {
                        "status": "degraded",
                        "source": "read_store",
                        "reason": "contributor_read_error",
                        "message": f"approval_queue_items contributor failed: {exc}",
                        "snapshot_at": snap,
                    }
                    surfaces["approval_queue"] = failures["approval_queue"]
            else:
                surfaces["approval_queue"] = {
                    "status": "unavailable",
                    "source": "missing",
                    "snapshot_at": snap,
                    "message": "Approval queue has no readable source records.",
                }

            # 2. Governance Reviews
            if hasattr(store, "list_governance_review_queue_items"):
                try:
                    records = store.list_governance_review_queue_items() or []
                    surfaces["governance_review_queue"] = {
                        "status": "ok" if records else "unavailable",
                        "source": "read_store",
                        "snapshot_at": snap,
                        **({"message": "Governance review queue has no readable source records."} if not records else {}),
                    }
                    for r in records:
                        if isinstance(r, dict):
                            item_id_val = str(r.get("item_id") or r.get("id") or r.get("review_item_id") or "")
                            if item_id_val:
                                st = str(r.get("status") or "pending")
                                all_items.append({
                                    "id": f"governance_review:{item_id_val}",
                                    "item_id": item_id_val,
                                    "inbox_id": f"governance_review:{item_id_val}",
                                    "source_id": item_id_val,
                                    "review_item_id": item_id_val,
                                    "source_type": "governance_review",
                                    "inboxType": "governance_review",
                                    "status": st,
                                    "action_state": "pending" if st in ("pending", "open", "in_review") else "resolved",
                                    "priority": str(r.get("priority") or "high"),
                                    "title": str(r.get("title") or "Governance Review"),
                                    "summary": str(r.get("summary") or "Policy review required"),
                                    "created_at": str(r.get("created_at") or snap),
                                    "updated_at": str(r.get("updated_at") or r.get("created_at") or snap),
                                    "details": r,
                                })
                except Exception as exc:
                    failures["governance_review_queue"] = {
                        "status": "degraded",
                        "source": "read_store",
                        "reason": "contributor_read_error",
                        "message": f"governance_review_queue_items contributor failed: {exc}",
                        "snapshot_at": snap,
                    }
                    surfaces["governance_review_queue"] = failures["governance_review_queue"]
            else:
                surfaces["governance_review_queue"] = {
                    "status": "unavailable",
                    "source": "missing",
                    "snapshot_at": snap,
                    "message": "Governance review queue has no readable source records.",
                }

            # 3. Interventions
            if hasattr(store, "list_v5_interventions") or hasattr(store, "list_interventions") or hasattr(store, "list_intervention_records"):
                try:
                    fn = getattr(store, "list_v5_interventions", None) or getattr(store, "list_interventions", None) or getattr(store, "list_intervention_records", None)
                    records = fn() or []
                    surfaces["v5_interventions"] = {
                        "status": "ok" if records else "unavailable",
                        "source": "read_store",
                        "snapshot_at": snap,
                        **({"message": "V5 interventions have no readable source records."} if not records else {}),
                    }
                    for r in records:
                        if isinstance(r, dict):
                            intv_id = str(r.get("intervention_id") or r.get("id") or "")
                            if intv_id:
                                st = str(r.get("status") or "open")
                                all_items.append({
                                    "id": f"intervention:{intv_id}",
                                    "item_id": intv_id,
                                    "inbox_id": f"intervention:{intv_id}",
                                    "source_id": intv_id,
                                    "intervention_id": intv_id,
                                    "source_type": "intervention",
                                    "inboxType": "intervention",
                                    "status": st,
                                    "action_state": "pending" if st in ("open", "pending", "claimed", "escalated") else "resolved",
                                    "priority": str(r.get("priority") or "high"),
                                    "title": str(r.get("title") or r.get("summary") or "Intervention Required"),
                                    "summary": str(r.get("summary") or "Operator intervention needed"),
                                    "created_at": str(r.get("created_at") or snap),
                                    "updated_at": str(r.get("updated_at") or r.get("created_at") or snap),
                                    "details": r,
                                })
                except Exception as exc:
                    failures["v5_interventions"] = {
                        "status": "degraded",
                        "source": "read_store",
                        "reason": "contributor_read_error",
                        "message": f"v5_interventions contributor failed: {exc}",
                        "snapshot_at": snap,
                    }
                    surfaces["v5_interventions"] = failures["v5_interventions"]
            else:
                surfaces["v5_interventions"] = {
                    "status": "unavailable",
                    "source": "missing",
                    "snapshot_at": snap,
                    "message": "V5 interventions have no readable source records.",
                }

            # 4. Sentinel Findings
            if hasattr(store, "list_sentinel_findings"):
                try:
                    s_res = store.list_sentinel_findings()
                    available = s_res[0] if isinstance(s_res, tuple) else True
                    s_findings = s_res[1] if isinstance(s_res, tuple) else (s_res or [])
                    surfaces["sentinel_findings"] = {
                        "status": "ok" if available and s_findings else "unavailable",
                        "source": "read_store" if available else "missing",
                        "snapshot_at": snap,
                        **({"message": "Sentinel findings have no readable source records."} if not (available and s_findings) else {}),
                    }
                    for r in (s_findings or []):
                        if isinstance(r, dict):
                            f_id = str(r.get("finding_id") or r.get("id") or "")
                            if f_id:
                                st = str(r.get("status") or "active")
                                all_items.append({
                                    "id": f"sentinel_finding:{f_id}",
                                    "item_id": f_id,
                                    "inbox_id": f"sentinel_finding:{f_id}",
                                    "source_id": f_id,
                                    "finding_id": f_id,
                                    "source_type": "sentinel_finding",
                                    "inboxType": "sentinel_finding",
                                    "status": st,
                                    "action_state": "pending" if st in ("active", "open", "new", "escalated") else "resolved",
                                    "priority": "critical" if str(r.get("severity") or "").lower() in ("critical", "sev1", "high") else "medium",
                                    "title": str(r.get("title") or r.get("summary") or "Sentinel Anomaly"),
                                    "summary": str(r.get("summary") or "Anomaly detected"),
                                    "created_at": str(r.get("created_at") or snap),
                                    "updated_at": str(r.get("updated_at") or r.get("created_at") or snap),
                                    "details": r,
                                })
                except Exception as exc:
                    failures["sentinel_findings"] = {
                        "status": "degraded",
                        "source": "read_store",
                        "reason": "contributor_read_error",
                        "message": f"sentinel_findings contributor failed: {exc}",
                        "snapshot_at": snap,
                    }
                    surfaces["sentinel_findings"] = failures["sentinel_findings"]
            else:
                surfaces["sentinel_findings"] = {
                    "status": "unavailable",
                    "source": "missing",
                    "snapshot_at": snap,
                    "message": "Sentinel findings have no readable source records.",
                }

            # 5. Persona Readiness
            if hasattr(store, "list_personas"):
                try:
                    try:
                        personas = list(store.list_personas(include_market_persona_defaults=True) or [])
                    except TypeError:
                        personas = list(store.list_personas() or [])
                    surfaces["persona_readiness"] = {
                        "status": "ok" if personas else "unavailable",
                        "source": "bff_composed",
                        "snapshot_at": snap,
                    }
                    for p in personas:
                        if isinstance(p, dict) and bool(p.get("human_needed") or p.get("humanNeeded")):
                            p_id = str(p.get("persona_id") or p.get("id") or "")
                            if p_id:
                                all_items.append({
                                    "id": f"readiness_blocker:persona:{p_id}",
                                    "item_id": p_id,
                                    "inbox_id": f"readiness_blocker:persona:{p_id}",
                                    "source_id": p_id,
                                    "persona_id": p_id,
                                    "source_type": "readiness_blocker",
                                    "inboxType": "readiness_blocker",
                                    "status": str(p.get("state") or p.get("status") or "needs_human_approval"),
                                    "action_state": "pending",
                                    "priority": "high",
                                    "title": f"Persona needs review: {p.get('name') or p_id}",
                                    "summary": str(p.get("current_work") or "Persona readiness is blocked on human governance review."),
                                    "created_at": str(p.get("updated_at") or snap),
                                    "updated_at": str(p.get("updated_at") or snap),
                                    "details": p,
                                })
                except Exception as exc:
                    failures["persona_readiness"] = {
                        "status": "degraded",
                        "source": "bff_composed",
                        "reason": "contributor_read_error",
                        "message": str(exc),
                        "snapshot_at": snap,
                    }
                    surfaces["persona_readiness"] = failures["persona_readiness"]

            # 6. Promotion Reviews
            if hasattr(store, "list_promotion_reviews") or hasattr(store, "list_promotion_review_records"):
                try:
                    fn = getattr(store, "list_promotion_reviews", None) or getattr(store, "list_promotion_review_records", None)
                    records = fn() or []
                    surfaces["promotion_reviews"] = {
                        "status": "ok" if records else "unavailable",
                        "source": "read_store",
                        "snapshot_at": snap,
                    }
                except Exception as exc:
                    failures["promotion_reviews"] = {
                        "status": "degraded",
                        "source": "read_store",
                        "reason": "contributor_read_error",
                        "message": str(exc),
                        "snapshot_at": snap,
                    }
                    surfaces["promotion_reviews"] = failures["promotion_reviews"]

            surfaces["human_inbox"] = _aggregate_group_surface("human_inbox", list(surfaces.values()), snapshot_at=snap)

        filtered = []
        for item in all_items:
            st = str(item.get("source_type") or item.get("inboxType") or "").lower()
            if source_type and st != str(source_type).lower():
                continue
            item_status = str(item.get("status") or item.get("action_state") or "").lower()
            if status and item_status != str(status).lower():
                continue
            item_priority = str(item.get("priority") or item.get("risk_level") or "").lower()
            if priority and item_priority != str(priority).lower():
                continue
            filtered.append(item)

        filtered.sort(
            key=lambda x: (
                _HUMAN_INBOX_PRIORITY_RANK.get(str(x.get("priority") or "").lower(), 0),
                str(x.get("created_at") or ""),
            ),
            reverse=True,
        )
        page_items, next_token = _page_slice(filtered, page_token, page_size)

        summary = {
            "total": len(filtered),
            "total_items": len(filtered),
            "governance_review_count": sum(1 for x in filtered if x.get("source_type") == "governance_review"),
            "approval_count": sum(1 for x in filtered if x.get("source_type") in ("approval", "governance_approval")),
            "intervention_count": sum(1 for x in filtered if x.get("source_type") == "intervention"),
            "sentinel_finding_count": sum(1 for x in filtered if x.get("source_type") == "sentinel_finding"),
            "priority_counts": {
                "critical": sum(1 for x in filtered if str(x.get("priority") or "").lower() == "critical"),
                "high": sum(1 for x in filtered if str(x.get("priority") or "").lower() == "high"),
                "medium": sum(1 for x in filtered if str(x.get("priority") or "").lower() == "medium"),
                "low": sum(1 for x in filtered if str(x.get("priority") or "").lower() == "low"),
            },
        }

        meta = {
            "snapshot_at": snap,
            "version": "v1",
            "surfaces": surfaces,
        }
        if failures:
            meta["partial"] = True
            meta["degradation"] = {
                "reason": "one_or_more_human_inbox_contributors_incomplete",
                "contributors": sorted(failures.keys()),
            }

        return {
            "data": {
                "id": "management-human-inbox",
                "items": page_items,
                "summary": summary,
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
                "page_size": page_size,
            },
            "meta": meta,
        }

    def get_human_inbox_detail_result(
        self,
        item_id: str,
        identity: Optional[Any] = None,
    ) -> Tuple[Optional[Dict[str, Any]], List[str]]:
        res = self.get_human_inbox(page_size=2000, identity=identity)
        items = res.get("data", {}).get("items", [])
        failures = res.get("meta", {}).get("degradation", {}).get("contributors", [])
        for item in items:
            if _human_inbox_detail_match(item, item_id):
                return {"data": item, "meta": res.get("meta", {})}, failures
        return None, failures

    def get_human_inbox_detail(self, item_id: str, identity: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        detail, failures = self.get_human_inbox_detail_result(item_id, identity=identity)
        if detail is not None:
            return detail
        if failures:
            raise _default_bff_error(
                503,
                ErrorCode.DEPENDENCY_UNAVAILABLE,
                "Human inbox detail could not be resolved from a partial aggregate",
                "One or more Human Inbox contributors timed out or failed; retry before treating the item as absent.",
                precondition_failed="human_inbox_partial_read",
                suggestion="Retry the Human Inbox detail after the degraded contributor recovers.",
            )
        return None

    def get_hiq_backlog(
        self,
        source_type: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        priority: Optional[str] = None,
        q: str = "",
        page_token: Optional[str] = None,
        page_size: int = 50,
        identity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        inbox = self.get_human_inbox(status=status, priority=priority, page_size=2000, identity=identity)
        items = inbox.get("data", {}).get("items", [])

        filtered = []
        for item in items:
            st = item.get("source_type")
            if source_type and st != source_type:
                continue
            if not source_type and st not in ("intervention", "sentinel_finding", "governance_approval", "governance_review", "approval", "readiness_blocker"):
                continue
            if kind and (item.get("details") or {}).get("kind") != kind:
                continue
            if q and q.lower() not in json.dumps(item).lower():
                continue
            filtered.append(item)

        page_items, next_token = _page_slice(filtered, page_token, page_size)
        return {
            "data": {
                "id": "management-hiq-backlog",
                "items": page_items,
                "summary": {
                    "total": len(filtered),
                    "total_items": len(filtered),
                    "status_counts": {
                        "pending": sum(1 for x in filtered if x.get("status") in ("pending", "open", "active")),
                        "resolved": sum(1 for x in filtered if x.get("status") in ("resolved", "completed", "approved", "rejected")),
                    },
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
                "page_size": page_size,
            },
            "meta": inbox.get("meta", {}),
        }

    def get_operator_alerts(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        store = self._resolve_store()
        surfaces: Dict[str, Any] = {}

        if store is None:
            alerts_surface = {"status": "unavailable", "source": "missing", "dataset": "alerts", "snapshot_at": snap}
            meta = _snapshot_meta(snap)
            meta["surfaces"] = {
                "alerts": alerts_surface,
                "incident_feed": alerts_surface,
                "review_queue": alerts_surface,
                "approval_queue": alerts_surface,
                "kill_switch": alerts_surface,
                "runtime_roster": alerts_surface,
                "telemetry_summary": alerts_surface,
            }
            return {
                "alerts": [],
                "summary": {
                    "total_active": 0,
                    "highest_severity": "normal",
                    "by_severity": {"critical": 0, "high": 0, "medium": 0, "low": 0, "normal": 0},
                    "by_category": {"incident": 0, "governance": 0, "runtime": 0, "kill_switch": 0},
                },
                "meta": meta,
            }

        # 1. Incidents
        incident_alerts = []
        try:
            if hasattr(store, "list_incidents"):
                raw_incidents = store.list_incidents() or []
            elif hasattr(store, "list_incident_alerts"):
                raw_incidents = store.list_incident_alerts() or []
            else:
                raw_incidents = []
            for inc in raw_incidents:
                if not isinstance(inc, dict):
                    continue
                st = str(inc.get("status") or "").lower()
                if st in ("open", "active", "triggered", "elevated"):
                    inc_id = str(inc.get("incident_id") or inc.get("id") or "")
                    incident_alerts.append({
                        "alert_id": inc_id or f"inc-{len(incident_alerts)}",
                        "severity": str(inc.get("severity") or "high").lower(),
                        "category": "incident",
                        "raised_at": inc.get("created_at") or snap,
                        "summary": inc.get("title") or inc.get("summary") or "Active incident",
                        "status": st,
                    })
            surfaces["incident_feed"] = {"status": "ok", "source": "store"}
        except Exception:
            surfaces["incident_feed"] = {"status": "unavailable", "source": "error"}

        # 2. Governance review queue
        gov_alerts = []
        try:
            if hasattr(store, "list_governance_review_queue_items"):
                reviews = store.list_governance_review_queue_items() or []
                for rev in reviews:
                    if isinstance(rev, dict) and str(rev.get("status") or "").lower() in ("pending", "in_review", "open"):
                        gov_alerts.append({
                            "alert_id": str(rev.get("item_id") or rev.get("id") or ""),
                            "severity": str(rev.get("risk_level") or rev.get("priority") or "medium").lower(),
                            "category": "governance",
                            "raised_at": rev.get("submitted_at") or rev.get("created_at") or snap,
                            "summary": rev.get("title") or rev.get("item_type") or "Pending governance review",
                            "status": str(rev.get("status") or "pending").lower(),
                        })
            surfaces["review_queue"] = {"status": "ok", "source": "store"}
        except Exception:
            surfaces["review_queue"] = {"status": "unavailable", "source": "error"}

        # 3. Approval queue
        approval_alerts = []
        try:
            if hasattr(store, "list_approval_queue_items"):
                approvals = store.list_approval_queue_items() or []
                for app in approvals:
                    if isinstance(app, dict) and str(app.get("decision_state") or app.get("status") or "").lower() in ("pending", "in_review", "open"):
                        approval_alerts.append({
                            "alert_id": str(app.get("decision_id") or app.get("id") or ""),
                            "severity": str(app.get("risk_level") or app.get("priority") or "high").lower(),
                            "category": "governance",
                            "raised_at": app.get("submitted_at") or app.get("created_at") or snap,
                            "summary": app.get("title") or app.get("decision_type") or "Pending operator approval",
                            "status": str(app.get("decision_state") or app.get("status") or "pending").lower(),
                        })
            surfaces["approval_queue"] = {"status": "ok", "source": "store"}
        except Exception:
            surfaces["approval_queue"] = {"status": "unavailable", "source": "error"}

        # 4. Kill switch
        kill_alerts = []
        try:
            if hasattr(store, "get_kill_switch_status"):
                ks = store.get_kill_switch_status() or {}
                if isinstance(ks, dict) and ks.get("active"):
                    kill_alerts.append({
                        "alert_id": "alert-kill-switch-active",
                        "severity": "critical",
                        "category": "kill_switch",
                        "raised_at": ks.get("last_triggered_at") or snap,
                        "summary": "Kill switch is active",
                        "status": "active",
                    })
            surfaces["kill_switch"] = {"status": "ok", "source": "store"}
        except Exception:
            surfaces["kill_switch"] = {"status": "unavailable", "source": "error"}

        # 5. Runtime anomalies / alerts
        runtime_alerts = []
        try:
            runtime_bindings = []
            if hasattr(store, "list_runtime_bindings"):
                runtime_bindings = store.list_runtime_bindings() or []
            surfaces["runtime_roster"] = {"status": "ok", "source": "store"}
            surfaces["telemetry_summary"] = {"status": "ok", "source": "store"}
            telemetry_map = {}
            if hasattr(store, "list_telemetry_summaries"):
                t_res = store.list_telemetry_summaries()
                if isinstance(t_res, dict):
                    telemetry_map = t_res
                elif isinstance(t_res, list):
                    telemetry_map = {str(t.get("runtime_id") or t.get("id") or ""): t for t in t_res if isinstance(t, dict)}
            for b in runtime_bindings:
                if not isinstance(b, dict):
                    continue
                rt_id = str(b.get("runtime_id") or b.get("id") or "")
                if not rt_id:
                    continue
                t_entry = telemetry_map.get(rt_id) or {}
                dd = _as_float(t_entry.get("drawdown") or (t_entry.get("metrics") or {}).get("drawdown"))
                if dd is not None and dd >= 0.10:
                    runtime_alerts.append({
                        "alert_id": f"alert-runtime-{rt_id}",
                        "severity": "high",
                        "category": "runtime",
                        "raised_at": t_entry.get("collected_at") or snap,
                        "summary": f"Runtime {rt_id} drawdown breach ({dd:.2%})",
                        "status": "active",
                    })
        except Exception:
            surfaces["runtime_roster"] = {"status": "unavailable", "source": "error"}
            surfaces["telemetry_summary"] = {"status": "unavailable", "source": "error"}

        alerts = incident_alerts + gov_alerts + approval_alerts + kill_alerts + runtime_alerts
        alerts_surface = _aggregate_group_surface("alerts", list(surfaces.values()), snapshot_at=snap)

        meta = _snapshot_meta(snap)
        meta["acknowledgement_supported"] = True
        meta["surfaces"] = {
            "alerts": alerts_surface,
            **surfaces,
        }

        by_sev: Dict[str, int] = {}
        by_cat: Dict[str, int] = {}
        for a in alerts:
            s = str(a.get("severity") or "normal").lower()
            c = str(a.get("category") or "runtime").lower()
            by_sev[s] = by_sev.get(s, 0) + 1
            by_cat[c] = by_cat.get(c, 0) + 1

        highest = "normal"
        for candidate in ("critical", "sev1", "high", "sev2", "medium", "sev3", "low", "normal"):
            if by_sev.get(candidate, 0) > 0:
                highest = candidate
                break

        return {
            "alerts": alerts,
            "summary": {
                "total_active": len(alerts),
                "highest_severity": highest,
                "by_severity": by_sev,
                "by_category": by_cat,
            },
            "meta": meta,
        }

    def get_management_anomalies(self, snapshot_at: Optional[str] = None) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        store = self._resolve_store()
        anomalies: List[Dict[str, Any]] = []
        surfaces: Dict[str, Any] = {}

        if store is None:
            surface = {"status": "unavailable", "source": "missing", "dataset": "management_anomalies", "snapshot_at": snap}
            meta = _snapshot_meta(snap)
            meta["surfaces"] = {
                "management_anomalies": surface,
                "anomalies": surface,
            }
            return {
                "items": [],
                "summary": {"total": 0},
                "meta": meta,
            }

        # 1. Sentinel findings
        try:
            if hasattr(store, "list_sentinel_findings"):
                res = store.list_sentinel_findings()
                findings = res[1] if isinstance(res, tuple) else (res or [])
                for f in findings:
                    if not isinstance(f, dict):
                        continue
                    f_id = str(f.get("id") or f.get("finding_id") or "")
                    if not f_id:
                        continue
                    anomalies.append({
                        "id": f_id,
                        "kind": f.get("kind") or "sentinel_finding",
                        "severity": str(f.get("severity") or f.get("risk_level") or "medium").lower(),
                        "status": f.get("status") or "active",
                        "summary": f.get("title") or f.get("summary") or f_id,
                        "created_at": f.get("created_at") or snap,
                    })
                surfaces["sentinel_findings"] = {"status": "ok", "source": "store"}
            else:
                surfaces["sentinel_findings"] = {"status": "unavailable", "source": "missing"}
        except Exception:
            surfaces["sentinel_findings"] = {"status": "unavailable", "source": "error"}

        # 2. Runtime anomalies
        try:
            runtime_bindings = []
            if hasattr(store, "list_runtime_bindings"):
                runtime_bindings = store.list_runtime_bindings() or []
            telemetry_map = {}
            if hasattr(store, "list_telemetry_summaries"):
                t_res = store.list_telemetry_summaries()
                if isinstance(t_res, dict):
                    telemetry_map = t_res
                elif isinstance(t_res, list):
                    telemetry_map = {str(t.get("runtime_id") or t.get("id") or ""): t for t in t_res if isinstance(t, dict)}
            for b in runtime_bindings:
                if not isinstance(b, dict):
                    continue
                rt_id = str(b.get("runtime_id") or b.get("id") or "")
                if not rt_id:
                    continue
                t_entry = telemetry_map.get(rt_id) or {}
                dd = _as_float(t_entry.get("drawdown") or (t_entry.get("metrics") or {}).get("drawdown"))
                if dd is not None and dd >= 0.10:
                    anomalies.append({
                        "id": f"anomaly-runtime-{rt_id}",
                        "kind": "runtime_alert",
                        "severity": "high",
                        "status": "active",
                        "summary": f"Runtime {rt_id} drawdown breach ({dd:.2%})",
                        "raised_at": t_entry.get("collected_at") or snap,
                    })
            surfaces["runtime_roster"] = {"status": "ok", "source": "store"}
            surfaces["telemetry_summary"] = {"status": "ok", "source": "store"}
        except Exception:
            surfaces["runtime_roster"] = {"status": "unavailable", "source": "error"}
            surfaces["telemetry_summary"] = {"status": "unavailable", "source": "error"}

        anomalies_surface = _aggregate_group_surface("management_anomalies", list(surfaces.values()), snapshot_at=snap)

        meta = _snapshot_meta(snap)
        meta["surfaces"] = {
            "management_anomalies": anomalies_surface,
            "anomalies": anomalies_surface,
            **surfaces,
        }

        return {
            "items": anomalies,
            "summary": {"total": len(anomalies)},
            "meta": meta,
        }

    # -----------------------------------------------------------------------
    # 7. Cockpit Aggregate
    # -----------------------------------------------------------------------
    def get_management_cockpit(
        self,
        snapshot_at: Optional[str] = None,
        human_inbox: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        snap = snapshot_at or self._utc_now()
        operator_home = self.get_operator_home(snapshot_at=snap)
        runtime_health = self.get_operator_health_status(snapshot_at=snap)
        trading_pulse = self.get_trading_pulse(snapshot_at=snap)
        if human_inbox is None:
            human_inbox = self.get_human_inbox()
        alerts_payload = self.get_operator_alerts(snapshot_at=snap)
        anomalies_payload = self.get_management_anomalies(snapshot_at=snap)

        alerts = {
            "items": alerts_payload.get("alerts", []),
            "summary": alerts_payload.get("summary", {}),
            "meta": alerts_payload.get("meta", {}),
        }

        tp_surface = trading_pulse.get("meta", {}).get("surfaces", {}).get("management_trading_pulse") or trading_pulse.get("meta", {}).get("surfaces", {}).get("trading_pulse", {"status": "ok", "source": "store"})

        cockpit_surface = _aggregate_group_surface("management_cockpit", [
            operator_home.get("meta", {}).get("surfaces", {}).get("operator_home", {"status": "ok"}),
            runtime_health.get("meta", {}).get("surfaces", {}).get("health_status", {"status": "ok"}),
            alerts_payload.get("meta", {}).get("surfaces", {}).get("alerts", {"status": "ok"}),
            human_inbox.get("meta", {}).get("surfaces", {}).get("human_inbox", {"status": "ok"}),
            tp_surface,
            anomalies_payload.get("meta", {}).get("surfaces", {}).get("management_anomalies", {"status": "ok"}),
        ], snapshot_at=snap)

        tp_data = trading_pulse.get("data") if isinstance(trading_pulse.get("data"), dict) else trading_pulse

        return {
            "data": {
                "id": "management-cockpit",
                "snapshot_at": snap,
                "operator_home": operator_home,
                "runtime_health": runtime_health,
                "alerts": alerts,
                "human_inbox": human_inbox,
                "trading_pulse": tp_data,
                "anomalies": anomalies_payload,
                "links": {
                    "self": "/bff/management/cockpit",
                    "operator_home": "/api/v1/operator/home",
                    "runtime_health": "/api/v1/operator/health-status",
                    "alerts": "/bff/alerts",
                    "human_inbox": "/bff/management/human-inbox",
                    "trading_pulse": "/bff/management/trading-pulse",
                },
            },
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "management_cockpit": cockpit_surface,
                    "operator_home": operator_home.get("meta", {}).get("surfaces", {}).get("operator_home", {"status": "ok"}),
                    "runtime_health": runtime_health.get("meta", {}).get("surfaces", {}).get("health_status", {"status": "ok"}),
                    "alerts": alerts_payload.get("meta", {}).get("surfaces", {}).get("alerts", {"status": "ok"}),
                    "human_inbox": human_inbox.get("meta", {}).get("surfaces", {}).get("human_inbox", {"status": "ok"}),
                    "trading_pulse": tp_surface,
                    "management_trading_pulse": tp_surface,
                    "anomalies": anomalies_payload.get("meta", {}).get("surfaces", {}).get("management_anomalies", {"status": "ok"}),
                    "management_anomalies": anomalies_payload.get("meta", {}).get("surfaces", {}).get("management_anomalies", {"status": "ok"}),
                },
            },
        }

    # -----------------------------------------------------------------------
    # 8. Loop Throughput
    # -----------------------------------------------------------------------
    def get_loop_throughput(
        self,
        status: Optional[str] = None,
        runtime_id: Optional[str] = None,
        loop_type: Optional[str] = None,
        window_minutes: int = 60,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_records: List[Dict[str, Any]] = []
        loop_avail = True

        if store is not None:
            try:
                if hasattr(store, "list_loop_runs"):
                    res = store.list_loop_runs()
                    if isinstance(res, tuple):
                        loop_avail, raw_records = res
                    elif isinstance(res, list):
                        loop_avail, raw_records = True, res
                elif hasattr(store, "list_loop_executions"):
                    res = store.list_loop_executions()
                    raw_records = res if isinstance(res, list) else []
            except Exception:
                loop_avail, raw_records = False, []

        filtered = []
        for index, item in enumerate(raw_records or [], start=1):
            if not isinstance(item, dict):
                continue
            if status and str(item.get("status", "")).lower() != status.lower():
                continue
            if runtime_id and str(item.get("runtime_id", "")) != runtime_id:
                continue
            if loop_type and str(item.get("loop_type", "")).lower() != loop_type.lower():
                continue
            item_copy = dict(item)
            item_copy["sequence"] = index
            item_copy["rank"] = index
            filtered.append(item_copy)

        filtered.sort(key=lambda x: _parse_time(x.get("event_at") or x.get("created_at") or x.get("timestamp")), reverse=True)
        page_items, next_token = _page_slice(filtered, page_token, page_size)

        runs_pm = round(len(filtered) / max(1, window_minutes), 2)
        status_counts = {
            "queued": sum(1 for x in filtered if x.get("status") in ("queued", "admitted")),
            "active": sum(1 for x in filtered if x.get("status") in ("running", "active")),
            "completed": sum(1 for x in filtered if x.get("status") == "completed"),
            "failed": sum(1 for x in filtered if x.get("status") == "failed"),
        }

        summary = {
            "loop_count": len(filtered),
            "total_runs": len(filtered),
            "returned_loop_count": len(page_items),
            "runtime_count": len({x.get("runtime_id") for x in filtered if x.get("runtime_id")}),
            "queue_depth": status_counts["queued"],
            "active_loop_count": status_counts["active"],
            "completed_loop_count": status_counts["completed"],
            "failed_loop_count": status_counts["failed"],
            "runs_per_minute": runs_pm,
            "completed_runs_per_minute": round(status_counts["completed"] / max(1, window_minutes), 2),
            "observed_window_minutes": window_minutes,
            "status_counts": status_counts,
            "by_status": status_counts,
        }

        return {
            "data": {
                "id": "management-loop-throughput",
                "items": page_items,
                "summary": summary,
                "metrics": summary,
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
                "page_size": page_size,
            },
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "loop_throughput": {"status": "ok" if loop_avail else "unavailable", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # 9. Risk Radar
    # -----------------------------------------------------------------------
    def get_risk_radar(
        self,
        persona_id: Optional[str] = None,
        strategy_id: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        risk_state: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 50,
        tenant_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_rows: List[Dict[str, Any]] = []

        if store is not None:
            if hasattr(store, "list_risk_radar_rows"):
                try:
                    raw_rows = store.list_risk_radar_rows() or []
                except Exception:
                    raw_rows = []
            elif hasattr(store, "list_runtime_bindings"):
                try:
                    bindings = store.list_runtime_bindings() or []
                    for b in bindings:
                        if isinstance(b, dict):
                            raw_rows.append({
                                "persona_id": b.get("persona_id") or "persona-default",
                                "strategy_id": b.get("strategy_id") or "strat-default",
                                "capital_pool_id": b.get("capital_pool_id") or "pool-main",
                                "risk_state": "normal" if b.get("status") == "running" else "elevated",
                                "total_exposure": 10000.0,
                                "worst_drawdown": 0.05,
                                "value_at_risk": 500.0,
                            })
                except Exception:
                    raw_rows = []

        filtered = []
        for r in raw_rows:
            if not isinstance(r, dict):
                continue
            if tenant_id and r.get("tenant_id") and r.get("tenant_id") != tenant_id:
                continue
            if persona_id and r.get("persona_id") != persona_id:
                continue
            if strategy_id and r.get("strategy_id") != strategy_id:
                continue
            if capital_pool_id and r.get("capital_pool_id") != capital_pool_id:
                continue
            if risk_state and str(r.get("risk_state", "")).lower() != risk_state.lower():
                continue
            filtered.append(r)

        page_items, next_token = _page_slice(filtered, page_token, page_size)
        risk_counts = {
            "normal": sum(1 for x in filtered if x.get("risk_state") == "normal"),
            "elevated": sum(1 for x in filtered if x.get("risk_state") == "elevated"),
            "critical": sum(1 for x in filtered if x.get("risk_state") == "critical"),
        }

        summary = {
            "indicator_count": len(filtered),
            "returned_indicator_count": len(page_items),
            "persona_count": len({x.get("persona_id") for x in filtered if x.get("persona_id")}),
            "strategy_count": len({x.get("strategy_id") for x in filtered if x.get("strategy_id")}),
            "capital_pool_count": len({x.get("capital_pool_id") for x in filtered if x.get("capital_pool_id")}),
            "by_risk_state": risk_counts,
            "total_exposure": sum((_as_float(x.get("total_exposure")) or 0.0) for x in filtered),
            "worst_drawdown": max([(_as_float(x.get("worst_drawdown")) or 0.0) for x in filtered], default=None),
            "value_at_risk_total": sum((_as_float(x.get("value_at_risk") or x.get("var_95")) or 0.0) for x in filtered),
        }

        return {
            "data": {
                "id": "management-risk-radar",
                "items": page_items,
                "rows": page_items,
                "summary": summary,
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
                "page_size": page_size,
            },
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "risk_radar": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # 10. Incident Timeline
    # -----------------------------------------------------------------------
    def get_incident_timeline(
        self,
        status: Optional[str] = None,
        severity: Optional[str] = None,
        capital_pool_id: Optional[str] = None,
        affected_pool_id: Optional[str] = None,
        runtime_id: Optional[str] = None,
        sort_order: Optional[str] = None,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_incidents: List[Dict[str, Any]] = []

        if store is not None:
            try:
                if hasattr(store, "list_incidents"):
                    raw_incidents = store.list_incidents() or []
                elif hasattr(store, "list_incident_records"):
                    raw_incidents = store.list_incident_records() or []
                elif hasattr(store, "list_incident_alerts"):
                    raw_incidents = store.list_incident_alerts() or []
            except Exception:
                raw_incidents = []

        filtered = []
        pool_filter = capital_pool_id or affected_pool_id
        for inc in raw_incidents:
            if not isinstance(inc, dict):
                continue
            if status and str(inc.get("status", "")).lower() != status.lower():
                continue
            if severity and str(inc.get("severity", "")).lower() != severity.lower():
                continue
            if pool_filter and str(inc.get("capital_pool_id") or inc.get("affected_pool_id")) != pool_filter:
                continue
            if runtime_id and str(inc.get("runtime_id")) != runtime_id:
                continue
            filtered.append(inc)

        filtered.sort(
            key=lambda x: _parse_time(x.get("created_at") or x.get("timestamp")),
            reverse=(sort_order != "asc"),
        )
        page_items, next_token = _page_slice(filtered, page_token, page_size)

        severity_counts = {
            "critical": sum(1 for x in filtered if str(x.get("severity", "")).lower() in ("critical", "sev1", "sev0")),
            "high": sum(1 for x in filtered if str(x.get("severity", "")).lower() in ("high", "sev2")),
            "medium": sum(1 for x in filtered if str(x.get("severity", "")).lower() in ("medium", "sev3")),
            "low": sum(1 for x in filtered if str(x.get("severity", "")).lower() in ("low", "sev4")),
        }

        summary = {
            "total_incidents": len(filtered),
            "severity_counts": severity_counts,
            "status_counts": {
                "open": sum(1 for x in filtered if x.get("status") in ("open", "active")),
                "resolved": sum(1 for x in filtered if x.get("status") in ("resolved", "closed")),
            },
        }

        return {
            "data": {
                "id": "management-incident-timeline",
                "items": page_items,
                "summary": summary,
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
                "page_size": page_size,
            },
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "incident_timeline": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # 11. Intervention Stream
    # -----------------------------------------------------------------------
    def get_intervention_stream(
        self,
        persona_id: Optional[str] = None,
        status: Optional[str] = None,
        kind: Optional[str] = None,
        q: str = "",
        window_hours: int = 24,
        page_token: Optional[str] = None,
        page_size: int = 50,
    ) -> Dict[str, Any]:
        snap = self._utc_now()
        store = self._resolve_store()
        raw_items: List[Dict[str, Any]] = []

        if store is not None:
            try:
                if hasattr(store, "list_v5_interventions"):
                    raw_items = store.list_v5_interventions() or []
                elif hasattr(store, "list_interventions"):
                    raw_items = store.list_interventions() or []
                elif hasattr(store, "list_intervention_records"):
                    raw_items = store.list_intervention_records() or []
            except Exception:
                raw_items = []

        filtered = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            if persona_id and item.get("persona_id") != persona_id:
                continue
            if status and str(item.get("status", "")).lower() != status.lower():
                continue
            if kind and str(item.get("kind", "")).lower() != kind.lower():
                continue
            if q and q.lower() not in json.dumps(item).lower():
                continue
            filtered.append(item)

        filtered.sort(key=lambda x: _parse_time(x.get("created_at") or x.get("timestamp")), reverse=True)
        page_items, next_token = _page_slice(filtered, page_token, page_size)

        return {
            "data": {
                "id": "management-intervention-stream",
                "items": page_items,
                "summary": {
                    "total_items": len(filtered),
                    "window_hours": window_hours,
                },
            },
            "page_info": {
                "next_page_token": next_token,
                "total": len(filtered),
                "page_size": page_size,
            },
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "intervention_stream": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # 12. Evidence Explorer
    # -----------------------------------------------------------------------
    def get_evidence(
        self,
        ref_id: Optional[str] = None,
        linked_entity_type: Optional[str] = None,
        linked_entity_ref: Optional[str] = None,
        link_type: Optional[str] = None,
        credibility_tier: Optional[str] = None,
        verified: Optional[bool] = None,
        page_token: Optional[str] = None,
        page_size: int = 20,
        identity: Optional[Any] = None,
    ) -> Dict[str, Any]:
        validated_linked_entity_type = None
        if linked_entity_type is not None:
            validated_linked_entity_type = _kw03_validate_linked_entity_type(linked_entity_type)
        if linked_entity_ref is not None and validated_linked_entity_type is None:
            raise ManagementValidationError(
                "Invalid linked_entity_ref filter",
                "linked_entity_ref requires linked_entity_type to be set",
                field="linked_entity_ref",
                status_code=400,
            )
        validated_link_type = _kw03_validate_link_type(link_type) if link_type is not None else None
        validated_credibility_tier = (
            _kw03_validate_credibility_tier(credibility_tier)
            if credibility_tier is not None
            else None
        )

        snap = self._utc_now()
        current_run_evidence_refs = _management_current_run_live_evidence_refs()
        stored_evidence_refs: List[Dict[str, Any]] = []

        store = self._resolve_store()
        if store is not None:
            if hasattr(store, "list_evidence_refs"):
                try:
                    stored_evidence_refs = store.list_evidence_refs() or []
                except Exception:
                    stored_evidence_refs = []
            elif hasattr(store, "list_evidence_records"):
                try:
                    stored_evidence_refs = store.list_evidence_records() or []
                except Exception:
                    stored_evidence_refs = []
            elif hasattr(store, "list_records"):
                try:
                    res = store.list_records("evidence")
                    stored_evidence_refs = res[1] if isinstance(res, tuple) else (res or [])
                except Exception:
                    stored_evidence_refs = []
        else:
            try:
                import read_store as _rs
                if hasattr(_rs, "list_evidence_refs"):
                    stored_evidence_refs = _rs.list_evidence_refs() or []
            except Exception:
                stored_evidence_refs = []

        evidence_refs = [*current_run_evidence_refs, *stored_evidence_refs]

        dataset_source_fn = getattr(store, "dataset_source", None)
        if callable(dataset_source_fn):
            try:
                evidence_dataset_source = dataset_source_fn("evidence_refs")
            except Exception:
                evidence_dataset_source = "missing"
        else:
            evidence_dataset_source = "service_backend" if stored_evidence_refs else "missing"

        evidence_dataset_available = (evidence_dataset_source != "missing") or bool(current_run_evidence_refs)
        if evidence_dataset_source != "missing":
            evidence_surface_source = evidence_dataset_source
        elif current_run_evidence_refs:
            evidence_surface_source = "bff_current_run_artifact"
        else:
            evidence_surface_source = evidence_dataset_source

        clean_ref_id = str(ref_id or "").strip()
        if clean_ref_id:
            evidence_refs = [
                item for item in evidence_refs if str(item.get("ref_id") or item.get("id") or "") == clean_ref_id
            ]
        if validated_linked_entity_type:
            evidence_refs = [
                item
                for item in evidence_refs
                if str(
                    ((item.get("linked_object_summary") or {}).get("entity_type"))
                    or item.get("linked_entity_type")
                    or ""
                ).lower() == validated_linked_entity_type
            ]
        if linked_entity_ref is not None:
            evidence_refs = [
                item
                for item in evidence_refs
                if str(
                    ((item.get("linked_object_summary") or {}).get("entity_ref"))
                    or item.get("linked_entity_ref")
                    or ""
                ) == str(linked_entity_ref)
            ]
        if validated_link_type:
            evidence_refs = [
                item
                for item in evidence_refs
                if str(item.get("link_type") or "").lower() == validated_link_type
            ]
        if validated_credibility_tier:
            evidence_refs = [
                item
                for item in evidence_refs
                if str(
                    ((item.get("credibility") or {}).get("tier"))
                    or item.get("credibility_tier")
                    or ""
                ).lower() == validated_credibility_tier
            ]
        if verified is not None:
            evidence_refs = [
                item
                for item in evidence_refs
                if bool(
                    (item.get("credibility") or {}).get("verified")
                    if (item.get("credibility") or {}).get("verified") is not None
                    else item.get("verified")
                ) is verified
            ]

        evidence_surface = {
            "status": "ok" if evidence_dataset_available else "unavailable",
            "source": evidence_surface_source,
            "snapshot_at": snap,
        }
        if not evidence_dataset_available:
            evidence_surface["message"] = "Evidence reference read surface is unavailable."
            evidence_surface["staleness"] = {"served_from": "unverifiable", "last_known_at": snap}

        management_surface = _aggregate_group_surface(
            "management_evidence",
            [evidence_surface],
            snapshot_at=snap,
            unavailable_message="Management evidence aggregate unavailable.",
            degraded_message="Management evidence aggregate is available, but the evidence read surface is degraded.",
        )

        total = len(evidence_refs)
        if evidence_surface.get("status") == "unavailable" and not evidence_refs:
            page_items: List[Dict[str, Any]] = []
            next_page_token = None
        else:
            page_items, next_page_token = _page_slice(evidence_refs, page_token, page_size)

        capabilities = _capabilities_for_identity(identity)
        if redact_evidence_refs is not None:
            try:
                processed_items, redacted_count = redact_evidence_refs(
                    identity,
                    list(page_items),
                    capabilities=capabilities,
                )
            except Exception:
                processed_items, redacted_count = list(page_items), 0
        else:
            processed_items, redacted_count = list(page_items), 0

        public_items = [
            _management_evidence_public_item(item)
            for item in processed_items
            if isinstance(item, dict)
        ]
        summary = _management_evidence_summary(
            filtered_total=total,
            page_items=processed_items,
            redacted_count=redacted_count,
        )
        facets = {
            "source_types": summary["by_source_type"],
            "link_types": summary["by_link_type"],
            "credibility_tiers": summary["by_credibility_tier"],
        }
        meta = _snapshot_meta(snap)
        meta["surfaces"] = {
            "management_evidence": management_surface,
            "evidence_refs": evidence_surface,
            "knowledge_evidence": evidence_surface,
        }
        meta["redacted_evidence_count"] = redacted_count

        return {
            "data": {
                "id": "management-evidence",
                "items": public_items,
                "summary": summary,
                "facets": facets,
            },
            "page_info": {
                "next_page_token": next_page_token,
                "total": total,
                "page_size": page_size,
            },
            "meta": meta,
        }

    # -----------------------------------------------------------------------
    # 13. Operations Read Model
    # -----------------------------------------------------------------------
    def get_operations_read_model(
        self,
        persona_id: str,
        period: str = "latest",
        tenant_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if self._ops_read_model_entry_fn is not None:
            try:
                entry = self._ops_read_model_entry_fn(
                    persona_id,
                    period=period,
                    tenant_id=tenant_id,
                )
                if entry is None:
                    return None
                snap = self._utc_now()
                return {
                    "data": entry.model_dump(mode="json") if hasattr(entry, "model_dump") else entry,
                    "meta": {
                        "snapshot_at": snap,
                        "surface": "operations_read_model",
                        "surfaces": {
                            "operations_read_model": {"status": "ok", "source": "store"},
                        },
                    },
                }
            except Exception as exc:
                log.warning("ops_read_model_entry_fn failed: %r", exc)

        snap = self._utc_now()
        period_key = str(period or "").strip() or "latest"
        store = self._resolve_store()

        if store is None:
            return None

        persona: Optional[Dict[str, Any]] = None
        if hasattr(store, "get_persona"):
            try:
                persona = store.get_persona(persona_id)
            except Exception:
                persona = None
        elif hasattr(store, "list_personas"):
            try:
                for p in (store.list_personas() or []):
                    if isinstance(p, dict) and (p.get("persona_id") == persona_id or p.get("id") == persona_id):
                        persona = p
                        break
            except Exception:
                persona = None

        # Hard failure: if persona does not exist in store, return None (never fabricate synthetic persona)
        if persona is None:
            return None

        # Tenant isolation check: if persona belongs to a specific tenant, ensure tenant_id matches
        if tenant_id and persona.get("tenant_id") and persona.get("tenant_id") != tenant_id:
            return None

        league_entry: Dict[str, Any] = {}
        if hasattr(store, "get_persona_league_entry"):
            try:
                league_entry = store.get_persona_league_entry(persona_id) or {}
            except Exception:
                league_entry = {}

        persona_metadata = (
            persona.get("metadata") if isinstance(persona.get("metadata"), dict) else {}
        )
        fallback_performance = (
            league_entry.get("performance_summary")
            if isinstance(league_entry.get("performance_summary"), dict)
            else persona_metadata.get("performance")
            if isinstance(persona_metadata.get("performance"), dict)
            else persona.get("performance_summary")
            if isinstance(persona.get("performance_summary"), dict)
            else {
                k: persona.get(k)
                for k in ("pnl", "pnl_pct", "drawdown_pct", "sharpe", "sharpe_ratio", "rank", "score")
                if persona.get(k) is not None
            }
        )
        fleet_row = {
            "state": (
                league_entry.get("state")
                or persona.get("lifecycle_state")
                or persona.get("status")
                or persona.get("stage")
            ),
            "performance_summary": fallback_performance,
            "runtime_id": league_entry.get("runtime_id"),
            "paper_ledger_id": league_entry.get("paper_ledger_id"),
            "capital_pool_id": league_entry.get("capital_pool_id"),
            "league_rank": league_entry.get("rank") or league_entry.get("league_rank"),
            "league_score": league_entry.get("score") or league_entry.get("league_score"),
            "perf_delta": league_entry.get("perf_delta"),
        }

        # Query runtime bindings, deployment plans, bindings, capital pools
        runtime_bindings: List[Dict[str, Any]] = []
        if hasattr(store, "list_runtime_bindings"):
            try:
                runtime_bindings = list(store.list_runtime_bindings(include_market_persona_defaults=True) or [])
            except TypeError:
                try:
                    runtime_bindings = list(store.list_runtime_bindings() or [])
                except Exception:
                    runtime_bindings = []
            except Exception:
                runtime_bindings = []

        deployment_plans: List[Dict[str, Any]] = []
        if hasattr(store, "list_deployment_plans"):
            try:
                deployment_plans = list(store.list_deployment_plans() or [])
            except Exception:
                deployment_plans = []

        bindings: List[Dict[str, Any]] = []
        if hasattr(store, "list_bindings"):
            try:
                bindings = list(store.list_bindings(include_market_persona_defaults=True) or [])
            except TypeError:
                try:
                    bindings = list(store.list_bindings() or [])
                except Exception:
                    bindings = []
            except Exception:
                bindings = []

        capital_pools: List[Dict[str, Any]] = []
        if hasattr(store, "list_capital_pools"):
            try:
                capital_pools = list(store.list_capital_pools(include_market_persona_defaults=True) or [])
            except TypeError:
                try:
                    capital_pools = list(store.list_capital_pools() or [])
                except Exception:
                    capital_pools = []
            except Exception:
                capital_pools = []

        plans_by_id = {
            str(plan.get("plan_id") or plan.get("id") or ""): plan
            for plan in deployment_plans
            if str(plan.get("plan_id") or plan.get("id") or "")
        }
        bindings_by_id = {
            str(binding.get("binding_id") or binding.get("id") or binding.get("persona_capital_binding_id") or ""): binding
            for binding in bindings
            if str(binding.get("binding_id") or binding.get("id") or binding.get("persona_capital_binding_id") or "")
        }
        pools_by_id = {
            str(pool.get("pool_id") or pool.get("id") or ""): pool
            for pool in capital_pools
            if str(pool.get("pool_id") or pool.get("id") or "")
        }

        # Build persona facts
        persona_facts: List[Dict[str, Any]] = []
        for runtime in runtime_bindings:
            if not isinstance(runtime, dict):
                continue
            r_id = str(runtime.get("runtime_id") or runtime.get("id") or runtime.get("binding_id") or "")
            plan_id = str(runtime.get("plan_id") or runtime.get("deployment_plan_id") or "")
            plan = plans_by_id.get(plan_id, {})
            plan_binding_ids = [
                str(v).strip() for v in (plan.get("binding_ids") or []) if str(v).strip()
            ]
            persona_binding_id = (
                str(runtime.get("persona_capital_binding_id") or "")
                or (plan_binding_ids[0] if plan_binding_ids else "")
            )
            persona_binding = bindings_by_id.get(persona_binding_id, {})
            p_id = str(
                runtime.get("persona_id")
                or persona_binding.get("persona_id")
                or ""
            )
            if p_id != persona_id:
                continue

            telemetry = {}
            if hasattr(store, "get_telemetry_summary") and r_id:
                try:
                    telemetry = store.get_telemetry_summary(r_id) or {}
                except Exception:
                    telemetry = {}
            summary = telemetry.get("summary") if isinstance(telemetry.get("summary"), dict) else {}
            pnl_val = ops_read_model_sanitize_metric(
                telemetry.get("pnl") if telemetry.get("pnl") is not None else summary.get("total_pnl")
            )
            market_val = ops_read_model_sanitize_metric(
                telemetry.get("market_value") if telemetry.get("market_value") is not None else summary.get("market_value")
            )
            pool_id = str(
                runtime.get("capital_pool_id")
                or plan.get("capital_pool_id")
                or persona_binding.get("capital_pool_id")
                or ""
            )

            fact = {
                "persona_id": persona_id,
                "runtime_id": r_id or None,
                "capital_pool_id": pool_id or None,
                "strategy_id": str(runtime.get("strategy_id") or "") or None,
                "broker_id": str(runtime.get("broker_id") or "") or None,
                "total_pnl": pnl_val,
                "market_value": market_val,
                "worst_drawdown": ops_read_model_sanitize_metric(
                    telemetry.get("max_drawdown") or summary.get("max_drawdown") or telemetry.get("worst_drawdown")
                ),
                "telemetry_available": bool(telemetry),
            }
            persona_facts.append(fact)

        has_formal_attribution = any(
            fact.get("telemetry_available") and ops_read_model_sanitize_metric(fact.get("total_pnl")) is not None
            for fact in persona_facts
        )
        has_partial_attribution = bool(persona_facts) and not has_formal_attribution

        sources: List[SourceStatus] = []
        diagnostics: List[SourceDiagnostic] = []

        if has_formal_attribution:
            attribution_status = SourceState.OK
        elif has_partial_attribution:
            attribution_status = SourceState.PARTIAL
        else:
            attribution_status = SourceState.UNAVAILABLE
            diagnostics.append(
                ops_read_model_diagnostic(
                    "performance_attribution",
                    "MISSING_ATTRIBUTION_MATCH",
                    f"No performance-attribution row matched persona {persona_id} in period {period_key}.",
                )
            )
        sources.append(
            SourceStatus(
                source_name="performance_attribution",
                source_status=attribution_status,
                source_row_count=len(persona_facts),
                coverage_ratio=1.0 if persona_facts else 0.0,
            )
        )

        holdings_rows = [
            fact for fact in persona_facts
            if ops_read_model_sanitize_metric(fact.get("market_value")) is not None
        ]
        holdings_status = SourceState.OK if holdings_rows else SourceState.UNAVAILABLE
        if not holdings_rows:
            diagnostics.append(
                ops_read_model_diagnostic(
                    "portfolio_holdings",
                    "MISSING_HOLDINGS_MATCH",
                    f"No holdings source returned a matching row for persona {persona_id}.",
                )
            )
        sources.append(
            SourceStatus(
                source_name="portfolio_holdings",
                source_status=holdings_status,
                source_row_count=len(holdings_rows),
            )
        )

        pool_ids_seen = dedupe_ids(fact.get("capital_pool_id") for fact in persona_facts)
        unresolved_pool_ids = [pid for pid in pool_ids_seen if pid not in pools_by_id]
        if unresolved_pool_ids:
            capital_pool_status = SourceState.DEGRADED
            diagnostics.append(
                ops_read_model_diagnostic(
                    "capital_pools",
                    "CAPITAL_POOL_ID_UNRESOLVED",
                    f"Capital pool id(s) {unresolved_pool_ids} referenced by attribution facts do not resolve to a capital-pool record.",
                )
            )
        elif pool_ids_seen:
            capital_pool_status = SourceState.OK
        else:
            capital_pool_status = SourceState.UNAVAILABLE
        sources.append(
            SourceStatus(
                source_name="capital_pools",
                source_status=capital_pool_status,
                source_row_count=len(pool_ids_seen),
            )
        )

        if fleet_row:
            sources.append(
                SourceStatus(
                    source_name="persona_fleet_summary",
                    source_status=SourceState.OK,
                    source_row_count=1,
                )
            )
        else:
            sources.append(
                SourceStatus(
                    source_name="persona_fleet_summary",
                    source_status=SourceState.UNAVAILABLE,
                )
            )
            diagnostics.append(
                ops_read_model_diagnostic(
                    "persona_fleet_summary",
                    "PERSONA_NOT_IN_FLEET",
                    f"Persona {persona_id} has no persona-fleet row to source a fallback summary from.",
                )
            )

        stage = (
            str(fleet_row.get("state") or "").strip()
            or str(persona.get("lifecycle_state") or persona.get("status") or persona.get("stage") or "").strip()
            or None
        )
        is_operational = stage in (
            "deployed", "paper", "active", "canary", "live", "paper_running",
            "paper_halted", "live_running", "live_halted", "staging", "evaluation",
        )
        fallback_has_signal = bool(fleet_row) and is_operational
        is_fallback = not has_formal_attribution and not has_partial_attribution and fallback_has_signal
        if is_fallback:
            diagnostics.append(
                ops_read_model_diagnostic(
                    "persona_fleet_summary",
                    "FORMAL_ATTRIBUTION_MISSING_USING_FLEET_FALLBACK",
                    "The persona-fleet row is the only persona-scoped summary because no formal attribution or holdings row matched this persona; preserve unavailable values and treat the row as fallback, not formal evidence.",
                )
            )

        has_degraded_source = any(s.source_status == SourceState.DEGRADED for s in sources)
        has_unavailable_source = any(s.source_status == SourceState.UNAVAILABLE for s in sources)

        confidence = classify_confidence(
            has_formal_match=has_formal_attribution,
            has_partial_evidence=has_partial_attribution,
            is_fallback=is_fallback,
            has_degraded_source=has_degraded_source,
            has_unavailable_source=has_unavailable_source,
        )

        if has_formal_attribution or has_partial_attribution:
            pnl = ops_read_model_sanitize_metric(sum(f.get("total_pnl") or 0.0 for f in persona_facts))
            drawdown = ops_read_model_sanitize_metric(max((f.get("worst_drawdown") or 0.0 for f in persona_facts), default=None))
            sharpe = None
        else:
            pnl = ops_read_model_sanitize_metric(fallback_performance.get("pnl"))
            drawdown = ops_read_model_sanitize_metric(fallback_performance.get("max_drawdown") or fallback_performance.get("drawdown_pct"))
            sharpe = ops_read_model_sanitize_metric(fallback_performance.get("sharpe") or fallback_performance.get("sharpe_ratio"))

        rank_value = league_entry.get("rank") or league_entry.get("league_rank") or fleet_row.get("league_rank")
        score_value = ops_read_model_sanitize_metric(
            league_entry.get("score") or league_entry.get("league_score") or fleet_row.get("league_score")
        )

        persona_label = str(persona.get("name") or persona.get("label") or "").strip() or None

        identity = build_operations_identity(
            persona_id=persona_id,
            persona_label=persona_label,
            stage=stage,
            runtime_ids=[fact.get("runtime_id") for fact in persona_facts if fact.get("runtime_id")] + ([fleet_row.get("runtime_id")] if fleet_row.get("runtime_id") else []),
            paper_ledger_ids=[fleet_row.get("paper_ledger_id")] if fleet_row.get("paper_ledger_id") else [],
            capital_pool_ids=pool_ids_seen + ([fleet_row.get("capital_pool_id")] if fleet_row.get("capital_pool_id") else []),
            strategy_ids=[fact.get("strategy_id") for fact in persona_facts if fact.get("strategy_id")],
            broker_ids=[fact.get("broker_id") for fact in persona_facts if fact.get("broker_id")],
            period=period_key,
            as_of=snap,
        )

        performance = OperationsPerformance(
            pnl=pnl,
            drawdown_pct=drawdown,
            sharpe=sharpe,
            rank=int(rank_value) if isinstance(rank_value, (int, float)) and not isinstance(rank_value, bool) else None,
            score=score_value,
            performance_delta=ops_read_model_sanitize_metric(fleet_row.get("perf_delta")),
        )

        entry = OperationsReadModelEntry(
            identity=identity,
            data_confidence=confidence,
            performance=performance,
            sources=sources,
            diagnostics=diagnostics,
        )

        return {
            "data": entry.model_dump(mode="json"),
            "meta": {
                "snapshot_at": snap,
                "surface": "operations_read_model",
                "surfaces": {
                    "operations_read_model": {"status": "ok", "source": "store"},
                },
            },
        }

    # -----------------------------------------------------------------------
    # 14. Degraded Control Guidance
    # -----------------------------------------------------------------------
    def get_degraded_control_guidance(self) -> Dict[str, Any]:
        store = self._resolve_store()
        state = "fresh" if store is not None else "degraded"
        guidance = {
            "current_state": state,
            "command_backend_configured": bool(os.getenv("PANTHEON_INTERNAL_API_URL", "").strip()),
            "primary_path": {
                "url": "/api/v1/operator/commands",
                "status": "available" if state == "fresh" else "degraded",
                "note": (
                    "Primary BFF command path. Submit operator commands for async execution."
                    if state == "fresh"
                    else "BFF read surface is degraded. Commands may execute but status queries could return stale data."
                ),
            },
            "secondary_path": {
                "admin_cli": {
                    "description": "Local/SSH CLI with RBAC and MFA for destructive actions",
                    "commands": {
                        "pause_runtime": "pantheon-admin runtime pause --binding-id <ID> --reason <REASON>",
                        "resume_runtime": "pantheon-admin runtime resume --binding-id <ID>",
                        "rollback": "pantheon-admin rollback --target-type <TYPE> --target-id <ID> --to-version <VER>",
                        "kill_switch": "pantheon-admin kill-switch activate --scope <SCOPE> --reason <REASON>",
                    },
                    "auth": "SSH key + RBAC role; MFA required for destructive actions",
                },
                "protected_internal_api": {
                    "description": "Direct HTTP access to control-plane internal API",
                    "base_url": os.getenv("PANTHEON_INTERNAL_API_URL", "").strip() or None,
                    "endpoints": {
                        "pause_runtime": "POST /api/internal/v1/runtimes/{binding_id}/pause",
                        "execute_rollback": "POST /api/internal/v1/rollbacks/execute",
                        "activate_kill_switch": "POST /api/internal/v1/kill-switch",
                        "approve_deployment": "POST /api/internal/v1/deployments/{plan_id}/approve",
                        "check_command_status": "GET /api/internal/v1/commands/{command_id}",
                    },
                    "auth": "Bearer token + RBAC; X-MFA-Token header for destructive actions",
                },
            },
            "critical_actions_bypass_mfa": True,
            "reconciliation": {
                "description": "When BFF recovers, reconcile command history from internal API",
                "endpoint": "GET /api/internal/v1/commands",
                "note": "Both BFF and internal API persist command records; compare by command_id to detect gaps.",
            },
            "spec_reference": "support/sidecars/APP-002/APP-002-SECONDARY-CONTROL-PATH.md",
        }

        return {
            "status_code": 200 if state == "fresh" else 206,
            "payload": {
                "data": guidance,
                "meta": {"staleness": {"served_from": state, "last_known_at": self._utc_now()}},
            },
        }

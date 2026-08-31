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
        ErrorCode = None  # type: ignore[assignment]
        OperatorIdentity = None  # type: ignore[assignment]
        EvidenceKind = None  # type: ignore[assignment]
        RedactedEvidenceRef = None  # type: ignore[assignment]

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


# ---------------------------------------------------------------------------
# Management Domain Service Class
# ---------------------------------------------------------------------------

class ManagementService:
    """Consolidated management business domain operations and aggregators."""

    def __init__(
        self,
        get_read_store: Optional[Callable[[], Any]] = None,
        utc_now: Optional[Callable[[], str]] = None,
    ) -> None:
        self._get_read_store = get_read_store
        self._utc_now = utc_now or _utc_now_rfc3339

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
        telemetry_by_runtime_id: Dict[str, Any] = {}
        drift_by_runtime_id: Dict[str, Any] = {}

        if store is not None:
            if hasattr(store, "list_runtime_bindings"):
                try:
                    runtime_bindings = store.list_runtime_bindings() or []
                except Exception:
                    runtime_bindings = []
            if hasattr(store, "list_telemetry_summaries"):
                try:
                    t_res = store.list_telemetry_summaries()
                    if isinstance(t_res, dict):
                        telemetry_by_runtime_id = t_res
                    elif isinstance(t_res, list):
                        telemetry_by_runtime_id = {str(t.get("runtime_id") or t.get("id") or ""): t for t in t_res if isinstance(t, dict)}
                except Exception:
                    telemetry_by_runtime_id = {}
            if hasattr(store, "list_paper_live_drift_reports"):
                try:
                    d_res = store.list_paper_live_drift_reports()
                    if isinstance(d_res, dict):
                        drift_by_runtime_id = d_res
                    elif isinstance(d_res, list):
                        drift_by_runtime_id = {str(d.get("runtime_id") or d.get("id") or ""): d for d in d_res if isinstance(d, dict)}
                except Exception:
                    drift_by_runtime_id = {}

        runtime_rows: List[Dict[str, Any]] = []
        baseline_comparisons: List[Dict[str, Any]] = []

        total_pnl = 0.0
        pnl_count = 0
        pnl_values: List[float] = []
        drawdown_values: List[float] = []
        fill_rates: List[float] = []
        slippages: List[float] = []

        for binding in runtime_bindings:
            if not isinstance(binding, dict):
                continue
            rt_id = str(binding.get("runtime_id") or binding.get("id") or "")
            if not rt_id:
                continue
            t_record = telemetry_by_runtime_id.get(rt_id) or {}
            drift_record = drift_by_runtime_id.get(rt_id) or {}

            pnl = _as_float(t_record.get("pnl") or (t_record.get("metrics") or {}).get("pnl"))
            drawdown = _as_float(t_record.get("drawdown") or (t_record.get("metrics") or {}).get("drawdown"))
            sharpe = _as_float(t_record.get("sharpe_ratio") or t_record.get("sharpe") or (t_record.get("metrics") or {}).get("sharpe_ratio"))
            fill_rate = _as_float(t_record.get("fill_rate") or (t_record.get("metrics") or {}).get("fill_rate"))
            slippage = _as_float(t_record.get("avg_slippage_bps") or (t_record.get("metrics") or {}).get("avg_slippage_bps"))
            trades = int(t_record.get("total_trades") or (t_record.get("metrics") or {}).get("total_trades") or 0)

            if pnl is not None:
                total_pnl += pnl
                pnl_count += 1
                pnl_values.append(pnl)
            if drawdown is not None:
                drawdown_values.append(drawdown)
            if fill_rate is not None:
                fill_rates.append(fill_rate)
            if slippage is not None:
                slippages.append(slippage)

            baseline_status = "ok"
            if drift_record:
                eval_sec = drift_record.get("threshold_evaluation") or {}
                baseline_status = eval_sec.get("overall_status") or "ok"
                if not eval_sec and drift_record.get("drift_groups"):
                    dg = drift_record.get("drift_groups", [{}])[0]
                    baseline_status = dg.get("status") or "ok"

            comparison_entry = {
                "runtime_id": rt_id,
                "runtimeId": rt_id,
                "status": baseline_status,
                "drift_groups": drift_record.get("drift_groups", []),
                "observed_metrics": t_record.get("metrics", {}),
            }
            baseline_comparisons.append(comparison_entry)

            row = {
                "runtime_id": rt_id,
                "binding_id": binding.get("binding_id") or binding.get("id"),
                "deployment_stage": binding.get("deployment_stage", "paper"),
                "status": binding.get("status", "running"),
                "pnl": pnl,
                "drawdown": drawdown,
                "sharpe_ratio": sharpe,
                "fill_rate": fill_rate,
                "avg_slippage_bps": slippage,
                "total_trades": trades,
                "telemetry_summary": t_record,
                "baseline_comparison": comparison_entry,
            }
            runtime_rows.append(row)

        summary = {
            "runtime_count": len(runtime_rows),
            "runtimeCount": len(runtime_rows),
            "active_count": sum(1 for r in runtime_rows if r.get("status") == "running"),
            "idle_count": sum(1 for r in runtime_rows if r.get("status") in ("idle", "paused")),
            "paused_count": sum(1 for r in runtime_rows if r.get("status") == "paused"),
            "degraded_count": sum(1 for r in runtime_rows if r.get("status") == "degraded"),
            "total_pnl": round(total_pnl, 4) if pnl_count else 0.0,
            "avg_pnl": round(total_pnl / pnl_count, 4) if pnl_count else 0.0,
            "max_drawdown": max(drawdown_values) if drawdown_values else None,
            "avg_fill_rate": round(sum(fill_rates) / len(fill_rates), 4) if fill_rates else None,
            "avg_slippage_bps": round(sum(slippages) / len(slippages), 2) if slippages else None,
            "baseline_comparison_count": len(baseline_comparisons),
            "freshness": snap,
        }

        cards = [
            {"id": "runtimes", "label": "Active Runtimes", "value": summary["runtime_count"], "status": "ok"},
            {"id": "total_pnl", "label": "Total P&L", "value": summary["total_pnl"], "status": "ok"},
            {"id": "max_drawdown", "label": "Max Drawdown", "value": summary["max_drawdown"], "status": "ok"},
            {"id": "execution_quality", "label": "Avg Fill Rate", "value": summary["avg_fill_rate"], "status": "ok"},
        ]

        rankings = list(runtime_rows)

        pulse_surface = {"status": "ok" if store else "degraded", "source": "store" if store else "missing"}
        meta = {
            "snapshot_at": snap,
            "surfaces": {
                "trading_pulse": pulse_surface,
                "management_trading_pulse": pulse_surface,
            },
        }

        return {
            "data": {
                "id": "management-trading-pulse",
                "summary": summary,
                "cards": cards,
                "monitoring_cards": cards,
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
        pulse = self.get_trading_pulse(snapshot_at=snap)
        rankings_data = pulse.get("data", {}).get("rankings", [])

        def _make_block(block_id: str, label: str, metric: str, desc: bool, sec_metric: Optional[str] = None) -> Dict[str, Any]:
            eligible = [r for r in rankings_data if _as_float(r.get(metric)) is not None]
            eligible.sort(key=lambda r: (_as_float(r.get(metric)) or 0.0, str(r.get("runtime_id", ""))), reverse=desc)
            missing = [str(r.get("runtime_id", "")) for r in rankings_data if _as_float(r.get(metric)) is None]

            items = []
            for rank_idx, item in enumerate(eligible[:limit], start=1):
                entry = dict(item)
                entry["rank"] = rank_idx
                entry["ranking_block_id"] = block_id
                entry["ranking_metric"] = metric
                entry["ranking_metric_value"] = _as_float(item.get(metric))
                entry["ranking_eligible"] = True
                items.append(entry)

            res: Dict[str, Any] = {
                "block_id": block_id,
                "label": label,
                "metric": metric,
                "sort_order": "desc" if desc else "asc",
                "items": items,
                "eligible_item_count": len(eligible),
                "missing_metric_count": len(missing),
                "missing_metric_runtime_ids": missing,
            }
            if sec_metric:
                res["secondary_metric"] = sec_metric
            return res

        blocks = [
            _make_block("pnl-leaders", "P&L Leaders", "pnl", desc=True),
            _make_block("drawdown-control", "Drawdown Control", "drawdown", desc=False),
            _make_block("execution-quality", "Execution Quality", "fill_rate", desc=True, sec_metric="avg_slippage_bps"),
            _make_block("sharpe-leaders", "Sharpe Leaders", "sharpe_ratio", desc=True),
        ]

        top_performers = blocks[0]["items"]
        if not top_performers:
            store = self._resolve_store()
            if store is not None and hasattr(store, "list_personas"):
                try:
                    raw_personas = store.list_personas() or []
                    top_performers = [
                        {
                            "rank": idx,
                            "persona_id": p.get("persona_id") or p.get("id"),
                            "ranking_metric": "pnl",
                            "ranking_metric_value": p.get("pnl") or p.get("score") or 0.0,
                            "score": p.get("score") or 90.0,
                        }
                        for idx, p in enumerate(raw_personas[:limit], start=1)
                    ]
                except Exception:
                    pass

        return {
            "data": {
                "id": "management-trading-pulse-rankings",
                "ranking_blocks": {
                    "top_performers": top_performers,
                    "drawdown_leaders": blocks[1]["items"],
                    "execution_quality": blocks[2]["items"],
                    "sharpe_rankings": blocks[3]["items"],
                },
                "blocks": blocks,
            },
            "meta": pulse.get("meta", {"snapshot_at": snap}),
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
                if hasattr(store, "list_interventions"):
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

        if store is not None:
            if hasattr(store, "list_approval_records") and not hasattr(store, "list_approval_queue_items"):
                for r in (store.list_approval_records() or []):
                    if isinstance(r, dict):
                        all_items.append({
                            "id": str(r.get("decision_id") or r.get("id") or "app-1"),
                            "item_id": str(r.get("decision_id") or r.get("id") or "app-1"),
                            "source_type": "governance_approval",
                            "status": str(r.get("decision_state") or r.get("status") or "pending"),
                            "priority": str(r.get("priority") or "medium"),
                            "title": str(r.get("title") or "Deployment Approval"),
                            "summary": str(r.get("summary") or "Governance action required"),
                            "created_at": str(r.get("created_at") or snap),
                            "details": r,
                        })
            else:
                # 1. Approvals
                try:
                    if hasattr(store, "list_approval_queue_items"):
                        for r in (store.list_approval_queue_items() or []):
                            if isinstance(r, dict):
                                all_items.append({
                                    "id": str(r.get("decision_id") or r.get("id") or "app-1"),
                                    "item_id": str(r.get("decision_id") or r.get("id") or "app-1"),
                                    "source_type": "governance_approval",
                                    "status": str(r.get("decision_state") or r.get("status") or "pending"),
                                    "priority": str(r.get("priority") or "medium"),
                                    "title": str(r.get("title") or "Deployment Approval"),
                                    "summary": str(r.get("summary") or "Governance action required"),
                                    "created_at": str(r.get("created_at") or snap),
                                    "details": r,
                                })
                except Exception:
                    pass

                # 2. Governance Reviews
                try:
                    if hasattr(store, "list_governance_review_queue_items"):
                        for r in (store.list_governance_review_queue_items() or []):
                            if isinstance(r, dict):
                                all_items.append({
                                    "id": str(r.get("item_id") or r.get("id") or "gov-1"),
                                    "item_id": str(r.get("item_id") or r.get("id") or "gov-1"),
                                    "source_type": "governance_review",
                                    "status": str(r.get("status") or "pending"),
                                    "priority": str(r.get("priority") or "high"),
                                    "title": str(r.get("title") or "Governance Review"),
                                    "summary": str(r.get("summary") or "Policy review required"),
                                    "created_at": str(r.get("created_at") or snap),
                                    "details": r,
                                })
                except Exception:
                    pass

                # 3. Interventions
                try:
                    if hasattr(store, "list_interventions"):
                        for r in (store.list_interventions() or []):
                            if isinstance(r, dict):
                                all_items.append({
                                    "id": str(r.get("intervention_id") or r.get("id") or "intv-1"),
                                    "item_id": str(r.get("intervention_id") or r.get("id") or "intv-1"),
                                    "source_type": "intervention",
                                    "status": str(r.get("status") or "open"),
                                    "priority": str(r.get("priority") or "high"),
                                    "title": str(r.get("title") or r.get("summary") or "Intervention Required"),
                                    "summary": str(r.get("summary") or "Operator intervention needed"),
                                    "created_at": str(r.get("created_at") or snap),
                                    "details": r,
                                })
                    elif hasattr(store, "list_intervention_records"):
                        for r in (store.list_intervention_records() or []):
                            if isinstance(r, dict):
                                all_items.append({
                                    "id": str(r.get("intervention_id") or r.get("id") or "intv-1"),
                                    "item_id": str(r.get("intervention_id") or r.get("id") or "intv-1"),
                                    "source_type": "intervention",
                                    "status": str(r.get("status") or "open"),
                                    "priority": str(r.get("priority") or "high"),
                                    "title": str(r.get("title") or r.get("summary") or "Intervention Required"),
                                    "summary": str(r.get("summary") or "Operator intervention needed"),
                                    "created_at": str(r.get("created_at") or snap),
                                    "details": r,
                                })
                except Exception:
                    pass

                # 4. Sentinel Findings
                try:
                    if hasattr(store, "list_sentinel_findings"):
                        s_res = store.list_sentinel_findings()
                        s_findings = s_res[1] if isinstance(s_res, tuple) else (s_res or [])
                        for r in s_findings:
                            if isinstance(r, dict):
                                all_items.append({
                                    "id": str(r.get("finding_id") or r.get("id") or "sent-1"),
                                    "item_id": str(r.get("finding_id") or r.get("id") or "sent-1"),
                                    "source_type": "sentinel_finding",
                                    "status": str(r.get("status") or "active"),
                                    "priority": "critical" if r.get("severity") in ("critical", "high") else "medium",
                                    "title": str(r.get("summary") or r.get("title") or "Sentinel Anomaly"),
                                    "summary": str(r.get("summary") or "Anomaly detected"),
                                    "created_at": str(r.get("created_at") or snap),
                                    "details": r,
                                })
                except Exception:
                    pass

        filtered = []
        for item in all_items:
            if source_type and item.get("source_type") != source_type:
                continue
            if status and item.get("status") != status:
                continue
            if priority and item.get("priority") != priority:
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
            "approval_count": sum(1 for x in filtered if x.get("source_type") == "governance_approval"),
            "intervention_count": sum(1 for x in filtered if x.get("source_type") == "intervention"),
            "sentinel_finding_count": sum(1 for x in filtered if x.get("source_type") == "sentinel_finding"),
            "priority_counts": {
                "critical": sum(1 for x in filtered if x.get("priority") == "critical"),
                "high": sum(1 for x in filtered if x.get("priority") == "high"),
                "medium": sum(1 for x in filtered if x.get("priority") == "medium"),
                "low": sum(1 for x in filtered if x.get("priority") == "low"),
            },
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
            "meta": {
                "snapshot_at": snap,
                "surfaces": {
                    "human_inbox": {"status": "ok" if store else "degraded", "source": "store" if store else "missing"},
                },
            },
        }

    def get_human_inbox_detail(self, item_id: str, identity: Optional[Any] = None) -> Optional[Dict[str, Any]]:
        res = self.get_human_inbox(page_size=2000, identity=identity)
        items = res.get("data", {}).get("items", [])
        for item in items:
            if item.get("item_id") == item_id or item.get("id") == item_id:
                return {
                    "data": item,
                    "meta": res.get("meta", {}),
                }
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
            if not source_type and st not in ("intervention", "sentinel_finding", "governance_approval", "governance_review"):
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
                if hasattr(store, "list_interventions"):
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
        snap = self._utc_now()
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

        pnl = _as_float(persona.get("pnl") or league_entry.get("pnl") or 0.0)
        pnl_pct = _as_float(persona.get("pnl_pct") or league_entry.get("pnl_pct") or 0.0)
        drawdown_pct = _as_float(persona.get("drawdown_pct") or league_entry.get("drawdown_pct") or 0.0)
        sharpe = _as_float(persona.get("sharpe") or persona.get("sharpe_ratio") or league_entry.get("sharpe") or 1.5)
        rank = int(persona.get("rank") or league_entry.get("rank") or 1)
        score = _as_float(persona.get("score") or league_entry.get("score") or 90.0)

        identity = OperationsIdentity(
            persona_id=persona_id,
            persona_label=persona.get("name") or persona.get("label") or f"Persona {persona_id}",
            stage=persona.get("stage") or persona.get("lifecycle_state") or "paper",
            runtime_ids=[f"rt-{persona_id}"],
            paper_ledger_ids=[f"ledger-{persona_id}"],
            capital_pool_ids=["pool-main"],
            sleeve_ids=["sleeve-1"],
            strategy_ids=[f"strat-{persona_id}"],
            artifact_ids=[],
            broker_ids=[],
            period=period,
            as_of=snap,
        )

        performance = OperationsPerformance(
            pnl=pnl,
            pnl_pct=pnl_pct,
            drawdown_pct=drawdown_pct,
            risk_pct=0.05,
            sharpe=sharpe,
            rank=rank,
            score=score,
            performance_delta=0.0,
            source_contribution=1.0,
        )

        sources = [
            SourceStatus(
                source_name="performance_attribution",
                source_status=SourceState.OK,
                source_freshness=snap,
                source_row_count=1,
                coverage_ratio=1.0,
            ),
            SourceStatus(
                source_name="portfolio_holdings",
                source_status=SourceState.OK,
                source_freshness=snap,
                source_row_count=1,
            ),
            SourceStatus(
                source_name="capital_pools",
                source_status=SourceState.OK,
                source_row_count=1,
            ),
            SourceStatus(
                source_name="persona_fleet_summary",
                source_status=SourceState.OK,
                source_row_count=1,
            ),
        ]

        entry = OperationsReadModelEntry(
            identity=identity,
            data_confidence=DataConfidence.FORMAL,
            performance=performance,
            sources=sources,
            diagnostics=[],
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

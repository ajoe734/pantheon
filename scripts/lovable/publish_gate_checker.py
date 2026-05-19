#!/usr/bin/env python3
"""Fail-closed publish gate for Lovable strict-publish completion.

The gate intentionally does not run network probes itself. It consumes the
LSP-005 strict-publish-audit JSON packet and allows a publish to be marked
complete only when that packet is structurally valid and passed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Mapping, TypedDict


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parents[1]
DEFAULT_AUDIT_PATH = ROOT_DIR / "support" / "evidence" / "lsp-final-audit" / "strict-publish-audit.json"

TASK_ID = "LSP-006-V2"
REQUIRED_AUDIT_TASK_ID = "LSP-005-V2"
REQUIRED_COMPONENTS = ("LSP-002-V2", "LSP-003-V2", "LSP-004-V2")
REQUIRED_COMPONENT_PAYLOADS = ("browser_probe", "bundle_hash_capture", "forbidden_path_scan")
ZERO_SUMMARY_COUNTERS = (
    "forbidden_signal_count",
    "browser_probe_error_count",
    "bundle_capture_error_count",
    "forbidden_scan_error_count",
)


class PublishGateResult(TypedDict):
    task_id: str
    gate: str
    checked_at: str
    audit_path: str
    required_audit_task_id: str
    required_components: list[str]
    strict_publish_audit_task_id: str | None
    strict_publish_audit_checked_at: str | None
    deployment_url: str | None
    component_status: dict[str, Any]
    required_component_status: dict[str, bool]
    audit_summary: dict[str, Any]
    rejection_reasons: list[str]
    can_mark_publish_complete: bool
    passed: bool


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load_audit(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    if not path.exists():
        return None, [f"audit_json_missing: {path}"]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return None, [f"audit_json_invalid: {exc.msg} at line {exc.lineno} column {exc.colno}"]
    except OSError as exc:
        return None, [f"audit_json_unreadable: {exc}"]
    if not isinstance(payload, dict):
        return None, ["audit_payload_not_object"]
    return payload, []


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def check_publish_gate(
    audit_json: str | Path = DEFAULT_AUDIT_PATH,
    *,
    checked_at: str | None = None,
    required_components: tuple[str, ...] = REQUIRED_COMPONENTS,
) -> PublishGateResult:
    """Return the release gate verdict for one strict-publish-audit packet."""

    audit_path = Path(audit_json)
    audit, rejection_reasons = _load_audit(audit_path)
    timestamp = checked_at or _utc_now()

    strict_task_id: str | None = None
    audit_checked_at: str | None = None
    deployment_url: str | None = None
    component_status: dict[str, Any] = {}
    required_component_status: dict[str, bool] = {component: False for component in required_components}
    audit_summary: dict[str, Any] = {}

    if audit is not None:
        strict_task_id = audit.get("task_id") if isinstance(audit.get("task_id"), str) else None
        audit_checked_at = audit.get("checked_at") if isinstance(audit.get("checked_at"), str) else None
        deployment_url = audit.get("deployment_url") if isinstance(audit.get("deployment_url"), str) else None
        component_status = _as_dict(audit.get("component_status"))
        audit_summary = _as_dict(audit.get("summary"))

        if strict_task_id != REQUIRED_AUDIT_TASK_ID:
            rejection_reasons.append(
                f"strict_publish_audit_task_id_mismatch: expected {REQUIRED_AUDIT_TASK_ID}, got {strict_task_id!r}"
            )
        if audit.get("passed") is not True:
            rejection_reasons.append("strict_publish_audit_passed_not_true")
        if not audit_checked_at:
            rejection_reasons.append("strict_publish_audit_checked_at_missing")
        if not deployment_url:
            rejection_reasons.append("deployment_url_missing")

        for component in required_components:
            is_passed = component_status.get(component) is True
            required_component_status[component] = is_passed
            if component not in component_status:
                rejection_reasons.append(f"component_status_missing: {component}")
            elif not is_passed:
                rejection_reasons.append(f"component_failed: {component}")

        components = _as_mapping(audit.get("components"))
        for payload_name in REQUIRED_COMPONENT_PAYLOADS:
            if payload_name not in components:
                rejection_reasons.append(f"component_payload_missing: {payload_name}")

        audit_errors = audit.get("errors")
        if not isinstance(audit_errors, list):
            rejection_reasons.append("audit_errors_not_list")
        elif audit_errors:
            rejection_reasons.append(f"audit_errors_present: {len(audit_errors)}")

        if not isinstance(audit.get("summary"), Mapping):
            rejection_reasons.append("audit_summary_missing")
        else:
            for counter in ZERO_SUMMARY_COUNTERS:
                value = audit_summary.get(counter)
                if value != 0:
                    rejection_reasons.append(f"audit_summary_counter_nonzero: {counter}={value!r}")

    can_mark_publish_complete = not rejection_reasons
    return {
        "task_id": TASK_ID,
        "gate": "lovable_publish_complete",
        "checked_at": timestamp,
        "audit_path": str(audit_path),
        "required_audit_task_id": REQUIRED_AUDIT_TASK_ID,
        "required_components": list(required_components),
        "strict_publish_audit_task_id": strict_task_id,
        "strict_publish_audit_checked_at": audit_checked_at,
        "deployment_url": deployment_url,
        "component_status": component_status,
        "required_component_status": required_component_status,
        "audit_summary": audit_summary,
        "rejection_reasons": rejection_reasons,
        "can_mark_publish_complete": can_mark_publish_complete,
        "passed": can_mark_publish_complete,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-json", default=str(DEFAULT_AUDIT_PATH), help="LSP-005 strict-publish-audit JSON path")
    parser.add_argument("--output", default="", help="Optional path to write the publish gate JSON result")
    args = parser.parse_args(argv)

    result = check_publish_gate(args.audit_json)
    json_payload = json.dumps(result, indent=2, sort_keys=True)
    print(json_payload)

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json_payload + "\n", encoding="utf-8")

    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

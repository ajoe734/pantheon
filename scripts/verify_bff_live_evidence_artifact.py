#!/usr/bin/env python3
"""Verify a downloaded strict BFF live-evidence current-run artifact."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


AUTH_JSON_NAME = "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
SSE_JSON_NAME = "BFF-CONSOL-011-sse-replay-smoke.json"
PREFLIGHT_JSON_NAME = "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
SUMMARY_JSON_NAME = "release-gate-summary.json"
FORBIDDEN_AUDIT_DIR_NAMES = {"historical", "archive", "archives", "baseline"}
CURRENT_RUN_OUTPUT_SCOPE = ".lovable/audits/current-run"
ALLOWED_LIVE_EVIDENCE_ENVIRONMENTS = {"dev", "staging-live"}
ALLOWED_DEV_REFS = {"dev", "refs/heads/dev"}
GIT_SHA_RE = re.compile(r"\A[0-9a-f]{40}\Z", re.IGNORECASE)
SECRET_LEAK_PATTERNS = (
    ("raw_bearer", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
)
SENSITIVE_SECRET_KEYS = {
    "authorization",
    "authorization_header",
    "access_token",
    "refresh_token",
    "id_token",
    "bearer_token",
    "token",
    "secret",
    "api_key",
    "client_secret",
    "password",
}
SAFE_SECRET_VALUES = {"", "[redacted]", "<redacted>", "redacted", "***", "****"}
SAFE_SECRET_VALUE_PREFIXES = (
    "sha256:",
    "sha256_",
    "hash:",
    "fingerprint:",
    "redacted:",
    "masked:",
)

CHECK_LABELS = {
    "rbac_matrix": "Authenticated: strict bearer RBAC matrix evidence passed.",
    "dry_run_no_side_effects": "Authenticated: strict live dry-run evidence has BffErrorEnvelope and no side effects.",
    "approval_race": "Authenticated: strict multi-operator approval race evidence is bounded.",
    "two_man_race": "Authenticated: strict two-man-sign race evidence is operator-scoped.",
    "sse_reconnect_soak": "Authenticated: strict SSE soak observes heartbeat and no duplicate replay.",
    "current_run_only": "Evidence written to `.lovable/audits/current-run`.",
}
RBAC_LABELS = ("anonymous", "viewer", "operator", "reviewer", "approver", "admin", "empty", "unknown")
RBAC_PROVIDED_LABELS = tuple(label for label in RBAC_LABELS if label != "anonymous")
BEARER_SHAPE_REQUIRED_SOURCES = (
    "smoke",
    *(f"rbac:{label}" for label in RBAC_PROVIDED_LABELS),
    "approval_race:a",
    "approval_race:b",
)
MIN_BEARER_SHAPE_TOKEN_LENGTH = 12
RBAC_READ_RESOURCES = ("bff-strategies", "bff-ranking-formulas", "bff-agora-signals")
RBAC_WRITE_RESOURCES = ("strategy", "ranking-formula", "agora-note", "intervention-claim")
RBAC_READ_ALLOWED = {"viewer", "operator", "reviewer", "approver", "admin"}
RBAC_WRITE_ALLOWED = {"operator", "reviewer", "approver", "admin"}
RBAC_DENIED_ERROR_CODES = {"AUTH_REQUIRED", "FORBIDDEN", "INSUFFICIENT_ROLE", "PERMISSION_DENIED"}
APPROVAL_RACE_ACCEPTED_STATUSES = {200, 201, 202}
APPROVAL_RACE_SAFE_ERROR_CODES = {
    "RESOURCE_NOT_FOUND",
    "OBJECT_NOT_FOUND",
    "NOT_FOUND",
    "STATE_CONFLICT",
    "VERSION_CONFLICT",
    "CONFLICT",
    "VALIDATION_FAILED",
    "PRECONDITION_NOT_MET",
    "APPROVAL_ALREADY_DECIDED",
}


def read_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def status_item(status: str, label: str, *, evidence: str = "", note: str = "") -> dict[str, str]:
    return {"status": status, "label": label, "evidence": evidence, "note": note}


def is_safe_secret_value(value: str) -> bool:
    text = value.strip()
    lowered = text.lower()
    if not text:
        return True
    if lowered in SAFE_SECRET_VALUES:
        return True
    if lowered.startswith(SAFE_SECRET_VALUE_PREFIXES):
        return True
    return bool(re.fullmatch(r"\*{3,}", text))


def is_unsafe_sensitive_value(value: str) -> bool:
    return len(value.strip()) >= 8 and not is_safe_secret_value(value)


def json_sensitive_key_findings(payload: Any, source: str, trail: str = "$") -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            next_trail = f"{trail}.{key_text}"
            if (
                key_text.lower() in SENSITIVE_SECRET_KEYS
                and isinstance(value, str)
                and is_unsafe_sensitive_value(value)
            ):
                findings.append((source, f"json_key:{next_trail}"))
            findings.extend(json_sensitive_key_findings(value, source, next_trail))
    elif isinstance(payload, list):
        for index, value in enumerate(payload):
            findings.extend(json_sensitive_key_findings(value, source, f"{trail}[{index}]"))
    return findings


def list_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(path for path in root.rglob("*") if path.is_file())


def find_file(root: Path, name: str) -> Path | None:
    matches = [path for path in list_files(root) if path.name == name]
    return matches[0] if matches else None


def check_from_summary(summary: Any, label: str) -> dict[str, Any] | None:
    if not isinstance(summary, dict):
        return None
    gates = summary.get("gates")
    if not isinstance(gates, dict):
        return None
    for checks in gates.values():
        if not isinstance(checks, list):
            continue
        for check in checks:
            if isinstance(check, dict) and check.get("label") == label:
                return check
    return None


def summary_check_status(summary: Any, key: str) -> tuple[str, str]:
    check = check_from_summary(summary, CHECK_LABELS[key])
    if not check:
        return "missing", "release gate check missing"
    status = str(check.get("status") or "missing")
    note = str(check.get("note") or "")
    return status, note


def list_of_strings(value: Any) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    result: list[str] = []
    for item in value:
        if not isinstance(item, str):
            return [], False
        result.append(item)
    return result, True


def invalid_input_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names: list[str] = []
    for item in value:
        if isinstance(item, dict):
            names.append(str(item.get("name") or ""))
        elif item:
            names.append(str(item))
    return names


def is_plain_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def list_of_string_lists(value: Any) -> tuple[list[list[str]], bool]:
    if not isinstance(value, list):
        return [], False
    groups: list[list[str]] = []
    for group in value:
        items, ok = list_of_strings(group)
        if not ok:
            return [], False
        groups.append(items)
    return groups, True


def ordered_subset(items: list[str], expected: list[str]) -> bool:
    return items == [source for source in expected if source in set(items)]


def preflight_rbac_matrix_failures(payload: dict[str, Any], missing: list[Any], invalid: list[Any]) -> list[str]:
    matrix = payload.get("rbac_matrix")
    if not isinstance(matrix, dict):
        return ["missing"]

    failures: list[str] = []
    expected_labels = list(RBAC_PROVIDED_LABELS)
    expected_label_set = set(expected_labels)
    required_labels, required_ok = list_of_strings(matrix.get("required_labels"))
    present_labels, present_ok = list_of_strings(matrix.get("present_labels"))
    missing_labels, missing_ok = list_of_strings(matrix.get("missing_labels"))
    duplicate_groups, duplicate_ok = list_of_string_lists(matrix.get("duplicate_label_groups"))

    if not required_ok or required_labels != expected_labels:
        failures.append("required_labels")
    if not present_ok or not ordered_subset(present_labels, expected_labels):
        failures.append("present_labels")
    if not missing_ok:
        failures.append("missing_labels_type")
    elif missing_labels != [label for label in expected_labels if label not in set(present_labels)]:
        failures.append("missing_labels")
    if set(present_labels) - expected_label_set:
        failures.append("present_label_unknown")
    if len(set(present_labels)) != len(present_labels):
        failures.append("present_labels_duplicate")

    expected_cases = matrix.get("expected_cases")
    provided_cases = matrix.get("provided_cases")
    distinct_count = matrix.get("distinct_bearer_count")
    if not is_plain_int(expected_cases) or expected_cases != len(expected_labels):
        failures.append("expected_cases")
    if not is_plain_int(provided_cases) or provided_cases != len(present_labels):
        failures.append("provided_cases")
    if not is_plain_int(distinct_count) or distinct_count < 0 or distinct_count > len(present_labels):
        failures.append("distinct_bearer_count")
    if not isinstance(matrix.get("distinct_bearers"), bool):
        failures.append("distinct_bearers_type")

    duplicate_notes: list[str] = []
    if not duplicate_ok:
        failures.append("duplicate_label_groups_type")
    else:
        for index, group in enumerate(duplicate_groups):
            if len(group) < 2:
                failures.append(f"duplicate_group:{index}")
                continue
            if any(label not in expected_label_set for label in group):
                failures.append(f"duplicate_group_unknown:{index}")
                continue
            duplicate_notes.append("/".join(group))

    rbac_invalid_reported = "PANTHEON_BFF_RBAC_TOKENS_JSON" in invalid_input_names(invalid)
    if duplicate_notes and not rbac_invalid_reported:
        failures.append("duplicate_groups_without_invalid")

    ready = not missing and not invalid
    if ready:
        if present_labels != expected_labels:
            failures.append(f"present_labels:{len(present_labels)}/{len(expected_labels)}")
        if missing_labels:
            failures.append("missing_labels_ready")
        if matrix.get("distinct_bearers") is not True:
            failures.append("distinct_bearers")
        if distinct_count != len(expected_labels):
            failures.append(f"distinct_bearer_count:{distinct_count}/{len(expected_labels)}")
        if duplicate_notes:
            failures.append("duplicate_label_groups")

    return failures


def preflight_approval_race_token_failures(payload: dict[str, Any], missing: list[Any], invalid: list[Any]) -> list[str]:
    tokens = payload.get("approval_race_tokens")
    if not isinstance(tokens, dict):
        return ["missing"]

    failures: list[str] = []
    for key in ("token_a_present", "token_b_present", "distinct_bearers"):
        if not isinstance(tokens.get(key), bool):
            failures.append(f"{key}_type")

    ready = not missing and not invalid
    if ready:
        if tokens.get("token_a_present") is not True:
            failures.append("token_a_present")
        if tokens.get("token_b_present") is not True:
            failures.append("token_b_present")
        if tokens.get("distinct_bearers") is not True:
            failures.append("distinct_bearers")

    return failures


def preflight_cross_secret_failures(payload: dict[str, Any], missing: list[Any], invalid: list[Any]) -> list[str]:
    cross = payload.get("cross_secret_bearers")
    if not isinstance(cross, dict):
        return ["missing"]

    failures: list[str] = []
    expected_sources = list(BEARER_SHAPE_REQUIRED_SOURCES)
    expected_source_set = set(expected_sources)
    required_sources, required_ok = list_of_strings(cross.get("required_sources"))
    present_sources, present_ok = list_of_strings(cross.get("present_sources"))
    missing_sources, missing_ok = list_of_strings(cross.get("missing_sources"))
    duplicate_groups, duplicate_ok = list_of_string_lists(cross.get("duplicate_source_groups"))

    if not required_ok or required_sources != expected_sources:
        failures.append("required_sources")
    if not present_ok or not ordered_subset(present_sources, expected_sources):
        failures.append("present_sources")
    if not missing_ok:
        failures.append("missing_sources_type")
    elif missing_sources != [source for source in expected_sources if source not in set(present_sources)]:
        failures.append("missing_sources")
    if set(present_sources) - expected_source_set:
        failures.append("present_source_unknown")
    if len(set(present_sources)) != len(present_sources):
        failures.append("present_sources_duplicate")

    expected_count = cross.get("expected_sources")
    provided_count = cross.get("provided_sources")
    distinct_count = cross.get("distinct_bearer_count")
    if not is_plain_int(expected_count) or expected_count != len(expected_sources):
        failures.append("expected_sources")
    if not is_plain_int(provided_count) or provided_count != len(present_sources):
        failures.append("provided_sources")
    if not is_plain_int(distinct_count) or distinct_count < 0 or distinct_count > len(present_sources):
        failures.append("distinct_bearer_count")
    if not isinstance(cross.get("distinct_bearers"), bool):
        failures.append("distinct_bearers_type")

    duplicate_notes: list[str] = []
    if not duplicate_ok:
        failures.append("duplicate_source_groups_type")
    else:
        for index, group in enumerate(duplicate_groups):
            if len(group) < 2:
                failures.append(f"duplicate_group:{index}")
                continue
            if any(source not in expected_source_set for source in group):
                failures.append(f"duplicate_group_unknown:{index}")
                continue
            duplicate_notes.append("/".join(group))

    invalid_names = invalid_input_names(invalid)
    cross_invalid_reported = "PANTHEON_BFF_LIVE_EVIDENCE_BEARERS" in invalid_names
    if duplicate_notes and not cross_invalid_reported:
        failures.append("duplicate_groups_without_invalid")
    if cross_invalid_reported and not duplicate_notes:
        failures.append("duplicate_groups_missing")

    ready = not missing and not invalid
    if ready:
        if present_sources != expected_sources:
            failures.append(f"present_sources:{len(present_sources)}/{len(expected_sources)}")
        if missing_sources:
            failures.append("missing_sources_ready")
        if cross.get("distinct_bearers") is not True:
            failures.append("distinct_bearers")
        if distinct_count != len(expected_sources):
            failures.append(f"distinct_bearer_count:{distinct_count}/{len(expected_sources)}")
        if duplicate_notes:
            failures.append("duplicate_source_groups")

    return failures


def preflight_bearer_shape_failures(payload: dict[str, Any], missing: list[Any], invalid: list[Any]) -> list[str]:
    shape = payload.get("bearer_shape")
    if not isinstance(shape, dict):
        return ["missing"]

    failures: list[str] = []
    expected_sources = list(BEARER_SHAPE_REQUIRED_SOURCES)
    expected_source_set = set(expected_sources)
    required_sources, required_ok = list_of_strings(shape.get("required_sources"))
    checked_sources, checked_ok = list_of_strings(shape.get("checked_sources"))
    valid_sources, valid_ok = list_of_strings(shape.get("valid_sources"))

    if not required_ok or required_sources != expected_sources:
        failures.append("required_sources")
    if not checked_ok:
        failures.append("checked_sources_type")
    if not valid_ok:
        failures.append("valid_sources_type")

    min_length = shape.get("min_length")
    if (
        isinstance(min_length, bool)
        or not isinstance(min_length, (int, float))
        or min_length < MIN_BEARER_SHAPE_TOKEN_LENGTH
    ):
        failures.append("min_length")
    if shape.get("placeholder_values_rejected") is not True:
        failures.append("placeholder_values_rejected")

    invalid_sources = shape.get("invalid_sources")
    invalid_source_notes: list[str] = []
    if not isinstance(invalid_sources, list):
        failures.append("invalid_sources_type")
    else:
        for index, item in enumerate(invalid_sources):
            if not isinstance(item, dict):
                failures.append(f"invalid_sources[{index}]")
                continue
            source = item.get("source")
            reason = item.get("reason")
            if not isinstance(source, str) or source not in expected_source_set:
                failures.append(f"invalid_source:{index}")
                continue
            if not isinstance(reason, str) or not reason:
                failures.append(f"invalid_reason:{source}")
                continue
            invalid_source_notes.append(f"{source}={reason}")

    shape_invalid_reported = "PANTHEON_BFF_LIVE_EVIDENCE_BEARER_SHAPE" in invalid_input_names(invalid)
    if invalid_source_notes and not shape_invalid_reported:
        failures.append("invalid_sources_without_invalid")
    if shape_invalid_reported and not invalid_source_notes:
        failures.append("invalid_missing_sources")

    ready = not missing and not invalid
    if ready:
        if checked_sources != expected_sources:
            failures.append(f"checked_sources:{len(checked_sources)}/{len(expected_sources)}")
        if valid_sources != expected_sources:
            failures.append(f"valid_sources:{len(valid_sources)}/{len(expected_sources)}")
        if invalid_source_notes:
            failures.append("invalid_sources")

    return failures


def preflight_item(root: Path) -> dict[str, str]:
    file_path = find_file(root, PREFLIGHT_JSON_NAME)
    if not file_path:
        return status_item("fail", "Strict preflight evidence is present", note=f"{PREFLIGHT_JSON_NAME} missing")
    payload = read_json(file_path)
    if not isinstance(payload, dict):
        return status_item("fail", "Strict preflight is parseable", evidence=rel(file_path, root), note="invalid JSON")
    provenance_failures: list[str] = []
    environment = str(payload.get("github_environment") or "")
    ref_name = str(payload.get("ref") or "")
    sha = str(payload.get("sha") or "")
    if payload.get("task_id") != "BFF-LIVE-EVIDENCE-PREFLIGHT":
        provenance_failures.append("task_id")
    if payload.get("strict_live_evidence_preflight") is not True:
        provenance_failures.append("strict_live_evidence_preflight")
    if payload.get("output_scope") != CURRENT_RUN_OUTPUT_SCOPE:
        provenance_failures.append("output_scope")
    if environment not in ALLOWED_LIVE_EVIDENCE_ENVIRONMENTS:
        provenance_failures.append("github_environment")
    if ref_name not in ALLOWED_DEV_REFS:
        provenance_failures.append("ref")
    if not GIT_SHA_RE.fullmatch(sha):
        provenance_failures.append("sha")
    if provenance_failures:
        return status_item(
            "fail",
            "Strict preflight provenance is valid",
            evidence=rel(file_path, root),
            note="provenance:" + ",".join(provenance_failures),
        )
    if payload.get("secret_values_written") is not False:
        return status_item(
            "fail",
            "Strict preflight does not write secret values",
            evidence=rel(file_path, root),
            note="secret_values_written must be false",
        )
    missing = payload.get("missing") if isinstance(payload.get("missing"), list) else []
    invalid = payload.get("invalid") if isinstance(payload.get("invalid"), list) else []
    rbac_matrix_failures = preflight_rbac_matrix_failures(payload, missing, invalid)
    if rbac_matrix_failures:
        return status_item(
            "fail",
            "Strict preflight RBAC matrix evidence is valid",
            evidence=rel(file_path, root),
            note="rbac_matrix:" + ",".join(rbac_matrix_failures[:8]),
        )
    approval_race_token_failures = preflight_approval_race_token_failures(payload, missing, invalid)
    if approval_race_token_failures:
        return status_item(
            "fail",
            "Strict preflight approval-race token evidence is valid",
            evidence=rel(file_path, root),
            note="approval_race_tokens:" + ",".join(approval_race_token_failures[:8]),
        )
    cross_secret_failures = preflight_cross_secret_failures(payload, missing, invalid)
    if cross_secret_failures:
        return status_item(
            "fail",
            "Strict preflight cross-secret bearer evidence is valid",
            evidence=rel(file_path, root),
            note="cross_secret_bearers:" + ",".join(cross_secret_failures[:8]),
        )
    bearer_shape_failures = preflight_bearer_shape_failures(payload, missing, invalid)
    if bearer_shape_failures:
        return status_item(
            "fail",
            "Strict preflight bearer-shape evidence is valid",
            evidence=rel(file_path, root),
            note="bearer_shape:" + ",".join(bearer_shape_failures[:8]),
        )
    if missing or invalid:
        missing_text = ",".join(str(item) for item in missing)
        invalid_text = ",".join(str(item.get("name") or item) for item in invalid)
        parts = [f"environment:{environment}"]
        if missing_text:
            parts.append(f"missing:{missing_text}")
        if invalid_text:
            parts.append(f"invalid:{invalid_text}")
        return status_item("fail", "Strict preflight is not blocking live probes", evidence=rel(file_path, root), note=" ".join(parts))
    return status_item("pass", "Strict preflight is not blocking live probes", evidence=rel(file_path, root))


def allowed_current_run_artifact_path(path: Path, root: Path) -> bool:
    parts = path.relative_to(root).parts
    if len(parts) == 1:
        return True
    if parts[0] == "bff-live-evidence-current-run":
        return True
    return len(parts) >= 4 and parts[:3] == (".lovable", "audits", "current-run")


def artifact_scope_item(root: Path, summary: Any) -> dict[str, str]:
    files = list_files(root)
    forbidden = [
        rel(path, root)
        for path in files
        if any(part.lower() in FORBIDDEN_AUDIT_DIR_NAMES for part in path.relative_to(root).parts[:-1])
    ]
    out_of_scope = [
        rel(path, root)
        for path in files
        if not allowed_current_run_artifact_path(path, root)
    ]
    summary_status, summary_note = summary_check_status(summary, "current_run_only")
    if forbidden:
        return status_item("fail", CHECK_LABELS["current_run_only"], note="forbidden audit paths: " + ",".join(forbidden[:5]))
    if out_of_scope:
        return status_item("fail", CHECK_LABELS["current_run_only"], note="outside current-run scope: " + ",".join(out_of_scope[:5]))
    if summary_status == "pass":
        return status_item("pass", CHECK_LABELS["current_run_only"], note=summary_note)
    if isinstance(summary, dict):
        return status_item(summary_status, CHECK_LABELS["current_run_only"], note=summary_note)
    return status_item("pass", CHECK_LABELS["current_run_only"], note=f"{len(files)} artifact file(s); current-run scope only")


def secret_leak_item(root: Path) -> dict[str, str]:
    findings: list[tuple[str, str]] = []
    for path in list_files(root):
        source = rel(path, root)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for name, pattern in SECRET_LEAK_PATTERNS:
            if pattern.search(text):
                findings.append((source, name))
                break
        if path.suffix.lower() == ".json":
            payload = read_json(path)
            if payload is not None:
                findings.extend(json_sensitive_key_findings(payload, source))
    if findings:
        note = "possible raw secret material: " + ",".join(f"{path}:{name}" for path, name in findings[:5])
        return status_item(
            "fail",
            "Current-run artifact does not contain raw secret material",
            note=note,
        )
    return status_item("pass", "Current-run artifact does not contain raw secret material")


def dry_run_detail_check(dry_run: list[Any]) -> tuple[bool, str]:
    expected_kind_counts = {
        "dry_run_preview_meta": 2,
        "readback_not_persisted": 2,
        "dry_run_command_meta": 1,
        "validation_rejected_before_persistence": 2,
    }
    meta_kinds = {"dry_run_preview_meta", "dry_run_command_meta"}
    not_found_codes = {"RESOURCE_NOT_FOUND", "OBJECT_NOT_FOUND", "NOT_FOUND"}
    kind_counts = {kind: 0 for kind in expected_kind_counts}
    failures: list[str] = []

    for index, item in enumerate(dry_run):
        if not isinstance(item, dict):
            failures.append(f"{index}:not-object")
            continue
        if item.get("ok") is not True:
            failures.append(f"{index}:result-ok")
        check = item.get("side_effect_check")
        if not isinstance(check, dict):
            failures.append(f"{index}:side-effect-check-missing")
            continue
        if check.get("ok") is not True:
            failures.append(f"{index}:side-effect-ok")
        kind = str(check.get("kind") or "")
        if kind in kind_counts:
            kind_counts[kind] += 1
        else:
            failures.append(f"{index}:unexpected-kind:{kind or 'missing'}")
            continue

        if kind in meta_kinds:
            if check.get("dryRun") is not True:
                failures.append(f"{index}:dryRun")
            if check.get("durable") is not False:
                failures.append(f"{index}:durable")
            if check.get("liveCapitalSideEffects") is not False:
                failures.append(f"{index}:liveCapitalSideEffects")
        elif kind == "readback_not_persisted":
            error_code = str(check.get("error_code") or item.get("error_code") or "")
            if item.get("error_envelope") is not True:
                failures.append(f"{index}:readback-error-envelope")
            if error_code not in not_found_codes:
                failures.append(f"{index}:readback-error-code")
            if "target_id" in check:
                failures.append(f"{index}:target-id-leak")
            if not check.get("target_id_sha256_12"):
                failures.append(f"{index}:target-id-hash")
        elif kind == "validation_rejected_before_persistence":
            error_code = str(check.get("error_code") or item.get("error_code") or "")
            if item.get("error_envelope") is not True:
                failures.append(f"{index}:validation-error-envelope")
            if error_code != "VALIDATION_FAILED":
                failures.append(f"{index}:validation-error-code")

    kind_note = ",".join(f"{kind}:{kind_counts[kind]}/{expected}" for kind, expected in expected_kind_counts.items())
    count_ok = len(dry_run) == 7
    kinds_ok = all(kind_counts[kind] == expected for kind, expected in expected_kind_counts.items())
    detail_ok = count_ok and kinds_ok and not failures
    failure_note = ";failures:" + ",".join(failures[:8]) if failures else ""
    return detail_ok, f"dryRunDetails:{len(dry_run)}/7 kinds:{kind_note}{failure_note}"


def auth_json_item(root: Path, summary: Any, key: str, raw_ok: bool, raw_note: str) -> dict[str, str]:
    summary_status, summary_note = summary_check_status(summary, key)
    file_path = find_file(root, AUTH_JSON_NAME)
    evidence = rel(file_path, root) if file_path else ""
    if summary_status != "pass":
        return status_item(summary_status, CHECK_LABELS[key], evidence=evidence, note=summary_note or raw_note)
    if not raw_ok:
        return status_item("fail", CHECK_LABELS[key], evidence=evidence, note=raw_note)
    return status_item("pass", CHECK_LABELS[key], evidence=evidence, note=raw_note or summary_note)


def rbac_expected_keys() -> set[tuple[str, str, str]]:
    return {
        *( (label, "read", resource) for label in RBAC_LABELS for resource in RBAC_READ_RESOURCES ),
        *( (label, "write", resource) for label in RBAC_LABELS for resource in RBAC_WRITE_RESOURCES ),
    }


def rbac_source_hashes(payload: dict[str, Any]) -> tuple[dict[str, str], bool, str]:
    source = payload.get("rbac_auth_source") if isinstance(payload.get("rbac_auth_source"), dict) else {}
    cases = source.get("cases") if isinstance(source.get("cases"), dict) else {}
    hashes: dict[str, str] = {}
    for label in RBAC_PROVIDED_LABELS:
        case = cases.get(label) if isinstance(cases.get(label), dict) else {}
        digest = str(case.get("sha256_12") or "")
        if case.get("kind") == "provided_bearer" and digest:
            hashes[label] = digest
    duplicate_groups = source.get("duplicate_bearer_label_groups")
    distinct_count = len(set(hashes.values()))
    distinct_ok = (
        source.get("kind") == "rbac_matrix"
        and source.get("distinct_provided_bearers") is True
        and int(source.get("provided_bearer_count") or 0) == len(RBAC_PROVIDED_LABELS)
        and int(source.get("distinct_provided_bearer_count") or 0) == len(RBAC_PROVIDED_LABELS)
        and isinstance(duplicate_groups, list)
        and not duplicate_groups
        and len(hashes) == len(RBAC_PROVIDED_LABELS)
        and distinct_count == len(RBAC_PROVIDED_LABELS)
    )
    cases_note = f"providedCases:{len(hashes)}/{len(RBAC_PROVIDED_LABELS)} distinctBearers:{distinct_count}/{len(RBAC_PROVIDED_LABELS)}"
    return hashes, distinct_ok, cases_note


def rbac_detail_check(payload: dict[str, Any], rbac_matrix: list[Any], summary: dict[str, Any]) -> tuple[bool, str]:
    expected_keys = rbac_expected_keys()
    source_hashes, source_ok, source_note = rbac_source_hashes(payload)
    actual_keys: set[tuple[str, str, str]] = set()
    ok_count = 0
    detail_links = 0
    bearer_links = 0
    write_items = 0
    write_side_effect_proofs = 0
    write_marker_links = 0
    read_denials = 0
    write_denials = 0
    failures: list[str] = []

    for index, item in enumerate(rbac_matrix):
        if not isinstance(item, dict):
            failures.append(f"{index}:not-object")
            continue
        if item.get("ok") is True:
            ok_count += 1
        label = str(item.get("rbac_label") or "")
        operation = str(item.get("rbac_operation") or "")
        resource = str(item.get("rbac_resource") or "")
        family = str(item.get("family") or "")
        key = (label, operation, resource)
        if key in expected_keys:
            actual_keys.add(key)
        if key in expected_keys and family == f"rbac-{operation}-{label}-{resource}":
            detail_links += 1
        else:
            failures.append(f"{index}:detail-link")

        if label == "anonymous":
            if item.get("auth_case_kind") != "anonymous" or item.get("request_bearer_sha256_12"):
                failures.append(f"{index}:anonymous-auth-link")
        elif label in source_hashes:
            if item.get("auth_case_kind") == "provided_bearer" and item.get("request_bearer_sha256_12") == source_hashes[label]:
                bearer_links += 1
            else:
                failures.append(f"{index}:bearer-link")

        error_code = str(item.get("error_code") or "")
        if operation == "read" and label not in RBAC_READ_ALLOWED:
            if item.get("error_envelope") is True and error_code in RBAC_DENIED_ERROR_CODES:
                read_denials += 1
            else:
                failures.append(f"{index}:read-denial-envelope")
        if operation != "write":
            continue

        write_items += 1
        check = item.get("side_effect_check") if isinstance(item.get("side_effect_check"), dict) else {}
        marker_hash = str(item.get("request_marker_sha256_12") or "")
        if check.get("ok") is True:
            if label in RBAC_WRITE_ALLOWED:
                proof_ok = (
                    check.get("kind") == "rbac_dry_run_write_meta"
                    and check.get("dryRun") is True
                    and check.get("durable") is False
                    and check.get("liveCapitalSideEffects") is False
                    and item.get("error_envelope") is not True
                )
            else:
                denied_code = str(check.get("error_code") or item.get("error_code") or "")
                proof_ok = (
                    check.get("kind") == "authorization_rejected_before_persistence"
                    and item.get("error_envelope") is True
                    and denied_code in RBAC_DENIED_ERROR_CODES
                )
                if proof_ok:
                    write_denials += 1
            if proof_ok:
                write_side_effect_proofs += 1
        if marker_hash and check.get("target_marker_sha256_12") == marker_hash:
            write_marker_links += 1
        else:
            failures.append(f"{index}:write-marker-link")

    matrix_coverage = len(actual_keys)
    expected_non_anonymous = len(RBAC_PROVIDED_LABELS) * (len(RBAC_READ_RESOURCES) + len(RBAC_WRITE_RESOURCES))
    expected_read_denials = (len(RBAC_LABELS) - len(RBAC_READ_ALLOWED)) * len(RBAC_READ_RESOURCES)
    expected_write_denials = (len(RBAC_LABELS) - len(RBAC_WRITE_ALLOWED)) * len(RBAC_WRITE_RESOURCES)
    expected_write_items = len(RBAC_LABELS) * len(RBAC_WRITE_RESOURCES)
    detail_ok = (
        len(rbac_matrix) == len(expected_keys)
        and safe_int(summary.get("rbac_matrix_probes") or len(rbac_matrix)) >= len(expected_keys)
        and ok_count == len(expected_keys)
        and matrix_coverage == len(expected_keys)
        and detail_links == len(expected_keys)
        and source_ok
        and bearer_links == expected_non_anonymous
        and read_denials == expected_read_denials
        and write_items == expected_write_items
        and write_side_effect_proofs == expected_write_items
        and write_marker_links == expected_write_items
        and write_denials == expected_write_denials
        and not failures
    )
    failure_note = ";failures:" + ",".join(failures[:8]) if failures else ""
    note = (
        f"rbac:{ok_count}/{len(expected_keys)} matrixCoverage:{matrix_coverage}/{len(expected_keys)} "
        f"detailLinks:{detail_links}/{len(expected_keys)} {source_note} "
        f"bearerLinks:{bearer_links}/{expected_non_anonymous} readDenials:{read_denials}/{expected_read_denials} "
        f"writeSideEffectProofs:{write_side_effect_proofs}/{expected_write_items} "
        f"writeMarkerLinks:{write_marker_links}/{expected_write_items} writeDenials:{write_denials}/{expected_write_denials}"
        f"{failure_note}"
    )
    return detail_ok, note


def race_token_hashes(race: dict[str, Any]) -> tuple[dict[str, str], bool, str]:
    source = race.get("token_source") if isinstance(race.get("token_source"), dict) else {}
    hashes: dict[str, str] = {}
    for actor in ("a", "b"):
        digest = str(source.get(f"token_{actor}_sha256_12") or "")
        if digest:
            hashes[actor] = digest
    distinct_count = len(set(hashes.values()))
    source_kind = str(source.get("kind") or "")
    source_ok = (
        source_kind == "provided_bearer_pair"
        and len(hashes) == 2
        and distinct_count == 2
    )
    return hashes, source_ok, f"tokenSource:{source_kind or 'missing'} distinctTokens:{distinct_count}/2"


def extracted_value(result: dict[str, Any], path: tuple[str, ...]) -> Any:
    extracted = result.get("extracted")
    if not isinstance(extracted, dict):
        return None
    return extracted.get(".".join(path))


def approval_race_detail_check(race: dict[str, Any]) -> tuple[bool, str]:
    source_hashes, source_ok, source_note = race_token_hashes(race)
    results = as_list(race.get("results"))
    target_hash = str(race.get("target_id_sha256_12") or "")
    path_text = str(race.get("path") or "")
    family_ok = race.get("family") == "approval-race"
    method_ok = race.get("method") == "POST"
    path_ok = path_text.startswith("/bff/approvals/") and path_text.endswith("/decide")
    actor_labels: set[str] = set()
    idempotency_hashes: list[str] = []
    target_links = 0
    bearer_links = 0
    accepted_count = 0
    safe_error_count = 0
    transport_failures = 0
    failures: list[str] = []

    for index, item in enumerate(results):
        if not isinstance(item, dict):
            failures.append(f"{index}:not-object")
            continue
        actor = str(item.get("actor_label") or "")
        if actor in {"a", "b"}:
            actor_labels.add(actor)
        else:
            failures.append(f"{index}:actor")
        if actor and item.get("family") != f"approval-race-{actor}":
            failures.append(f"{index}:family")
        if item.get("method") != "POST" or str(item.get("path") or "") != path_text:
            failures.append(f"{index}:request-link")
        if target_hash and item.get("target_id_sha256_12") == target_hash:
            target_links += 1
        else:
            failures.append(f"{index}:target-link")
        if actor in source_hashes and item.get("request_bearer_sha256_12") == source_hashes[actor]:
            bearer_links += 1
        else:
            failures.append(f"{index}:bearer-link")
        idempotency_hash = str(item.get("request_idempotency_key_sha256_12") or "")
        if idempotency_hash:
            idempotency_hashes.append(idempotency_hash)
        else:
            failures.append(f"{index}:idempotency-hash")

        status = safe_int(item.get("status"))
        if status == 0:
            transport_failures += 1
        result_ok = item.get("ok") is True
        error_code = str(item.get("error_code") or "")
        if status in APPROVAL_RACE_ACCEPTED_STATUSES and result_ok and item.get("error_envelope") is not True:
            accepted_count += 1
        elif (
            status >= 400
            and result_ok
            and item.get("error_envelope") is True
            and error_code in APPROVAL_RACE_SAFE_ERROR_CODES
        ):
            safe_error_count += 1
        else:
            failures.append(f"{index}:winner-loser-shape")

    idempotency_distinct = len(idempotency_hashes) == 2 and len(set(idempotency_hashes)) == 2
    top_counts_ok = (
        safe_int(race.get("accepted_count")) == accepted_count == 1
        and safe_int(race.get("safe_error_count")) == safe_error_count == 1
    )
    detail_ok = (
        race.get("ok") is True
        and race.get("bounded") is True
        and family_ok
        and method_ok
        and path_ok
        and bool(target_hash)
        and source_ok
        and len(results) == 2
        and actor_labels == {"a", "b"}
        and target_links == 2
        and bearer_links == 2
        and accepted_count == 1
        and safe_error_count == 1
        and transport_failures == 0
        and race.get("duplicate_winners") is False
        and idempotency_distinct
        and top_counts_ok
        and not failures
    )
    failure_note = ";failures:" + ",".join(failures[:8]) if failures else ""
    note = (
        f"approvalResults:{len(results)}/2 actors:{len(actor_labels)}/2 targetLinks:{target_links}/2 "
        f"bearerLinks:{bearer_links}/2 accepted:{accepted_count}/1 safeErrors:{safe_error_count}/1 "
        f"transportFailures:{transport_failures}/0 duplicateWinners:{race.get('duplicate_winners')} "
        f"idempotencyDistinct:{idempotency_distinct} topCounts:{top_counts_ok} "
        f"family:{family_ok} method:{method_ok} path:{path_ok} {source_note}{failure_note}"
    )
    return detail_ok, note


def two_man_race_detail_check(race: dict[str, Any]) -> tuple[bool, str]:
    source_hashes, source_ok, source_note = race_token_hashes(race)
    results = as_list(race.get("results"))
    target_hash = str(race.get("target_id_sha256_12") or "")
    path_text = str(race.get("path") or "")
    family_ok = race.get("family") == "two-man-race"
    method_ok = race.get("method") == "POST"
    path_ok = path_text.startswith("/bff/v5/interventions/") and path_text.endswith("/two-man-sign")
    actor_labels: set[str] = set()
    idempotency_hashes: list[str] = []
    signature_hashes: set[str] = set()
    command_ids: list[str] = []
    target_links = 0
    bearer_links = 0
    accepted_count = 0
    replayed_count = 0
    replay_proofs = 0
    transport_failures = 0
    failures: list[str] = []

    for index, item in enumerate(results):
        if not isinstance(item, dict):
            failures.append(f"{index}:not-object")
            continue
        actor = str(item.get("actor_label") or "")
        if actor in {"a", "b"}:
            actor_labels.add(actor)
        else:
            failures.append(f"{index}:actor")
        if item.get("family") != "two-man-race" or item.get("method") != "POST" or str(item.get("path") or "") != path_text:
            failures.append(f"{index}:request-link")
        if target_hash and item.get("target_id_sha256_12") == target_hash:
            target_links += 1
        else:
            failures.append(f"{index}:target-link")
        if actor in source_hashes and item.get("request_bearer_sha256_12") == source_hashes[actor]:
            bearer_links += 1
        else:
            failures.append(f"{index}:bearer-link")
        idempotency_hash = str(item.get("request_idempotency_key_sha256_12") or "")
        if idempotency_hash:
            idempotency_hashes.append(idempotency_hash)
        else:
            failures.append(f"{index}:idempotency-hash")
        signature_hash = str(item.get("request_signature_id_sha256_12") or "")
        if signature_hash:
            signature_hashes.add(signature_hash)
        else:
            failures.append(f"{index}:signature-hash")

        status = safe_int(item.get("status"))
        if status == 0:
            transport_failures += 1
        if status in APPROVAL_RACE_ACCEPTED_STATUSES and item.get("ok") is True and item.get("error_envelope") is not True:
            accepted_count += 1
        else:
            failures.append(f"{index}:accepted-shape")
        replayed = extracted_value(item, ("meta", "idempotency", "replayed"))
        if replayed is False:
            replay_proofs += 1
        elif replayed is True:
            replayed_count += 1
        else:
            failures.append(f"{index}:replay-proof")
        command_id = extracted_value(item, ("data", "command_id")) or extracted_value(item, ("data", "commandId"))
        if command_id:
            command_ids.append(str(command_id))
        else:
            failures.append(f"{index}:command-id")

    shared_idempotency = len(idempotency_hashes) == 2 and len(set(idempotency_hashes)) == 1
    distinct_signatures = len(signature_hashes) == 2
    distinct_commands = len(command_ids) == 2 and len(set(command_ids)) == 2
    top_counts_ok = (
        safe_int(race.get("accepted_count")) == accepted_count == 2
        and safe_int(race.get("replayed_count")) == replayed_count == 0
        and safe_int(race.get("command_id_count")) == len(set(command_ids)) == 2
        and race.get("distinct_command_ids") is True
    )
    detail_ok = (
        race.get("ok") is True
        and race.get("operator_scoped") is True
        and family_ok
        and method_ok
        and path_ok
        and bool(target_hash)
        and source_ok
        and len(results) == 2
        and actor_labels == {"a", "b"}
        and target_links == 2
        and bearer_links == 2
        and accepted_count == 2
        and replayed_count == 0
        and replay_proofs == 2
        and transport_failures == 0
        and shared_idempotency
        and distinct_signatures
        and distinct_commands
        and top_counts_ok
        and not failures
    )
    failure_note = ";failures:" + ",".join(failures[:8]) if failures else ""
    note = (
        f"twoManResults:{len(results)}/2 actors:{len(actor_labels)}/2 targetLinks:{target_links}/2 "
        f"bearerLinks:{bearer_links}/2 accepted:{accepted_count}/2 replayed:{replayed_count}/0 "
        f"replayProofs:{replay_proofs}/2 sharedIdempotency:{shared_idempotency} "
        f"distinctSignatures:{len(signature_hashes)}/2 distinctCommands:{len(set(command_ids))}/2 "
        f"transportFailures:{transport_failures}/0 topCounts:{top_counts_ok} "
        f"family:{family_ok} method:{method_ok} path:{path_ok} {source_note}{failure_note}"
    )
    return detail_ok, note


def evaluate_auth_json(root: Path) -> tuple[Any, dict[str, tuple[bool, str]]]:
    file_path = find_file(root, AUTH_JSON_NAME)
    payload = read_json(file_path) if file_path else None
    if not isinstance(payload, dict):
        return None, {
            "rbac_matrix": (False, "authenticated live JSON missing"),
            "dry_run_no_side_effects": (False, "authenticated live JSON missing"),
            "approval_race": (False, "authenticated live JSON missing"),
            "two_man_race": (False, "authenticated live JSON missing"),
        }
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    strict = payload.get("strict_live_evidence") is True
    includes = (
        payload.get("include_rbac_matrix") is True
        and payload.get("include_dry_run") is True
        and payload.get("include_approval_race") is True
        and payload.get("include_two_man_race") is True
    )
    rbac_matrix = payload.get("rbac_matrix") if isinstance(payload.get("rbac_matrix"), list) else []
    dry_run = payload.get("dry_run") if isinstance(payload.get("dry_run"), list) else []
    approval_race = payload.get("approval_race") if isinstance(payload.get("approval_race"), dict) else {}
    two_man_race = payload.get("two_man_race") if isinstance(payload.get("two_man_race"), dict) else {}
    dry_run_count = int(summary.get("dry_run_probes") or len(dry_run))
    approval_count = int(summary.get("approval_race_probes") or int(bool(approval_race)))
    two_man_count = int(summary.get("two_man_race_probes") or int(bool(two_man_race)))
    rbac_detail_ok, rbac_detail_note = rbac_detail_check(payload, rbac_matrix, summary)
    dry_run_detail_ok, dry_run_detail_note = dry_run_detail_check(dry_run)
    approval_detail_ok, approval_detail_note = approval_race_detail_check(approval_race)
    two_man_detail_ok, two_man_detail_note = two_man_race_detail_check(two_man_race)
    base = strict and includes
    return payload, {
        "rbac_matrix": (
            base and rbac_detail_ok,
            f"strict:{strict} includes:{includes} {rbac_detail_note}",
        ),
        "dry_run_no_side_effects": (
            base and dry_run_count >= 7 and dry_run_detail_ok and summary.get("live_capital_side_effects") is False,
            f"strict:{strict} includes:{includes} dryRun:{dry_run_count}/7 {dry_run_detail_note} sideEffects:{summary.get('live_capital_side_effects')}",
        ),
        "approval_race": (
            base and approval_count == 1 and summary.get("approval_race_bounded") is True and approval_detail_ok,
            f"strict:{strict} includes:{includes} approvalRace:{approval_count}/1 bounded:{summary.get('approval_race_bounded') is True} {approval_detail_note}",
        ),
        "two_man_race": (
            base and two_man_count == 1 and summary.get("two_man_race_operator_scoped") is True and two_man_detail_ok,
            f"strict:{strict} includes:{includes} twoManRace:{two_man_count}/1 operatorScoped:{summary.get('two_man_race_operator_scoped') is True} {two_man_detail_note}",
        ),
    }


def safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def sse_auth_source_check(payload: dict[str, Any]) -> tuple[bool, str, bool]:
    source = payload.get("auth_source") if isinstance(payload.get("auth_source"), dict) else {}
    kind = str(source.get("kind") or "")
    token_hash = str(source.get("token_sha256_12") or "")
    token_hash_ok = bool(re.fullmatch(r"[0-9a-f]{12}", token_hash, re.IGNORECASE))
    ok = kind == "provided_bearer" and token_hash_ok
    return ok, f"authSource:{kind or 'missing'} tokenHash:{token_hash_ok}", token_hash_ok


def sse_request_used_bearer_auth(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    headers = item.get("request_headers") if isinstance(item.get("request_headers"), dict) else {}
    return headers.get("Authorization") == "present" and headers.get("Cookie") == "absent"


def sse_attempts_have_lineage(attempts: list[Any]) -> bool:
    if not attempts:
        return False
    for attempt in attempts:
        if not isinstance(attempt, dict):
            return False
        lineage = attempt.get("lineage_checks")
        if not isinstance(lineage, dict) or not lineage:
            return False
        if any(value is not True for value in lineage.values()):
            return False
        expected = str(attempt.get("expected_replayed_event_id") or "")
        observed = str(attempt.get("observed_replayed_event_id") or "")
        cursor = str(attempt.get("cursor_event_id") or "")
        if not cursor or not expected or observed != expected:
            return False
        if attempt.get("ok") is not True or attempt.get("replayed_expected_event") is not True:
            return False
    return True


def sse_detail_check(payload: dict[str, Any]) -> tuple[bool, str]:
    soak = payload.get("soak") if isinstance(payload.get("soak"), dict) else {}
    bearer_soak = soak.get("bearer_polyfill") if isinstance(soak.get("bearer_polyfill"), dict) else {}
    blocks = bearer_soak.get("blocks") if isinstance(bearer_soak.get("blocks"), dict) else {}
    reconnect = payload.get("reconnect_sequence") if isinstance(payload.get("reconnect_sequence"), dict) else {}
    bearer_reconnect = reconnect.get("bearer_polyfill") if isinstance(reconnect.get("bearer_polyfill"), dict) else {}
    attempts = as_list(bearer_reconnect.get("attempts"))
    expected_ids = [str(item) for item in as_list(bearer_reconnect.get("expected_event_ids")) if item]
    observed_ids = [str(item) for item in as_list(bearer_reconnect.get("observed_event_ids")) if item]
    soak_duplicates = as_list(blocks.get("duplicate_event_ids"))
    reconnect_duplicates = as_list(bearer_reconnect.get("duplicate_event_ids"))
    soak_missing = as_list(bearer_soak.get("missing_expected_event_ids"))
    reconnect_missing = as_list(bearer_reconnect.get("missing_expected_event_ids"))

    strict = payload.get("strict_live_evidence") is True
    seconds = safe_float(soak.get("seconds"))
    min_heartbeats = max(1, safe_int(soak.get("min_heartbeats") or bearer_soak.get("min_heartbeats")))
    heartbeat_count = safe_int(blocks.get("heartbeat_count"))
    attempt_count = safe_int(bearer_reconnect.get("attempt_count") or len(attempts))
    attempt_details_ok = attempt_count >= 5 and len(attempts) >= 5
    attempt_lineage_ok = sse_attempts_have_lineage(attempts)
    observed_sequence_ok = len(observed_ids) >= 5 and observed_ids == expected_ids
    cursors_advanced = bearer_reconnect.get("cursors_advanced") is True
    duplicates = len(soak_duplicates) + len(reconnect_duplicates)
    missing_replay = len(soak_missing) + len(reconnect_missing)
    bearer_soak_ok = bearer_soak.get("ok") is True
    bearer_reconnect_ok = bearer_reconnect.get("ok") is True
    auth_source_ok, auth_source_note, _token_hash_ok = sse_auth_source_check(payload)
    soak_bearer_auth_ok = sse_request_used_bearer_auth(bearer_soak)
    bearer_attempt_auth_count = sum(1 for attempt in attempts if sse_request_used_bearer_auth(attempt))
    bearer_attempt_auth_ok = attempt_count >= 5 and bearer_attempt_auth_count >= 5

    detail_ok = (
        strict
        and auth_source_ok
        and seconds >= 75
        and bearer_soak_ok
        and soak_bearer_auth_ok
        and heartbeat_count >= min_heartbeats
        and duplicates == 0
        and missing_replay == 0
        and bearer_reconnect_ok
        and attempt_count >= 5
        and attempt_details_ok
        and bearer_attempt_auth_ok
        and attempt_lineage_ok
        and observed_sequence_ok
        and cursors_advanced
    )
    note = (
        f"strict:{strict} {auth_source_note} soak:{seconds:g}/75 "
        f"soakBearerAuth:{soak_bearer_auth_ok} heartbeat:{heartbeat_count}/{min_heartbeats} "
        f"reconnect:{attempt_count}/5 attemptDetails:{attempt_details_ok} "
        f"bearerAttemptAuth:{bearer_attempt_auth_count}/5 attemptLineage:{attempt_lineage_ok} "
        f"observed:{len(observed_ids)}/5 observedSequence:{observed_sequence_ok} duplicates:{duplicates} "
        f"missingReplay:{missing_replay} cursorsAdvanced:{cursors_advanced} "
        f"soakOk:{bearer_soak_ok} reconnectOk:{bearer_reconnect_ok}"
    )
    return detail_ok, note


def sse_item(root: Path, summary: Any) -> dict[str, str]:
    summary_status, summary_note = summary_check_status(summary, "sse_reconnect_soak")
    file_path = find_file(root, SSE_JSON_NAME)
    evidence = rel(file_path, root) if file_path else ""
    payload = read_json(file_path) if file_path else None
    if not isinstance(payload, dict):
        return status_item(summary_status, CHECK_LABELS["sse_reconnect_soak"], evidence=evidence, note=summary_note or "SSE JSON missing")
    raw_ok, raw_note = sse_detail_check(payload)
    if summary_status != "pass":
        return status_item(summary_status, CHECK_LABELS["sse_reconnect_soak"], evidence=evidence, note=summary_note or raw_note)
    if not raw_ok:
        return status_item("fail", CHECK_LABELS["sse_reconnect_soak"], evidence=evidence, note=raw_note)
    return status_item("pass", CHECK_LABELS["sse_reconnect_soak"], evidence=evidence, note=raw_note)


def verify(root: Path) -> dict[str, Any]:
    root = root.resolve()
    summary_file = find_file(root, SUMMARY_JSON_NAME)
    summary = read_json(summary_file) if summary_file else None
    _auth_payload, auth_checks = evaluate_auth_json(root)
    criteria = {
        "preflight_ready": preflight_item(root),
        "rbac_matrix": auth_json_item(root, summary, "rbac_matrix", *auth_checks["rbac_matrix"]),
        "dry_run_no_side_effects": auth_json_item(root, summary, "dry_run_no_side_effects", *auth_checks["dry_run_no_side_effects"]),
        "approval_race": auth_json_item(root, summary, "approval_race", *auth_checks["approval_race"]),
        "two_man_race": auth_json_item(root, summary, "two_man_race", *auth_checks["two_man_race"]),
        "sse_reconnect_soak": sse_item(root, summary),
        "current_run_only": artifact_scope_item(root, summary),
        "raw_secret_scan": secret_leak_item(root),
    }
    overall = "pass" if all(item["status"] == "pass" for item in criteria.values()) else "fail"
    return {
        "task_id": "BFF-LIVE-EVIDENCE-ARTIFACT-VERIFY",
        "artifact_dir": str(root),
        "overall": overall,
        "criteria": criteria,
        "summary_file": rel(summary_file, root) if summary_file else "",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = verify(args.artifact_dir)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"overall: {result['overall']}")
        for key, item in result["criteria"].items():
            note = f" - {item['note']}" if item.get("note") else ""
            print(f"{key}: {item['status']}{note}")
    return 0 if result["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())

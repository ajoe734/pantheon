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


def artifact_scope_item(root: Path, summary: Any) -> dict[str, str]:
    files = list_files(root)
    forbidden = [
        rel(path, root)
        for path in files
        if any(part.lower() in FORBIDDEN_AUDIT_DIR_NAMES for part in path.relative_to(root).parts[:-1])
    ]
    summary_status, summary_note = summary_check_status(summary, "current_run_only")
    if forbidden:
        return status_item("fail", CHECK_LABELS["current_run_only"], note="forbidden audit paths: " + ",".join(forbidden[:5]))
    if summary_status == "pass":
        return status_item("pass", CHECK_LABELS["current_run_only"], note=summary_note)
    if isinstance(summary, dict):
        return status_item(summary_status, CHECK_LABELS["current_run_only"], note=summary_note)
    return status_item("pass", CHECK_LABELS["current_run_only"], note=f"{len(files)} artifact file(s); no historical/archive paths")


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
    rbac_count = int(summary.get("rbac_matrix_probes") or len(rbac_matrix))
    dry_run_count = int(summary.get("dry_run_probes") or len(dry_run))
    approval_count = int(summary.get("approval_race_probes") or int(bool(approval_race)))
    two_man_count = int(summary.get("two_man_race_probes") or int(bool(two_man_race)))
    rbac_all_ok = bool(rbac_matrix) and all(item.get("ok") is True for item in rbac_matrix)
    dry_run_detail_ok, dry_run_detail_note = dry_run_detail_check(dry_run)
    approval_ok = approval_race.get("ok") is True and approval_race.get("bounded") is True
    two_man_ok = two_man_race.get("ok") is True and two_man_race.get("operator_scoped") is True
    base = strict and includes
    return payload, {
        "rbac_matrix": (
            base and rbac_count >= 56 and rbac_all_ok,
            f"strict:{strict} includes:{includes} rbac:{rbac_count}/56 allOk:{rbac_all_ok}",
        ),
        "dry_run_no_side_effects": (
            base and dry_run_count >= 7 and dry_run_detail_ok and summary.get("live_capital_side_effects") is False,
            f"strict:{strict} includes:{includes} dryRun:{dry_run_count}/7 {dry_run_detail_note} sideEffects:{summary.get('live_capital_side_effects')}",
        ),
        "approval_race": (
            base and approval_count == 1 and summary.get("approval_race_bounded") is True and approval_ok,
            f"strict:{strict} includes:{includes} approvalRace:{approval_count}/1 bounded:{summary.get('approval_race_bounded') is True} detailOk:{approval_ok}",
        ),
        "two_man_race": (
            base and two_man_count == 1 and summary.get("two_man_race_operator_scoped") is True and two_man_ok,
            f"strict:{strict} includes:{includes} twoManRace:{two_man_count}/1 operatorScoped:{summary.get('two_man_race_operator_scoped') is True} detailOk:{two_man_ok}",
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

    detail_ok = (
        strict
        and seconds >= 75
        and bearer_soak_ok
        and heartbeat_count >= min_heartbeats
        and duplicates == 0
        and missing_replay == 0
        and bearer_reconnect_ok
        and attempt_count >= 5
        and attempt_details_ok
        and attempt_lineage_ok
        and observed_sequence_ok
        and cursors_advanced
    )
    note = (
        f"strict:{strict} soak:{seconds:g}/75 heartbeat:{heartbeat_count}/{min_heartbeats} "
        f"reconnect:{attempt_count}/5 attemptDetails:{attempt_details_ok} "
        f"attemptLineage:{attempt_lineage_ok} observed:{len(observed_ids)}/5 "
        f"observedSequence:{observed_sequence_ok} duplicates:{duplicates} "
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

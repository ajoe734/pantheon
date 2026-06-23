#!/usr/bin/env python3
"""Verify a downloaded strict BFF live-evidence current-run artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


AUTH_JSON_NAME = "BFF-LUV-AUTHED-LIVE-001-live-smoke.json"
SSE_JSON_NAME = "BFF-CONSOL-011-sse-replay-smoke.json"
PREFLIGHT_JSON_NAME = "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
SUMMARY_JSON_NAME = "release-gate-summary.json"
FORBIDDEN_AUDIT_DIR_NAMES = {"historical", "archive", "archives", "baseline"}

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
        environment = str(payload.get("github_environment") or "unknown")
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
    dry_run_all_ok = bool(dry_run) and all(item.get("ok") is True for item in dry_run)
    approval_ok = approval_race.get("ok") is True and approval_race.get("bounded") is True
    two_man_ok = two_man_race.get("ok") is True and two_man_race.get("operator_scoped") is True
    base = strict and includes
    return payload, {
        "rbac_matrix": (
            base and rbac_count >= 56 and rbac_all_ok,
            f"strict:{strict} includes:{includes} rbac:{rbac_count}/56 allOk:{rbac_all_ok}",
        ),
        "dry_run_no_side_effects": (
            base and dry_run_count >= 7 and dry_run_all_ok and summary.get("live_capital_side_effects") is False,
            f"strict:{strict} includes:{includes} dryRun:{dry_run_count}/7 allOk:{dry_run_all_ok} sideEffects:{summary.get('live_capital_side_effects')}",
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


def sse_item(root: Path, summary: Any) -> dict[str, str]:
    summary_status, summary_note = summary_check_status(summary, "sse_reconnect_soak")
    file_path = find_file(root, SSE_JSON_NAME)
    evidence = rel(file_path, root) if file_path else ""
    payload = read_json(file_path) if file_path else None
    if not isinstance(payload, dict):
        return status_item(summary_status, CHECK_LABELS["sse_reconnect_soak"], evidence=evidence, note=summary_note or "SSE JSON missing")
    soak = payload.get("soak") if isinstance(payload.get("soak"), dict) else {}
    reconnect = payload.get("reconnect_sequence") if isinstance(payload.get("reconnect_sequence"), dict) else {}
    bearer = reconnect.get("bearer_polyfill") if isinstance(reconnect.get("bearer_polyfill"), dict) else {}
    strict = payload.get("strict_live_evidence") is True
    seconds = float(soak.get("seconds") or 0)
    attempts = int(bearer.get("attempt_count") or len(bearer.get("attempts") or []))
    raw_ok = strict and seconds >= 75 and bearer.get("ok") is True and attempts >= 5
    raw_note = f"strict:{strict} soak:{seconds:g}/75 reconnect:{attempts}/5 bearerOk:{bearer.get('ok') is True}"
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

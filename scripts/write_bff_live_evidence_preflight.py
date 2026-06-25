#!/usr/bin/env python3
"""Write fail-closed preflight evidence for strict BFF live probes."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_OUTPUT = Path(".lovable") / "audits" / "current-run" / "BFF-LIVE-EVIDENCE-PREFLIGHT.json"
STRICT_LIVE_SOAK_MIN_SECONDS = 75.0

REQUIRED_SECRET_ENV_VARS = (
    "PANTHEON_BFF_SMOKE_BEARER_TOKEN",
    "PANTHEON_BFF_RBAC_TOKENS_JSON",
    "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A",
    "PANTHEON_BFF_APPROVAL_RACE_TOKEN_B",
)
RBAC_REQUIRED_LABELS = (
    "viewer",
    "operator",
    "reviewer",
    "approver",
    "admin",
    "empty",
    "unknown",
)

REQUIRED_INPUT_NAMES = (
    "PANTHEON_BFF_BASE_URL",
    "APPROVAL_RACE_ID",
    "TWO_MAN_RACE_ID",
    "SOAK_SECONDS",
)
DEFAULT_REPOSITORY = "ajoe734/pantheon"
DEFAULT_WORKFLOW_NAME = "Pantheon Stage 0 CI"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def present(value: str | None) -> bool:
    return bool((value or "").strip())


def bearer_value(value: str | None) -> str:
    return (value or "").removeprefix("Bearer ").strip()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.environ.get("PANTHEON_BFF_BASE_URL", ""))
    parser.add_argument("--approval-race-id", default=os.environ.get("APPROVAL_RACE_ID", ""))
    parser.add_argument("--two-man-race-id", default=os.environ.get("TWO_MAN_RACE_ID", ""))
    parser.add_argument("--soak-seconds", default=os.environ.get("SOAK_SECONDS", ""))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def invalid_soak_seconds(value: str) -> str:
    if not present(value):
        return ""
    try:
        seconds = float(value)
    except ValueError:
        return f"SOAK_SECONDS must be numeric and >= {STRICT_LIVE_SOAK_MIN_SECONDS:g}"
    if seconds < STRICT_LIVE_SOAK_MIN_SECONDS:
        return f"SOAK_SECONDS must be >= {STRICT_LIVE_SOAK_MIN_SECONDS:g}"
    return ""


def rbac_token_value(value: Any) -> str:
    if isinstance(value, str):
        token = value
    elif isinstance(value, dict):
        token = str(value.get("token") or value.get("bearer") or "")
    else:
        token = ""
    return token.removeprefix("Bearer ").strip()


def empty_rbac_matrix() -> dict[str, Any]:
    return {
        "required_labels": list(RBAC_REQUIRED_LABELS),
        "present_labels": [],
        "missing_labels": list(RBAC_REQUIRED_LABELS),
        "provided_cases": 0,
        "expected_cases": len(RBAC_REQUIRED_LABELS),
        "distinct_bearers": False,
        "distinct_bearer_count": 0,
        "duplicate_label_groups": [],
    }


def duplicate_bearer_label_groups(tokens_by_label: dict[str, str]) -> list[list[str]]:
    labels_by_token: dict[str, list[str]] = {}
    for label in RBAC_REQUIRED_LABELS:
        token = tokens_by_label.get(label, "")
        if token:
            labels_by_token.setdefault(token, []).append(label)
    return [labels for labels in labels_by_token.values() if len(labels) > 1]


def inspect_rbac_tokens_json(raw: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    if not present(raw):
        return empty_rbac_matrix(), []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        return empty_rbac_matrix(), [
            {"name": "PANTHEON_BFF_RBAC_TOKENS_JSON", "reason": f"must be valid JSON: {exc.msg}"}
        ]
    if not isinstance(parsed, dict):
        return empty_rbac_matrix(), [
            {"name": "PANTHEON_BFF_RBAC_TOKENS_JSON", "reason": "must be an object keyed by role label"}
        ]

    tokens_by_label = {
        label: rbac_token_value(parsed.get(label))
        for label in RBAC_REQUIRED_LABELS
    }
    present_labels = [label for label in RBAC_REQUIRED_LABELS if tokens_by_label[label]]
    missing_labels = [label for label in RBAC_REQUIRED_LABELS if label not in present_labels]
    duplicate_groups = duplicate_bearer_label_groups(tokens_by_label)
    distinct_count = len({tokens_by_label[label] for label in present_labels})
    invalid = []
    if missing_labels:
        invalid.append(
            {
                "name": "PANTHEON_BFF_RBAC_TOKENS_JSON",
                "reason": "missing bearer tokens for labels: " + ", ".join(missing_labels),
            }
        )
    if duplicate_groups:
        invalid.append(
            {
                "name": "PANTHEON_BFF_RBAC_TOKENS_JSON",
                "reason": "bearer tokens must be distinct per RBAC label: "
                + "; ".join("/".join(group) for group in duplicate_groups),
            }
        )
    return {
        "required_labels": list(RBAC_REQUIRED_LABELS),
        "present_labels": present_labels,
        "missing_labels": missing_labels,
        "provided_cases": len(present_labels),
        "expected_cases": len(RBAC_REQUIRED_LABELS),
        "distinct_bearers": not duplicate_groups and len(present_labels) == len(RBAC_REQUIRED_LABELS),
        "distinct_bearer_count": distinct_count,
        "duplicate_label_groups": duplicate_groups,
    }, invalid


def inspect_approval_race_tokens(token_a: str, token_b: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    bearer_a = bearer_value(token_a)
    bearer_b = bearer_value(token_b)
    both_present = bool(bearer_a and bearer_b)
    distinct = bool(both_present and bearer_a != bearer_b)
    invalid = []
    if both_present and not distinct:
        invalid.append(
            {
                "name": "PANTHEON_BFF_APPROVAL_RACE_TOKEN_A/B",
                "reason": "must be distinct bearer tokens for two operators",
            }
        )
    return {
        "token_a_present": bool(bearer_a),
        "token_b_present": bool(bearer_b),
        "distinct_bearers": distinct,
    }, invalid


def workflow_repository() -> str:
    return os.environ.get("GITHUB_REPOSITORY", "").strip() or DEFAULT_REPOSITORY


def live_evidence_environment() -> str:
    return os.environ.get("PANTHEON_LIVE_EVIDENCE_ENVIRONMENT", "").strip() or "dev"


def build_operator_remediation(missing: list[str], invalid: list[dict[str, str]], args: argparse.Namespace) -> dict[str, Any]:
    repository = workflow_repository()
    environment = live_evidence_environment()
    secret_names = list(REQUIRED_SECRET_ENV_VARS)
    return {
        "github_environment": environment,
        "repository": repository,
        "required_secret_names": secret_names,
        "missing_secret_names": [name for name in missing if name in REQUIRED_SECRET_ENV_VARS],
        "missing_workflow_inputs": [name for name in missing if name in REQUIRED_INPUT_NAMES],
        "invalid_inputs": invalid,
        "secret_set_commands": [
            f"gh secret set {name} --repo {repository} --env {environment} < /secure/path/{name}.txt"
            for name in secret_names
        ],
        "workflow_dispatch": {
            "recommended_workflow": DEFAULT_WORKFLOW_NAME,
            "mode": "live-evidence",
            "environment": environment,
            "run_command_template": (
                f"gh workflow run \"{DEFAULT_WORKFLOW_NAME}\" --repo {repository} --ref dev "
                f"-f mode=live-evidence -f environment={environment} "
                f"-f bff_base_url={args.base_url.strip() or '<bff-base-url>'} "
                "-f approval_race_id=<expendable-approval-id> "
                "-f two_man_race_id=<expendable-intervention-id> "
                f"-f soak_seconds={args.soak_seconds.strip() or '75'}"
            ),
        },
        "notes": [
            "Set secrets on the selected GitHub environment, not only at repository scope.",
            "PANTHEON_BFF_RBAC_TOKENS_JSON must include viewer, operator, reviewer, approver, admin, empty, and unknown labels.",
            "Use expendable approval/intervention ids for live race probes.",
            "Do not write bearer token values into artifacts, issues, logs, or workflow inputs.",
        ],
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    required = [*REQUIRED_SECRET_ENV_VARS, *REQUIRED_INPUT_NAMES]
    present_map = {
        name: present(os.environ.get(name))
        for name in REQUIRED_SECRET_ENV_VARS
    }
    present_map["PANTHEON_BFF_BASE_URL"] = present(args.base_url)
    present_map["APPROVAL_RACE_ID"] = present(args.approval_race_id)
    present_map["TWO_MAN_RACE_ID"] = present(args.two_man_race_id)
    present_map["SOAK_SECONDS"] = present(args.soak_seconds)
    missing = [name for name in required if not present_map[name]]
    rbac_matrix, rbac_invalid = inspect_rbac_tokens_json(os.environ.get("PANTHEON_BFF_RBAC_TOKENS_JSON", ""))
    approval_race_tokens, approval_race_invalid = inspect_approval_race_tokens(
        os.environ.get("PANTHEON_BFF_APPROVAL_RACE_TOKEN_A", ""),
        os.environ.get("PANTHEON_BFF_APPROVAL_RACE_TOKEN_B", ""),
    )
    invalid = [
        {"name": "SOAK_SECONDS", "reason": reason}
        for reason in [invalid_soak_seconds(args.soak_seconds)]
        if reason
    ]
    invalid.extend(rbac_invalid)
    invalid.extend(approval_race_invalid)
    remediation = build_operator_remediation(missing, invalid, args)

    return {
        "task_id": "BFF-LIVE-EVIDENCE-PREFLIGHT",
        "strict_live_evidence_preflight": True,
        "generated_at": utc_now(),
        "github_environment": remediation["github_environment"],
        "target_url": args.base_url.strip(),
        "approval_race_id_present": present(args.approval_race_id),
        "two_man_race_id_present": present(args.two_man_race_id),
        "soak_seconds": args.soak_seconds.strip(),
        "min_soak_seconds": STRICT_LIVE_SOAK_MIN_SECONDS,
        "rbac_matrix": rbac_matrix,
        "approval_race_tokens": approval_race_tokens,
        "ref": os.environ.get("GITHUB_REF") or os.environ.get("GITHUB_REF_NAME", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
        "required": required,
        "present": present_map,
        "missing": missing,
        "invalid": invalid,
        "operator_remediation": remediation,
        "output_scope": ".lovable/audits/current-run",
        "secret_values_written": False,
    }


def main() -> int:
    args = parse_args()
    payload = build_payload(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(f"{json.dumps(payload, indent=2, sort_keys=True)}\n", encoding="utf-8")

    if payload["missing"] or payload["invalid"]:
        if payload["missing"]:
            print(
                "Missing strict live evidence inputs: " + ", ".join(payload["missing"]),
                file=sys.stderr,
            )
        if payload["invalid"]:
            invalid_text = "; ".join(f"{item['name']}: {item['reason']}" for item in payload["invalid"])
            print("Invalid strict live evidence inputs: " + invalid_text, file=sys.stderr)
        return 1

    print("Strict live evidence inputs present: " + ", ".join(payload["required"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

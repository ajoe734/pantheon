#!/usr/bin/env python3
"""Production release preflight (fail-closed) for ENV-PROD-CONTROL-IMPL-001.

Checks, without touching any cloud resource:
- explicit production configuration (independent project, WIF/SA/IAM/secrets refs,
  disk/network/DNS/HTTPS/IdP, RPO/RTO/retention/on-call/rollback owner). Missing,
  placeholder, retired or staging/dev-shared values fail closed; nothing is guessed.
- the immutable release manifest (outer SHA-256 match, no relabeling).
- `staging_verified` evidence for that exact manifest checksum.
- genuine protected `production` Environment approval whose approver differs from
  the initiator (GitHub run-approvals API shape).
- rollback baseline presence, or an explicit first-release bootstrap declaration.

Local checks prove controller behavior only; they are never hosted drill evidence.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_manifest  # noqa: E402


class PreflightError(ValueError):
    """Raised when production preflight fails closed."""


REQUIRED_CONFIG = (
    "PROD_GCP_PROJECT_ID",
    "PROD_GCP_ZONE",
    "PROD_WIF_PROVIDER",
    "PROD_SERVICE_ACCOUNT",
    "PROD_IAM_POLICY_REF",
    "PROD_SECRETS_REF",
    "PROD_DATA_DISK",
    "PROD_NETWORK",
    "PROD_BFF_HOST",
    "PROD_FE_HOST",
    "PROD_IDP_ISSUER",
    "PROD_RPO_SECONDS",
    "PROD_RTO_SECONDS",
    "PROD_RETENTION_DAYS",
    "PROD_ONCALL_OWNER",
    "PROD_ROLLBACK_OWNER",
)
POSITIVE_INT_CONFIG = ("PROD_RPO_SECONDS", "PROD_RTO_SECONDS", "PROD_RETENTION_DAYS")
NONPROD_PROJECT_VARS = ("STAGING_GCP_PROJECT_ID", "DEV_GCP_PROJECT_ID")
PLACEHOLDER_RE = re.compile(
    r"(todo|tbd|changeme|replace[-_ ]?me|placeholder|example\.(com|org)|^<.*>$|^\$\{.*\}$|unspecified)",
    re.IGNORECASE,
)
HOST_RE = re.compile(r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$")
SAFETY_ENV = {
    "PANTHEON_PROVIDER_ENABLED": "false",
    "PANTHEON_LIVE_BROKER_ENABLED": "false",
    "PANTHEON_REAL_WRITES_ENABLED": "false",
    "PANTHEON_CONTROL_VM_BROKER_CREDENTIALS_PRESENT": "false",
}


def check_config(env: Mapping[str, str]) -> dict[str, str]:
    """Validate explicit production configuration; returns the accepted values."""
    problems: list[str] = []
    values: dict[str, str] = {}
    for key in REQUIRED_CONFIG:
        raw = (env.get(key) or "").strip()
        if not raw:
            problems.append(f"{key} is missing")
            continue
        if PLACEHOLDER_RE.search(raw):
            problems.append(f"{key} is a placeholder")
            continue
        for retired in release_manifest.RETIRED_IDENTIFIERS:
            if retired in raw:
                problems.append(f"{key} references retired identifier {retired!r}")
        values[key] = raw
    for key in POSITIVE_INT_CONFIG:
        if key in values and not (values[key].isdigit() and int(values[key]) > 0):
            problems.append(f"{key} must be a positive integer")
    for key in ("PROD_BFF_HOST", "PROD_FE_HOST"):
        if key in values and not HOST_RE.fullmatch(values[key]):
            problems.append(f"{key} must be a DNS hostname (HTTPS only, no scheme/port)")
    if {"PROD_BFF_HOST", "PROD_FE_HOST"} <= values.keys() and values["PROD_BFF_HOST"] == values["PROD_FE_HOST"]:
        problems.append("PROD_BFF_HOST and PROD_FE_HOST must differ")
    if "PROD_IDP_ISSUER" in values and not values["PROD_IDP_ISSUER"].startswith("https://"):
        problems.append("PROD_IDP_ISSUER must be an https:// URL")
    project = values.get("PROD_GCP_PROJECT_ID")
    if project:
        for nonprod in NONPROD_PROJECT_VARS:
            if (env.get(nonprod) or "").strip() == project:
                problems.append(f"PROD_GCP_PROJECT_ID must be independent of {nonprod}")
        if re.search(r"(^|[-_])(dev|stg|staging|nonprod|test)([-_]|$)", project):
            problems.append("PROD_GCP_PROJECT_ID looks like a non-production project")
    for key, expected in SAFETY_ENV.items():
        if (env.get(key) or "false").strip().lower() != expected:
            problems.append(f"{key} must be {expected} (safe default)")
    if problems:
        raise PreflightError("production configuration preflight failed: " + "; ".join(problems))
    return values


def check_staging_evidence(evidence: Any, manifest: dict[str, Any], manifest_sha256: str) -> None:
    """Require staging_verified evidence bound to this exact manifest checksum."""
    if not isinstance(evidence, dict):
        raise PreflightError("staging evidence must be a JSON object")
    if evidence.get("status") != "staging_verified":
        raise PreflightError("staging evidence status must be 'staging_verified'")
    if evidence.get("release_id") != manifest["release_id"]:
        raise PreflightError("staging evidence release_id does not match manifest")
    summary = evidence.get("manifest")
    if not isinstance(summary, dict) or summary.get("manifest_sha256") != manifest_sha256:
        raise PreflightError("staging evidence is bound to a different manifest checksum")
    if summary.get("operator_bff_image") != manifest["backend"]["images"]["operator_bff"]:
        raise PreflightError("staging evidence BFF image differs from manifest")
    if summary.get("frontend_artifact_sha256") != manifest["frontend"]["artifact_sha256"]:
        raise PreflightError("staging evidence FE artifact differs from manifest")
    if not str(evidence.get("workflow_run_id") or "").strip():
        raise PreflightError("staging evidence lacks workflow_run_id")
    gates = evidence.get("gates")
    required = {
        "sanitized_data",
        "migration_and_compatibility",
        "backup_restore_readability",
        "exact_pair_rollback",
        "security_and_safety_readbacks",
        "cleanup_dry_run",
    }
    if not isinstance(gates, dict) or not required <= gates.keys():
        raise PreflightError(f"staging evidence gates incomplete; need {sorted(required)}")


def check_approval(approvals: Any, initiator: str) -> str:
    """Require an approved `production` Environment review by someone other than the initiator.

    `approvals` is the GitHub `actions/runs/{id}/approvals` response (list of
    {state, user:{login}, environments:[{name}]}). Returns the distinct approver login.
    """
    if not initiator or not initiator.strip():
        raise PreflightError("initiator identity is missing")
    if not isinstance(approvals, list):
        raise PreflightError("approval record must be a list of run approvals")
    for entry in approvals:
        if not isinstance(entry, dict) or entry.get("state") != "approved":
            continue
        envs = entry.get("environments") or []
        if not any(isinstance(e, dict) and e.get("name") == "production" for e in envs):
            continue
        login = ((entry.get("user") or {}).get("login") or "").strip()
        if login and login.lower() != initiator.strip().lower():
            return login
    raise PreflightError(
        "no approved 'production' Environment review by a reviewer distinct from the initiator"
    )


def check_baseline(manifest: dict[str, Any], *, bootstrap: bool, baseline_state: Any) -> str:
    """Decide rollback posture: 'baseline' (healthy retained baseline) or 'bootstrap'."""
    if baseline_state is None:
        if not bootstrap:
            raise PreflightError(
                "no retained production baseline; first release must explicitly declare bootstrap"
            )
        if "rollback_baseline" in manifest:
            raise PreflightError("bootstrap release must not invent a rollback_baseline")
        return "bootstrap"
    if bootstrap:
        raise PreflightError("bootstrap declared but a retained baseline exists")
    rb = manifest.get("rollback_baseline")
    if not isinstance(rb, dict):
        raise PreflightError("manifest lacks rollback_baseline for non-bootstrap release")
    if baseline_state.get("healthy") is not True:
        raise PreflightError("retained baseline is not recorded healthy")
    if baseline_state.get("frontend_artifact_sha256") != rb["frontend_artifact_sha256"] or baseline_state.get(
        "operator_bff_image"
    ) != rb["backend_operator_bff_image"]:
        raise PreflightError("retained baseline does not match manifest rollback_baseline")
    return "baseline"


def run_all(
    *,
    env: Mapping[str, str],
    manifest_path: Path,
    expected_sha256: str,
    staging_evidence_path: Path,
    approvals_path: Path | None,
    initiator: str,
    baseline_path: Path | None,
    bootstrap: bool,
    require_approval: bool = True,
) -> dict[str, Any]:
    config = check_config(env)
    manifest = release_manifest.load_manifest_file(manifest_path)
    try:
        release_manifest.validate_manifest(manifest)
        actual = release_manifest.compute_manifest_sha256(manifest)
    except release_manifest.ReleaseManifestError as exc:
        raise PreflightError(str(exc)) from exc
    normalized = expected_sha256 if expected_sha256.startswith("sha256:") else f"sha256:{expected_sha256}"
    if actual != normalized:
        raise PreflightError(f"manifest checksum mismatch: expected {normalized}, got {actual}")
    if manifest["release_state"] not in {"staging_verified", "production_approved", "dev_verified"}:
        raise PreflightError(f"release_state {manifest['release_state']!r} not promotable to production")
    try:
        evidence = json.loads(staging_evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"cannot read staging evidence: {exc}") from exc
    check_staging_evidence(evidence, manifest, actual)
    approver = None
    if require_approval:
        if approvals_path is None:
            raise PreflightError("approval record path is required")
        try:
            approvals = json.loads(approvals_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PreflightError(f"cannot read approval record: {exc}") from exc
        approver = check_approval(approvals, initiator)
    baseline_state = None
    if baseline_path is not None and baseline_path.exists():
        try:
            baseline_state = json.loads(baseline_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PreflightError(f"baseline state unreadable: {exc}") from exc
    posture = check_baseline(manifest, bootstrap=bootstrap, baseline_state=baseline_state)
    return {
        "status": "preflight_passed",
        "release_id": manifest["release_id"],
        "manifest_sha256": actual,
        "approver": approver,
        "rollback_posture": posture,
        "project_id": config["PROD_GCP_PROJECT_ID"],
        "frontend_artifact_sha256": manifest["frontend"]["artifact_sha256"],
        "operator_bff_image": manifest["backend"]["images"]["operator_bff"],
    }


def _cli() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("config", help="validate production configuration from the environment")
    a = sub.add_parser("all", help="full production preflight")
    a.add_argument("--manifest", type=Path, required=True)
    a.add_argument("--expected-sha256", required=True)
    a.add_argument("--staging-evidence", type=Path, required=True)
    a.add_argument("--approvals", type=Path, default=None)
    a.add_argument("--initiator", default="")
    a.add_argument("--baseline", type=Path, default=None)
    a.add_argument("--bootstrap", action="store_true")
    a.add_argument("--skip-approval", action="store_true", help="pre-approval validation only")
    args = p.parse_args()
    try:
        if args.command == "config":
            check_config(os.environ)
            print(json.dumps({"status": "config_ok"}))
            return 0
        result = run_all(
            env=os.environ,
            manifest_path=args.manifest,
            expected_sha256=args.expected_sha256,
            staging_evidence_path=args.staging_evidence,
            approvals_path=args.approvals,
            initiator=args.initiator,
            baseline_path=args.baseline,
            bootstrap=args.bootstrap,
            require_approval=not args.skip_approval,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (PreflightError, release_manifest.ReleaseManifestError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli())

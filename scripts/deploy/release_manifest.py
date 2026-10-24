#!/usr/bin/env python3
"""Release manifest contract validator and builder for immutable FE/BFF promotion.

Enforces:
- Exact FE (`ajoe734/execute-plans`) and BFF (`ajoe734/pantheon`) 40-hex commits.
- Immutable FE artifact SHA-256 checksum and BFF `@sha256:...` container digest.
- Compatibility manifest SHA-256, migration set SHA-256, config schema version,
  and build workflow identity.
- Fail-closed safe defaults (`provider_enabled=False`, `live_broker_enabled=False`,
  `real_writes_enabled=False`, `control_vm_broker_credentials_present=False`).
- Promotion eligibility gate: only genuinely `dev_verified` manifests with matching
  outer SHA-256 and artifact digests may provision ephemeral Staging. Explicitly
  refuses to relabel the unaccepted all12 chain `h12-01a11-20261023-9aeb` or any
  non-passed all12 state as `dev_verified`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any


RELEASE_ID_RE = re.compile(r"^pantheon-[0-9]{8}-[0-9]{3}$")
COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
SHA256_PREFIXED_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_DIGEST_RE = re.compile(r"^[a-zA-Z0-9._:/-]+@sha256:[0-9a-f]{64}$")
WORKFLOW_RUN_RE = re.compile(r"^ajoe734/(?:pantheon|execute-plans)/actions/runs/[0-9]+$")
PANTHEON_WORKFLOW_RUN_RE = re.compile(r"^ajoe734/pantheon/actions/runs/[0-9]+$")
RFC3339_UTC_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")

ALLOWED_RELEASE_STATES = frozenset({
    "candidate_built",
    "dev_verified",
    "staging_provisioned",
    "staging_verified",
    "production_approved",
    "prod_green_verified",
    "prod_active",
    "release_complete",
    "dev_failed",
    "staging_failed",
    "prod_aborted",
    "prod_rolled_back",
    "cleanup_failed",
})

RETIRED_IDENTIFIERS = (
    "pantheon-lupin-dev-20260719",
    "pantheon-benjamin-20260528",
    "35.201.204.12",
    "35.201.239.38",
    "34.81.75.241",
    "104.155.223.192",
    "/home/lupin",
    "front-ai-trading-system",
)

UNACCEPTED_ALL12_CHAINS = frozenset({
    "h12-01a11-20261023-9aeb",
})

REQUIRED_SAFETY_DEFAULTS = {
    "provider_enabled": False,
    "live_broker_enabled": False,
    "real_writes_enabled": False,
    "control_vm_broker_credentials_present": False,
}

REQUIRED_TOP_KEYS = frozenset({
    "schema_version",
    "release_id",
    "release_state",
    "frontend",
    "backend",
    "compatibility_manifest_sha256",
    "migration_set_sha256",
    "config_schema_version",
    "safety_defaults",
    "created_by_workflow",
    "created_at",
})
OPTIONAL_TOP_KEYS = frozenset({
    "dev_verification",
    "rollback_baseline",
})


class ReleaseManifestError(ValueError):
    """Raised when a release manifest fails contract or promotion validation."""


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseManifestError(f"duplicate JSON key in manifest: {key!r}")
        result[key] = value
    return result


def parse_manifest_json(raw: str | bytes) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        parsed = json.loads(text, object_pairs_hook=_no_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseManifestError(f"invalid release manifest JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ReleaseManifestError("release manifest root must be a JSON object")
    return parsed


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Return deterministic canonical JSON bytes for checksum calculation."""
    return (
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode("utf-8")


def compute_manifest_sha256(manifest: dict[str, Any]) -> str:
    """Return `sha256:<64-hex>` over canonical manifest bytes."""
    digest = hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()
    return f"sha256:{digest}"


def _require_str(mapping: dict[str, Any], key: str, pattern: re.Pattern[str] | None, label: str) -> str:
    val = mapping.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ReleaseManifestError(f"missing or non-string {label} ({key})")
    if pattern is not None and not pattern.fullmatch(val):
        raise ReleaseManifestError(f"invalid {label} ({key}): {val!r}")
    return val


def _check_no_retired_identifiers(payload: Any) -> None:
    serialized = json.dumps(payload, sort_keys=True)
    for retired in RETIRED_IDENTIFIERS:
        if retired in serialized:
            raise ReleaseManifestError(
                f"manifest contains retired project/host/repo identifier: {retired!r}"
            )


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Validate a release manifest dictionary against all schema and safety invariants."""
    if not isinstance(manifest, dict):
        raise ReleaseManifestError("manifest must be a dictionary")

    _check_no_retired_identifiers(manifest)

    keys = set(manifest.keys())
    missing = REQUIRED_TOP_KEYS - keys
    if missing:
        raise ReleaseManifestError(f"manifest missing required keys: {sorted(missing)}")
    unknown = keys - (REQUIRED_TOP_KEYS | OPTIONAL_TOP_KEYS)
    if unknown:
        raise ReleaseManifestError(f"manifest contains unknown keys: {sorted(unknown)}")

    if manifest.get("schema_version") is not True and manifest.get("schema_version") != 1:
        raise ReleaseManifestError("schema_version must be integer 1")
    if isinstance(manifest.get("schema_version"), bool):
        raise ReleaseManifestError("schema_version must be integer 1, not boolean")

    _require_str(manifest, "release_id", RELEASE_ID_RE, "release ID")
    release_state = _require_str(manifest, "release_state", None, "release state")
    if release_state not in ALLOWED_RELEASE_STATES:
        raise ReleaseManifestError(f"unsupported release_state: {release_state!r}")

    _require_str(
        manifest,
        "compatibility_manifest_sha256",
        SHA256_PREFIXED_RE,
        "compatibility manifest SHA-256",
    )
    _require_str(
        manifest,
        "migration_set_sha256",
        SHA256_PREFIXED_RE,
        "migration set SHA-256",
    )
    _require_str(manifest, "config_schema_version", None, "config schema version")
    _require_str(manifest, "created_by_workflow", WORKFLOW_RUN_RE, "created_by_workflow")
    _require_str(manifest, "created_at", RFC3339_UTC_RE, "created_at timestamp")

    frontend = manifest.get("frontend")
    if not isinstance(frontend, dict) or set(frontend.keys()) != {
        "repository",
        "commit",
        "artifact_sha256",
    }:
        raise ReleaseManifestError(
            "frontend must be an object with exact keys: repository, commit, artifact_sha256"
        )
    if frontend.get("repository") != "ajoe734/execute-plans":
        raise ReleaseManifestError("frontend.repository must be 'ajoe734/execute-plans'")
    _require_str(frontend, "commit", COMMIT_SHA_RE, "frontend.commit")
    _require_str(frontend, "artifact_sha256", SHA256_PREFIXED_RE, "frontend.artifact_sha256")

    backend = manifest.get("backend")
    if not isinstance(backend, dict) or set(backend.keys()) != {
        "repository",
        "commit",
        "images",
    }:
        raise ReleaseManifestError(
            "backend must be an object with exact keys: repository, commit, images"
        )
    if backend.get("repository") != "ajoe734/pantheon":
        raise ReleaseManifestError("backend.repository must be 'ajoe734/pantheon'")
    _require_str(backend, "commit", COMMIT_SHA_RE, "backend.commit")

    images = backend.get("images")
    if not isinstance(images, dict) or "operator_bff" not in images:
        raise ReleaseManifestError("backend.images must include 'operator_bff'")
    for service_name, image_ref in images.items():
        if not isinstance(service_name, str) or not service_name.strip():
            raise ReleaseManifestError("backend.images service key must be non-empty string")
        if not isinstance(image_ref, str) or not IMAGE_DIGEST_RE.fullmatch(image_ref):
            raise ReleaseManifestError(
                f"backend.images[{service_name!r}] must be an immutable digest reference "
                f"('<repo>@sha256:<64-hex>'), got {image_ref!r}"
            )

    safety = manifest.get("safety_defaults")
    if not isinstance(safety, dict) or set(safety.keys()) != set(REQUIRED_SAFETY_DEFAULTS.keys()):
        raise ReleaseManifestError(
            f"safety_defaults must contain exact keys: {sorted(REQUIRED_SAFETY_DEFAULTS.keys())}"
        )
    for key, expected_val in REQUIRED_SAFETY_DEFAULTS.items():
        actual_val = safety.get(key)
        if type(actual_val) is not bool or actual_val is not expected_val:
            raise ReleaseManifestError(
                f"safety_defaults.{key} must be strictly {expected_val!r} (fail-closed)"
            )

    if "dev_verification" in manifest:
        _validate_dev_verification(manifest["dev_verification"], frontend["commit"], backend["commit"])

    if "rollback_baseline" in manifest:
        _validate_rollback_baseline(manifest["rollback_baseline"], manifest["release_id"])

    return manifest


def _validate_dev_verification(
    dev_ver: Any,
    expected_fe_commit: str,
    expected_bff_commit: str,
) -> None:
    if not isinstance(dev_ver, dict):
        raise ReleaseManifestError("dev_verification must be an object")
    required_keys = {
        "status",
        "verified_at",
        "workflow_run",
        "frontend_commit",
        "bff_version_commit",
        "fe_deployment_manifest_sha256",
    }
    optional_keys = {"all12_chain_id", "all12_status"}
    keys = set(dev_ver.keys())
    if not required_keys.issubset(keys) or not keys.issubset(required_keys | optional_keys):
        raise ReleaseManifestError(
            "dev_verification has invalid or missing keys"
        )
    if dev_ver.get("status") != "dev_verified":
        raise ReleaseManifestError("dev_verification.status must be 'dev_verified'")
    _require_str(dev_ver, "verified_at", RFC3339_UTC_RE, "dev_verification.verified_at")
    _require_str(dev_ver, "workflow_run", PANTHEON_WORKFLOW_RUN_RE, "dev_verification.workflow_run")
    fe_commit = _require_str(dev_ver, "frontend_commit", COMMIT_SHA_RE, "dev_verification.frontend_commit")
    bff_commit = _require_str(dev_ver, "bff_version_commit", COMMIT_SHA_RE, "dev_verification.bff_version_commit")
    _require_str(
        dev_ver,
        "fe_deployment_manifest_sha256",
        SHA256_PREFIXED_RE,
        "dev_verification.fe_deployment_manifest_sha256",
    )
    if fe_commit != expected_fe_commit:
        raise ReleaseManifestError(
            "dev_verification.frontend_commit does not match manifest frontend.commit"
        )
    if bff_commit != expected_bff_commit:
        raise ReleaseManifestError(
            "dev_verification.bff_version_commit does not match manifest backend.commit"
        )
    chain_id = dev_ver.get("all12_chain_id")
    if chain_id is not None:
        if not isinstance(chain_id, str) or not chain_id.strip():
            raise ReleaseManifestError("dev_verification.all12_chain_id must be a non-empty string")
        if chain_id in UNACCEPTED_ALL12_CHAINS:
            raise ReleaseManifestError(
                f"refusing to relabel unaccepted hosted all12 chain {chain_id!r} as dev_verified"
            )
    all12_status = dev_ver.get("all12_status")
    if all12_status is not None and all12_status != "passed":
        raise ReleaseManifestError(
            f"dev_verification.all12_status must be 'passed' to claim dev_verified (got {all12_status!r})"
        )


def _validate_rollback_baseline(baseline: Any, current_release_id: str) -> None:
    if not isinstance(baseline, dict):
        raise ReleaseManifestError("rollback_baseline must be an object")
    required_keys = {
        "previous_release_id",
        "frontend_commit",
        "frontend_artifact_sha256",
        "backend_commit",
        "backend_operator_bff_image",
    }
    if set(baseline.keys()) != required_keys:
        raise ReleaseManifestError(
            f"rollback_baseline must have exact keys: {sorted(required_keys)}"
        )
    prev_id = _require_str(baseline, "previous_release_id", RELEASE_ID_RE, "rollback_baseline.previous_release_id")
    if prev_id == current_release_id:
        raise ReleaseManifestError("rollback_baseline.previous_release_id must differ from release_id")
    _require_str(baseline, "frontend_commit", COMMIT_SHA_RE, "rollback_baseline.frontend_commit")
    _require_str(
        baseline,
        "frontend_artifact_sha256",
        SHA256_PREFIXED_RE,
        "rollback_baseline.frontend_artifact_sha256",
    )
    _require_str(baseline, "backend_commit", COMMIT_SHA_RE, "rollback_baseline.backend_commit")
    _require_str(
        baseline,
        "backend_operator_bff_image",
        IMAGE_DIGEST_RE,
        "rollback_baseline.backend_operator_bff_image",
    )


def verify_promotion_eligibility(
    manifest: dict[str, Any],
    *,
    expected_manifest_sha256: str | None = None,
    observed_fe_sha256: str | None = None,
    observed_bff_digest: str | None = None,
    require_rollback_baseline: bool = True,
) -> dict[str, Any]:
    """Verify a manifest is eligible to provision/promote to Ephemeral Staging.

    Fails closed on:
    - Schema or safety-default violations.
    - Outer manifest SHA-256 mismatch (tampering after Dev verification).
    - Release state not `dev_verified` or missing `dev_verification` proof.
    - Missing `rollback_baseline` when required.
    - Observed FE artifact checksum or BFF container digest mismatch.
    """
    validate_manifest(manifest)

    actual_manifest_sha256 = compute_manifest_sha256(manifest)
    if expected_manifest_sha256 is not None:
        normalized_expected = (
            expected_manifest_sha256
            if expected_manifest_sha256.startswith("sha256:")
            else f"sha256:{expected_manifest_sha256}"
        )
        if not SHA256_PREFIXED_RE.fullmatch(normalized_expected):
            raise ReleaseManifestError(f"invalid expected_manifest_sha256: {expected_manifest_sha256!r}")
        if actual_manifest_sha256 != normalized_expected:
            raise ReleaseManifestError(
                f"immutable manifest checksum mismatch: expected {normalized_expected}, "
                f"got {actual_manifest_sha256}"
            )

    if manifest.get("release_state") != "dev_verified":
        raise ReleaseManifestError(
            f"only 'dev_verified' releases may provision staging "
            f"(got release_state={manifest.get('release_state')!r})"
        )

    if "dev_verification" not in manifest:
        raise ReleaseManifestError(
            "release claims release_state='dev_verified' but lacks dev_verification evidence block"
        )

    if require_rollback_baseline and "rollback_baseline" not in manifest:
        raise ReleaseManifestError(
            "staging promotion requires rollback_baseline in release manifest"
        )

    if observed_fe_sha256 is not None:
        normalized_fe = (
            observed_fe_sha256
            if observed_fe_sha256.startswith("sha256:")
            else f"sha256:{observed_fe_sha256}"
        )
        expected_fe = manifest["frontend"]["artifact_sha256"]
        if normalized_fe != expected_fe:
            raise ReleaseManifestError(
                f"immutable FE artifact checksum mismatch: manifest={expected_fe}, "
                f"observed={normalized_fe}"
            )

    if observed_bff_digest is not None:
        expected_bff_image = manifest["backend"]["images"]["operator_bff"]
        expected_digest = "sha256:" + expected_bff_image.split("@sha256:", 1)[1]
        observed_norm = (
            "sha256:" + observed_bff_digest.split("@sha256:", 1)[1]
            if "@sha256:" in observed_bff_digest
            else (
                observed_bff_digest
                if observed_bff_digest.startswith("sha256:")
                else f"sha256:{observed_bff_digest}"
            )
        )
        if observed_norm != expected_digest or (
            "@sha256:" in observed_bff_digest and observed_bff_digest != expected_bff_image
        ):
            raise ReleaseManifestError(
                f"immutable BFF image digest mismatch: manifest={expected_bff_image}, "
                f"observed={observed_bff_digest}"
            )

    return {
        "release_id": manifest["release_id"],
        "manifest_sha256": actual_manifest_sha256,
        "frontend_commit": manifest["frontend"]["commit"],
        "frontend_artifact_sha256": manifest["frontend"]["artifact_sha256"],
        "backend_commit": manifest["backend"]["commit"],
        "operator_bff_image": manifest["backend"]["images"]["operator_bff"],
        "release_state": manifest["release_state"],
    }


def build_manifest(
    *,
    release_id: str,
    release_state: str,
    frontend_commit: str,
    frontend_artifact_sha256: str,
    backend_commit: str,
    operator_bff_image: str,
    compatibility_manifest_sha256: str,
    migration_set_sha256: str,
    config_schema_version: str,
    created_by_workflow: str,
    created_at: str,
    additional_images: dict[str, str] | None = None,
    dev_verification: dict[str, Any] | None = None,
    rollback_baseline: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct and validate an immutable release manifest."""
    images = {"operator_bff": operator_bff_image}
    if additional_images:
        images.update(additional_images)
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "release_id": release_id,
        "release_state": release_state,
        "frontend": {
            "repository": "ajoe734/execute-plans",
            "commit": frontend_commit,
            "artifact_sha256": frontend_artifact_sha256,
        },
        "backend": {
            "repository": "ajoe734/pantheon",
            "commit": backend_commit,
            "images": images,
        },
        "compatibility_manifest_sha256": compatibility_manifest_sha256,
        "migration_set_sha256": migration_set_sha256,
        "config_schema_version": config_schema_version,
        "safety_defaults": dict(REQUIRED_SAFETY_DEFAULTS),
        "created_by_workflow": created_by_workflow,
        "created_at": created_at,
    }
    if dev_verification is not None:
        manifest["dev_verification"] = dev_verification
    if rollback_baseline is not None:
        manifest["rollback_baseline"] = rollback_baseline
    return validate_manifest(manifest)


def load_manifest_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReleaseManifestError(f"cannot read manifest file {path}: {exc}") from exc
    if len(raw) > 256 * 1024:
        raise ReleaseManifestError("release manifest exceeds 256 KiB size bound")
    return parse_manifest_json(raw)


def _cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    val_p = sub.add_parser("validate", help="Validate a release manifest and optional promotion gate")
    val_p.add_argument("--manifest", type=Path, required=True)
    val_p.add_argument("--expected-sha256", default=None)
    val_p.add_argument("--observed-fe-sha256", default=None)
    val_p.add_argument("--observed-bff-digest", default=None)
    val_p.add_argument("--require-dev-verified", action="store_true")
    val_p.add_argument("--no-require-rollback-baseline", action="store_true")

    hash_p = sub.add_parser("hash", help="Print the canonical sha256:<hex> of a release manifest")
    hash_p.add_argument("--manifest", type=Path, required=True)

    args = parser.parse_args()
    try:
        manifest = load_manifest_file(args.manifest)
        if args.command == "hash":
            validate_manifest(manifest)
            print(compute_manifest_sha256(manifest))
            return 0
        if args.require_dev_verified or args.expected_sha256 or args.observed_fe_sha256 or args.observed_bff_digest:
            summary = verify_promotion_eligibility(
                manifest,
                expected_manifest_sha256=args.expected_sha256,
                observed_fe_sha256=args.observed_fe_sha256,
                observed_bff_digest=args.observed_bff_digest,
                require_rollback_baseline=not args.no_require_rollback_baseline,
            )
        else:
            validate_manifest(manifest)
            summary = {
                "release_id": manifest["release_id"],
                "manifest_sha256": compute_manifest_sha256(manifest),
                "frontend_commit": manifest["frontend"]["commit"],
                "frontend_artifact_sha256": manifest["frontend"]["artifact_sha256"],
                "backend_commit": manifest["backend"]["commit"],
                "operator_bff_image": manifest["backend"]["images"]["operator_bff"],
                "release_state": manifest["release_state"],
            }
        print(json.dumps(summary, sort_keys=True))
        return 0
    except ReleaseManifestError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_cli())

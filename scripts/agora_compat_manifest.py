#!/usr/bin/env python3
"""Generate and validate the Agora dev compatibility manifest.

The manifest is the cross-repo checksum gate for the Pantheon BFF and the
execute-plans Agora frontend. It follows the contract-closure rules in
docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/06_compatibility_manifest_and_hash_rules.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "contracts" / "agora" / "dev-compatibility-manifest.json"
DEFAULT_FRONTEND_ROOT = REPO_ROOT / "execute-plans"
BASE_BUNDLE_PATH = "services/control-plane/specs/agora/bundle_index.json"
EXTENSION_BUNDLE_PATH = "services/control-plane/specs/agora/bundle_index.v1_1.json"
OPENAPI_PATH = "services/control-plane/openapi/agora_v1_1.openapi.yaml"
BASE_CAPABILITY_PATH = "services/control-plane/specs/agora/capability_manifest.json"
EXTENSION_CAPABILITY_PATH = "services/control-plane/specs/agora/v2/capability_manifest_v1_1.json"
COMPAT_SCHEMA_PATH = "services/control-plane/specs/agora/v2/compatibility_manifest.schema.json"
DEFAULT_GENERATED_TYPE_PATHS = (
    "src/lib/bff-v1/agora/contract-snapshot.json",
    "src/lib/bff-v1/agora/types.ts",
)
ZERO_COMMIT = "0" * 40
ZERO_SHA256 = "0" * 64
SHA_RE = re.compile(r"^[a-f0-9]{64}$")
COMMIT_RE = re.compile(r"^[a-f0-9]{40}$")


class ManifestError(ValueError):
    """Raised when manifest generation cannot continue."""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_generated_types(frontend_root: Path, relative_paths: list[str]) -> str:
    lines: list[str] = []
    for rel in sorted(relative_paths):
        path = frontend_root / rel
        if not path.is_file():
            continue
        lines.append(f"{rel}\t{sha256_file(path)}\n")
    if not lines:
        return ZERO_SHA256
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def missing_generated_type_paths(frontend_root: Path, relative_paths: list[str]) -> list[str]:
    return [rel for rel in sorted(relative_paths) if not (frontend_root / rel).is_file()]


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ManifestError(f"missing JSON file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ManifestError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ManifestError(f"JSON file must contain an object: {path}")
    return payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def git_output(args: list[str], default: str = "") -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        return default
    return proc.stdout.strip() or default


def current_commit() -> str:
    return git_output(["rev-parse", "HEAD"], ZERO_COMMIT)


def commit_timestamp(commit: str) -> str:
    if not COMMIT_RE.match(commit) or commit == ZERO_COMMIT:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    value = git_output(["show", "-s", "--format=%cI", commit])
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if value.endswith("+00:00"):
        return value[:-6] + "Z"
    return value


def require_file(rel_path: str) -> Path:
    path = REPO_ROOT / rel_path
    if not path.is_file():
        raise ManifestError(f"required contract file is missing: {rel_path}")
    return path


def load_capabilities() -> dict[str, str]:
    advertised: dict[str, str] = {}

    def add_capability(name: str, version: str) -> None:
        existing = advertised.get(name)
        if existing is None or compare_versions(version, existing) > 0:
            advertised[name] = version

    base = read_json(require_file(BASE_CAPABILITY_PATH))
    for capability in base.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        name = str(capability.get("name") or "")
        if name:
            add_capability(name, str(capability.get("version") or "1.0"))

    extension = read_json(require_file(EXTENSION_CAPABILITY_PATH))
    for capability in extension.get("capabilities", []):
        if not isinstance(capability, dict):
            continue
        name = str(capability.get("name") or "")
        if name:
            add_capability(name, str(capability.get("version") or "1.0"))

    return advertised


def required_capabilities() -> list[dict[str, Any]]:
    return [
        {"name": name, "version": version, "required": True}
        for name, version in sorted(load_capabilities().items())
    ]


def compare_versions(left: str, right: str) -> int:
    def parts(value: str) -> tuple[int, ...]:
        parsed: list[int] = []
        for piece in str(value).split("."):
            try:
                parsed.append(int(piece))
            except ValueError:
                parsed.append(0)
        return tuple(parsed)

    left_parts = parts(left)
    right_parts = parts(right)
    width = max(len(left_parts), len(right_parts))
    left_parts = left_parts + (0,) * (width - len(left_parts))
    right_parts = right_parts + (0,) * (width - len(right_parts))
    return (left_parts > right_parts) - (left_parts < right_parts)


def generated_types_snapshot_reasons(frontend_root: Path) -> list[str]:
    snapshot_path = frontend_root / "src/lib/bff-v1/agora/contract-snapshot.json"
    if not snapshot_path.is_file():
        return ["frontend-generated-types-missing"]
    try:
        snapshot = read_json(snapshot_path)
    except ManifestError:
        return ["frontend-generated-types-invalid-json"]
    if snapshot.get("contract_version") != "1.1":
        return ["frontend-generated-types-not-agora-v1.1"]
    if snapshot.get("source_bundle") != EXTENSION_BUNDLE_PATH:
        return ["frontend-generated-types-source-bundle-mismatch"]
    return []


def build_manifest(args: argparse.Namespace) -> dict[str, Any]:
    backend_runtime_commit = args.backend_runtime_commit or current_commit()
    backend_contract_commit = args.backend_contract_commit or backend_runtime_commit
    frontend_runtime_commit = args.frontend_runtime_commit or os.environ.get("AGORA_FRONTEND_RUNTIME_COMMIT") or ZERO_COMMIT
    frontend_contract_commit = (
        args.frontend_generated_from_contract_commit
        or os.environ.get("AGORA_FRONTEND_GENERATED_FROM_CONTRACT_COMMIT")
        or ZERO_COMMIT
    )
    frontend_root = Path(args.frontend_root).resolve()
    generated_type_paths = list(args.generated_type_path or DEFAULT_GENERATED_TYPE_PATHS)

    base_bundle_sha = sha256_file(require_file(BASE_BUNDLE_PATH))
    extension_bundle_sha = sha256_file(require_file(EXTENSION_BUNDLE_PATH))
    openapi_sha = sha256_file(require_file(OPENAPI_PATH))
    generated_types_sha = sha256_generated_types(frontend_root, generated_type_paths)

    blocking_reasons: list[str] = []
    if frontend_runtime_commit == ZERO_COMMIT:
        blocking_reasons.append("frontend-runtime-commit-placeholder")
    if frontend_contract_commit == ZERO_COMMIT:
        blocking_reasons.append("frontend-generated-contract-commit-placeholder")
    elif frontend_contract_commit != backend_contract_commit:
        blocking_reasons.append("frontend-generated-contract-commit-mismatch")
    if generated_types_sha == ZERO_SHA256 or missing_generated_type_paths(frontend_root, generated_type_paths):
        blocking_reasons.append("frontend-generated-types-missing")
    else:
        blocking_reasons.extend(generated_types_snapshot_reasons(frontend_root))

    status = args.compatibility_status
    if status == "auto":
        status = "compatible" if not blocking_reasons else "pending"

    generated_at = args.generated_at or commit_timestamp(backend_contract_commit)

    return {
        "manifest_version": "1.0",
        "contract_family": "agora.v1.1",
        "environment": args.environment,
        "generated": True,
        "backend": {
            "repo": "ajoe734/pantheon",
            "runtime_commit": backend_runtime_commit,
            "contract_commit": backend_contract_commit,
            "base_bundle_index_sha256": base_bundle_sha,
            "extension_bundle_index_sha256": extension_bundle_sha,
            "openapi_sha256": openapi_sha,
        },
        "frontend": {
            "repo": "ajoe734/execute-plans",
            "runtime_commit": frontend_runtime_commit,
            "generated_from_contract_commit": frontend_contract_commit,
            "base_bundle_index_sha256": base_bundle_sha,
            "extension_bundle_index_sha256": extension_bundle_sha,
            "openapi_sha256": openapi_sha,
            "generated_types_sha256": generated_types_sha,
        },
        "contract_bundle": {
            "base_path": BASE_BUNDLE_PATH,
            "extension_path": EXTENSION_BUNDLE_PATH,
            "openapi_path": OPENAPI_PATH,
        },
        "required_capabilities": required_capabilities(),
        "hash_policy": {
            "file_hash": "sha256-exact-git-bytes-v1",
            "generated_types_hash": "sha256-path-tab-filehash-lf-v1",
        },
        "compatibility_status": status,
        "blocking_reasons": sorted(set(blocking_reasons)),
        "generated_at": generated_at,
    }


def validate_manifest_shape(manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    required_top = {
        "manifest_version",
        "contract_family",
        "environment",
        "generated",
        "backend",
        "frontend",
        "contract_bundle",
        "required_capabilities",
        "hash_policy",
        "compatibility_status",
        "generated_at",
    }
    missing = sorted(required_top - set(manifest))
    issues.extend(f"missing top-level field: {field}" for field in missing)
    if manifest.get("manifest_version") != "1.0":
        issues.append("manifest_version must be 1.0")
    if manifest.get("contract_family") != "agora.v1.1":
        issues.append("contract_family must be agora.v1.1")
    if manifest.get("environment") not in {"dev", "staging", "production"}:
        issues.append("environment must be dev, staging, or production")
    if manifest.get("generated") is not True:
        issues.append("generated must be true")
    if manifest.get("compatibility_status") not in {"compatible", "incompatible", "pending"}:
        issues.append("compatibility_status must be compatible, incompatible, or pending")

    backend = manifest.get("backend")
    frontend = manifest.get("frontend")
    if not isinstance(backend, dict):
        issues.append("backend must be an object")
        backend = {}
    if not isinstance(frontend, dict):
        issues.append("frontend must be an object")
        frontend = {}

    if backend.get("repo") != "ajoe734/pantheon":
        issues.append("backend.repo must be ajoe734/pantheon")
    if frontend.get("repo") != "ajoe734/execute-plans":
        issues.append("frontend.repo must be ajoe734/execute-plans")

    for owner, payload in (("backend", backend), ("frontend", frontend)):
        for key, value in payload.items():
            if key.endswith("_sha256") and not SHA_RE.match(str(value)):
                issues.append(f"{owner}.{key} must be a 64-character lowercase sha256")
        commit_keys = ["runtime_commit"]
        if owner == "backend":
            commit_keys.append("contract_commit")
        else:
            commit_keys.append("generated_from_contract_commit")
        for key in commit_keys:
            if not COMMIT_RE.match(str(payload.get(key) or "")):
                issues.append(f"{owner}.{key} must be a 40-character lowercase git sha")

    contract_bundle = manifest.get("contract_bundle")
    if not isinstance(contract_bundle, dict):
        issues.append("contract_bundle must be an object")
        contract_bundle = {}
    expected_paths = {
        "base_path": BASE_BUNDLE_PATH,
        "extension_path": EXTENSION_BUNDLE_PATH,
        "openapi_path": OPENAPI_PATH,
    }
    for key, expected in expected_paths.items():
        if contract_bundle.get(key) != expected:
            issues.append(f"contract_bundle.{key} must be {expected}")

    hash_policy = manifest.get("hash_policy")
    if not isinstance(hash_policy, dict):
        issues.append("hash_policy must be an object")
        hash_policy = {}
    if hash_policy.get("file_hash") != "sha256-exact-git-bytes-v1":
        issues.append("hash_policy.file_hash must be sha256-exact-git-bytes-v1")
    if hash_policy.get("generated_types_hash") != "sha256-path-tab-filehash-lf-v1":
        issues.append("hash_policy.generated_types_hash must be sha256-path-tab-filehash-lf-v1")

    capabilities = manifest.get("required_capabilities")
    if not isinstance(capabilities, list) or not capabilities:
        issues.append("required_capabilities must be a non-empty array")
    else:
        for index, capability in enumerate(capabilities):
            if not isinstance(capability, dict):
                issues.append(f"required_capabilities[{index}] must be an object")
                continue
            if not capability.get("name"):
                issues.append(f"required_capabilities[{index}].name is required")
            if not capability.get("version"):
                issues.append(f"required_capabilities[{index}].version is required")
            if capability.get("required") is not True:
                issues.append(f"required_capabilities[{index}].required must be true")
    return issues


def validate_local_contract_hashes(manifest: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    expected_hashes = {
        "base_bundle_index_sha256": sha256_file(require_file(BASE_BUNDLE_PATH)),
        "extension_bundle_index_sha256": sha256_file(require_file(EXTENSION_BUNDLE_PATH)),
        "openapi_sha256": sha256_file(require_file(OPENAPI_PATH)),
    }
    for key, actual in expected_hashes.items():
        if backend.get(key) != actual:
            issues.append(f"backend.{key} does not match local {key}: expected {actual}, got {backend.get(key)}")
    return issues


def validate_local_frontend_hashes(
    manifest: dict[str, Any],
    frontend_root: Path,
    generated_type_paths: list[str],
) -> list[str]:
    if not frontend_root.exists():
        return []
    issues: list[str] = []
    missing = missing_generated_type_paths(frontend_root, generated_type_paths)
    if missing:
        issues.append(f"frontend generated type files are missing: {', '.join(missing)}")
        return issues
    actual = sha256_generated_types(frontend_root, generated_type_paths)
    frontend = manifest.get("frontend") if isinstance(manifest.get("frontend"), dict) else {}
    if frontend.get("generated_types_sha256") != actual:
        issues.append(
            "frontend.generated_types_sha256 does not match local generated types: "
            f"expected {actual}, got {frontend.get('generated_types_sha256')}"
        )
    return issues


def validate_capabilities(manifest: dict[str, Any]) -> list[str]:
    advertised = load_capabilities()
    issues: list[str] = []
    for capability in manifest.get("required_capabilities") or []:
        if not isinstance(capability, dict) or not capability.get("required"):
            continue
        name = str(capability.get("name") or "")
        required_version = str(capability.get("version") or "")
        advertised_version = advertised.get(name)
        if advertised_version is None:
            issues.append(f"required capability is not advertised: {name}")
        elif compare_versions(advertised_version, required_version) < 0:
            issues.append(
                f"required capability {name} needs version {required_version}, advertised {advertised_version}"
            )
    return issues


def validate_deployment_rules(
    manifest: dict[str, Any],
    *,
    expected_backend_runtime_commit: str | None = None,
) -> list[str]:
    issues: list[str] = []
    backend = manifest.get("backend") if isinstance(manifest.get("backend"), dict) else {}
    frontend = manifest.get("frontend") if isinstance(manifest.get("frontend"), dict) else {}

    if manifest.get("compatibility_status") != "compatible":
        issues.append("compatibility_status must be compatible for deployment")

    for owner, payload in (("backend", backend), ("frontend", frontend)):
        for key, value in payload.items():
            if key.endswith("_sha256") and value == ZERO_SHA256:
                issues.append(f"{owner}.{key} is a placeholder sha256")
        commit_keys = ["runtime_commit"]
        if owner == "backend":
            commit_keys.append("contract_commit")
        else:
            commit_keys.append("generated_from_contract_commit")
        for key in commit_keys:
            if payload.get(key) == ZERO_COMMIT:
                issues.append(f"{owner}.{key} is a placeholder commit")

    if expected_backend_runtime_commit and backend.get("runtime_commit") != expected_backend_runtime_commit:
        issues.append(
            "backend.runtime_commit does not match deployment ref: "
            f"expected {expected_backend_runtime_commit}, got {backend.get('runtime_commit')}"
        )

    comparisons = (
        ("frontend.generated_from_contract_commit", frontend.get("generated_from_contract_commit"), "backend.contract_commit", backend.get("contract_commit")),
        ("frontend.base_bundle_index_sha256", frontend.get("base_bundle_index_sha256"), "backend.base_bundle_index_sha256", backend.get("base_bundle_index_sha256")),
        ("frontend.extension_bundle_index_sha256", frontend.get("extension_bundle_index_sha256"), "backend.extension_bundle_index_sha256", backend.get("extension_bundle_index_sha256")),
        ("frontend.openapi_sha256", frontend.get("openapi_sha256"), "backend.openapi_sha256", backend.get("openapi_sha256")),
    )
    for left_name, left, right_name, right in comparisons:
        if left != right:
            issues.append(f"{left_name} must equal {right_name}: {left} != {right}")

    if manifest.get("blocking_reasons"):
        issues.append("blocking_reasons must be empty for deployment")

    return issues


def load_manifest_pair(primary: Path, frontend_manifest: Path | None) -> tuple[dict[str, Any], list[str]]:
    manifest = read_json(primary)
    issues: list[str] = []
    if frontend_manifest is not None:
        frontend = read_json(frontend_manifest)
        if frontend != manifest:
            issues.append(f"frontend manifest does not exactly match backend manifest: {frontend_manifest}")
    return manifest, issues


def verify_manifest(
    manifest_path: Path,
    *,
    frontend_manifest_path: Path | None = None,
    frontend_root: Path = DEFAULT_FRONTEND_ROOT,
    generated_type_paths: list[str] | None = None,
    allow_pending: bool = False,
    deployment_gate: bool = False,
    expected_backend_runtime_commit: str | None = None,
) -> list[str]:
    manifest, issues = load_manifest_pair(manifest_path, frontend_manifest_path)
    generated_type_paths = generated_type_paths or list(DEFAULT_GENERATED_TYPE_PATHS)
    issues.extend(validate_manifest_shape(manifest))
    issues.extend(validate_local_contract_hashes(manifest))
    issues.extend(validate_local_frontend_hashes(manifest, frontend_root, generated_type_paths))
    issues.extend(validate_capabilities(manifest))
    if deployment_gate or not allow_pending:
        issues.extend(
            validate_deployment_rules(
                manifest,
                expected_backend_runtime_commit=expected_backend_runtime_commit,
            )
        )
    return issues


def print_issues(issues: list[str]) -> None:
    for issue in issues:
        print(f"ERROR: {issue}", file=sys.stderr)


def command_write(args: argparse.Namespace) -> int:
    try:
        manifest = build_manifest(args)
        if args.stdout:
            print(json.dumps(manifest, indent=2, sort_keys=False))
        else:
            write_json(Path(args.output), manifest)
            print(f"written {Path(args.output)}")
        print(f"compatibility_status={manifest['compatibility_status']}")
        for reason in manifest.get("blocking_reasons", []):
            print(f"blocking_reason={reason}")
        return 0
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def command_verify(args: argparse.Namespace) -> int:
    try:
        issues = verify_manifest(
            Path(args.manifest),
            frontend_manifest_path=Path(args.frontend_manifest) if args.frontend_manifest else None,
            frontend_root=Path(args.frontend_root).resolve(),
            generated_type_paths=list(args.generated_type_path or DEFAULT_GENERATED_TYPE_PATHS),
            allow_pending=args.allow_pending,
            deployment_gate=False,
            expected_backend_runtime_commit=args.backend_runtime_commit,
        )
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if issues:
        print_issues(issues)
        return 1
    print(f"ok {Path(args.manifest)}")
    return 0


def command_deployment_gate(args: argparse.Namespace) -> int:
    try:
        issues = verify_manifest(
            Path(args.manifest),
            frontend_manifest_path=Path(args.frontend_manifest) if args.frontend_manifest else None,
            frontend_root=Path(args.frontend_root).resolve(),
            generated_type_paths=list(args.generated_type_path or DEFAULT_GENERATED_TYPE_PATHS),
            allow_pending=False,
            deployment_gate=True,
            expected_backend_runtime_commit=args.backend_runtime_commit,
        )
    except ManifestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if issues:
        print_issues(issues)
        return 1
    print(f"deployment gate passed {Path(args.manifest)}")
    return 0


def add_write_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("write", help="generate the dev compatibility manifest")
    parser.add_argument("--output", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--stdout", action="store_true")
    parser.add_argument("--environment", default="dev", choices=["dev", "staging", "production"])
    parser.add_argument("--backend-runtime-commit", default="")
    parser.add_argument("--backend-contract-commit", default="")
    parser.add_argument("--frontend-runtime-commit", default="")
    parser.add_argument("--frontend-generated-from-contract-commit", default="")
    parser.add_argument("--frontend-root", default=str(DEFAULT_FRONTEND_ROOT))
    parser.add_argument("--generated-type-path", action="append", default=[])
    parser.add_argument("--generated-at", default="")
    parser.add_argument(
        "--compatibility-status",
        default="auto",
        choices=["auto", "compatible", "incompatible", "pending"],
    )
    parser.set_defaults(func=command_write)


def add_verify_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("verify", help="verify manifest shape and contract hashes")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--frontend-manifest", default="")
    parser.add_argument("--frontend-root", default=str(DEFAULT_FRONTEND_ROOT))
    parser.add_argument("--generated-type-path", action="append", default=[])
    parser.add_argument("--backend-runtime-commit", default="")
    parser.add_argument(
        "--allow-pending",
        action="store_true",
        help="allow pending/incompatible deployment status while still checking schema, hashes and capabilities",
    )
    parser.set_defaults(func=command_verify)


def add_deployment_gate_parser(subparsers: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    parser = subparsers.add_parser("deployment-gate", help="fail unless the manifest is deploy-compatible")
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    parser.add_argument("--frontend-manifest", default="")
    parser.add_argument("--frontend-root", default=str(DEFAULT_FRONTEND_ROOT))
    parser.add_argument("--generated-type-path", action="append", default=[])
    parser.add_argument("--backend-runtime-commit", default="")
    parser.set_defaults(func=command_deployment_gate)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_write_parser(subparsers)
    add_verify_parser(subparsers)
    add_deployment_gate_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())

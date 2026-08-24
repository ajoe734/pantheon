#!/usr/bin/env python3
"""LIFECYCLE-PROJ-RETIRE-001 CLI: allowlisted legacy JSON projection retirement.

This tool scans the legacy lifecycle JSON projection directory, generates a
checksummed exact-path inventory of obsolete dev generations and legacy root
files, and safely archives or prunes them only with explicit operator approval
and an exact bound dry-run inventory.

Security invariants:
- Reject broad paths (e.g., '/', '/data', '/data/bff', '/workspace', '/var').
- Reject globs, wildcards, and unresolved shell variables.
- Strictly prohibit targeting canonical sources (e.g. telemetry_events, pgdata).
- Prohibit targeting relational projection tables or PostgreSQL database cluster.
- Only allow-list known legacy files, gen-NNNNNN directories, and staging directories;
  fail-closed immediately on any un-allowlisted file or directory.
- Default to dry-run mode producing an inventory manifest and inventory digest.
- Execution requires an exact --dry-run-manifest binding and a non-forgeable
  governed Human/Ops --approval-record binding exact inventory SHA-256 digest,
  root, action, recovery posture, and quarantine path. Caller-supplied string
  tokens cannot bypass governed approval.
- Live scan before execution must match the dry-run inventory digest item-for-item.
- Maintain a complete SHA-256 manifest and deletion/quarantine receipt.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence

SCHEMA_VERSION = "pantheon.lifecycle-projector-legacy-retirement.v1"
APPROVAL_SCHEMA_VERSION = "pantheon.lifecycle-projector-retirement-approval.v1"
TASK_ID = "LIFECYCLE-PROJ-RETIRE-001"
DEFAULT_LIFECYCLE_ROOT = "/data/bff/lifecycle-projection"
DEFAULT_QUARANTINE_SUBDIR = "quarantine"
CANONICAL_REPO_ROOT = Path(__file__).resolve().parent.parent
AUTHORITATIVE_SUPERVISOR_CONFIG_PATH = Path(
    "/home/lupin/pantheon-ci-deploy/runtime/live-supervisor-mainroot-config.json"
)
AUTHORITATIVE_SIGNING_KEY_PATHS = (
    Path("/home/lupin/pantheon-ci-deploy/runtime/human-ops-signing.key"),
    Path("/home/lupin/pantheon-ci-deploy/runtime/authority-signing.env"),
)

FORBIDDEN_ROOTS = frozenset(
    {
        "/",
        "/data",
        "/data/bff",
        "/workspace",
        "/var",
        "/tmp",
        "/home",
        "/etc",
        "/usr",
        "/bin",
        "/sbin",
        "/lib",
        "/root",
        "/proc",
        "/sys",
        "/dev",
        "/opt",
    }
)

CANONICAL_SOURCE_PATTERNS = (
    "telemetry_events",
    "public.telemetry_events",
    "pgdata",
    "postgresql",
    "database",
    ".git",
)

UNRESOLVED_VAR_RE = re.compile(r"\$[A-Za-z0-9_]+|\$\{[^}]+\}")
GLOB_CHARS = frozenset({"*", "?", "[", "]"})

KNOWN_LEGACY_ROOT_FILES = frozenset(
    {
        "controller_state.json",
        "health_state.json",
        "trade_journey_events.json",
        "loop_runs.json",
        "cutover-legacy-baseline.snapshot.json",
        "cutover-migrate-result.json",
        "pre-switch-parity.json",
    }
)

KNOWN_GEN_FILES = frozenset(
    {
        "controller_state.json",
        "health_state.json",
        "trade_journey_events.json",
        "loop_runs.json",
    }
)

KNOWN_LEGACY_SYMLINKS = frozenset({"current", "staging"})

GEN_DIR_PATTERN = re.compile(r"^gen-\d{6}$")
STAGING_DIR_PATTERN = re.compile(r"^staging-[a-zA-Z0-9_-]+$")
STAGING_FILE_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]+\.json$")
ALLOWED_APPROVERS = frozenset({"Human/Ops"})


class RetirementValidationError(ValueError):
    """Raised when safety validation rejects a target path, parameter, or inventory."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def compute_inventory_digest(items: List[Dict[str, Any]]) -> str:
    """Compute a deterministic SHA-256 digest over the sorted inventory items."""
    sorted_items = sorted(items, key=lambda x: str(x.get("relative_path", "")))
    h = hashlib.sha256()
    for item in sorted_items:
        line = f"{item.get('relative_path')}:{item.get('size_bytes')}:{item.get('sha256')}\n"
        h.update(line.encode("utf-8"))
    return h.hexdigest()


def resolve_signing_key(
    signing_key_override: Optional[str | bytes] = None,
    *,
    status_root: Optional[Path] = None,
    allow_custom_root: bool = False,
) -> Optional[bytes]:
    """Resolve the authoritative Human/Ops signing key with bound provenance.

    The signing key is loaded strictly from verified authoritative supervisor
    or Human-Ops protected key files. Caller-controlled environment variables
    (such as PANTHEON_HUMAN_OPS_SIGNING_KEY or PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON)
    cannot satisfy the execution gate and are never used as a fallback.
    """
    if allow_custom_root and signing_key_override is not None:
        if isinstance(signing_key_override, str):
            key_bytes = signing_key_override.strip().encode("utf-8")
        else:
            key_bytes = bytes(signing_key_override)
        if key_bytes:
            return key_bytes

    candidate_key_paths: List[Path] = list(AUTHORITATIVE_SIGNING_KEY_PATHS)

    authoritative_config_path = AUTHORITATIVE_SUPERVISOR_CONFIG_PATH.resolve()
    live_config_env = os.environ.get("PANTHEON_LIVE_SUPERVISOR_CONFIG")

    config_candidates: List[Path] = []
    if authoritative_config_path.exists() and authoritative_config_path.is_file():
        config_candidates.append(authoritative_config_path)
    elif allow_custom_root and live_config_env and live_config_env.strip():
        config_candidates.append(Path(live_config_env.strip()).resolve())

    for config_candidate in config_candidates:
        if config_candidate.exists() and config_candidate.is_file():
            try:
                config_data = json.loads(config_candidate.read_text(encoding="utf-8"))
                store = config_data.get("task_state_store")
                if isinstance(store, dict) and store.get("mode") == "authoritative":
                    event_log_raw = store.get("event_log")
                    if event_log_raw:
                        runtime_dir = Path(event_log_raw).resolve().parent
                        candidate_key_paths.append(runtime_dir / "human-ops-signing.key")
                        candidate_key_paths.append(runtime_dir / "authority-signing.env")
            except Exception:
                pass

    if allow_custom_root and status_root is not None:
        resolved_status_root = status_root.resolve()
        candidate_key_paths.append(resolved_status_root.parent / "runtime" / "human-ops-signing.key")
        candidate_key_paths.append(resolved_status_root.parent / "runtime" / "authority-signing.env")

    for key_path in candidate_key_paths:
        resolved_key_path = key_path.resolve()
        if (
            resolved_key_path.exists()
            and resolved_key_path.is_file()
            and not resolved_key_path.is_symlink()
        ):
            try:
                content = resolved_key_path.read_text(encoding="utf-8").strip()
                if resolved_key_path.name.endswith(".env"):
                    for line in content.splitlines():
                        line = line.strip()
                        if line.startswith("PANTHEON_HUMAN_OPS_SIGNING_KEY="):
                            k = line.split("=", 1)[1].strip().strip("'\"")
                            if k:
                                return k.encode("utf-8")
                elif content:
                    return content.encode("utf-8")
            except OSError:
                pass

    return None


def compute_approval_signature(
    task_id: str,
    actor: str,
    action: str,
    root_path: str,
    inventory_sha256: str,
    recovery_possible: bool,
    quarantine_path: Optional[str],
    approved_at_utc: str,
    *,
    signing_key: str | bytes,
) -> str:
    """Compute an authoritative HMAC-SHA256 signature for a Human/Ops approval record."""
    if isinstance(signing_key, str):
        key_bytes = signing_key.strip().encode("utf-8")
    else:
        key_bytes = bytes(signing_key)
    if not key_bytes:
        raise RetirementValidationError("Signing key cannot be empty.")

    canonical_payload = {
        "action": action,
        "actor": actor,
        "approved": True,
        "approved_at_utc": approved_at_utc,
        "inventory_sha256": inventory_sha256,
        "quarantine_path": quarantine_path,
        "recovery_possible": recovery_possible,
        "root_path": root_path,
        "task_id": task_id,
    }
    canonical_bytes = json.dumps(
        canonical_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hmac.new(key_bytes, canonical_bytes, hashlib.sha256).hexdigest()


def _is_unkeyed_sha256_hash(
    record_signature: str,
    task_id: str,
    actor: str,
    action: str,
    root_path: str,
    inventory_sha256: str,
    recovery_possible: bool,
    quarantine_path: Optional[str],
    approved_at_utc: str,
) -> bool:
    """Check if the provided signature is an unkeyed/forgeable SHA-256 hash."""
    canonical_payload = {
        "action": action,
        "actor": actor,
        "approved": True,
        "approved_at_utc": approved_at_utc,
        "inventory_sha256": inventory_sha256,
        "quarantine_path": quarantine_path,
        "recovery_possible": recovery_possible,
        "root_path": root_path,
        "task_id": task_id,
    }
    canonical_bytes = json.dumps(
        canonical_payload, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    unkeyed_hash = hashlib.sha256(canonical_bytes).hexdigest()
    return hmac.compare_digest(record_signature, unkeyed_hash)


def resolve_governed_status_root(
    status_root_override: Optional[Path] = None,
    allow_custom_root: bool = False,
) -> Path:
    """Resolve the authoritative immutable status root for Human/Ops approval records.

    The status root must be an authoritative repository or supervisor coordination root.
    Arbitrary caller-controlled environment overrides are strictly prohibited.
    """
    if allow_custom_root:
        if status_root_override is not None:
            return status_root_override.resolve()
        env_root = os.environ.get("PANTHEON_STATUS_ROOT")
        if env_root and env_root.strip():
            return Path(env_root).resolve()

    # Determine authoritative supervisor config and expected canonical identity
    authoritative_config_path = AUTHORITATIVE_SUPERVISOR_CONFIG_PATH.resolve()
    live_config_env = os.environ.get("PANTHEON_LIVE_SUPERVISOR_CONFIG")

    # In production mode (allow_custom_root=False), caller cannot override the fixed authoritative supervisor config
    if not allow_custom_root and live_config_env and live_config_env.strip():
        resolved_env_config = Path(live_config_env.strip()).resolve()
        if authoritative_config_path.exists() and resolved_env_config != authoritative_config_path:
            raise RetirementValidationError(
                f"Caller-controlled PANTHEON_LIVE_SUPERVISOR_CONFIG override ({str(resolved_env_config)!r}) "
                f"outside fixed authoritative config ({str(authoritative_config_path)!r}) is prohibited."
            )
        if not authoritative_config_path.exists():
            try:
                is_in_repo = resolved_env_config.is_relative_to(CANONICAL_REPO_ROOT.resolve())
            except AttributeError:
                is_in_repo = (
                    resolved_env_config == CANONICAL_REPO_ROOT.resolve()
                    or CANONICAL_REPO_ROOT.resolve() in resolved_env_config.parents
                )
            if not is_in_repo:
                raise RetirementValidationError(
                    f"Caller-controlled PANTHEON_LIVE_SUPERVISOR_CONFIG override ({str(resolved_env_config)!r}) "
                    f"outside canonical repository root ({str(CANONICAL_REPO_ROOT.resolve())!r}) is prohibited."
                )

    live_config_candidates: List[Path] = []
    if authoritative_config_path.exists() and authoritative_config_path.is_file():
        live_config_candidates.append(authoritative_config_path)
    elif live_config_env and live_config_env.strip():
        live_config_candidates.append(Path(live_config_env.strip()).resolve())

    authoritative_identity: Optional[Dict[str, Any]] = None
    authoritative_status_root: Optional[Path] = None

    for config_candidate in live_config_candidates:
        if config_candidate.exists() and config_candidate.is_file():
            try:
                config_data = json.loads(config_candidate.read_text(encoding="utf-8"))
                store = config_data.get("task_state_store")
                if isinstance(store, dict) and store.get("mode") == "authoritative":
                    event_log_raw = store.get("event_log")
                    paths = config_data.get("paths", {})
                    status_file_raw = paths.get("status_file", "ai-status.json")
                    status_file = Path(status_file_raw)
                    if not status_file.is_absolute():
                        status_file = config_candidate.parent.parent / status_file
                    status_root = status_file.parent.resolve()
                    event_log = Path(event_log_raw).resolve() if event_log_raw else None

                    if event_log and status_root.exists() and (status_root / "ai-status.json").exists():
                        payload = {
                            "schema_version": 1,
                            "status_root": str(status_root),
                            "status_file": str(status_root / "ai-status.json"),
                            "archive_root": str(status_root / "ai-task-archive"),
                            "event_log": str(event_log),
                        }
                        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
                        identity_sha256 = hashlib.sha256(encoded).hexdigest()
                        authoritative_identity = {**payload, "identity_sha256": identity_sha256}
                        authoritative_status_root = status_root
                        break
            except Exception:
                pass

    if authoritative_status_root is None:
        if (CANONICAL_REPO_ROOT / "ai-status.json").exists() or (CANONICAL_REPO_ROOT / ".orchestrator").exists():
            authoritative_status_root = CANONICAL_REPO_ROOT.resolve()

    if status_root_override is not None:
        resolved = status_root_override.resolve()
        if (
            authoritative_status_root is not None
            and resolved == authoritative_status_root
        ) or (
            (resolved / "ai-status.json").exists()
            and not (resolved / "ai-status.json").is_symlink()
            and resolved == CANONICAL_REPO_ROOT.resolve()
        ):
            return resolved
        raise RetirementValidationError(
            f"Explicit status root override {str(resolved)!r} is not a valid canonical status root."
        )

    canonical_identity_raw = os.environ.get("PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON")
    if canonical_identity_raw and canonical_identity_raw.strip():
        try:
            identity_data = json.loads(canonical_identity_raw)
        except Exception as exc:
            raise RetirementValidationError(
                f"PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON is invalid JSON: {exc}"
            ) from exc

        if not isinstance(identity_data, dict):
            raise RetirementValidationError(
                "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON must be a JSON object"
            )

        bound_status_root = identity_data.get("status_root")
        bound_status_file = identity_data.get("status_file")
        bound_archive_root = identity_data.get("archive_root")
        bound_event_log = identity_data.get("event_log")
        identity_sha256 = identity_data.get("identity_sha256")

        if not all([bound_status_root, bound_status_file, bound_archive_root, bound_event_log, identity_sha256]):
            raise RetirementValidationError(
                "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON is missing required fields"
            )

        expected_payload = {
            "schema_version": int(identity_data.get("schema_version", 1)),
            "status_root": str(bound_status_root),
            "status_file": str(bound_status_file),
            "archive_root": str(bound_archive_root),
            "event_log": str(bound_event_log),
        }
        encoded = json.dumps(expected_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        computed_identity_sha = hashlib.sha256(encoded).hexdigest()
        if computed_identity_sha != identity_sha256:
            raise RetirementValidationError(
                "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON identity_sha256 integrity mismatch"
            )

        resolved_bound_root = Path(bound_status_root).resolve()

        if authoritative_identity is not None:
            if identity_data != authoritative_identity:
                raise RetirementValidationError(
                    "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON does not match the authoritative supervisor task state identity."
                )
        else:
            if not allow_custom_root:
                raise RetirementValidationError(
                    "PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON cannot be verified: "
                    "authoritative supervisor configuration is absent. Unverified task state identity is prohibited."
                )
            if resolved_bound_root != CANONICAL_REPO_ROOT.resolve():
                raise RetirementValidationError(
                    f"PANTHEON_CANONICAL_TASK_STATE_IDENTITY_JSON status root {str(resolved_bound_root)!r} does not match canonical repository root {str(CANONICAL_REPO_ROOT.resolve())!r}"
                )

        if not resolved_bound_root.exists() or not (resolved_bound_root / "ai-status.json").exists():
            raise RetirementValidationError(
                f"Authoritative status root from identity binding {str(resolved_bound_root)!r} does not exist or lacks ai-status.json"
            )

        env_status_root = os.environ.get("PANTHEON_STATUS_ROOT")
        if env_status_root and env_status_root.strip():
            if Path(env_status_root).resolve() != resolved_bound_root:
                raise RetirementValidationError(
                    f"PANTHEON_STATUS_ROOT override {env_status_root!r} conflicts with authoritative identity root {str(resolved_bound_root)!r}"
                )

        return resolved_bound_root

    env_root = os.environ.get("PANTHEON_STATUS_ROOT")
    if env_root and env_root.strip():
        resolved_env_root = Path(env_root).resolve()
        expected_target = authoritative_status_root or CANONICAL_REPO_ROOT.resolve()
        if resolved_env_root != expected_target and resolved_env_root != CANONICAL_REPO_ROOT.resolve():
            raise RetirementValidationError(
                f"Caller-controlled PANTHEON_STATUS_ROOT override ({str(resolved_env_root)!r}) outside authoritative status root "
                f"({str(expected_target)!r}) is prohibited for destructive retirement execution without authoritative identity."
            )
        return resolved_env_root

    if authoritative_status_root is not None and (authoritative_status_root / "ai-status.json").exists():
        return authoritative_status_root

    if (CANONICAL_REPO_ROOT / "ai-status.json").exists() or (CANONICAL_REPO_ROOT / ".orchestrator").exists():
        return CANONICAL_REPO_ROOT.resolve()

    return Path.cwd().resolve()


def validate_path_safety(root_path: Path, allow_custom_root: bool = False) -> Path:
    raw_str = str(root_path).strip()
    if not raw_str:
        raise RetirementValidationError("Root path must not be empty.")

    if UNRESOLVED_VAR_RE.search(raw_str):
        raise RetirementValidationError(
            f"Root path contains unresolved environment variables: {raw_str!r}"
        )

    if any(ch in raw_str for ch in GLOB_CHARS):
        raise RetirementValidationError(
            f"Root path contains prohibited glob characters: {raw_str!r}"
        )

    for pat in CANONICAL_SOURCE_PATTERNS:
        if pat in raw_str.lower():
            raise RetirementValidationError(
                f"Root path matches canonical source pattern {pat!r}: {raw_str!r}"
            )

    resolved = root_path.resolve()
    resolved_str = str(resolved)
    normalized_str = resolved_str.rstrip("/") or "/"

    if resolved_str == "/" or normalized_str in FORBIDDEN_ROOTS:
        raise RetirementValidationError(
            f"Root path {resolved_str!r} is a broad or system directory; retirement is prohibited."
        )

    if not allow_custom_root:
        default_resolved = Path(DEFAULT_LIFECYCLE_ROOT).resolve()
        if resolved != default_resolved and not resolved_str.startswith(
            str(default_resolved) + "/"
        ):
            raise RetirementValidationError(
                f"Target path {resolved_str!r} is outside the allowed default root {DEFAULT_LIFECYCLE_ROOT!r}. "
                "Retirement of non-lifecycle paths is prohibited."
            )

    return resolved


def validate_destination_path_safety(
    dest_path: Path, root_path: Path, allow_custom_root: bool = False
) -> Path:
    raw_str = str(dest_path).strip()
    if not raw_str:
        raise RetirementValidationError("Quarantine destination path must not be empty.")

    if UNRESOLVED_VAR_RE.search(raw_str):
        raise RetirementValidationError(
            f"Quarantine destination path contains unresolved environment variables: {raw_str!r}"
        )

    if any(ch in raw_str for ch in GLOB_CHARS):
        raise RetirementValidationError(
            f"Quarantine destination path contains prohibited glob characters: {raw_str!r}"
        )

    for pat in CANONICAL_SOURCE_PATTERNS:
        if pat in raw_str.lower():
            raise RetirementValidationError(
                f"Quarantine destination path matches canonical source pattern {pat!r}: {raw_str!r}"
            )

    resolved = dest_path.resolve()
    resolved_str = str(resolved)
    normalized_str = resolved_str.rstrip("/") or "/"

    if resolved_str == "/" or normalized_str in FORBIDDEN_ROOTS:
        raise RetirementValidationError(
            f"Quarantine destination path {resolved_str!r} is a broad or system directory; quarantine destination is prohibited."
        )

    resolved_root = root_path.resolve()
    if resolved == resolved_root:
        raise RetirementValidationError(
            f"Quarantine destination path {resolved_str!r} cannot be identical to the lifecycle root path."
        )

    if not allow_custom_root:
        default_resolved = Path(DEFAULT_LIFECYCLE_ROOT).resolve()
        if resolved != default_resolved and not resolved_str.startswith(
            str(default_resolved) + "/"
        ):
            raise RetirementValidationError(
                f"Quarantine destination path {resolved_str!r} is outside the allowed default root {DEFAULT_LIFECYCLE_ROOT!r}. "
                "Use --allow-custom-root for testing with explicit temporary directories."
            )

    return resolved


def scan_legacy_inventory(
    root: Path, quarantine_dir: Optional[Path] = None
) -> List[Dict[str, Any]]:
    if not root.exists():
        return []

    quarantine_resolved = quarantine_dir.resolve() if quarantine_dir else None
    items: List[Dict[str, Any]] = []

    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if quarantine_resolved and entry.resolve() == quarantine_resolved:
            continue

        rel_name = entry.name

        if entry.is_symlink():
            if rel_name in KNOWN_LEGACY_SYMLINKS or GEN_DIR_PATTERN.match(rel_name):
                items.append(
                    {
                        "path": str(entry.resolve()),
                        "entry_path": str(entry),
                        "relative_path": rel_name,
                        "size_bytes": 0,
                        "sha256": "symlink",
                        "category": "legacy_symlink",
                        "is_symlink": True,
                        "is_dir": False,
                    }
                )
            else:
                raise RetirementValidationError(
                    f"Unexpected un-allowlisted symlink in lifecycle root: {rel_name!r}; scan failed closed."
                )
            continue

        if entry.is_dir():
            if GEN_DIR_PATTERN.match(rel_name):
                for sub_file in sorted(entry.iterdir(), key=lambda p: p.name):
                    if sub_file.is_dir() or sub_file.is_symlink():
                        raise RetirementValidationError(
                            f"Unexpected nested directory or symlink in generation {rel_name}: {sub_file.name!r}; scan failed closed."
                        )
                    if sub_file.name not in KNOWN_GEN_FILES:
                        raise RetirementValidationError(
                            f"Unexpected non-allowlisted file in generation {rel_name}: {sub_file.name!r}; scan failed closed."
                        )
                    rel_path = str(sub_file.relative_to(root))
                    items.append(
                        {
                            "path": str(sub_file.resolve()),
                            "entry_path": str(sub_file),
                            "relative_path": rel_path,
                            "size_bytes": sub_file.stat().st_size,
                            "sha256": _compute_sha256(sub_file),
                            "category": "legacy_generation_file",
                            "is_symlink": False,
                            "is_dir": False,
                        }
                    )
            elif STAGING_DIR_PATTERN.match(rel_name):
                for sub_file in sorted(entry.iterdir(), key=lambda p: p.name):
                    if sub_file.is_dir() or sub_file.is_symlink():
                        raise RetirementValidationError(
                            f"Unexpected nested directory or symlink in staging {rel_name}: {sub_file.name!r}; scan failed closed."
                        )
                    if not STAGING_FILE_PATTERN.match(sub_file.name) and sub_file.name not in KNOWN_GEN_FILES:
                        raise RetirementValidationError(
                            f"Unexpected non-allowlisted file in staging {rel_name}: {sub_file.name!r}; scan failed closed."
                        )
                    rel_path = str(sub_file.relative_to(root))
                    items.append(
                        {
                            "path": str(sub_file.resolve()),
                            "entry_path": str(sub_file),
                            "relative_path": rel_path,
                            "size_bytes": sub_file.stat().st_size,
                            "sha256": _compute_sha256(sub_file),
                            "category": "legacy_staging_file",
                            "is_symlink": False,
                            "is_dir": False,
                        }
                    )
            else:
                raise RetirementValidationError(
                    f"Unexpected un-allowlisted directory in lifecycle root: {rel_name!r}; scan failed closed."
                )
            continue

        if entry.is_file():
            if rel_name not in KNOWN_LEGACY_ROOT_FILES:
                raise RetirementValidationError(
                    f"Unexpected un-allowlisted file in lifecycle root: {rel_name!r}; scan failed closed."
                )
            items.append(
                {
                    "path": str(entry.resolve()),
                    "entry_path": str(entry),
                    "relative_path": rel_name,
                    "size_bytes": entry.stat().st_size,
                    "sha256": _compute_sha256(entry),
                    "category": "legacy_root_file",
                    "is_symlink": False,
                    "is_dir": False,
                }
            )

    return items


def load_and_validate_approval_record(
    record_path: Optional[Path],
    *,
    expected_task_id: str = TASK_ID,
    expected_inventory_sha256: str,
    expected_root_path: str,
    expected_action: str,
    expected_recovery_possible: bool,
    expected_quarantine_path: Optional[str],
    status_root: Optional[Path] = None,
    allow_custom_root: bool = False,
    signing_key: Optional[str | bytes] = None,
) -> Dict[str, Any]:
    """Load and validate an authoritative, signed Human/Ops approval record bound to the governed status root."""
    if record_path is None:
        raise RetirementValidationError(
            "Execution requires --approval-record pointing to an authorized Human/Ops approval record JSON file; "
            "caller-supplied tokens or approvers cannot bypass governed approval."
        )

    if not record_path.exists():
        raise RetirementValidationError(
            f"Approval record file does not exist at {record_path}."
        )

    if record_path.is_symlink():
        raise RetirementValidationError(
            f"Approval record path cannot be a symlink: {record_path}."
        )

    governed_root = resolve_governed_status_root(status_root, allow_custom_root=allow_custom_root)
    resolved_record_path = record_path.resolve()
    resolved_governed_root = governed_root.resolve()

    # Enforce containment in the authoritative governed status root to reject self-authored/unauthoritative JSON
    try:
        is_contained = resolved_record_path.is_relative_to(resolved_governed_root)
    except AttributeError:
        # Python < 3.9 fallback
        is_contained = (
            resolved_record_path == resolved_governed_root
            or resolved_governed_root in resolved_record_path.parents
        )

    if not is_contained:
        raise RetirementValidationError(
            f"Approval record path {str(resolved_record_path)!r} is outside the governed status root {str(resolved_governed_root)!r}; "
            "self-authored or unauthoritative approval records outside the status root are prohibited."
        )

    try:
        content = record_path.read_text(encoding="utf-8")
        record = json.loads(content)
    except Exception as err:
        raise RetirementValidationError(
            f"Failed to read or parse approval record JSON at {record_path}: {err}"
        ) from err

    if not isinstance(record, dict):
        raise RetirementValidationError("Governed approval record must be a JSON object.")

    schema_ver = record.get("schema_version")
    if schema_ver not in {APPROVAL_SCHEMA_VERSION, 1, "1"}:
        raise RetirementValidationError(
            f"Approval record schema mismatch: expected {APPROVAL_SCHEMA_VERSION!r}, got {schema_ver!r}"
        )

    task_id = record.get("task_id")
    if task_id != expected_task_id:
        raise RetirementValidationError(
            f"Approval record task mismatch: expected {expected_task_id!r}, got {task_id!r}"
        )

    actor = record.get("actor") or record.get("approver")
    if actor != "Human/Ops" and actor not in ALLOWED_APPROVERS:
        raise RetirementValidationError(
            f"Approval record must be issued by an authorized operator ('Human/Ops'); got actor {actor!r}"
        )

    if record.get("approved") is not True:
        raise RetirementValidationError(
            "Governed approval record 'approved' field must be boolean True; retirement execution is not approved."
        )

    record_digest = (
        record.get("inventory_sha256")
        or record.get("dry_run_digest")
        or record.get("manifest_sha256")
    )
    if not record_digest or record_digest != expected_inventory_sha256:
        raise RetirementValidationError(
            f"Approval record inventory digest mismatch: record specifies {record_digest!r}, "
            f"expected exact dry-run digest {expected_inventory_sha256!r}. "
            "Re-run dry-run scan and obtain a fresh Human/Ops approval record."
        )

    record_root = record.get("root_path")
    if record_root != expected_root_path:
        raise RetirementValidationError(
            f"Approval record root mismatch: record specifies root_path {record_root!r}, "
            f"expected exact root_path {expected_root_path!r}."
        )

    record_action = record.get("action")
    if record_action != expected_action:
        raise RetirementValidationError(
            f"Approval record action mismatch: record specifies action {record_action!r}, "
            f"expected exact action {expected_action!r}."
        )

    record_recovery = record.get("recovery_possible")
    if record_recovery != expected_recovery_possible:
        raise RetirementValidationError(
            f"Approval record recovery posture mismatch: record specifies recovery_possible={record_recovery!r}, "
            f"expected recovery_possible={expected_recovery_possible!r}."
        )

    record_quarantine = record.get("quarantine_path")
    if expected_action in {"archive", "quarantine"}:
        if record_quarantine != expected_quarantine_path:
            raise RetirementValidationError(
                f"Approval record quarantine path mismatch: record specifies quarantine_path {record_quarantine!r}, "
                f"expected exact quarantine_path {expected_quarantine_path!r}."
            )
    elif expected_action == "delete":
        if record_quarantine is not None:
            raise RetirementValidationError(
                f"Approval record quarantine path mismatch: delete action must specify quarantine_path=null, "
                f"got {record_quarantine!r}."
            )

    approved_at_utc = record.get("approved_at_utc")
    if not approved_at_utc or not isinstance(approved_at_utc, str):
        raise RetirementValidationError(
            "Approval record must include an approved_at_utc timestamp string."
        )

    # Validate authoritative signature
    record_signature = (
        record.get("signature_sha256")
        or record.get("signature_hmac_sha256")
        or record.get("signature")
        or record.get("approval_signature")
    )
    if not record_signature:
        raise RetirementValidationError(
            "Approval record must contain an authoritative signature ('signature_sha256'); unsigned records are rejected."
        )

    # Explicitly detect and reject forgeable unkeyed deterministic hashes
    if _is_unkeyed_sha256_hash(
        record_signature=record_signature,
        task_id=expected_task_id,
        actor=actor,
        action=expected_action,
        root_path=expected_root_path,
        inventory_sha256=expected_inventory_sha256,
        recovery_possible=expected_recovery_possible,
        quarantine_path=expected_quarantine_path,
        approved_at_utc=approved_at_utc,
    ):
        raise RetirementValidationError(
            "Approval record signature is a forgeable unkeyed SHA-256 hash; authoritative HMAC-SHA256 signature from Human/Ops is required."
        )

    resolved_key = resolve_signing_key(
        signing_key,
        status_root=status_root,
        allow_custom_root=allow_custom_root,
    )
    if not resolved_key:
        raise RetirementValidationError(
            "Authoritative Human/Ops signing key (human-ops-signing.key or authority-signing.env) is required to verify approval record; unauthenticated execution is prohibited."
        )

    expected_signature = compute_approval_signature(
        task_id=expected_task_id,
        actor=actor,
        action=expected_action,
        root_path=expected_root_path,
        inventory_sha256=expected_inventory_sha256,
        recovery_possible=expected_recovery_possible,
        quarantine_path=expected_quarantine_path,
        approved_at_utc=approved_at_utc,
        signing_key=resolved_key,
    )
    if not hmac.compare_digest(record_signature, expected_signature):
        raise RetirementValidationError(
            f"Approval record signature mismatch: record specifies {record_signature!r}, "
            f"expected exact HMAC signature {expected_signature!r}."
        )

    return record


def execute_retirement(
    root: Path,
    items: List[Dict[str, Any]],
    action: str,
    quarantine_dir: Optional[Path] = None,
    approval_record: Optional[Dict[str, Any]] = None,
    approval_record_path: Optional[Path] = None,
    dry_run_manifest_path: Optional[str] = None,
    bound_inventory_sha256: str = "",
) -> Dict[str, Any]:
    executed_at = _utc_now()
    processed_count = 0
    processed_bytes = 0
    item_receipts: List[Dict[str, Any]] = []

    approver_actor = (approval_record.get("actor") or approval_record.get("approver") if approval_record else "Human/Ops") or "Human/Ops"
    approval_record_str = str(approval_record_path) if approval_record_path else None
    approval_record_sha = _compute_sha256(approval_record_path) if approval_record_path and approval_record_path.exists() else None
    approval_time = approval_record.get("approved_at_utc") if approval_record else None
    approval_sig = (
        approval_record.get("signature_sha256")
        or approval_record.get("signature_hmac_sha256")
        or approval_record.get("signature")
        if approval_record
        else None
    )

    if action in {"archive", "quarantine"}:
        if quarantine_dir is None:
            quarantine_dir = root / DEFAULT_QUARANTINE_SUBDIR
        quarantine_dir.mkdir(parents=True, exist_ok=True)

        for item in items:
            src = Path(item["entry_path"])
            if not src.exists() and not src.is_symlink():
                continue

            rel = item["relative_path"]
            dest = quarantine_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)

            if src.is_symlink():
                target = os.readlink(src)
                src.unlink()
                try:
                    os.symlink(target, dest)
                except OSError:
                    pass
            else:
                shutil.move(str(src), str(dest))
                processed_bytes += item["size_bytes"]

            processed_count += 1
            item_receipts.append(
                {
                    "relative_path": rel,
                    "action": "quarantined",
                    "sha256": item["sha256"],
                    "size_bytes": item["size_bytes"],
                    "destination": str(dest),
                }
            )

        for gen_dir in sorted(root.glob("gen-*")):
            if gen_dir.is_dir() and not any(gen_dir.iterdir()):
                try:
                    gen_dir.rmdir()
                except OSError:
                    pass
        for staging_dir in sorted(root.glob("staging*")):
            if staging_dir.is_dir() and not any(staging_dir.iterdir()):
                try:
                    staging_dir.rmdir()
                except OSError:
                    pass

        return {
            "status": "completed",
            "action": "quarantine",
            "executed_at_utc": executed_at,
            "files_processed": processed_count,
            "bytes_processed": processed_bytes,
            "approver": approver_actor,
            "approval_record_path": approval_record_str,
            "approval_record_sha256": approval_record_sha,
            "approval_record_signature": approval_sig,
            "approval_record_approved_at_utc": approval_time,
            "dry_run_manifest_path": dry_run_manifest_path,
            "bound_inventory_sha256": bound_inventory_sha256,
            "quarantine_location": str(quarantine_dir),
            "recovery_possible": True,
            "receipt_items": item_receipts,
        }

    if action == "delete":
        for item in items:
            src = Path(item["entry_path"])
            if not src.exists() and not src.is_symlink():
                continue

            if src.is_symlink() or src.is_file():
                src.unlink()
                processed_bytes += item["size_bytes"]
                processed_count += 1
                item_receipts.append(
                    {
                        "relative_path": item["relative_path"],
                        "action": "deleted",
                        "sha256": item["sha256"],
                        "size_bytes": item["size_bytes"],
                    }
                )

        for gen_dir in sorted(root.glob("gen-*")):
            if gen_dir.is_dir() and not any(gen_dir.iterdir()):
                try:
                    gen_dir.rmdir()
                except OSError:
                    pass
        for staging_dir in sorted(root.glob("staging*")):
            if staging_dir.is_dir() and not any(staging_dir.iterdir()):
                try:
                    staging_dir.rmdir()
                except OSError:
                    pass

        return {
            "status": "completed",
            "action": "delete",
            "executed_at_utc": executed_at,
            "files_processed": processed_count,
            "bytes_processed": processed_bytes,
            "approver": approver_actor,
            "approval_record_path": approval_record_str,
            "approval_record_sha256": approval_record_sha,
            "approval_record_signature": approval_sig,
            "approval_record_approved_at_utc": approval_time,
            "dry_run_manifest_path": dry_run_manifest_path,
            "bound_inventory_sha256": bound_inventory_sha256,
            "quarantine_location": None,
            "recovery_possible": False,
            "receipt_items": item_receipts,
        }

    raise ValueError(f"Unsupported action: {action!r}")


def run_retirement(
    root_path: Path,
    action: str = "archive",
    execute: bool = False,
    approval_record_path: Optional[Path] = None,
    dry_run_manifest_path: Optional[Path] = None,
    quarantine_dir: Optional[Path] = None,
    allow_custom_root: bool = False,
    status_root: Optional[Path] = None,
    approval_token: str = "",
    approver: str = "",
    signing_key: Optional[str | bytes] = None,
) -> Dict[str, Any]:
    safe_root = validate_path_safety(root_path, allow_custom_root=allow_custom_root)

    safe_quarantine: Optional[Path] = None
    if action in {"archive", "quarantine"}:
        if quarantine_dir is None:
            quarantine_dir = safe_root / DEFAULT_QUARANTINE_SUBDIR
        safe_quarantine = validate_destination_path_safety(
            quarantine_dir, safe_root, allow_custom_root=allow_custom_root
        )
    elif quarantine_dir is not None:
        safe_quarantine = validate_destination_path_safety(
            quarantine_dir, safe_root, allow_custom_root=allow_custom_root
        )

    items = scan_legacy_inventory(safe_root, quarantine_dir=safe_quarantine)
    total_files = len(items)
    total_bytes = sum(item["size_bytes"] for item in items)
    inventory_digest = compute_inventory_digest(items)

    req_approved_at = _utc_now()
    req_quarantine_path = (
        str(safe_quarantine)
        if action != "delete" and safe_quarantine is not None
        else None
    )

    resolved_key = resolve_signing_key(
        signing_key,
        status_root=status_root,
        allow_custom_root=allow_custom_root,
    )
    if resolved_key:
        req_sig = compute_approval_signature(
            task_id=TASK_ID,
            actor="Human/Ops",
            action=action,
            root_path=str(safe_root),
            inventory_sha256=inventory_digest,
            recovery_possible=action != "delete",
            quarantine_path=req_quarantine_path,
            approved_at_utc=req_approved_at,
            signing_key=resolved_key,
        )
    else:
        req_sig = "<REQUIRED_HUMAN_OPS_HMAC_SHA256_SIGNATURE>"

    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task_id": TASK_ID,
        "generated_at_utc": req_approved_at,
        "mode": "executed" if execute else "dry_run",
        "action": action,
        "root_path": str(safe_root),
        "quarantine_path": req_quarantine_path,
        "inventory_sha256": inventory_digest,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "recovery_possible": action != "delete",
        "items": items,
        "required_approval_record": {
            "schema_version": APPROVAL_SCHEMA_VERSION,
            "task_id": TASK_ID,
            "actor": "Human/Ops",
            "approved": True,
            "approved_at_utc": req_approved_at,
            "action": action,
            "recovery_possible": action != "delete",
            "root_path": str(safe_root),
            "quarantine_path": req_quarantine_path,
            "inventory_sha256": inventory_digest,
            "signature_sha256": req_sig,
        },
        "execution_receipt": None,
    }

    if execute:
        # 1. Require and validate dry-run manifest binding
        if dry_run_manifest_path is None or not dry_run_manifest_path.exists():
            raise RetirementValidationError(
                "Execution requires --dry-run-manifest pointing to an approved dry-run manifest file."
            )

        try:
            manifest_content = dry_run_manifest_path.read_text(encoding="utf-8")
            approved_manifest = json.loads(manifest_content)
        except Exception as err:
            raise RetirementValidationError(
                f"Failed to parse --dry-run-manifest at {dry_run_manifest_path}: {err}"
            ) from err

        if approved_manifest.get("schema_version") != SCHEMA_VERSION:
            raise RetirementValidationError(
                f"Dry-run manifest schema mismatch: expected {SCHEMA_VERSION!r}, got {approved_manifest.get('schema_version')!r}"
            )

        if approved_manifest.get("task_id") != TASK_ID:
            raise RetirementValidationError(
                f"Dry-run manifest task mismatch: expected {TASK_ID!r}, got {approved_manifest.get('task_id')!r}"
            )

        if approved_manifest.get("mode") != "dry_run":
            raise RetirementValidationError(
                f"Bound manifest is not in dry_run mode: {approved_manifest.get('mode')!r}"
            )

        manifest_root = approved_manifest.get("root_path")
        if manifest_root != str(safe_root):
            raise RetirementValidationError(
                f"Root mismatch: dry-run manifest root {manifest_root!r} != target root {str(safe_root)!r}"
            )

        manifest_action = approved_manifest.get("action")
        if manifest_action != action:
            raise RetirementValidationError(
                f"Action mismatch: approved dry-run manifest specifies action {manifest_action!r}, "
                f"but execution requested action {action!r}. Action and recovery posture are bound to the approved manifest."
            )

        manifest_recovery = approved_manifest.get("recovery_possible")
        if manifest_recovery != manifest.get("recovery_possible"):
            raise RetirementValidationError(
                f"Recovery posture mismatch: approved dry-run manifest specifies recovery_possible={manifest_recovery!r}, "
                f"but execution requested recovery_possible={manifest.get('recovery_possible')!r}."
            )

        manifest_quarantine = approved_manifest.get("quarantine_path")
        expected_quarantine = (
            str(safe_quarantine)
            if safe_quarantine is not None and action != "delete"
            else None
        )
        if action in {"archive", "quarantine"}:
            if manifest_quarantine is None:
                raise RetirementValidationError(
                    "Quarantine path missing from approved dry-run manifest for archive/quarantine action."
                )
            if manifest_quarantine != expected_quarantine:
                raise RetirementValidationError(
                    f"Quarantine path mismatch: approved dry-run manifest specifies quarantine_path {manifest_quarantine!r}, "
                    f"but execution requested quarantine_path {expected_quarantine!r}. "
                    "Quarantine destination is bound to the approved dry-run manifest."
                )
        elif action == "delete":
            if manifest_quarantine is not None:
                raise RetirementValidationError(
                    f"Quarantine path mismatch: approved dry-run manifest specifies quarantine_path {manifest_quarantine!r}, "
                    "but delete action must have null quarantine_path."
                )

        approved_items = approved_manifest.get("items", [])
        expected_digest = approved_manifest.get("inventory_sha256") or compute_inventory_digest(
            approved_items
        )

        if inventory_digest != expected_digest or len(items) != len(approved_items):
            raise RetirementValidationError(
                f"Live inventory drifted from approved dry-run manifest! "
                f"Expected digest {expected_digest} ({len(approved_items)} items), "
                f"live digest {inventory_digest} ({len(items)} items). Execution aborted."
            )

        # 2. Validate authoritative Human/Ops approval record bound to governed status root
        approval_record = load_and_validate_approval_record(
            approval_record_path,
            expected_task_id=TASK_ID,
            expected_inventory_sha256=inventory_digest,
            expected_root_path=str(safe_root),
            expected_action=action,
            expected_recovery_possible=manifest["recovery_possible"],
            expected_quarantine_path=manifest["quarantine_path"],
            status_root=status_root,
            allow_custom_root=allow_custom_root,
            signing_key=signing_key,
        )

        receipt = execute_retirement(
            safe_root,
            items,
            action=action,
            quarantine_dir=safe_quarantine,
            approval_record=approval_record,
            approval_record_path=approval_record_path,
            dry_run_manifest_path=str(dry_run_manifest_path),
            bound_inventory_sha256=inventory_digest,
        )
        manifest["execution_receipt"] = receipt

    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(DEFAULT_LIFECYCLE_ROOT),
        help=f"Target legacy lifecycle projection root (default: {DEFAULT_LIFECYCLE_ROOT})",
    )
    parser.add_argument(
        "--action",
        choices=["archive", "quarantine", "delete"],
        default="archive",
        help="Action to perform: 'archive'/'quarantine' (move to quarantine) or 'delete' (default: archive)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Perform a dry-run scan and print the checksummed manifest (default: True)",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="Execute retirement/pruning with governed Human/Ops approval record and dry-run manifest binding",
    )
    parser.add_argument(
        "--dry-run-manifest",
        type=Path,
        default=None,
        help="Required path to approved dry-run manifest for --execute mode",
    )
    parser.add_argument(
        "--approval-record",
        type=Path,
        default=None,
        help="Required path to approved Human/Ops approval record JSON file in the governed status root for --execute mode",
    )
    parser.add_argument(
        "--human-ops-evidence",
        type=Path,
        default=None,
        help="Alias for --approval-record",
    )
    parser.add_argument(
        "--approval-token",
        default="",
        help="Legacy token flag (cannot bypass --approval-record)",
    )
    parser.add_argument(
        "--approver",
        default="",
        help="Legacy approver flag (cannot bypass --approval-record)",
    )
    parser.add_argument(
        "--quarantine-dir",
        type=Path,
        default=None,
        help="Custom quarantine directory for archived files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write inventory manifest/receipt JSON to file",
    )

    args = parser.parse_args(argv)
    execute_mode = bool(args.execute)
    approval_record_file = args.approval_record or args.human_ops_evidence

    # Custom roots and caller-chosen keys are strictly prohibited in the CLI
    allow_custom_root = False

    try:
        manifest = run_retirement(
            root_path=args.root,
            action=args.action,
            execute=execute_mode,
            approval_record_path=approval_record_file,
            dry_run_manifest_path=args.dry_run_manifest,
            quarantine_dir=args.quarantine_dir,
            allow_custom_root=allow_custom_root,
            approval_token=args.approval_token,
            approver=args.approver,
            signing_key=None,
        )
    except RetirementValidationError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(manifest, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
        print(f"Wrote retirement manifest to {args.output}")
    else:
        print(rendered)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

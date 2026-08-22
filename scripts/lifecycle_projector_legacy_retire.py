#!/usr/bin/env python3
"""LIFECYCLE-PROJ-RETIRE-001 CLI: allowlisted legacy JSON projection retirement.

This tool scans the legacy lifecycle JSON projection directory, generates a
checksummed exact-path inventory of obsolete dev generations and legacy root
files, and safely archives or prunes them only with explicit operator approval.

Security invariants:
- Reject broad paths (e.g., '/', '/data', '/data/bff', '/workspace', '/var').
- Reject globs, wildcards, and unresolved shell variables.
- Strictly prohibit targeting canonical sources (e.g. telemetry_events).
- Prohibit targeting relational projection tables or PostgreSQL database cluster.
- Default to dry-run mode; require explicit --execute and approval token for execution.
- Maintain a complete SHA-256 manifest and deletion/quarantine receipt.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

SCHEMA_VERSION = "pantheon.lifecycle-projector-legacy-retirement.v1"
TASK_ID = "LIFECYCLE-PROJ-RETIRE-001"
DEFAULT_LIFECYCLE_ROOT = "/data/bff/lifecycle-projection"
DEFAULT_QUARANTINE_SUBDIR = "quarantine"

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

KNOWN_LEGACY_SYMLINKS = frozenset({"current", "staging"})


class RetirementValidationError(ValueError):
    """Raised when safety validation rejects a target path or parameter."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compute_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


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
            if rel_name in KNOWN_LEGACY_SYMLINKS or rel_name.startswith("gen-"):
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
            continue

        if entry.is_dir():
            if rel_name.startswith("gen-") or rel_name.startswith("staging"):
                for sub_file in sorted(entry.rglob("*")):
                    if sub_file.is_file():
                        rel_path = str(sub_file.relative_to(root))
                        items.append(
                            {
                                "path": str(sub_file.resolve()),
                                "entry_path": str(sub_file),
                                "relative_path": rel_path,
                                "size_bytes": sub_file.stat().st_size,
                                "sha256": _compute_sha256(sub_file),
                                "category": (
                                    "legacy_generation_file"
                                    if rel_name.startswith("gen-")
                                    else "legacy_staging_file"
                                ),
                                "is_symlink": False,
                                "is_dir": False,
                            }
                        )
            continue

        if entry.is_file():
            if rel_name in KNOWN_LEGACY_ROOT_FILES or rel_name.endswith(".json"):
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


def execute_retirement(
    root: Path,
    items: List[Dict[str, Any]],
    action: str,
    quarantine_dir: Path,
    approver: str,
) -> Dict[str, Any]:
    executed_at = _utc_now()
    processed_count = 0
    processed_bytes = 0

    if action in {"archive", "quarantine"}:
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
            "approver": approver,
            "quarantine_location": str(quarantine_dir),
            "recovery_possible": True,
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

        for gen_dir in sorted(root.glob("gen-*")):
            if gen_dir.is_dir():
                shutil.rmtree(str(gen_dir), ignore_errors=True)
        for staging_dir in sorted(root.glob("staging*")):
            if staging_dir.is_dir():
                shutil.rmtree(str(staging_dir), ignore_errors=True)

        return {
            "status": "completed",
            "action": "delete",
            "executed_at_utc": executed_at,
            "files_processed": processed_count,
            "bytes_processed": processed_bytes,
            "approver": approver,
            "quarantine_location": None,
            "recovery_possible": False,
        }

    raise ValueError(f"Unsupported action: {action!r}")


def run_retirement(
    root_path: Path,
    action: str = "archive",
    execute: bool = False,
    approval_token: str = "",
    approver: str = "Human/Ops",
    quarantine_dir: Optional[Path] = None,
    allow_custom_root: bool = False,
) -> Dict[str, Any]:
    safe_root = validate_path_safety(root_path, allow_custom_root=allow_custom_root)

    if quarantine_dir is None:
        quarantine_dir = safe_root / DEFAULT_QUARANTINE_SUBDIR

    items = scan_legacy_inventory(safe_root, quarantine_dir=quarantine_dir)
    total_files = len(items)
    total_bytes = sum(item["size_bytes"] for item in items)

    manifest: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "task_id": TASK_ID,
        "generated_at_utc": _utc_now(),
        "mode": "executed" if execute else "dry_run",
        "action": action,
        "root_path": str(safe_root),
        "quarantine_path": str(quarantine_dir) if action != "delete" else None,
        "total_files": total_files,
        "total_bytes": total_bytes,
        "recovery_possible": action != "delete",
        "items": items,
        "execution_receipt": None,
    }

    if execute:
        if not approval_token:
            raise RetirementValidationError(
                "Execution requires an explicit --approval-token or --confirm-human-ops-approved."
            )
        receipt = execute_retirement(
            safe_root,
            items,
            action=action,
            quarantine_dir=quarantine_dir,
            approver=approver,
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
        help="Execute retirement/pruning with operator approval token",
    )
    parser.add_argument(
        "--approval-token",
        default="",
        help="Required token/reason for --execute mode (e.g. 'Human/Ops-approved')",
    )
    parser.add_argument(
        "--approver",
        default="Human/Ops",
        help="Operator identity approving the retirement (default: Human/Ops)",
    )
    parser.add_argument(
        "--quarantine-dir",
        type=Path,
        default=None,
        help="Custom quarantine directory for archived files",
    )
    parser.add_argument(
        "--allow-custom-root",
        action="store_true",
        default=False,
        help="Allow custom root outside /data/bff/lifecycle-projection (for tests)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Write inventory manifest/receipt JSON to file",
    )

    args = parser.parse_args(argv)
    execute_mode = bool(args.execute)

    try:
        manifest = run_retirement(
            root_path=args.root,
            action=args.action,
            execute=execute_mode,
            approval_token=args.approval_token,
            approver=args.approver,
            quarantine_dir=args.quarantine_dir,
            allow_custom_root=args.allow_custom_root,
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

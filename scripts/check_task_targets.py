#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
WRONG_REPO_PATTERNS = ("lean-platform", "ajoe734/lean-platform")
DEFAULT_REQUIRED_ADR_OVERRIDE = "ADR-EXEC-001-revision"
DEFAULT_SCAN_PATTERNS = (
    "ai-status.json",
    ".orchestrator/task-briefs/*.md",
    ".orchestrator/task-briefs/*.json",
    "docs/02-architecture/consensus/sessions/**/planning-session.json",
    "docs/02-architecture/consensus/sessions/**/execution-materialization.md",
)

TARGET_KEYS = {
    "bridge_remote",
    "bridge_repo",
    "execution_repo",
    "execution_target",
    "remote",
    "repo",
    "repo_url",
    "repository",
    "runtime_repo",
    "target",
    "target_remote",
    "target_repo",
    "target_repository",
}

TARGET_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?P<key>[A-Za-z][A-Za-z0-9_-]*)\s*[:=]\s*(?P<value>.+?)\s*$"
)
TASK_ID_RE = re.compile(r"\bP0-[A-Z0-9][A-Z0-9-]*\b")


def normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_")


def is_target_key(key: str) -> bool:
    return normalize_key(key) in TARGET_KEYS


def contains_wrong_repo(value: Any) -> bool:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    lowered = text.lower()
    return any(pattern in lowered for pattern in WRONG_REPO_PATTERNS)


def is_p0_record(record: dict[str, Any]) -> bool:
    for key in ("id", "task_id"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith("P0-"):
            return True
    phase = record.get("phase")
    if isinstance(phase, str) and "P0" in phase:
        return True
    return False


def has_migration_override(record: dict[str, Any], required_adr_override: str) -> bool:
    migration_only = record.get("migration_only") is True
    adr_override = str(record.get("adr_override") or "").strip()
    return migration_only and adr_override == required_adr_override


def record_task_id(record: dict[str, Any]) -> str | None:
    for key in ("id", "task_id"):
        value = record.get(key)
        if isinstance(value, str) and value.startswith("P0-"):
            return value
    return None


def iter_dict_records(value: Any, path: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from iter_dict_records(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from iter_dict_records(child, f"{path}[{index}]")


def scan_json_file(path: Path, root: Path, required_adr_override: str) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    violations: list[dict[str, Any]] = []
    for json_path, record in iter_dict_records(payload):
        if not is_p0_record(record):
            continue
        if has_migration_override(record, required_adr_override):
            continue
        for key, value in record.items():
            if not is_target_key(str(key)) or not contains_wrong_repo(value):
                continue
            violations.append(
                {
                    "path": str(path.relative_to(root)),
                    "location": json_path,
                    "task_id": record_task_id(record),
                    "key": key,
                    "value": value,
                    "message": (
                        "P0 execution target references lean-platform without "
                        f"migration_only=true and adr_override={required_adr_override}"
                    ),
                }
            )
    return violations


def markdown_window(lines: list[str], index: int, radius: int = 20) -> list[str]:
    start = max(0, index - radius)
    end = min(len(lines), index + radius + 1)
    return lines[start:end]


def text_has_p0_context(path: Path, lines: list[str]) -> bool:
    if "p0" in path.as_posix().lower():
        return True
    return any(TASK_ID_RE.search(line) or "Pantheon P0" in line for line in lines)


def text_has_migration_override(lines: list[str], required_adr_override: str) -> bool:
    text = "\n".join(lines)
    migration_only = re.search(r"\bmigration_only\s*[:=]\s*true\b", text, re.IGNORECASE) is not None
    adr_override = re.search(
        r"\badr_override\s*[:=]\s*['\"]?" + re.escape(required_adr_override) + r"['\"]?\b",
        text,
        re.IGNORECASE,
    ) is not None
    return migration_only and adr_override


def find_task_id(lines: list[str]) -> str | None:
    for line in lines:
        match = TASK_ID_RE.search(line)
        if match:
            return match.group(0)
    return None


def scan_text_file(path: Path, root: Path, required_adr_override: str) -> list[dict[str, Any]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    violations: list[dict[str, Any]] = []
    for index, line in enumerate(lines, start=1):
        match = TARGET_LINE_RE.match(line)
        if match is None:
            continue
        key = match.group("key")
        value = match.group("value")
        if not is_target_key(key) or not contains_wrong_repo(value):
            continue
        window = markdown_window(lines, index - 1)
        if not text_has_p0_context(path, window):
            continue
        if text_has_migration_override(window, required_adr_override):
            continue
        violations.append(
            {
                "path": str(path.relative_to(root)),
                "line": index,
                "task_id": find_task_id(window),
                "key": key,
                "value": value.strip(),
                "message": (
                    "P0 execution target references lean-platform without "
                    f"migration_only=true and adr_override={required_adr_override}"
                ),
            }
        )
    return violations


def resolve_scan_paths(root: Path, patterns: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        matches = sorted(root.glob(pattern)) if any(char in pattern for char in "*?[") else [root / pattern]
        for match in matches:
            if not match.is_file():
                continue
            if "__pycache__" in match.parts or ".git" in match.parts or "backups" in match.parts:
                continue
            resolved = match.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            paths.append(match)
    return paths


def scan_paths(
    root: Path,
    paths: list[Path],
    required_adr_override: str,
    *,
    include_scanned_files: bool = False,
) -> dict[str, Any]:
    violations: list[dict[str, Any]] = []
    scanned: list[str] = []
    for path in paths:
        scanned.append(str(path.relative_to(root)))
        try:
            if path.suffix == ".json":
                violations.extend(scan_json_file(path, root, required_adr_override))
            else:
                violations.extend(scan_text_file(path, root, required_adr_override))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            violations.append(
                {
                    "path": str(path.relative_to(root)),
                    "message": f"Unable to scan task target file: {exc}",
                }
            )
    report = {
        "ok": not violations,
        "wrong_repo_patterns": list(WRONG_REPO_PATTERNS),
        "required_override": {
            "migration_only": True,
            "adr_override": required_adr_override,
        },
        "scanned_file_count": len(scanned),
        "violations": violations,
    }
    if include_scanned_files:
        report["scanned_files"] = scanned
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fail P0 execution task targets that point at lean-platform.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--path",
        action="append",
        dest="paths",
        help="File or glob to scan. May be passed more than once. Defaults to current P0 task state and task packets.",
    )
    parser.add_argument("--required-adr-override", default=DEFAULT_REQUIRED_ADR_OVERRIDE)
    parser.add_argument(
        "--list-scanned-files",
        action="store_true",
        help="Include every scanned file path in the JSON report.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    root = args.root.resolve()
    patterns = args.paths or list(DEFAULT_SCAN_PATTERNS)
    paths = resolve_scan_paths(root, patterns)
    report = scan_paths(
        root,
        paths,
        args.required_adr_override,
        include_scanned_files=args.list_scanned_files,
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

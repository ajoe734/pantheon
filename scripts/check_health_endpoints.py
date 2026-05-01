#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
STANDARD_ENDPOINTS = ("/healthz", "/livez", "/readyz", "/metrics")
DEFAULT_DEV_COMPOSE_FILE = "docker-compose.yml"
DEFAULT_STAGED_COMPOSE_FILES = ("docker-compose.control.yml", "docker-compose.exec.yml")
DEFAULT_HELPER_FILE = "services/foundation/health.py"
LEGACY_ENDPOINT = "__health__"


def _line_col(text: str, offset: int) -> tuple[int, int]:
    line = text.count("\n", 0, offset) + 1
    last_newline = text.rfind("\n", 0, offset)
    col = offset + 1 if last_newline == -1 else offset - last_newline
    return line, col


def find_legacy_occurrences(path: Path, root: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    occurrences: list[dict[str, Any]] = []
    for match in re.finditer(re.escape(LEGACY_ENDPOINT), text):
        line, col = _line_col(text, match.start())
        line_text = text.splitlines()[line - 1].strip()
        occurrences.append(
            {
                "path": str(path.relative_to(root)),
                "line": line,
                "column": col,
                "endpoint": LEGACY_ENDPOINT,
                "context": line_text,
            }
        )
    return occurrences


def scan_standard_helper(path: Path, root: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    endpoints = {endpoint: endpoint in text for endpoint in STANDARD_ENDPOINTS}
    helper_functions = {
        "register_fastapi_health_routes": "def register_fastapi_health_routes" in text,
        "register_flask_health_routes": "def register_flask_health_routes" in text,
    }
    return {
        "path": str(path.relative_to(root)),
        "exists": path.is_file(),
        "endpoints": endpoints,
        "helper_functions": helper_functions,
        "ok": path.is_file() and all(endpoints.values()) and any(helper_functions.values()),
    }


def scan_dev_compose(path: Path, root: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    return {
        "path": str(path.relative_to(root)),
        "exists": path.is_file(),
        "uses_readyz": "/readyz" in text,
        "legacy_occurrences": find_legacy_occurrences(path, root),
    }


def build_report(
    *,
    root: Path = ROOT,
    mode: str = "warn",
    helper_file: str = DEFAULT_HELPER_FILE,
    dev_compose_file: str = DEFAULT_DEV_COMPOSE_FILE,
    staged_compose_files: tuple[str, ...] = DEFAULT_STAGED_COMPOSE_FILES,
) -> dict[str, Any]:
    root = root.resolve()
    helper = scan_standard_helper(root / helper_file, root)
    dev_compose = scan_dev_compose(root / dev_compose_file, root)

    staged_occurrences: list[dict[str, Any]] = []
    staged_files: list[dict[str, Any]] = []
    for compose_file in staged_compose_files:
        path = root / compose_file
        occurrences = find_legacy_occurrences(path, root)
        staged_files.append(
            {
                "path": str(path.relative_to(root)),
                "exists": path.is_file(),
                "legacy_count": len(occurrences),
            }
        )
        staged_occurrences.extend(occurrences)

    violations: list[str] = []
    if not helper["ok"]:
        violations.append(f"{helper_file} must expose {', '.join(STANDARD_ENDPOINTS)} helper routes")
    if not dev_compose["exists"]:
        violations.append(f"{dev_compose_file} must exist")
    elif not dev_compose["uses_readyz"]:
        violations.append(f"{dev_compose_file} must include /readyz health checks")
    if dev_compose["legacy_occurrences"]:
        violations.append(f"{dev_compose_file} must not use legacy {LEGACY_ENDPOINT}")
    if mode == "fail" and staged_occurrences:
        violations.append(
            f"staged compose files must not use legacy {LEGACY_ENDPOINT}; "
            f"found {len(staged_occurrences)} occurrence(s)"
        )

    return {
        "ok": not violations,
        "mode": mode,
        "standard_endpoints": list(STANDARD_ENDPOINTS),
        "legacy_endpoint": LEGACY_ENDPOINT,
        "standard_helper": helper,
        "dev_compose": dev_compose,
        "staged_compose_files": staged_files,
        "legacy_occurrences": staged_occurrences,
        "legacy_count": len(staged_occurrences),
        "cleanup_mode_can_fail_ci": mode == "fail",
        "violations": violations,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Report or fail legacy Pantheon health endpoint usage.")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--mode",
        choices=("warn", "fail"),
        default="warn",
        help="warn reports staged legacy occurrences; fail exits non-zero until they are cleaned.",
    )
    parser.add_argument("--helper-file", default=DEFAULT_HELPER_FILE)
    parser.add_argument("--dev-compose-file", default=DEFAULT_DEV_COMPOSE_FILE)
    parser.add_argument(
        "--staged-compose-file",
        action="append",
        dest="staged_compose_files",
        help="Control/exec compose file to scan. May be passed more than once.",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    report = build_report(
        root=args.root,
        mode=args.mode,
        helper_file=args.helper_file,
        dev_compose_file=args.dev_compose_file,
        staged_compose_files=tuple(args.staged_compose_files or DEFAULT_STAGED_COMPOSE_FILES),
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

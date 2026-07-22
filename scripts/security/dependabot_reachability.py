#!/usr/bin/env python3
"""Reconcile GitHub Dependabot alerts against the checked-out dependency graph."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


PIN_RE = re.compile(
    r"^\s*([A-Za-z0-9_.-]+)(?:\[[^]]+\])?\s*==\s*([^\s;#]+)",
    re.IGNORECASE,
)


def normalize_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value.strip().lower())


def requirement_pin(path: Path, package_name: str) -> str | None:
    wanted = normalize_package_name(package_name)
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = PIN_RE.match(line)
        if match and normalize_package_name(match.group(1)) == wanted:
            return match.group(2)
    return None


def npm_lock_pin(path: Path, package_name: str) -> str | None:
    payload = json.loads(path.read_text(encoding="utf-8"))
    packages = payload.get("packages", {})
    if isinstance(packages, dict):
        record = packages.get(f"node_modules/{package_name}")
        if isinstance(record, dict) and isinstance(record.get("version"), str):
            return record["version"]
    return None


def manifest_pin(path: Path, ecosystem: str, package_name: str) -> str | None:
    normalized_ecosystem = ecosystem.strip().lower()
    if normalized_ecosystem == "pip":
        return requirement_pin(path, package_name)
    if normalized_ecosystem == "npm" and path.name.endswith("lock.json"):
        return npm_lock_pin(path, package_name)
    return None


def version_is_vulnerable(version: str, vulnerable_range: str) -> bool:
    specifier = SpecifierSet(vulnerable_range.replace(" ", ""))
    return specifier.contains(Version(version), prereleases=True)


@dataclass(frozen=True)
class AlertResult:
    number: int
    severity: str
    ghsa_id: str
    package: str
    manifest_path: str
    current_version: str | None
    vulnerable_range: str
    disposition: str
    violation: bool


def reconcile_alerts(
    alerts: Iterable[dict[str, Any]],
    repo_root: Path,
    fail_severities: set[str],
) -> list[AlertResult]:
    results: list[AlertResult] = []
    for alert in alerts:
        advisory = alert.get("security_advisory") or {}
        vulnerability = alert.get("security_vulnerability") or {}
        dependency = alert.get("dependency") or {}
        package_record = dependency.get("package") or {}
        severity = str(advisory.get("severity", "unknown")).lower()
        manifest_path = str(dependency.get("manifest_path", ""))
        package_name = str(package_record.get("name", ""))
        ecosystem = str(package_record.get("ecosystem", ""))
        vulnerable_range = str(vulnerability.get("vulnerable_version_range", ""))
        path = repo_root / manifest_path

        current_version: str | None = None
        violation = False
        if not manifest_path or not path.is_file():
            disposition = "deleted_manifest"
        else:
            try:
                current_version = manifest_pin(path, ecosystem, package_name)
            except (json.JSONDecodeError, OSError):
                current_version = None
            if not current_version:
                disposition = "unresolved_reachable_version"
                violation = severity in fail_severities
            else:
                try:
                    vulnerable = version_is_vulnerable(current_version, vulnerable_range)
                except (InvalidSpecifier, InvalidVersion):
                    disposition = "unparseable_advisory_range"
                    violation = severity in fail_severities
                else:
                    if severity in fail_severities:
                        disposition = "reachable_vulnerable" if vulnerable else "candidate_fixed"
                        violation = vulnerable
                    else:
                        disposition = (
                            "below_threshold_vulnerable"
                            if vulnerable
                            else "below_threshold_fixed"
                        )

        results.append(
            AlertResult(
                number=int(alert.get("number", 0)),
                severity=severity,
                ghsa_id=str(advisory.get("ghsa_id", "")),
                package=package_name,
                manifest_path=manifest_path,
                current_version=current_version,
                vulnerable_range=vulnerable_range,
                disposition=disposition,
                violation=violation,
            )
        )
    return results


def render_markdown(results: Iterable[AlertResult]) -> str:
    rows = [
        "| Alert | Severity | Package | Manifest | Candidate | Range | Disposition |",
        "|---:|---|---|---|---|---|---|",
    ]
    for result in results:
        rows.append(
            "| #{number} | {severity} | `{package}` | `{manifest}` | `{version}` | "
            "`{range}` | {disposition} |".format(
                number=result.number,
                severity=result.severity,
                package=result.package,
                manifest=result.manifest_path or "(missing)",
                version=result.current_version or "n/a",
                range=result.vulnerable_range or "n/a",
                disposition=result.disposition,
            )
        )
    return "\n".join(rows)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--alerts-json", type=Path, required=True)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--fail-on",
        action="append",
        default=[],
        help="Severity that must have no reachable vulnerable alert (repeatable)",
    )
    parser.add_argument("--json-output", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    payload = json.loads(args.alerts_json.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise SystemExit("Dependabot alerts JSON must be an array")
    fail_severities = {value.lower() for value in args.fail_on} or {"critical", "high"}
    results = reconcile_alerts(payload, args.repo_root.resolve(), fail_severities)
    print(render_markdown(results))
    if args.json_output:
        args.json_output.write_text(
            json.dumps([asdict(item) for item in results], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    violations = [item for item in results if item.violation]
    if violations:
        print(
            f"\n{len(violations)} reachable critical/high dependency alert(s) remain vulnerable.",
            file=sys.stderr,
        )
        return 1
    print("\nNo reachable critical/high alert remains vulnerable in this checkout.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

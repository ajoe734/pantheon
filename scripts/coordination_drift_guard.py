#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FRONT_REPO = ROOT.parent / "front-ai-trading-system"
BACKLOG_PATH = ROOT / "WORKBENCH_DELIVERY_BACKLOG.md"
LOVABLE_MASTER_SA = ROOT / "docs" / "pantheon-handoffs" / "LOVABLE_MASTER_SA.md"

OVERVIEW_RULES = {
    "KW-": {
        "pantheon_packet": ROOT / ".coordination" / "responses" / "PKT-knowledge-workbench-contract-ready.yaml",
        "front_packet_name": "PKT-knowledge-workbench-contract-ready.yaml",
        "front_route_marker": 'path="/knowledge"',
        "stale_markers": (
            "remain blocked on net-new bff",
            "blocked on net-new bff",
            "pending-bff",
            "pending bff",
        ),
    },
    "CW-": {
        "pantheon_packet": ROOT / ".coordination" / "responses" / "PKT-consultation-workbench-contract-ready.yaml",
        "front_packet_name": "PKT-consultation-workbench-contract-ready.yaml",
        "front_route_marker": 'path="/consultation"',
        "stale_markers": (
            "remain blocked on net-new bff",
            "blocked on net-new bff",
            "pending-bff",
            "pending bff",
        ),
    },
}

ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$")
STALE_LINE_MARKERS = ("pending-bff", "pending bff", "missing-bff", "missing bff", "blocked on net-new bff")
STALE_NEGATIONS = ("do not treat", "must not treat", "not treat", "no longer", "already on the live-route side")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check Pantheon/front coordination truth for stale blocked-state drift.")
    parser.add_argument(
        "--front-repo",
        default=str(DEFAULT_FRONT_REPO),
        help="Path to the sibling front-ai-trading-system repository checkout.",
    )
    return parser.parse_args()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def parse_backlog_live_side_modules(path: Path) -> dict[str, str]:
    modules: dict[str, str] = {}
    for raw_line in read_text(path).splitlines():
        match = ROW_RE.match(raw_line.strip())
        if not match:
            continue
        module_label, state_cell, _, _ = match.groups()
        module_code = module_label.split()[0]
        lowered_state = state_cell.strip().lower()
        if any(marker in lowered_state for marker in ("route-live", "contract-live", "loop-complete")):
            modules[module_code] = module_label
    return modules


def packet_contains_stale_marker(path: Path, stale_markers: tuple[str, ...]) -> list[str]:
    text = read_text(path).lower()
    return [marker for marker in stale_markers if marker in text]


def route_exists(app_text: str, route_marker: str) -> bool:
    return route_marker in app_text


def handoff_exists(module_code: str) -> bool:
    pattern = f"{module_code}-*/FRONTEND_CHANGE_SPEC.md"
    return any((ROOT / "docs" / "pantheon-handoffs").glob(pattern))


def stale_module_lines(path: Path, module_code: str) -> list[str]:
    findings: list[str] = []
    for raw_line in read_text(path).splitlines():
        lowered = raw_line.lower()
        if module_code.lower() not in lowered:
            continue
        if not any(marker in lowered for marker in STALE_LINE_MARKERS):
            continue
        if any(negation in lowered for negation in STALE_NEGATIONS):
            continue
        findings.append(raw_line.strip())
    return findings


def packet_family_findings(module_code: str) -> list[str]:
    findings: list[str] = []
    for packet_family in (ROOT / "docs" / "pantheon-handoffs").glob("**/PACKET_FAMILY.md"):
        for raw_line in stale_module_lines(packet_family, module_code):
            findings.append(f"{packet_family.relative_to(ROOT)}: {raw_line}")
    return findings


def display_path(path: Path) -> str:
    for base in (ROOT, DEFAULT_FRONT_REPO):
        try:
            return str(path.relative_to(base))
        except ValueError:
            continue
    return str(path)


def main() -> int:
    args = parse_args()
    front_repo = Path(args.front_repo).resolve()
    front_app = front_repo / "src" / "App.tsx"
    front_responses = front_repo / ".coordination" / "responses"

    errors: list[str] = []
    live_side_modules = parse_backlog_live_side_modules(BACKLOG_PATH)
    front_app_text = read_text(front_app)

    for prefix, rule in OVERVIEW_RULES.items():
        matching_modules = [code for code in sorted(live_side_modules) if code.startswith(prefix)]
        if not matching_modules:
            continue

        pantheon_packet = Path(rule["pantheon_packet"])
        front_packet = front_responses / str(rule["front_packet_name"])
        stale_markers = tuple(rule["stale_markers"])

        for packet_path in (pantheon_packet, front_packet):
            hits = packet_contains_stale_marker(packet_path, stale_markers)
            if hits:
                errors.append(
                    f"{display_path(packet_path)}: "
                    f"overview still contains stale blocked/live drift markers {hits} while {prefix} backlog modules are live-side."
                )

        if route_exists(front_app_text, str(rule["front_route_marker"])):
            hits = packet_contains_stale_marker(front_packet, stale_markers)
            if hits:
                errors.append(
                    f"{front_packet}: front route marker '{rule['front_route_marker']}' exists in src/App.tsx, "
                    f"but the mirrored overview packet still claims blocked state via {hits}."
                )

    sa_text_path = LOVABLE_MASTER_SA
    for module_code in sorted(live_side_modules):
        if not handoff_exists(module_code):
            continue
        for stale_line in stale_module_lines(sa_text_path, module_code):
            errors.append(f"{sa_text_path.relative_to(ROOT)}: stale live-side blocker wording for {module_code}: {stale_line}")
        errors.extend(packet_family_findings(module_code))

    if errors:
        print("Coordination drift guard failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print("Coordination drift guard passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import load_config
from provider_permissions import provider_capabilities, write_provider_capabilities


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect orchestrator, provider, and approval capabilities in the current WSL workspace.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    parser.add_argument("--no-write", action="store_true", help="Do not refresh .orchestrator/provider_capabilities.json.")
    return parser.parse_args()


def human_report(report: dict) -> str:
    lines: list[str] = []
    workspace = report["workspace"]
    code_cli = workspace["code_cli"]
    custom_agents = workspace["custom_agents"]
    lines.append("Workspace")
    lines.append(f"- Root: {workspace['root']}")
    lines.append(
        f"- code CLI: {'available' if code_cli['available'] else 'missing'}"
        + (f" ({code_cli['version']})" if code_cli.get("version") else "")
    )
    lines.append(f"- code chat: {'available' if code_cli['code_chat_available'] else 'unavailable'}")
    lines.append(f"- custom agents: {custom_agents['verified']} ({custom_agents['workspace_path']})")
    lines.append("- extensions:")
    for item in workspace["extensions"]:
        lines.append(f"  - {item['id']}@{item['version']}")

    lines.append("")
    lines.append("Providers")
    for name, provider in report["providers"].items():
        lines.append(
            f"- {name}: installed={provider['installed']}, verified={provider['verified']}, "
            f"host={provider['host_layer']}, delivery={provider['delivery_mode']}, approval={provider['approval_mode']}, "
            f"local_cli={provider.get('local_cli_worker_supported')}, vscode_link={provider.get('vscode_link_supported')}, "
            f"cloud={provider.get('cloud_agent_supported')}, auto={provider.get('supports_auto_approve')}, "
            f"defer_resume={provider.get('supports_defer_resume')}, auth={provider.get('auth_ready')}, applied={provider['applied']}"
        )
        if provider.get("selected_model") or provider.get("supported_models"):
            lines.append(
                f"  - models: selected={provider.get('selected_model')}, supported={provider.get('supported_models')}"
            )
        for note in provider.get("notes", []):
            lines.append(f"  - note: {note}")
        for key, value in provider.get("paths", {}).items():
            if value:
                lines.append(f"  - {key}: {value}")
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    report = provider_capabilities(config)
    if not args.no_write:
        write_provider_capabilities(config, report=report)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(human_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

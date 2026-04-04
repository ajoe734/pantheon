#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

from common import load_config, write_activity_log
from provider_permissions import (
    CLAUDE_LOCAL_SETTINGS_PATH,
    GEMINI_SETTINGS_PATH,
    WORKSPACE_SETTINGS_PATH,
    apply_claude_local_settings,
    apply_gemini_settings,
    apply_workspace_settings,
    create_backup,
    latest_backup_dir,
    load_backup_manifest,
    provider_capabilities,
    restore_backup,
    desired_sync_state,
    write_provider_capabilities,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check, apply, or roll back provider permission settings.")
    parser.add_argument("--config", default=".orchestrator/config.json")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--rollback", action="store_true")
    return parser.parse_args()


def check_mode(config: dict) -> int:
    desired = desired_sync_state(config)
    report = provider_capabilities(config)
    output = {
        "desired": desired,
        "current_report": report,
        "targets": {
            "workspace_settings": str(WORKSPACE_SETTINGS_PATH),
            "claude_local_settings": str(CLAUDE_LOCAL_SETTINGS_PATH),
            "gemini_settings": str(GEMINI_SETTINGS_PATH),
        },
    }
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


def apply_mode(config: dict) -> int:
    backup_dir = create_backup(config)
    workspace = apply_workspace_settings(config)
    claude = apply_claude_local_settings(config)
    gemini = apply_gemini_settings(config)
    report = provider_capabilities(config)
    write_provider_capabilities(config, report=report)
    write_activity_log(
        config,
        {
            "type": "permission_sync",
            "provider": "workspace",
            "message": f"Applied provider permission sync. Backup: {backup_dir}",
            "backup_dir": str(backup_dir),
        },
    )
    print(
        json.dumps(
            {
                "backup_dir": str(backup_dir),
                "workspace_settings": workspace,
                "claude_local_settings": claude,
                "gemini_settings": gemini,
                "report_path": str(Path(config["paths"]["provider_capabilities"])),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def rollback_mode(config: dict) -> int:
    backup_dir = latest_backup_dir()
    if not backup_dir:
        print("No backup directory found.", file=sys.stderr)
        return 1
    manifest = load_backup_manifest(backup_dir)
    restored = restore_backup(backup_dir)
    report = provider_capabilities(config)
    write_provider_capabilities(config, report=report)
    write_activity_log(
        config,
        {
            "type": "permission_rollback",
            "provider": "workspace",
            "message": f"Rolled back provider permission sync from {backup_dir}",
            "backup_dir": str(backup_dir),
        },
    )
    print(json.dumps({"backup_dir": str(backup_dir), "manifest": manifest, "restored": restored}, indent=2, ensure_ascii=False))
    return 0


def main() -> int:
    args = parse_args()
    config = load_config(args.config)
    if args.check:
        return check_mode(config)
    if args.apply:
        return apply_mode(config)
    return rollback_mode(config)


if __name__ == "__main__":
    raise SystemExit(main())

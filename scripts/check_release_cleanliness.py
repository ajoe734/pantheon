#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

THIS_DIR = Path(__file__).resolve().parent
if str(THIS_DIR) not in sys.path:
    sys.path.insert(0, str(THIS_DIR))

import release_hardening


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fail fast when the Pantheon repo contains dirty or tracked generated release artifacts.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the cleanup report as JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = release_hardening.build_release_cleanup_report()
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        if report["ok"]:
            print("Release cleanup check passed.")
        else:
            print(release_hardening.format_release_cleanup_report(report))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

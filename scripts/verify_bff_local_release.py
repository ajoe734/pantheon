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
        description="Create or reuse the local BFF venv and run Pantheon BFF verification.",
    )
    parser.add_argument(
        "--venv-dir",
        default=str(release_hardening.DEFAULT_VENV_DIR),
        help="Path to the local virtualenv to use for verification.",
    )
    parser.add_argument(
        "--reinstall",
        action="store_true",
        help="Force pip reinstall even when the requirements hash matches.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the verification result as JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = release_hardening.run_bff_verification(
        venv_dir=Path(args.venv_dir),
        reinstall=args.reinstall,
        verbose=not args.json,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"BFF verification passed at {result['verified_at']}")
        print(f"Venv: {result['venv']['venv_dir']}")
        for step in result["steps"]:
            print(f"  PASS {step['name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

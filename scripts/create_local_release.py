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
        description="Create a local Pantheon release tarball after verification and cleanup checks.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(release_hardening.ROOT / "dist"),
        help="Directory where release artifacts should be written.",
    )
    parser.add_argument(
        "--venv-dir",
        default=str(release_hardening.DEFAULT_VENV_DIR),
        help="Path to the local virtualenv used for BFF verification.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the packaging result as JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = release_hardening.create_release_artifacts(
        output_dir=Path(args.output_dir),
        venv_dir=Path(args.venv_dir),
        verbose=not args.json,
    )
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"Release created: {result['release_id']}")
        print(f"  Tarball: {result['tarball_path']}")
        print(f"  Manifest: {result['manifest_path']}")
        print(f"  Verification: {result['verification_path']}")
        print(f"  SHA256: {result['tarball_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

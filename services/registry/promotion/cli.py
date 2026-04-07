#!/usr/bin/env python3
"""
CLI wrapper for Pantheon Promotion Gate.

Example:
    python3 services/registry/promotion/cli.py \
        --entry-file /tmp/registry-entry.json \
        --to paper \
        --approver "risk-committee"
"""
from __future__ import annotations

import argparse
import json
import sys

try:
    from .gate import PromotionError, PromotionGate, PromotionState
except ImportError:  # pragma: no cover - direct script execution fallback
    from gate import PromotionError, PromotionGate, PromotionState


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pantheon Promotion Gate CLI")
    parser.add_argument("--entry-file", required=True, help="Path to the registry entry JSON file")
    parser.add_argument("--to", required=True, choices=[s.value for s in PromotionState], help="Target state")
    parser.add_argument("--approver", help="Name of the person or agent approving the promotion")
    parser.add_argument("--inplace", action="store_true", help="Overwrite the entry file with updated state")

    args = parser.parse_args(argv)

    try:
        with open(args.entry_file, "r", encoding="utf-8") as f:
            entry = json.load(f)

        gate = PromotionGate()
        target_state = PromotionState(args.to)

        updated_entry = gate.promote(entry, target_state, approver=args.approver)

        if args.inplace:
            with open(args.entry_file, "w", encoding="utf-8") as f:
                json.dump(updated_entry, f, indent=2)
                f.write("\n")
            print(f"Successfully promoted {entry['strategy_id']} to {args.to} (Updated {args.entry_file})")
        else:
            print(json.dumps(updated_entry, indent=2))
        return 0

    except FileNotFoundError:
        print(f"Error: Entry file {args.entry_file} not found.", file=sys.stderr)
        return 1
    except PromotionError as e:
        print(f"Promotion Rejected: {str(e)}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Unexpected Error: {str(e)}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())

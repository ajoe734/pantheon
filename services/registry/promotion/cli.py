#!/usr/bin/env python3
"""
CLI wrapper for Pantheon Promotion Gate.
Usage: python3 cli.py --strategy alpha-1 --version 1.0.0 --to paper --approver "admin"
"""
import argparse
import json
import sys
from gate import PromotionGate, PromotionState, PromotionError

def main():
    parser = argparse.ArgumentParser(description="Pantheon Promotion Gate CLI")
    parser.add_argument("--entry-file", required=True, help="Path to the registry entry JSON file")
    parser.add_argument("--to", required=True, choices=[s.value for s in PromotionState], help="Target state")
    parser.add_argument("--approver", help="Name of the person or agent approving the promotion")
    parser.add_argument("--inplace", action="store_true", help="Overwrite the entry file with updated state")

    args = parser.parse_args()

    try:
        with open(args.entry_file, 'r') as f:
            entry = json.load(f)

        gate = PromotionGate()
        target_state = PromotionState(args.to)
        
        updated_entry = gate.promote(entry, target_state, approver=args.approver)

        if args.inplace:
            with open(args.entry_file, 'w') as f:
                json.dump(updated_entry, f, indent=2)
            print(f"Successfully promoted {entry['strategy_id']} to {args.to} (Updated {args.entry_file})")
        else:
            print(json.dumps(updated_entry, indent=2))

    except FileNotFoundError:
        print(f"Error: Entry file {args.entry_file} not found.", file=sys.stderr)
        sys.exit(1)
    except PromotionError as e:
        print(f"Promotion Rejected: {str(e)}", file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print(f"Unexpected Error: {str(e)}", file=sys.stderr)
        sys.exit(3)

if __name__ == "__main__":
    main()
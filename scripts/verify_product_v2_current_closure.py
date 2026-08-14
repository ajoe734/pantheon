#!/usr/bin/env python3
"""Legacy Evidence Directory Audit Tool

This script audits existing evidence directories for task completion artifacts.
NOTE: Per L12-MFC-R4 design, this tool strictly audits evidence files on disk
and CANNOT claim product execution closure or substitute for the live 12-loop verifier
(scripts/verify_l12_minimum_functional_closure.py).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def audit_evidence(evidence_dir: Path) -> dict:
    if not evidence_dir.exists():
        return {
            "audited": False,
            "error": f"Evidence directory '{evidence_dir}' does not exist",
            "can_claim_product_closure": False,
        }

    files = list(evidence_dir.glob("*"))
    return {
        "audited": True,
        "evidence_directory": str(evidence_dir),
        "file_count": len(files),
        "files": [f.name for f in files],
        "can_claim_product_closure": False,
        "note": "Evidence directory audit only. Live product execution closure requires scripts/verify_l12_minimum_functional_closure.py.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit evidence directory")
    parser.add_argument(
        "--evidence-dir",
        default="docs/04/pantheon_twelve_loop_code_gap_2026-08-13/evidence",
        help="Evidence directory path",
    )
    args = parser.parse_args()

    result = audit_evidence(Path(args.evidence_dir))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

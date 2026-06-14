#!/usr/bin/env python3
"""audit_dependency_hygiene.py — dependency pin hygiene across service requirements.

Reports:
  1. packages pinned to CONFLICTING exact versions across files (note: separate
     service images are isolated, so cross-service diffs are usually benign);
  2. UNPINNED dependencies (no `==`), which make builds non-reproducible and are
     the channel through which CVEs (see V2) drift in uncontrolled.

Usage: python3 scripts/audit_dependency_hygiene.py [--max-unpinned-ratio 1.0]
Exit 1 if the unpinned ratio exceeds the threshold (default 1.0 = report-only).
"""
from __future__ import annotations
import argparse, glob, re, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def scan():
    files = sorted(set(glob.glob(str(ROOT / "services/**/requirements*.txt"), recursive=True)
                       + glob.glob(str(ROOT / "requirements*.txt"))))
    pins = defaultdict(set)
    unpinned = defaultdict(list)
    total = pinned = 0
    for f in files:
        for raw in open(f, encoding="utf-8", errors="ignore"):
            line = raw.split("#")[0].strip()
            if not line or line.startswith("-") or line.startswith("git+") or "://" in line:
                continue
            total += 1
            m = re.match(r"^([A-Za-z0-9_.\-]+)\s*==\s*([0-9][A-Za-z0-9_.+\-]*)", line)
            if m:
                pinned += 1
                pins[m.group(1).lower()].add((m.group(2), f))
            else:
                unpinned[f].append(line)
    conflicts = {p: v for p, v in pins.items() if len({ver for ver, _ in v}) > 1}
    return files, total, pinned, unpinned, conflicts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-unpinned-ratio", type=float, default=1.0)
    a = ap.parse_args()
    files, total, pinned, unpinned, conflicts = scan()
    n_unpinned = total - pinned
    ratio = (n_unpinned / total) if total else 0.0
    print(f"files={len(files)} dep_lines={total} pinned={pinned} unpinned={n_unpinned} ({ratio:.0%})")
    print(f"conflicting cross-file exact pins: {len(conflicts)}")
    for p, v in sorted(conflicts.items()):
        print(f"  {p}: {sorted({ver for ver,_ in v})} (note: separate images may be isolated)")
    print(f"files with unpinned deps: {len(unpinned)}")
    for f, deps in sorted(unpinned.items(), key=lambda x: -len(x[1]))[:10]:
        print(f"  {len(deps):2d}  {Path(f).relative_to(ROOT)}")
    if ratio > a.max_unpinned_ratio:
        print(f"FAIL: unpinned ratio {ratio:.0%} > {a.max_unpinned_ratio:.0%}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

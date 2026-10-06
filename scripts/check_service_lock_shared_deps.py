#!/usr/bin/env python3
"""Check that service locks contain shared module runtime dependencies."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def normalize_name(name: str) -> str:
    return re.split(r"[=<>!~]", name.strip())[0].strip().lower().replace("_", "-")


def check_and_fix_locks(root: Path = ROOT, fix: bool = False) -> tuple[int, list[str]]:
    locks_dir = root / "dependencies" / "locks"
    constraints_path = root / "dependencies" / "constraints-core.txt"
    constraints = (
        {normalize_name(l): l.strip() for l in constraints_path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")}
        if constraints_path.exists()
        else {}
    )

    errors: list[str] = []
    for mod_dir in (root / "services").iterdir():
        req_path = mod_dir / "requirements.txt"
        if not req_path.exists():
            continue
        deps = [l.strip() for l in req_path.read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
        if (mod_dir / "Dockerfile").exists():
            if mod_dir.name != "telemetry":
                continue
            deps = [dep for dep in deps if normalize_name(dep) == "jsonschema"]
        pattern = f"services.{mod_dir.name}"

        importing_locks: dict[str, Path] = {}
        for py in (root / "services").rglob("*.py"):
            if f"services/{mod_dir.name}" in py.as_posix() or "/tests/" in py.as_posix() or py.name.startswith("test_"):
                continue
            try:
                if pattern in py.read_text(encoding="utf-8"):
                    rel = py.relative_to(root / "services")
                    for i in range(len(rel.parts) - 1, 0, -1):
                        cand = locks_dir / f"services-{'-'.join(rel.parts[:i])}.txt"
                        if cand.exists():
                            importing_locks[cand.name] = cand
                            break
            except Exception:
                continue

        for lock_name, lock_path in sorted(importing_locks.items()):
            content = lock_path.read_text(encoding="utf-8")
            existing = {normalize_name(l) for l in content.splitlines() if l.strip() and not l.startswith("#")}
            missing = [d for d in deps if normalize_name(d) not in existing]
            if missing:
                if fix:
                    to_add = [constraints.get(normalize_name(d), d) for d in missing]
                    lock_path.write_text(content.rstrip() + "\n" + "\n".join(to_add) + "\n", encoding="utf-8")
                else:
                    errors.extend(f"{lock_name} missing shared dependency '{d}' from services/{mod_dir.name}" for d in missing)

    return len(errors), errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--fix", action="store_true")
    args = parser.parse_args()
    count, errs = check_and_fix_locks(args.root, fix=args.fix)
    for err in errs:
        print(f"ERROR: {err}", file=sys.stderr)
    return 1 if count else 0


if __name__ == "__main__":
    raise SystemExit(main())

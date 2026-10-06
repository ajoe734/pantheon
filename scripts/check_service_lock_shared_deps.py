#!/usr/bin/env python3
"""Check that service locks contain shared module runtime dependencies."""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Gaps the AST check found in existing locks: (lock, shared module imported without all of its requirements). Each importing image
# still passes the in-image import smoke (the imports are lazy or reach pure-python submodules). Remove an entry once the lock is
# recompiled with the module's requirements; any gap not listed fails.
KNOWN_GAPS = {
    ("services-broker.txt", "governance"), ("services-capital.txt", "governance"), ("services-memory.txt", "governance"),
    ("services-evolution.txt", "governance"), ("services-registry.txt", "governance"), ("services-deployment.txt", "governance"),
    ("services-runtime-manager.txt", "governance"), ("services-deployment.txt", "registry"), ("services-runtime-manager.txt", "registry"),
    ("services-execution-lean_runtime.txt", "registry"), ("services-governance.txt", "broker"), ("services-lineage-read.txt", "telemetry"),
    ("services-control-plane-persona.txt", "source_ingestion"), ("services-research.txt", "source_ingestion"),
    ("services-learning-trl.txt", "evaluation"), ("services-learning-trl.txt", "feedback"), ("services-optimizer-svc.txt", "capital"),
    ("services-openclaw-gateway-adapter.txt", "consultation"), ("services-training-session.txt", "consultation"),
    ("services-promotion.txt", "deployment"),
}


def normalize_name(name: str) -> str:
    return re.split(r"[\[=<>!~;\s]", name.strip())[0].lower().replace("_", "-")


def requirement_lines(path: Path) -> list[str]:
    stripped = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
    return [line for line in stripped if line and not line.startswith("#")]


def imported_modules(source: str) -> set[str]:
    """Dotted module names a file imports, by import statement or a literal import_module/__import__ call."""
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            modules.add(node.module)
            modules.update(f"{node.module}.{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in ("import_module", "__import__"):
                modules.add(node.args[0].value)
    return modules


def lock_for(rel: Path, locks_dir: Path) -> Path | None:
    """Nearest service lock for a file under services/: services-<dir>-<subdir>.txt, longest directory prefix first."""
    for depth in range(len(rel.parts) - 1, 0, -1):
        candidate = locks_dir / f"services-{'-'.join(rel.parts[:depth])}.txt"
        if candidate.exists():
            return candidate
    return None


def check_and_fix_locks(root: Path = ROOT, fix: bool = False) -> tuple[int, list[str]]:
    locks_dir = root / "dependencies" / "locks"
    constraints_path = root / "dependencies" / "constraints-core.txt"
    constraints = {normalize_name(line): line for line in requirement_lines(constraints_path)} if constraints_path.exists() else {}

    # Every module that declares requirements is shared when another service imports it, even if it also ships a Dockerfile.
    shared = {
        mod_dir.name: requirement_lines(mod_dir / "requirements.txt")
        for mod_dir in (root / "services").iterdir()
        if (mod_dir / "requirements.txt").exists()
    }
    importers: dict[str, dict[str, Path]] = {name: {} for name in shared}
    for py in (root / "services").rglob("*.py"):
        rel = py.relative_to(root / "services")
        if "tests" in rel.parts or py.name.startswith("test_"):
            continue
        lock = lock_for(rel, locks_dir)
        if lock is None:
            continue
        try:
            imported = imported_modules(py.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for name in shared:
            if rel.parts[0] != name and any(m == f"services.{name}" or m.startswith(f"services.{name}.") for m in imported):
                importers[name][lock.name] = lock

    errors: list[str] = []
    for name, deps in shared.items():
        for lock_name, lock_path in sorted(importers[name].items()):
            if (lock_name, name) in KNOWN_GAPS:
                continue
            content = lock_path.read_text(encoding="utf-8")
            existing = {normalize_name(line) for line in requirement_lines(lock_path)}
            missing = [dep for dep in deps if normalize_name(dep) not in existing]
            if missing and fix:
                to_add = [constraints.get(normalize_name(dep), dep) for dep in missing]
                lock_path.write_text(content.rstrip() + "\n" + "\n".join(to_add) + "\n", encoding="utf-8")
            elif missing:
                errors.extend(f"{lock_name} missing shared dependency '{dep}' from services/{name}" for dep in missing)
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

#!/usr/bin/env python3
"""Check that each image's lock covers every third-party package imported at module level by code its entrypoint reaches."""
from __future__ import annotations

import argparse
import ast
import json
import re
import shlex
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Import names whose distribution is named differently; everything else matches the normalized distribution name.
DIST_ALIASES = {"yaml": "pyyaml", "jwt": "pyjwt", "dateutil": "python-dateutil", "dotenv": "python-dotenv", "sklearn": "scikit-learn",
                "psycopg2": "psycopg2-binary", "bs4": "beautifulsoup4", "PIL": "pillow", "cv2": "opencv-python", "multipart": "python-multipart"}


def normalize_name(name: str) -> str:
    return re.split(r"[\[=<>!~;\s]", name.strip())[0].lower().replace("_", "-")


def lock_names(path: Path) -> set[str]:
    """Distribution names a lock pins (hash and comment lines carry none)."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return {normalize_name(line) for line in lines if line and not line.startswith(("#", "-"))}


def is_import_error_handler(node: ast.Try) -> bool:
    names = {n.id for h in node.handlers for n in ast.walk(h.type) if isinstance(n, ast.Name)} if all(h.type for h in node.handlers) else {"*"}
    return bool(names & {"ImportError", "ModuleNotFoundError", "*"})


def module_level_imports(source: str, package: tuple[str, ...] | None) -> set[str]:
    """Dotted names imported when the module body runs: function bodies, TYPE_CHECKING blocks and ImportError fallbacks are skipped."""
    found: set[str] = set()

    def visit(body: list[ast.stmt]) -> None:
        for node in body:
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level and package is None:
                    continue
                base = ".".join(package[: len(package) - node.level + 1]) if node.level and package else ""
                module = ".".join(part for part in (base, node.module) if part)
                found.add(module)
                found.update(f"{module}.{alias.name}" if module else alias.name for alias in node.names)
            elif isinstance(node, ast.Try):
                guarded = is_import_error_handler(node)  # an optional import: neither the attempt nor its fallback needs the package
                visit(([] if guarded else node.body) + node.orelse + node.finalbody)
                if not guarded:
                    visit([stmt for handler in node.handlers for stmt in handler.body])
            elif isinstance(node, ast.If):
                if not (isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING" or isinstance(node.test, ast.Attribute) and node.test.attr == "TYPE_CHECKING"):
                    visit(node.body)
                visit(node.orelse)
            elif isinstance(node, (ast.With, ast.ClassDef, ast.For, ast.While)):
                visit(node.body)

    visit(ast.parse(source).body)
    return found


def spellings(part: str) -> set[str]:
    return {part, part.replace("_", "-"), part.replace("-", "_")}


def find_module(dotted: str, base: Path) -> Path | None:
    """File that ``import dotted`` loads from ``base``: ``a/b.py`` or ``a/b/__init__.py``, directory names spelled with _ or -."""
    here = base
    parts = dotted.split(".")
    for index, part in enumerate(parts):
        last = index == len(parts) - 1
        if last and (here / f"{part}.py").is_file():
            return here / f"{part}.py"
        package = next((here / name for name in sorted(spellings(part)) if (here / name).is_dir()), None)
        if package is None:
            return None
        here = package
    return here / "__init__.py" if (here / "__init__.py").is_file() else None


def entry_files(dockerfile: Path, root: Path) -> list[Path]:
    """Files the Dockerfile ENTRYPOINT/CMD runs (script, ``-m`` module or ``uvicorn module:app``); every top-level script when none is named."""
    text = re.sub(r"\\\n", " ", dockerfile.read_text(encoding="utf-8"))
    argv: list[str] = []
    for name in ("ENTRYPOINT", "CMD"):
        found = re.findall(rf"^{name}\s+(.*)$", text, re.MULTILINE)
        raw = found[-1].strip() if found else ""
        argv += json.loads(raw) if raw.startswith("[") else shlex.split(raw)
    argv = [token for arg in argv for token in (shlex.split(arg) if " " in arg else [arg])]
    app_dir = next((argv[i + 1] for i, arg in enumerate(argv) if arg == "--app-dir"), "")
    targets = [arg.split(":")[0] for arg in argv if re.fullmatch(r"[\w.]+:\w+", arg)] + [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "-m"]
    files = [find_module(t, root / app_dir) for t in targets] + [root / arg.removeprefix("/workspace/") for arg in argv if arg.endswith(".py")]
    files = [f for f in files if f and f.is_file()]
    return files or sorted(p for p in dockerfile.parent.glob("*.py") if not p.name.startswith("test_"))


def inserted_dirs(source: str, root: Path) -> set[Path]:
    """Directories a module puts on sys.path: repo directories whose trailing parts equal the string parts of the inserted path expression."""
    tree = ast.parse(source)
    literals = {t.id: [c.value for c in sorted((c for c in ast.walk(n.value) if isinstance(c, ast.Constant) and isinstance(c.value, str)), key=lambda c: c.col_offset)]
                for n in ast.walk(tree) if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
    found: set[Path] = set()
    for call in (n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in ("insert", "append")):
        names = [n.id for arg in call.args for n in ast.walk(arg) if isinstance(n, ast.Name)]
        parts = [part for name in names for part in literals.get(name, []) if "/" not in part and "." not in part]
        if parts and "path" in ast.unparse(call.func):
            found.update(d for d in (root / "services").rglob(parts[-1]) if d.is_dir() and d.parts[-len(parts):] == tuple(parts))
    return found


def reachable_third_party(entries: list[Path], root: Path) -> dict[str, list[Path]]:
    """{import name: chain of files from an entrypoint to the file importing it} for names that are not stdlib or repo code."""
    third_party: dict[str, list[Path]] = {}
    chains = {entry: [entry] for entry in entries}
    bases = [root, entries[0].parent]  # repo root (services.x), entrypoint directory, plus directories modules add to sys.path
    queue = list(entries)
    while queue:
        path = queue.pop(0)
        try:
            source = path.read_text(encoding="utf-8")
            relative = path.relative_to(root).with_suffix("").parts if path.is_relative_to(root) else None
            names = module_level_imports(source, relative[:-1] if relative else None)
            bases += [d for d in inserted_dirs(source, root) if d not in bases]
        except (SyntaxError, UnicodeDecodeError):
            continue
        for name in sorted(names):
            prefixes = [".".join(name.split(".")[:n]) for n in range(name.count(".") + 1, 0, -1)]
            hit = next(((f, prefix) for prefix in prefixes for base in [path.parent, *bases] if (f := find_module(prefix, base))), None)
            if hit is None:
                top = name.split(".")[0]
                if top not in sys.stdlib_module_names and top != "services":
                    third_party.setdefault(top, chains[path])
                continue
            target = hit[0]
            parents = [d / "__init__.py" for d in target.parents if root in d.parents and (d / "__init__.py").is_file()] if name.startswith("services.") else []
            for dep in [*reversed(parents), target]:
                if dep not in chains:
                    chains[dep] = chains[path] + [dep]
                    queue.append(dep)
    return third_party


def check_locks(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    for dockerfile in sorted((root / "services").rglob("Dockerfile")):
        locks = re.findall(r"dependencies/locks/([\w.-]+\.txt)", dockerfile.read_text(encoding="utf-8"))
        if not locks or not all((root / "dependencies" / "locks" / lock).is_file() for lock in locks):
            continue
        entries = entry_files(dockerfile, root)
        if not entries:
            continue
        pinned = set().union(*(lock_names(root / "dependencies" / "locks" / lock) for lock in locks))
        for name, chain in reachable_third_party(entries, root).items():
            if DIST_ALIASES.get(name, normalize_name(name)) not in pinned:
                route = " -> ".join(str(p.relative_to(root)) for p in chain[-3:])
                errors.append(f"{locks[0]} missing '{name}' imported at module level via {route} (image {dockerfile.parent.relative_to(root)})")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    errors = check_locks(parser.parse_args().root)
    for err in errors:
        print(f"ERROR: {err}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

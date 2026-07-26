#!/usr/bin/env python3
"""Governed provisioning of the Pantheon repository distribution.

OPS-L12-PYTHON-PACKAGING-PROVISION-001.

This is the single entry point that installs the repository as an importable
distribution. Dev CI and the auto-worker test bootstrap both call it, so both
get the *same* distribution from the *same* code path; nothing here reads or
writes live supervisor configuration.

Why a script instead of a documented ``pip install -e .``
---------------------------------------------------------

An editable install writes an absolute mapping from the three exported
top-level names to one checkout. Pantheon runs many checkouts at once — the
supervisor root plus one git worktree per auto-worker task — and they all
share the system interpreter. A bare ``pip install -e .`` into that shared
interpreter silently rebinds ``services`` for every other checkout on the
machine: worker A would then run worker B's code and never know. That is the
duplicate-module-identity hazard this task is required not to introduce.

Two rules close it, and they are why this file exists:

1. The default target is a **checkout-scoped** environment
   (``<checkout>/.venv-pantheon``, created with ``--system-site-packages`` so
   it costs a few hundred kilobytes and inherits every already-installed
   dependency). Installing from one checkout can then never affect another.
   The directory name matches the existing ``.venv-*/`` rule in .gitignore, so
   provisioning a worker worktree does not dirty it — a dirty worktree blocks
   the fleet.
2. Every run **verifies**, from a foreign working directory with no
   ``PYTHONPATH``, that each exported name resolves inside this checkout, and
   fails closed naming the offending path when it does not.

``--mode current`` installs into the running interpreter instead. That is for
disposable, single-checkout environments (a CI job container); it refuses a
non-virtual interpreter unless ``--allow-system-interpreter`` says the
environment really is disposable.

Usage
-----

::

    # auto-worker / local dev: provision the checkout-scoped environment
    python3 scripts/dev/provision_python_distribution.py
    "$(python3 scripts/dev/provision_python_distribution.py --print-python)" -m pytest ...

    # dev CI: the job container is disposable, install into its interpreter
    python3 scripts/dev/provision_python_distribution.py \
        --mode current --allow-system-interpreter

    # verify without installing (fails closed if not provisioned)
    python3 scripts/dev/provision_python_distribution.py --check-only
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

#: The top-level names ``pyproject.toml`` exports. Kept here as an independent
#: statement of the contract: if pyproject ever grows a fourth package, the
#: verification below still only certifies these three, and the packaging
#: contract test flags the divergence.
EXPORTED_TOP_LEVEL = ("integrations", "scripts", "services")

#: Additionally probed because it is the acceptance target of this task: the
#: telemetry package must resolve as a subpackage of the canonical ``services``.
PROBE_SUBPACKAGES = ("services.telemetry",)

DEFAULT_VENV_DIRNAME = ".venv-pantheon"


class ProvisionError(RuntimeError):
    """Fail-closed provisioning error. The message is operator-facing."""


# --------------------------------------------------------------------------
# verification
# --------------------------------------------------------------------------

# Runs in the *target* interpreter. Reports where each exported name resolves
# without importing any Pantheon code beyond the package __init__ files, so a
# broken service module can never be mistaken for a provisioning failure.
_PROBE_SOURCE = """
import importlib.util, json, sys

report = {"executable": sys.executable, "resolved": {}, "errors": {}}
for name in %(names)r:
    try:
        spec = importlib.util.find_spec(name)
    except Exception as exc:  # pragma: no cover - defensive
        report["errors"][name] = f"{type(exc).__name__}: {exc}"
        continue
    if spec is None:
        report["errors"][name] = "not found"
        continue
    origin = spec.origin
    if origin in (None, "namespace"):
        locations = list(spec.submodule_search_locations or [])
        report["resolved"][name] = locations[0] if locations else None
    else:
        report["resolved"][name] = origin
print(json.dumps(report))
"""


def _foreign_env() -> dict[str, str]:
    """A minimal environment with no ``PYTHONPATH`` and no Pantheon variables.

    Verification must prove the *installed distribution* resolves the packages.
    Inheriting the caller's ``PYTHONPATH`` would let an ambient repository-root
    entry certify a provisioning run that actually did nothing.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }


def probe_resolution(python: Path, names: tuple[str, ...]) -> dict:
    """Ask ``python`` where ``names`` resolve, from a foreign cwd, no PYTHONPATH."""
    source = _PROBE_SOURCE % {"names": list(names)}
    with tempfile.TemporaryDirectory() as foreign_cwd:
        proc = subprocess.run(
            [str(python), "-c", source],
            cwd=foreign_cwd,
            env=_foreign_env(),
            capture_output=True,
            text=True,
            timeout=120,
        )
    if proc.returncode != 0:
        raise ProvisionError(
            f"resolution probe failed under {python}:\n{proc.stdout}{proc.stderr}"
        )
    try:
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ProvisionError(
            f"resolution probe produced unreadable output under {python}: "
            f"{exc}\n{proc.stdout}{proc.stderr}"
        ) from None


def verify_checkout_binding(python: Path, checkout: Path) -> dict[str, str]:
    """Fail closed unless every exported name resolves inside ``checkout``.

    Returns the resolved path per name on success.
    """
    names = EXPORTED_TOP_LEVEL + PROBE_SUBPACKAGES
    report = probe_resolution(python, names)

    missing = sorted(report["errors"])
    if missing:
        details = "; ".join(f"{name}: {report['errors'][name]}" for name in missing)
        raise ProvisionError(
            f"{python} cannot import the Pantheon distribution from a foreign "
            f"working directory with no PYTHONPATH ({details}). Run this script "
            f"without --check-only to provision it."
        )

    checkout = checkout.resolve()
    foreign: list[str] = []
    resolved: dict[str, str] = {}
    for name in names:
        location = report["resolved"].get(name)
        if location is None:
            foreign.append(f"{name}: resolved to an empty namespace")
            continue
        resolved[name] = location
        try:
            Path(location).resolve().relative_to(checkout)
        except ValueError:
            foreign.append(f"{name} -> {location}")

    if foreign:
        raise ProvisionError(
            f"{python} has a Pantheon distribution installed, but it points at a "
            f"different checkout than {checkout}:\n  "
            + "\n  ".join(foreign)
            + "\n\nThis is the cross-checkout collision this script exists to "
            "prevent: another checkout installed itself into a shared "
            "interpreter. Provision a checkout-scoped environment instead "
            "(drop --mode current)."
        )
    return resolved


# --------------------------------------------------------------------------
# installation
# --------------------------------------------------------------------------


def ensure_venv(venv_dir: Path) -> Path:
    """Create ``venv_dir`` if absent; return its interpreter."""
    python = venv_dir / "bin" / "python3"
    if not python.exists():
        # --system-site-packages: the environment provisions import *paths*, not
        # dependencies (pyproject declares none). Inheriting site-packages keeps
        # pytest and every service dependency available without a second
        # dependency installation that could drift from requirements.txt.
        builder = venv.EnvBuilder(system_site_packages=True, with_pip=True, symlinks=True)
        builder.create(str(venv_dir))
    if not python.exists():  # pragma: no cover - defensive
        raise ProvisionError(f"virtual environment at {venv_dir} has no bin/python3")
    return python


def _pip_install(python: Path, checkout: Path, *, quiet: bool) -> str:
    """Editable-install ``checkout`` into ``python``. Returns the strategy used."""
    base = [str(python), "-m", "pip", "install", "--no-deps", "--editable", str(checkout)]
    if quiet:
        base.append("--quiet")

    # --no-build-isolation first: the build requirement is setuptools>=68, which
    # every provisioned environment already has, and it keeps provisioning
    # working on hosts with no package-index access.
    attempts = [
        ("no-build-isolation", base[:5] + ["--no-build-isolation"] + base[5:]),
        ("build-isolation", base),
    ]
    failures = []
    for strategy, command in attempts:
        proc = subprocess.run(command, capture_output=True, text=True, timeout=900)
        if proc.returncode == 0:
            return strategy
        failures.append(f"[{strategy}] {' '.join(command)}\n{proc.stdout}{proc.stderr}")
    raise ProvisionError("editable install failed:\n\n" + "\n\n".join(failures))


def resolve_target(args: argparse.Namespace, checkout: Path) -> Path:
    if args.mode == "current":
        if sys.prefix == sys.base_prefix and not args.allow_system_interpreter:
            raise ProvisionError(
                f"--mode current would install into the shared system interpreter "
                f"{sys.executable}, which rebinds the Pantheon packages for every "
                f"other checkout on this host. Use the default checkout-scoped "
                f"mode, or pass --allow-system-interpreter if this environment is "
                f"disposable (a CI job container)."
            )
        return Path(sys.executable)
    return ensure_venv(args.venv_dir or checkout / DEFAULT_VENV_DIRNAME)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--checkout",
        type=Path,
        default=REPO_ROOT,
        help="repository checkout to install (default: the checkout containing this script)",
    )
    parser.add_argument(
        "--mode",
        choices=("venv", "current"),
        default="venv",
        help=(
            "venv (default): install into a checkout-scoped environment; "
            "current: install into the running interpreter (disposable environments only)"
        ),
    )
    parser.add_argument(
        "--venv-dir",
        type=Path,
        default=None,
        help=f"override the checkout-scoped environment (default: <checkout>/{DEFAULT_VENV_DIRNAME})",
    )
    parser.add_argument(
        "--allow-system-interpreter",
        action="store_true",
        help="permit --mode current on a non-virtual interpreter (disposable CI containers)",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="verify an existing provision without installing; exits non-zero if unprovisioned",
    )
    parser.add_argument(
        "--print-python",
        action="store_true",
        help="print only the provisioned interpreter path on stdout (for shell capture)",
    )
    parser.add_argument("--quiet", action="store_true", help="suppress pip output")
    args = parser.parse_args(argv)

    checkout = args.checkout.resolve()
    if not (checkout / "pyproject.toml").is_file():
        raise ProvisionError(f"{checkout} has no pyproject.toml; not a Pantheon checkout")

    # Progress goes to stderr so --print-python owns stdout.
    def note(message: str) -> None:
        if not args.quiet:
            print(message, file=sys.stderr)

    if args.check_only:
        target = (
            Path(sys.executable)
            if args.mode == "current"
            else (args.venv_dir or checkout / DEFAULT_VENV_DIRNAME) / "bin" / "python3"
        )
        if not target.exists():
            raise ProvisionError(f"{target} does not exist; nothing is provisioned")
    else:
        target = resolve_target(args, checkout)
        note(f"→ installing {checkout} into {target}")
        strategy = _pip_install(target, checkout, quiet=args.quiet)
        note(f"  editable install succeeded ({strategy})")

    resolved = verify_checkout_binding(target, checkout)
    note("✓ verified from a foreign cwd with no PYTHONPATH:")
    for name in sorted(resolved):
        note(f"    {name} -> {resolved[name]}")

    if args.print_python:
        print(target)
    else:
        note(f"✓ provisioned interpreter: {target}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ProvisionError as error:
        print(f"error: {error}", file=sys.stderr)
        sys.exit(1)

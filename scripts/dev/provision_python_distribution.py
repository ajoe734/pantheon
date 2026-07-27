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

Why the invoking interpreter is not assumed to carry the dependencies
---------------------------------------------------------------------

The documented bootstrap starts with plain ``python3``, and on an auto-worker
host ``command -v python3`` is the bare system interpreter: it has no ``pytest``
and none of the repository's dependencies. Provisioning installs *import paths*
only, so an earlier revision of this script happily reported success and handed
back an interpreter that could not run the ``-m pytest`` line the guide prints
directly underneath it. The failure surfaced two commands later as a bare
``No module named pytest`` with nothing pointing back at provisioning — the
silent-success hole OPS-L12-PYTHON-PACKAGING-PROVISION-001 was rejected on.

The contract is therefore explicit. Dependencies come from a separately
resolved **dependency interpreter**, chosen from an ordered candidate list
(:func:`dependency_candidates`) in which every candidate is probed for
:data:`REQUIRED_TEST_MODULES` before it is accepted:

1. ``--dependency-python`` — explicit operator choice;
2. ``$PANTHEON_DEPENDENCY_PYTHON`` — governed environment override;
3. the interpreter running this script — the intended path when the caller
   already has the dependencies (dev CI, or a worker that starts from the
   fleet environment);
4. ``$VIRTUAL_ENV`` — the caller is inside an activated environment;
5. ``<checkout>/.venv`` — a checkout-local environment;
6. ``<main worktree>/.venv`` — derived from ``git rev-parse --git-common-dir``.
   This is the candidate that makes the documented bootstrap work from an
   auto-worker *worktree* started with the bare system interpreter.

An explicitly supplied interpreter (1 or 2) is authoritative: it is never
silently replaced by a fallback, because a silent fallback would hide operator
error. The checkout-scoped environment is created *by* the selected dependency
interpreter, so its version matches the inherited site directories by
construction, and every run ends by re-proving that the provisioned interpreter
can import :data:`REQUIRED_TEST_MODULES` from a foreign working directory with
no ``PYTHONPATH``. If it cannot, provisioning fails closed and prints the whole
candidate table with the reason each entry was rejected.

``--mode current`` installs into the running interpreter instead. That is for
disposable, single-checkout environments (a CI job container); it refuses a
non-virtual interpreter unless ``--allow-system-interpreter`` says the
environment really is disposable. In that mode the running interpreter *is* the
dependency interpreter, so it must already carry the dependencies — the CI job
installs ``requirements.txt`` before it calls this script, and the check below
turns that ordering from a convention into an enforced one.

Usage
-----

::

    # auto-worker / local dev: provision the checkout-scoped environment
    python3 scripts/dev/provision_python_distribution.py
    "$(python3 scripts/dev/provision_python_distribution.py --print-python)" -m pytest ...

    # point provisioning at a specific dependency interpreter
    PANTHEON_DEPENDENCY_PYTHON=/path/to/python \
        python3 scripts/dev/provision_python_distribution.py

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
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

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

#: Modules the provisioned interpreter must be able to import. This is not a
#: dependency declaration — requirements.txt remains the source of truth — it is
#: the *bootstrap* contract: AI_COLLABORATION_GUIDE.md documents
#: ``"$PANTHEON_PY" -m pytest`` as the command that follows provisioning, so an
#: interpreter that cannot import pytest is a failed provision, not a success
#: that happens to be unusable.
REQUIRED_TEST_MODULES = ("pytest",)

#: Governed override naming the interpreter that supplies the dependencies.
DEPENDENCY_PYTHON_ENV = "PANTHEON_DEPENDENCY_PYTHON"

#: Environment name of an activated virtual environment.
VIRTUAL_ENV_ENV = "VIRTUAL_ENV"

#: Environments searched for dependencies inside a candidate checkout, in order.
CHECKOUT_VENV_DIRNAMES = (".venv",)


class ProvisionError(RuntimeError):
    """Fail-closed provisioning error. The message is operator-facing."""


class DependencyCandidate(NamedTuple):
    """One entry of the dependency-interpreter search, with its provenance.

    ``source`` is operator-facing: it is what the fail-closed diagnostic prints
    so a reader can tell *why* a path was considered, not just that it was.
    ``explicit`` marks the two operator-supplied sources, which are
    authoritative — an explicit choice that lacks the dependencies is an error,
    never a reason to fall through to a guess.
    """

    source: str
    python: Path
    explicit: bool = False


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
# dependency interpreter selection
# --------------------------------------------------------------------------

# Runs in a *candidate* interpreter. Reports which of the required modules it
# cannot import. find_spec rather than import: a candidate must not be rejected
# because some unrelated import side effect raised.
_DEPENDENCY_PROBE_SOURCE = """
import importlib.util, json

missing = []
for name in %(names)r:
    try:
        found = importlib.util.find_spec(name) is not None
    except Exception:
        found = False
    if not found:
        missing.append(name)
print(json.dumps(missing))
"""

# Runs in the *dependency* interpreter. Reports the directories that hold its
# installed packages, so the checkout-scoped environment can inherit them.
_SITE_PROBE_SOURCE = """
import json, site, sys

directories = list(site.getsitepackages())
if site.ENABLE_USER_SITE:
    directories.append(site.getusersitepackages())
# Debian and several distributions add dist-packages through site customization
# rather than through getsitepackages(), so sweep sys.path for them too.
directories += [p for p in sys.path if p.endswith(("site-packages", "dist-packages"))]
print(json.dumps(directories))
"""


def missing_dependencies(python: Path, names: tuple[str, ...] = REQUIRED_TEST_MODULES) -> list[str]:
    """Which of ``names`` can ``python`` not import, from a foreign cwd?

    Foreign cwd with no ``PYTHONPATH`` for the same reason
    :func:`probe_resolution` uses one: the AC2 children run that way, so a
    dependency that is only reachable through the caller's ambient environment
    is not a dependency this interpreter actually has. A candidate that cannot
    be executed at all is reported as missing everything rather than raising,
    because a non-existent or broken candidate is a normal search outcome.
    """
    source = _DEPENDENCY_PROBE_SOURCE % {"names": list(names)}
    try:
        with tempfile.TemporaryDirectory() as foreign_cwd:
            proc = subprocess.run(
                [str(python), "-c", source],
                cwd=foreign_cwd,
                env=_foreign_env(),
                capture_output=True,
                text=True,
                timeout=120,
            )
    except (OSError, subprocess.SubprocessError):
        return list(names)
    if proc.returncode != 0:
        return list(names)
    try:
        return list(json.loads(proc.stdout.strip().splitlines()[-1]))
    except (ValueError, IndexError):
        return list(names)


def _main_worktree(checkout: Path) -> Path | None:
    """The main checkout backing ``checkout`` when it is a linked git worktree.

    Every auto worker runs in a linked worktree whose dependencies live in the
    main checkout's environment, and ``git rev-parse --git-common-dir`` is the
    only supported way to get from one to the other. Returns ``None`` — never
    raises — when ``checkout`` is not a worktree, git is unavailable, or the
    common directory is not a ``.git`` inside a checkout: an unavailable
    candidate is a search outcome, not an error.
    """
    try:
        proc = subprocess.run(
            ["git", "-C", str(checkout), "rev-parse", "--path-format=absolute", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    common = Path(proc.stdout.strip())
    if common.name != ".git":
        return None
    main = common.parent
    return main if main.resolve() != checkout.resolve() else None


def dependency_candidates(
    checkout: Path,
    *,
    explicit: Path | None = None,
    environ: dict[str, str] | None = None,
    invoking: Path | None = None,
) -> list[DependencyCandidate]:
    """The ordered interpreter search. See the module docstring for the rules.

    Ordering is deliberate: operator intent first, then the interpreter that is
    already running (the dev-CI and fleet-environment path, and free to probe),
    then the environments a checkout can be associated with. Duplicates are
    dropped by resolved path so the diagnostic never lists the same interpreter
    twice under two names.
    """
    environ = os.environ if environ is None else environ
    invoking = Path(sys.executable) if invoking is None else invoking

    ordered: list[DependencyCandidate] = []
    if explicit is not None:
        ordered.append(DependencyCandidate("--dependency-python", Path(explicit), explicit=True))
    env_python = environ.get(DEPENDENCY_PYTHON_ENV)
    if env_python:
        ordered.append(
            DependencyCandidate(f"${DEPENDENCY_PYTHON_ENV}", Path(env_python), explicit=True)
        )
    ordered.append(DependencyCandidate("the interpreter running this script", invoking))
    virtual_env = environ.get(VIRTUAL_ENV_ENV)
    if virtual_env:
        ordered.append(
            DependencyCandidate(f"${VIRTUAL_ENV_ENV}", Path(virtual_env) / "bin" / "python3")
        )
    roots: list[tuple[str, Path]] = [("<checkout>", checkout)]
    main = _main_worktree(checkout)
    if main is not None:
        roots.append(("<main worktree>", main))
    for label, root in roots:
        for dirname in CHECKOUT_VENV_DIRNAMES:
            ordered.append(
                DependencyCandidate(f"{label}/{dirname}", root / dirname / "bin" / "python3")
            )

    # Deduped on the absolute path, deliberately *not* the resolved one: a venv
    # interpreter is a symlink to the base interpreter it was created from, so
    # resolving would collapse every environment on the host into one candidate
    # and hide the only one that has the dependencies.
    deduped: list[DependencyCandidate] = []
    seen: set[str] = set()
    for candidate in ordered:
        key = os.path.abspath(candidate.python)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def select_dependency_interpreter(
    candidates: list[DependencyCandidate],
    *,
    required: tuple[str, ...] = REQUIRED_TEST_MODULES,
) -> tuple[Path, str]:
    """First candidate that can import ``required``. Returns (python, source).

    Fails closed with the whole search table when none can. An *explicit*
    candidate that fails is reported on its own: falling through from an
    operator's stated choice to a guessed one is how a wrong environment gets
    used without anybody noticing.
    """
    rejected: list[str] = []
    for candidate in candidates:
        if not candidate.python.exists():
            rejected.append(f"  {candidate.source}: {candidate.python} does not exist")
            if candidate.explicit:
                raise ProvisionError(
                    f"{candidate.source} names {candidate.python}, which does not exist."
                )
            continue
        missing = missing_dependencies(candidate.python, required)
        if not missing:
            return candidate.python, candidate.source
        rejected.append(
            f"  {candidate.source}: {candidate.python} cannot import "
            + ", ".join(missing)
        )
        if candidate.explicit:
            raise ProvisionError(
                f"{candidate.source} names {candidate.python}, which cannot import "
                f"{', '.join(missing)}.\n\n"
                f"An explicitly selected dependency interpreter is authoritative and is "
                f"never replaced by a fallback. {_install_hint(candidate.python, missing)}"
            )

    names = ", ".join(required)
    raise ProvisionError(
        f"no interpreter on this host can supply the test dependencies the "
        f"documented bootstrap needs ({names}).\n\n"
        f"Provisioning installs import paths only; the dependencies come from a "
        f"separately selected interpreter. Candidates probed, in order:\n"
        + "\n".join(rejected)
        + "\n\nEither point provisioning at an interpreter that has them:\n"
        f"  {DEPENDENCY_PYTHON_ENV}=/path/to/python python3 "
        f"scripts/dev/provision_python_distribution.py\n"
        f"or install them into one:\n"
        f"  /path/to/python -m pip install -r requirements.txt {names}\n\n"
        f"See AI_COLLABORATION_GUIDE.md § Python Test Environment Provisioning."
    )


def _install_hint(python: Path, missing: list[str]) -> str:
    return (
        f"Install them there — `{python} -m pip install -r requirements.txt "
        f"{' '.join(missing)}` — or name a different interpreter."
    )


def verify_dependencies(
    python: Path, *, required: tuple[str, ...] = REQUIRED_TEST_MODULES
) -> None:
    """Fail closed unless the *provisioned* interpreter can import ``required``.

    Selection above picks the interpreter that supplies the dependencies; this
    proves the inheritance actually reached the interpreter the caller is handed.
    Without it, a broken ``.pth``, a version-mismatched reused environment, or a
    future refactor could reopen the silent-success hole this check exists for.
    """
    missing = missing_dependencies(python, required)
    if missing:
        raise ProvisionError(
            f"{python} is provisioned with the Pantheon distribution but cannot "
            f"import {', '.join(missing)}, so the documented next command "
            f"(`{python} -m pytest ...`) would fail with a bare "
            f"ModuleNotFoundError.\n\n"
            f"The dependency inheritance did not reach the provisioned "
            f"environment. {_install_hint(python, missing)}"
        )


# --------------------------------------------------------------------------
# installation
# --------------------------------------------------------------------------


#: Name of the path-inheritance file written into a checkout-scoped environment.
PARENT_SITE_PTH = "_pantheon_parent_site.pth"


def _dependency_site_packages(dependency_python: Path, checkout: Path) -> list[str]:
    """Site directories of the *dependency* interpreter.

    Asked of that interpreter directly rather than read out of this process:
    the two are routinely different (the documented bootstrap starts from the
    bare system interpreter and the dependencies live elsewhere), and reading
    this process's ``site`` module is exactly the assumption that produced a
    pytest-less environment.

    Anything inside ``checkout`` is dropped. Import paths for the checkout come
    from the editable install; inheriting a checkout-local directory here would
    make the source of the mapping ambiguous.
    """
    proc = subprocess.run(
        [str(dependency_python), "-c", _SITE_PROBE_SOURCE],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise ProvisionError(
            f"could not read the site directories of {dependency_python}:\n"
            f"{proc.stdout}{proc.stderr}"
        )
    try:
        directories = json.loads(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError) as exc:
        raise ProvisionError(
            f"{dependency_python} produced unreadable site-directory output: {exc}"
        ) from None

    checkout = checkout.resolve()
    resolved: list[str] = []
    for entry in directories:
        if not entry or not Path(entry).is_dir():
            continue
        path = Path(entry).resolve()
        try:
            path.relative_to(checkout)
        except ValueError:
            pass
        else:
            continue
        text = str(path)
        if text not in resolved:
            resolved.append(text)
    return resolved


def _write_parent_site_pth(venv_dir: Path, dependency_python: Path, checkout: Path) -> None:
    """Let the new environment see the dependency interpreter's packages.

    ``--system-site-packages`` inherits the *base* prefix, not the environment
    that ran the command. When the dependencies live in a virtualenv — the fleet
    ``.venv`` is the normal case — the fresh environment would otherwise come up
    without pytest or any service dependency, and the whole suite would collapse
    into skips and import errors.

    Writing the dependency interpreter's site directories into a ``.pth`` closes
    that. ``site.addpackage`` adds the listed directories to ``sys.path``
    directly; it does not recurse into their own ``.pth`` files, so a Pantheon
    editable install belonging to a *different* checkout in the dependency
    environment is inherited as neither a path entry nor a finder. Dependency
    inheritance therefore cannot reintroduce the cross-checkout collision, and
    :func:`verify_checkout_binding` re-proves that on every run regardless.
    """
    site_packages = sorted(venv_dir.glob("lib/python*/site-packages"))
    if not site_packages:  # pragma: no cover - defensive
        raise ProvisionError(f"virtual environment at {venv_dir} has no site-packages")
    inherited = _dependency_site_packages(dependency_python, checkout)
    target = site_packages[0] / PARENT_SITE_PTH
    if not inherited:
        target.unlink(missing_ok=True)
        return
    header = (
        "# Written by scripts/dev/provision_python_distribution.py: dependency\n"
        f"# site directories inherited from {dependency_python}.\n"
        "# Import paths for the checkout itself come from the editable install,\n"
        "# not from here.\n"
    )
    target.write_text(header + "\n".join(inherited) + "\n", encoding="utf-8")


def _interpreter_version(python: Path) -> str:
    """``major.minor`` of ``python``; site directories are version-specific."""
    proc = subprocess.run(
        [str(python), "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if proc.returncode != 0:
        raise ProvisionError(f"could not read the version of {python}:\n{proc.stdout}{proc.stderr}")
    return proc.stdout.strip()


def ensure_venv(venv_dir: Path, dependency_python: Path, checkout: Path, *, recreate: bool) -> Path:
    """Create ``venv_dir`` from ``dependency_python`` if absent; return its interpreter.

    Created *by* the dependency interpreter rather than by this process, so the
    environment's version matches the site directories it is about to inherit.
    Mixing them would put one interpreter's compiled extension modules on
    another's ``sys.path``.
    """
    python = venv_dir / "bin" / "python3"
    wanted = _interpreter_version(dependency_python)

    if python.exists() and not recreate:
        existing = _interpreter_version(python)
        if existing != wanted:
            raise ProvisionError(
                f"{venv_dir} was built for Python {existing} but the dependency "
                f"interpreter {dependency_python} is Python {wanted}. Site "
                f"directories are version-specific, so reusing it would put one "
                f"interpreter's extension modules on the other's path.\n\n"
                f"Rebuild it: re-run with --recreate, or remove {venv_dir}."
            )
    elif python.exists():
        shutil.rmtree(venv_dir)

    if not python.exists():
        # --system-site-packages: the environment provisions import *paths*, not
        # dependencies (pyproject declares none). Inheriting site directories
        # keeps pytest and every service dependency available without a second
        # dependency installation that could drift from requirements.txt.
        proc = subprocess.run(
            [
                str(dependency_python),
                "-m",
                "venv",
                "--system-site-packages",
                "--symlinks",
                str(venv_dir),
            ],
            capture_output=True,
            text=True,
            timeout=900,
        )
        if proc.returncode != 0:
            raise ProvisionError(
                f"{dependency_python} could not create a virtual environment at "
                f"{venv_dir}:\n{proc.stdout}{proc.stderr}"
            )
    if not python.exists():  # pragma: no cover - defensive
        raise ProvisionError(f"virtual environment at {venv_dir} has no bin/python3")
    # Rewritten on every run: the dependency environment's package set changes,
    # and a stale inheritance list is how an environment silently goes missing a
    # package that the caller can see.
    _write_parent_site_pth(venv_dir, dependency_python, checkout)
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


def resolve_target(args: argparse.Namespace, checkout: Path) -> tuple[Path, str]:
    """The interpreter to install into, plus how its dependencies were sourced."""
    if args.mode == "current":
        if sys.prefix == sys.base_prefix and not args.allow_system_interpreter:
            raise ProvisionError(
                f"--mode current would install into the shared system interpreter "
                f"{sys.executable}, which rebinds the Pantheon packages for every "
                f"other checkout on this host. Use the default checkout-scoped "
                f"mode, or pass --allow-system-interpreter if this environment is "
                f"disposable (a CI job container)."
            )
        # In this mode the running interpreter *is* the dependency interpreter;
        # there is no environment to inherit into. It must already carry the
        # dependencies, which is why the CI job installs requirements.txt first.
        return Path(sys.executable), "the running interpreter (--mode current)"

    dependency_python, source = select_dependency_interpreter(
        dependency_candidates(checkout, explicit=args.dependency_python)
    )
    venv_dir = args.venv_dir or checkout / DEFAULT_VENV_DIRNAME
    return (
        ensure_venv(venv_dir, dependency_python, checkout, recreate=args.recreate),
        f"{dependency_python} (via {source})",
    )


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
        "--dependency-python",
        type=Path,
        default=None,
        help=(
            "interpreter that supplies the test dependencies for the provisioned "
            f"environment (also settable as ${DEPENDENCY_PYTHON_ENV}); authoritative "
            "when given, never replaced by a fallback"
        ),
    )
    parser.add_argument(
        "--recreate",
        action="store_true",
        help="rebuild the checkout-scoped environment from scratch before installing",
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
        target, dependency_source = resolve_target(args, checkout)
        note(f"→ dependencies from {dependency_source}")
        note(f"→ installing {checkout} into {target}")
        strategy = _pip_install(target, checkout, quiet=args.quiet)
        note(f"  editable install succeeded ({strategy})")

    resolved = verify_checkout_binding(target, checkout)
    # The bootstrap contract: an interpreter that cannot run the command the
    # guide prints on the next line is a failed provision, not a usable one.
    verify_dependencies(target)
    note("✓ verified from a foreign cwd with no PYTHONPATH:")
    for name in sorted(resolved):
        note(f"    {name} -> {resolved[name]}")
    note(f"    dependencies -> {', '.join(REQUIRED_TEST_MODULES)} importable")

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

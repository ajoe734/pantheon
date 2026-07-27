"""
OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001 — telemetry test-module import integrity.

`services/telemetry/test_capture.py` and `services/telemetry/test_feedback_adapter.py`
used to import their siblings by bare module name (`from capture import ...`,
`from feedback_adapter import ...`). That only resolves when the interpreter's
`sys.path[0]` happens to be `services/telemetry` itself, so the modules loaded
fine when run from inside the service directory and raised a loader
`ModuleNotFoundError` under repository-root `unittest discover` — 2 loader
errors and 75 silently unrun tests on merged dev `f9b6760d6`.

This module is the regression fence for that class of defect. It asserts the
import contract rather than the two specific call sites, so a newly added
telemetry test module that reintroduces a bare sibling import fails here:

* every `services/telemetry/test_*.py` imports package siblings through their
  fully qualified `services.telemetry.*` path (or an explicit relative import),
  never by bare module name;
* the repaired modules carry no `sys.path` mutation of their own — they rely on
  the package path, not on a process-global side effect that would leak into
  every other module loaded in the same discovery run;
* the modules import and load from a foreign working directory, under a hostile
  ambient environment, and under repeated in-process discovery, with no bare
  sibling alias left behind in `sys.modules`.

Repository-root resolution contract
-----------------------------------

`services.telemetry.<module>` resolves only when the repository is reachable
from `sys.path`. That is a property of Python's import system, not of these
modules, so the contract this file fences is deliberately precise:

* with **no** ``PYTHONPATH`` at all, ``pytest <abs path>`` from a foreign working
  directory passes — pytest walks the ``__init__.py`` chain up to the first
  non-package directory (the repository root) and puts it on ``sys.path`` itself.
  Proven with no ``conftest.py`` and no ``pytest.ini``, so it does not depend on
  this repository's pytest configuration;
* with **no** ``PYTHONPATH`` at all, ``python -m unittest`` — both dotted module
  execution and ``discover`` — passes when the working directory is the
  repository root, because the interpreter puts the working directory on
  ``sys.path``;
* from a foreign working directory, dotted execution
  (``python -m unittest services.telemetry.<module>``) and direct-file execution
  (``python <abs path>/test_<module>.py``) pass **when the checkout is installed
  as a distribution**, and only then. Nothing puts the repository on ``sys.path``
  for them otherwise.

That last clause used to be an open gap. It is closed by
OPS-L12-PYTHON-PACKAGING-PROVISION-001: the root ``pyproject.toml`` declares the
repository's three importable top-level names and
``scripts/dev/provision_python_distribution.py`` installs them, so
``TestAC2ModesUnderInstalledDistribution`` below now asserts an unconditional
pass for every mode instead of recording an expected failure. That class
provisions the interpreter it needs through the shipped script, so the proof is
of the real provisioning path and does not depend on the ambient environment
having been bootstrapped first.

The installed distribution is what makes this fix compatible with the rest of
the module's contract: it exports a name-to-directory mapping through a
meta-path finder rather than putting the repository root on ``sys.path``, so it
adds no process-global ``sys.path`` mutation, exports no repository-root module
as a top-level name, and leaves ``services.telemetry.<module>`` as the single
canonical identity for each module. ``TestInstalledDistributionIsCanonical``
fences all three.

Scope: test-loading only. Neither this task nor its predecessor changes
`capture.py`, `feedback_adapter.py`, or any live runtime configuration.
"""
from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TELEMETRY_DIR = REPO_ROOT / "services" / "telemetry"

# The governed provisioning entry point that installs this checkout as a
# distribution. The AC2 tests below drive the real script rather than writing a
# `.pth` by hand, so a regression in provisioning fails the acceptance test.
PROVISION_SCRIPT = REPO_ROOT / "scripts" / "dev" / "provision_python_distribution.py"

# Top-level names the distribution is allowed to export, and repository-root
# modules that must stay unexported. `cli`, `gate`, and `workflows` are the
# dangerous ones: they are ordinary root-level .py files here and popular
# distribution names on PyPI, so exporting them would create exactly the
# top-level collision the packaging task is required to avoid.
EXPORTED_TOP_LEVEL = ("integrations", "scripts", "services")
UNEXPORTED_ROOT_MODULES = ("cli", "gate", "workflows", "conftest", "mlflow_adapter")

# The two modules repaired by this task. They are named explicitly so the
# regression stays anchored to the reported defect even as the package grows;
# the broader package-wide contract is asserted separately below.
REPAIRED_MODULES = ("test_capture", "test_feedback_adapter")

# Recorded post-change counts for the repaired modules (35 + 40).
EXPECTED_REPAIRED_TEST_COUNT = 75

# Set in every child interpreter this module spawns. A child that ever loads
# this module (for example if someone widens a discovery pattern) skips the
# spawning tests instead of forking forever.
CHILD_SENTINEL = "PANTHEON_TELEMETRY_DISCOVERY_IMPORT_CHILD"

# Ambient values a discovery run must not need, and must not be derailed by.
HOSTILE_ENV = {
    "PANTHEON_RUNTIME_MANAGER_URL": "http://198.51.100.7:8099",
    "PANTHEON_RUNTIME_MANAGER_TOKEN": "ambient-operator-token",
    "PANTHEON_RUNTIME_MANAGER_TOKEN_FILE": "/nonexistent/ambient-token",
    "PANTHEON_RUNTIME_BINDING_STORE_PATH": "/nonexistent/ambient/bindings.json",
    "INCIDENTS_DATA_DIR": "/nonexistent/ambient/incidents",
}

_RAN_TESTS = re.compile(r"^Ran (\d+) tests?", re.MULTILINE)

# The bare sibling aliases the repair removed. No failure, in any environment,
# may be attributable to one of these names again.
BARE_SIBLING_ALIASES = ("capture", "feedback_adapter")


def _sibling_module_names() -> set[str]:
    """Top-level importable names that live inside the telemetry package."""
    names = {path.stem for path in TELEMETRY_DIR.glob("*.py") if path.stem != "__init__"}
    names |= {
        path.name
        for path in TELEMETRY_DIR.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    }
    return names


def _child_env(
    *,
    hostile: bool = False,
    pythonpath_prefix: str | None = None,
    repo_root_on_pythonpath: bool = True,
) -> dict[str, str]:
    """A minimal environment: nothing the parent run happens to export leaks in.

    With ``repo_root_on_pythonpath=False`` the child gets no ``PYTHONPATH`` key at
    all, which is what the repository-root resolution tests need: an environment
    that hands the child no import help whatsoever.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        CHILD_SENTINEL: "1",
    }
    path_entries = [str(REPO_ROOT)] if repo_root_on_pythonpath else []
    if pythonpath_prefix:
        path_entries.insert(0, pythonpath_prefix)
    if path_entries:
        env["PYTHONPATH"] = os.pathsep.join(path_entries)
    if hostile:
        env.update(HOSTILE_ENV)
    return env


def _run(argv: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


def _iter_tests(suite: unittest.TestSuite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from _iter_tests(item)
        else:
            yield item


def _resolves_this_checkout(python: str) -> bool:
    """Does ``python`` import ``services`` from this checkout unaided?

    Unaided means: foreign working directory, no ``PYTHONPATH``. Only an
    installed distribution can satisfy that.
    """
    with tempfile.TemporaryDirectory() as tmp:
        proc = _run(
            [
                python,
                "-c",
                "import importlib.util as u\n"
                "spec = u.find_spec('services')\n"
                "print(spec.origin if spec else '')\n",
            ],
            cwd=Path(tmp),
            env=_child_env(repo_root_on_pythonpath=False),
        )
    if proc.returncode != 0 or not proc.stdout.strip():
        return False
    try:
        Path(proc.stdout.strip()).resolve().relative_to(REPO_ROOT)
    except ValueError:
        return False
    return True


# Memo plus an owning reference to the throwaway environment, so it survives for
# the life of the process and is removed when the interpreter exits.
_PROVISIONED: list[str] = []
_PROVISIONED_TMPDIR: list[tempfile.TemporaryDirectory] = []


def provisioned_interpreter() -> str:
    """An interpreter with this checkout installed as a distribution.

    Prefers the running interpreter when dev CI or the auto-worker test
    bootstrap has already provisioned it — that is the normal case and costs
    nothing. Otherwise it provisions a throwaway environment by invoking the
    shipped ``scripts/dev/provision_python_distribution.py``, so the AC2 proof
    below is always a proof about the real provisioning path and never depends
    on the developer having bootstrapped first.

    The script is invoked exactly as the guide documents — no
    ``--dependency-python``, no ``PANTHEON_DEPENDENCY_PYTHON`` — so it has to
    resolve the dependency interpreter on its own. That keeps the AC2 proof
    honest on an auto-worker host, where ``sys.executable`` may well be the bare
    system interpreter with no pytest in it.

    Deliberately not a ``skipTest``: an environment where provisioning fails is
    an environment where AC2 is unproven, and that must read as a failure.
    """
    if _PROVISIONED:
        return _PROVISIONED[0]

    if _resolves_this_checkout(sys.executable):
        _PROVISIONED.append(sys.executable)
        return _PROVISIONED[0]

    holder = tempfile.TemporaryDirectory(prefix="pantheon-ac2-provision-")
    _PROVISIONED_TMPDIR.append(holder)
    proc = subprocess.run(
        [
            sys.executable,
            str(PROVISION_SCRIPT),
            "--venv-dir",
            str(Path(holder.name) / "venv"),
            "--quiet",
            "--print-python",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    if proc.returncode != 0:
        raise AssertionError(
            "provisioning the Pantheon distribution failed, so AC2 cannot be "
            f"proven in this environment:\n{proc.stdout}{proc.stderr}"
        )
    python = proc.stdout.strip()
    if not _resolves_this_checkout(python):
        raise AssertionError(
            f"provisioning reported success but {python} still does not import "
            "services from this checkout"
        )
    _PROVISIONED.append(python)
    return python


def _loader_failures(suite: unittest.TestSuite) -> list[str]:
    """Descriptions of every `unittest.loader._FailedTest` placeholder in a suite."""
    failures = []
    for test in _iter_tests(suite):
        if type(test).__name__ == "_FailedTest":
            reason = getattr(test, "_exception", "unknown import failure")
            failures.append(f"{test.id()}: {reason}")
    return failures


class TestTelemetryTestModuleImportContract(unittest.TestCase):
    """Static contract: no telemetry test module may import a bare sibling."""

    def test_no_telemetry_test_module_imports_a_bare_package_sibling(self):
        siblings = _sibling_module_names()
        self.assertIn("capture", siblings)
        self.assertIn("feedback_adapter", siblings)

        offenders = []
        for path in sorted(TELEMETRY_DIR.glob("test_*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    # level > 0 is an explicit relative import: package-correct.
                    if node.level == 0 and node.module and node.module.split(".")[0] in siblings:
                        offenders.append(f"{path.name}:{node.lineno} from {node.module} import ...")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name.split(".")[0] in siblings:
                            offenders.append(f"{path.name}:{node.lineno} import {alias.name}")

        self.assertEqual(
            offenders,
            [],
            "telemetry test modules must import siblings as services.telemetry.<module>; "
            f"bare sibling imports found: {offenders}",
        )

    def test_repaired_modules_import_siblings_through_the_package_path(self):
        for module in REPAIRED_MODULES:
            with self.subTest(module=module):
                source = (TELEMETRY_DIR / f"{module}.py").read_text(encoding="utf-8")
                self.assertIn("from services.telemetry.", source)

    def test_repaired_modules_do_not_mutate_process_global_sys_path(self):
        for module in REPAIRED_MODULES:
            with self.subTest(module=module):
                path = TELEMETRY_DIR / f"{module}.py"
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                touches = [
                    node.lineno
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Attribute)
                    and node.attr == "path"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "sys"
                ]
                self.assertEqual(
                    touches,
                    [],
                    f"{path.name} must resolve imports through the package path, not by "
                    f"mutating sys.path (lines {touches})",
                )


class TestTelemetryDiscoveryLoadsWithoutErrors(unittest.TestCase):
    """In-process loader proof, including repeated discovery in one interpreter."""

    def _discover(self, module: str) -> unittest.TestSuite:
        return unittest.TestLoader().discover(
            start_dir=str(TELEMETRY_DIR),
            pattern=f"{module}.py",
            top_level_dir=str(REPO_ROOT),
        )

    def test_repaired_modules_discover_with_zero_loader_errors(self):
        total = 0
        for module in REPAIRED_MODULES:
            with self.subTest(module=module):
                suite = self._discover(module)
                self.assertEqual(_loader_failures(suite), [])
                count = suite.countTestCases()
                self.assertGreater(count, 0, f"{module} contributed no tests")
                total += count
        self.assertEqual(
            total,
            EXPECTED_REPAIRED_TEST_COUNT,
            "repaired modules must keep contributing their full test count under discovery",
        )

    def test_repeated_discovery_in_one_process_is_stable(self):
        for module in REPAIRED_MODULES:
            with self.subTest(module=module):
                first = self._discover(module)
                second = self._discover(module)
                self.assertEqual(_loader_failures(first), [])
                self.assertEqual(_loader_failures(second), [])
                self.assertEqual(first.countTestCases(), second.countTestCases())

    def test_importing_repaired_modules_leaves_no_bare_sibling_in_sys_modules(self):
        # A bare `from capture import ...` registers `sys.modules['capture']`, a
        # second module object distinct from `services.telemetry.capture`. Proving
        # the alias is absent proves the duplicate-identity hazard is gone too.
        self._discover(REPAIRED_MODULES[0])
        self._discover(REPAIRED_MODULES[1])
        leaked = sorted(name for name in ("capture", "feedback_adapter") if name in sys.modules)
        self.assertEqual(leaked, [], f"bare sibling modules aliased into sys.modules: {leaked}")


class UnittestRunAssertions:
    """Shared assertion for "a spawned ``unittest`` run actually passed"."""

    def assertUnittestRunPassed(self, proc: subprocess.CompletedProcess, *, expected: int | None = None):
        combined = proc.stdout + proc.stderr
        self.assertNotIn("_FailedTest", combined, combined)
        self.assertNotIn("ModuleNotFoundError", combined, combined)
        self.assertEqual(proc.returncode, 0, combined)
        match = _RAN_TESTS.search(combined)
        self.assertIsNotNone(match, combined)
        ran = int(match.group(1))
        self.assertGreater(ran, 0, combined)
        if expected is not None:
            self.assertEqual(ran, expected, combined)
        return ran


@unittest.skipIf(
    os.environ.get(CHILD_SENTINEL) == "1",
    "already running inside a spawned discovery child",
)
class TestTelemetryDiscoveryUnderForeignCwdAndHostileEnv(UnittestRunAssertions, unittest.TestCase):
    """Subprocess proof: no cwd dependence, no ambient-environment dependence."""

    def test_repo_root_discovery_of_repaired_modules_reports_no_loader_error(self):
        total = 0
        for module in REPAIRED_MODULES:
            with self.subTest(module=module):
                proc = _run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "services/telemetry",
                        "-t",
                        ".",
                        "-p",
                        f"{module}.py",
                    ],
                    cwd=REPO_ROOT,
                    env=_child_env(),
                )
                total += self.assertUnittestRunPassed(proc)
        self.assertEqual(total, EXPECTED_REPAIRED_TEST_COUNT)

    def test_repaired_modules_run_from_a_foreign_working_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            for module in REPAIRED_MODULES:
                with self.subTest(module=module):
                    proc = _run(
                        [sys.executable, "-m", "unittest", f"services.telemetry.{module}"],
                        cwd=Path(tmp),
                        env=_child_env(),
                    )
                    self.assertUnittestRunPassed(proc)

    def test_repaired_modules_import_under_hostile_ambient_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = _child_env(hostile=True, pythonpath_prefix="/nonexistent/ambient/pythonpath")
            for module in REPAIRED_MODULES:
                with self.subTest(module=module):
                    proc = _run(
                        [
                            sys.executable,
                            "-c",
                            (
                                "import sys\n"
                                f"import services.telemetry.{module} as m\n"
                                "leaked = [n for n in ('capture', 'feedback_adapter') "
                                "if n in sys.modules]\n"
                                "print(m.__name__)\n"
                                "print('LEAKED=' + ','.join(sorted(leaked)))\n"
                            ),
                        ],
                        cwd=Path(tmp),
                        env=env,
                    )
                    self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
                    self.assertIn(f"services.telemetry.{module}", proc.stdout)
                    self.assertIn("LEAKED=\n", proc.stdout + "\n")

    def test_repaired_modules_collect_under_pytest_from_a_foreign_cwd(self):
        try:
            import pytest  # noqa: F401
        except ImportError:  # pragma: no cover - environment without pytest
            self.skipTest("pytest is not installed in this interpreter")

        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(
                [sys.executable, "-m", "pytest", "--collect-only", "-q"]
                + [str(TELEMETRY_DIR / f"{module}.py") for module in REPAIRED_MODULES],
                cwd=Path(tmp),
                env=_child_env(),
            )
            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, combined)
            self.assertNotIn("ModuleNotFoundError", combined, combined)
            self.assertIn(f"{EXPECTED_REPAIRED_TEST_COUNT} tests collected", combined, combined)


@unittest.skipIf(
    os.environ.get(CHILD_SENTINEL) == "1",
    "already running inside a spawned discovery child",
)
class TestRepositoryRootResolutionWithoutPythonPath(unittest.TestCase):
    """No-``PYTHONPATH`` proof for the modes that need no packaging at all.

    Every child here runs with no ``PYTHONPATH`` key in its environment, so
    nothing injects the repository root on the modules' behalf, and every child
    runs under the *ambient* interpreter — no distribution is assumed to be
    installed. These three modes therefore hold on a bare checkout.

    The two foreign-cwd modes that a bare checkout cannot satisfy live in
    ``TestAC2ModesUnderInstalledDistribution`` below, where they are asserted to
    pass outright under a provisioned interpreter.
    """

    def assertNoBareSiblingImportFailure(self, proc: subprocess.CompletedProcess) -> None:
        """The invariant that holds in *every* environment, pass or fail.

        The repaired defect was ``ModuleNotFoundError: No module named 'capture'``
        (and ``'feedback_adapter'``). If a child fails, it must fail on the
        repository root not being importable — never on a bare sibling name.
        """
        combined = proc.stdout + proc.stderr
        for alias in BARE_SIBLING_ALIASES:
            self.assertNotIn(f"No module named '{alias}'", combined, combined)
        if proc.returncode != 0:
            self.assertIn("No module named 'services'", combined, combined)

    def test_pytest_passes_from_foreign_cwd_with_no_pythonpath_or_repo_config(self):
        try:
            import pytest  # noqa: F401
        except ImportError:  # pragma: no cover - environment without pytest
            self.skipTest("pytest is not installed in this interpreter")

        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(
                [
                    sys.executable,
                    "-m",
                    "pytest",
                    "-q",
                    # -c /dev/null and --noconftest strip this repository's
                    # pytest.ini and root conftest.py, so the pass is attributable
                    # to the package path alone, not to repository test config.
                    "-c",
                    os.devnull,
                    "--noconftest",
                    "-p",
                    "no:cacheprovider",
                ]
                + [str(TELEMETRY_DIR / f"{module}.py") for module in REPAIRED_MODULES],
                cwd=Path(tmp),
                env=_child_env(repo_root_on_pythonpath=False),
            )
            combined = proc.stdout + proc.stderr
            self.assertNoBareSiblingImportFailure(proc)
            self.assertEqual(proc.returncode, 0, combined)
            self.assertIn(f"{EXPECTED_REPAIRED_TEST_COUNT} passed", combined, combined)

    def test_dotted_unittest_from_repo_root_cwd_needs_no_pythonpath(self):
        proc = _run(
            [sys.executable, "-m", "unittest"]
            + [f"services.telemetry.{module}" for module in REPAIRED_MODULES],
            cwd=REPO_ROOT,
            env=_child_env(repo_root_on_pythonpath=False),
        )
        self.assertNoBareSiblingImportFailure(proc)
        combined = proc.stdout + proc.stderr
        self.assertEqual(proc.returncode, 0, combined)
        match = _RAN_TESTS.search(combined)
        self.assertIsNotNone(match, combined)
        self.assertEqual(int(match.group(1)), EXPECTED_REPAIRED_TEST_COUNT, combined)

    def test_repo_root_discovery_needs_no_pythonpath(self):
        total = 0
        for module in REPAIRED_MODULES:
            with self.subTest(module=module):
                proc = _run(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "services/telemetry",
                        "-t",
                        ".",
                        "-p",
                        f"{module}.py",
                    ],
                    cwd=REPO_ROOT,
                    env=_child_env(repo_root_on_pythonpath=False),
                )
                self.assertNoBareSiblingImportFailure(proc)
                combined = proc.stdout + proc.stderr
                self.assertEqual(proc.returncode, 0, combined)
                match = _RAN_TESTS.search(combined)
                self.assertIsNotNone(match, combined)
                total += int(match.group(1))
        self.assertEqual(total, EXPECTED_REPAIRED_TEST_COUNT)


@unittest.skipIf(
    os.environ.get(CHILD_SENTINEL) == "1",
    "already running inside a spawned discovery child",
)
class TestAC2ModesUnderInstalledDistribution(UnittestRunAssertions, unittest.TestCase):
    """Canonical AC2: all four execution modes pass, foreign cwd, no PYTHONPATH.

    OPS-L12-PYTHON-PACKAGING-PROVISION-001. Every child below runs from a
    temporary working directory with no ``PYTHONPATH`` key, under an interpreter
    that has this checkout installed as a distribution — provisioned through
    ``scripts/dev/provision_python_distribution.py``, the same entry point dev
    CI and the auto-worker test bootstrap use.

    These are pass assertions, not recorded gaps. If provisioning regresses,
    every test here fails.
    """

    @classmethod
    def setUpClass(cls):
        cls.python = provisioned_interpreter()

    def test_m1_pytest_from_foreign_cwd(self):
        # Deliberately not a skip. Provisioning selects a dependency interpreter
        # and fails closed unless the environment it hands back can import
        # pytest, so a pytest-less provisioned interpreter is a provisioning
        # regression — the exact one this task was rejected on — and it must
        # read as a failure of M1, not as an unproven mode.
        probe = _run(
            [self.python, "-c", "import pytest"], cwd=REPO_ROOT, env=_child_env()
        )
        self.assertEqual(
            probe.returncode,
            0,
            "the provisioned interpreter cannot import pytest, so M1 is unrunnable; "
            "scripts/dev/provision_python_distribution.py is required to fail closed "
            f"rather than return it:\n{probe.stdout}{probe.stderr}",
        )

        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(
                [
                    self.python,
                    "-m",
                    "pytest",
                    "-q",
                    # As in the ambient-interpreter proof: strip this
                    # repository's pytest.ini and root conftest.py so the pass is
                    # attributable to the installed distribution alone.
                    "-c",
                    os.devnull,
                    "--noconftest",
                    "-p",
                    "no:cacheprovider",
                ]
                + [str(TELEMETRY_DIR / f"{module}.py") for module in REPAIRED_MODULES],
                cwd=Path(tmp),
                env=_child_env(repo_root_on_pythonpath=False),
            )
            combined = proc.stdout + proc.stderr
            self.assertEqual(proc.returncode, 0, combined)
            self.assertIn(f"{EXPECTED_REPAIRED_TEST_COUNT} passed", combined, combined)

    def test_m2_dotted_unittest_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(
                [self.python, "-m", "unittest"]
                + [f"services.telemetry.{module}" for module in REPAIRED_MODULES],
                cwd=Path(tmp),
                env=_child_env(repo_root_on_pythonpath=False),
            )
            self.assertUnittestRunPassed(proc, expected=EXPECTED_REPAIRED_TEST_COUNT)

    def test_m3_direct_file_execution_from_foreign_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            total = 0
            for module in REPAIRED_MODULES:
                with self.subTest(module=module):
                    proc = _run(
                        [self.python, str(TELEMETRY_DIR / f"{module}.py")],
                        cwd=Path(tmp),
                        env=_child_env(repo_root_on_pythonpath=False),
                    )
                    total += self.assertUnittestRunPassed(proc)
            self.assertEqual(total, EXPECTED_REPAIRED_TEST_COUNT)

    def test_m4_unittest_discovery_from_the_repository_root(self):
        total = 0
        for module in REPAIRED_MODULES:
            with self.subTest(module=module):
                proc = _run(
                    [
                        self.python,
                        "-m",
                        "unittest",
                        "discover",
                        "-s",
                        "services/telemetry",
                        "-t",
                        ".",
                        "-p",
                        f"{module}.py",
                    ],
                    cwd=REPO_ROOT,
                    env=_child_env(repo_root_on_pythonpath=False),
                )
                total += self.assertUnittestRunPassed(proc)
        self.assertEqual(total, EXPECTED_REPAIRED_TEST_COUNT)


@unittest.skipIf(
    os.environ.get(CHILD_SENTINEL) == "1",
    "already running inside a spawned discovery child",
)
class TestInstalledDistributionIsCanonical(unittest.TestCase):
    """The packaging fix must not buy AC2 at the cost of the other criteria.

    An installed distribution could close AC2 the crude way — drop the whole
    repository root onto ``sys.path`` via a plain ``.pth`` line. That would
    export every root-level module (``cli``, ``gate``, ``workflows``) as a
    top-level name and reintroduce the collision and duplicate-identity hazards
    the predecessor task closed. The declared-package editable install does not,
    and these tests hold it to that.
    """

    @classmethod
    def setUpClass(cls):
        cls.python = provisioned_interpreter()

    def _probe(self, source: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run(
                [self.python, "-c", source],
                cwd=Path(tmp),
                env=_child_env(repo_root_on_pythonpath=False),
            )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        return proc.stdout

    def test_repository_root_is_not_placed_on_sys_path(self):
        # The distribution exports a name-to-directory mapping through a
        # meta-path finder. The repository root itself never reaches sys.path,
        # so no process-global sys.path mutation is involved on any code path.
        out = self._probe(
            "import sys\n"
            f"print('ON_PATH=' + str({str(REPO_ROOT)!r} in sys.path))\n"
        )
        self.assertIn("ON_PATH=False", out, out)

    def test_only_the_declared_top_level_names_are_exported(self):
        out = self._probe(
            "import importlib.util as u\n"
            f"for name in {list(EXPORTED_TOP_LEVEL + UNEXPORTED_ROOT_MODULES)!r}:\n"
            "    spec = u.find_spec(name)\n"
            "    print(name + '=' + ('yes' if spec else 'no'))\n"
        )
        found = dict(line.split("=", 1) for line in out.split())
        for name in EXPORTED_TOP_LEVEL:
            self.assertEqual(found.get(name), "yes", f"{name} must be importable: {out}")
        for name in UNEXPORTED_ROOT_MODULES:
            self.assertEqual(
                found.get(name),
                "no",
                f"{name} is a repository-root module and must not become an "
                f"importable top-level name: {out}",
            )

    def test_module_identity_is_canonical_and_unduplicated(self):
        # Importing through the installed distribution must yield exactly the
        # module objects under services.telemetry.*, with no bare sibling alias
        # and no second copy loaded from a different path.
        out = self._probe(
            "import sys\n"
            "import services.telemetry.capture as a\n"
            "import services.telemetry.feedback_adapter as b\n"
            "import services.telemetry.test_capture  # noqa: F401\n"
            "import services.telemetry.test_feedback_adapter  # noqa: F401\n"
            "print('CAPTURE=' + a.__file__)\n"
            "print('ADAPTER=' + b.__file__)\n"
            "print('SAME=' + str(sys.modules['services.telemetry.capture'] is a))\n"
            "leaked = [n for n in ('capture', 'feedback_adapter') if n in sys.modules]\n"
            "print('LEAKED=' + ','.join(sorted(leaked)))\n"
        )
        self.assertIn(f"CAPTURE={TELEMETRY_DIR / 'capture.py'}", out, out)
        self.assertIn(f"ADAPTER={TELEMETRY_DIR / 'feedback_adapter.py'}", out, out)
        self.assertIn("SAME=True", out, out)
        self.assertIn("LEAKED=\n", out, out)


if __name__ == "__main__":
    unittest.main()

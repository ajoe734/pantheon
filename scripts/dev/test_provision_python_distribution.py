"""Packaging contract for the Pantheon repository distribution.

OPS-L12-PYTHON-PACKAGING-PROVISION-001.

The end-to-end proof that an installed distribution closes AC2 lives in
``services/telemetry/test_discovery_imports.py``, which provisions a real
environment and runs all four execution modes through it. That proof is
necessarily slow.

This module is the fast static half, and it fences the properties an
end-to-end pass could hide:

* the package allowlist stays an allowlist — no repository-root module ever
  becomes an importable top-level name;
* ``pyproject.toml`` does not quietly take over dependency or pytest
  configuration from ``requirements.txt`` and ``pytest.ini``;
* the three files that each restate the exported top-level names agree with
  each other, so a change to one cannot silently invalidate the others;
* the provisioning script's fail-closed behaviour actually fails closed;
* the documented bootstrap works from a **dependency-free interpreter** — the
  auto-worker default — and fails closed with a diagnostic when it cannot.

That last group is ``TestDependencyInterpreterContract``. It exists because the
first delivery of this task passed every other check on a host where the caller
already had pytest, and produced a silently unusable interpreter on a host where
the caller did not. The tests there run the guide's own command from an
interpreter constructed to have nothing installed, so a caller's ambient
environment can never certify the bootstrap again.
"""

from __future__ import annotations

import fnmatch
import importlib
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PROVISION_SCRIPT = REPO_ROOT / "scripts" / "dev" / "provision_python_distribution.py"
TELEMETRY_AC2_TEST = REPO_ROOT / "services" / "telemetry" / "test_discovery_imports.py"

EXPECTED_TOP_LEVEL = {"integrations", "scripts", "services"}


def provisioning_module():
    """Import the provisioning script as a module, without leaving sys.path dirty."""
    sys.path.insert(0, str(PROVISION_SCRIPT.parent))
    try:
        return importlib.import_module("provision_python_distribution")
    finally:
        sys.path.pop(0)


def _clean_env(**overrides: str) -> dict[str, str]:
    """A minimal child environment: no PYTHONPATH, no inherited provisioning hints.

    The whole point of these tests is that nothing the parent run happens to
    export may stand in for a real provision, so the child is built up rather
    than filtered down.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
    }
    env.update(overrides)
    return env


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def _include_patterns() -> list[str]:
    return _config()["tool"]["setuptools"]["packages"]["find"]["include"]


class TestPackagingAllowlist(unittest.TestCase):
    """Discovery must be an explicit allowlist over exactly three names."""

    def test_discovery_is_explicit_not_automatic(self):
        find = _config()["tool"]["setuptools"]["packages"]["find"]
        self.assertIn("include", find, "package discovery must carry an explicit allowlist")
        self.assertEqual(
            {pattern.rstrip("*") for pattern in find["include"]},
            EXPECTED_TOP_LEVEL,
            "the allowlist must name exactly the three importable top-level trees",
        )
        # scripts/ and several integrations/ subtrees have no __init__.py.
        self.assertTrue(
            find.get("namespaces"),
            "namespaces must stay enabled or scripts.* would be silently dropped",
        )

    def test_every_allowlisted_tree_exists(self):
        for name in EXPECTED_TOP_LEVEL:
            with self.subTest(name=name):
                self.assertTrue(
                    (REPO_ROOT / name).is_dir(),
                    f"{name} is allowlisted for packaging but is not a directory",
                )

    def test_no_repository_root_module_is_exported_as_a_top_level_name(self):
        # This is the collision criterion. Flat-layout auto-discovery would turn
        # cli.py, gate.py, and workflows.py into the top-level distributions
        # `cli`, `gate`, and `workflows`; the allowlist must never match them.
        patterns = _include_patterns()
        offenders = []
        for entry in sorted(REPO_ROOT.glob("*.py")):
            name = entry.stem
            if any(fnmatch.fnmatch(name, pattern) for pattern in patterns):
                offenders.append(entry.name)
        self.assertEqual(
            offenders,
            [],
            "repository-root modules must not be exported as top-level names; "
            f"the allowlist {patterns} matches {offenders}",
        )

    def test_no_unexpected_root_directory_is_exported(self):
        patterns = _include_patterns()
        exported = {
            entry.name
            for entry in REPO_ROOT.iterdir()
            if entry.is_dir()
            and not entry.name.startswith(".")
            and any(fnmatch.fnmatch(entry.name, pattern) for pattern in patterns)
        }
        self.assertEqual(
            exported,
            EXPECTED_TOP_LEVEL,
            "the allowlist matches a root directory outside the declared three",
        )


class TestPackagingDoesNotAnnexOtherConfiguration(unittest.TestCase):
    """pyproject.toml provisions import paths and nothing else."""

    def test_declares_no_dependencies(self):
        # requirements.txt is the dependency source of truth. A dependency here
        # would be installed by provisioning and could drift from it.
        self.assertEqual(
            _config()["project"].get("dependencies", []),
            [],
            "dependencies belong in requirements.txt, not in pyproject.toml",
        )

    def test_does_not_take_over_pytest_configuration(self):
        # pytest prefers pytest.ini over pyproject.toml; splitting the config
        # across both would make rootdir depend on which file is found first.
        self.assertNotIn(
            "pytest",
            _config().get("tool", {}),
            "pytest configuration and rootdir must stay in pytest.ini",
        )
        self.assertTrue((REPO_ROOT / "pytest.ini").is_file())

    def test_does_not_ship_package_data(self):
        self.assertIs(
            _config()["tool"]["setuptools"].get("include-package-data"),
            False,
            "an install must add import paths only, never repository data files",
        )


class TestExportedNamesAgreeAcrossFiles(unittest.TestCase):
    """Three files restate the exported names; they must not diverge."""

    def _literal_from(self, path: Path, name: str) -> set[str]:
        import ast

        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
                if name in targets:
                    return set(ast.literal_eval(node.value))
        raise AssertionError(f"{path} does not define {name}")

    def test_provisioning_script_matches_pyproject(self):
        self.assertEqual(
            self._literal_from(PROVISION_SCRIPT, "EXPORTED_TOP_LEVEL"),
            EXPECTED_TOP_LEVEL,
        )

    def test_ac2_regression_matches_pyproject(self):
        self.assertEqual(
            self._literal_from(TELEMETRY_AC2_TEST, "EXPORTED_TOP_LEVEL"),
            EXPECTED_TOP_LEVEL,
        )

    def test_ac2_regression_guards_the_dangerous_root_modules(self):
        guarded = self._literal_from(TELEMETRY_AC2_TEST, "UNEXPORTED_ROOT_MODULES")
        root_modules = {entry.stem for entry in REPO_ROOT.glob("*.py")}
        self.assertTrue(
            guarded <= root_modules,
            f"UNEXPORTED_ROOT_MODULES names modules that no longer exist: "
            f"{sorted(guarded - root_modules)}",
        )
        # The three names that collide with widely used PyPI distributions are
        # the ones worth pinning explicitly.
        self.assertTrue({"cli", "gate", "workflows"} <= guarded)


class TestProvisioningFailsClosed(unittest.TestCase):
    """The script must refuse, loudly, rather than provision something wrong."""

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(PROVISION_SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=300,
        )

    def test_check_only_reports_an_unprovisioned_environment(self):
        proc = self._run("--check-only", "--venv-dir", "/nonexistent/pantheon-venv")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("nothing is provisioned", proc.stderr)

    def test_rejects_a_directory_that_is_not_a_pantheon_checkout(self):
        proc = self._run("--check-only", "--checkout", "/tmp")
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("no pyproject.toml", proc.stderr)

    def test_verification_rejects_a_distribution_bound_to_another_checkout(self):
        # Drive the verification directly: an interpreter that resolves the
        # packages somewhere other than the requested checkout must be reported
        # as a collision, not accepted.
        module = provisioning_module()
        with self.assertRaises(module.ProvisionError) as caught:
            module.verify_checkout_binding(Path(sys.executable), Path("/tmp/not-this-checkout"))
        message = str(caught.exception)
        self.assertTrue(
            "different checkout" in message or "cannot import" in message,
            message,
        )


class TestDependencyInterpreterContract(unittest.TestCase):
    """The documented bootstrap, run from an interpreter that has nothing.

    OPS-L12-PYTHON-PACKAGING-PROVISION-001, second cut. The auto-worker default
    is ``/usr/bin/python3`` with no pytest; provisioning used to report success
    there and hand back an environment where the guide's very next command died
    with ``No module named pytest``. Every test below drives the real script
    from a purpose-built dependency-free interpreter, so that host can never be
    the one nobody tested on again.
    """

    @classmethod
    def setUpClass(cls):
        cls._workspace = tempfile.TemporaryDirectory(prefix="pantheon-dep-free-")
        cls.workspace = Path(cls._workspace.name)

        # A plain venv is isolated from user site and from the base prefix's
        # site-packages, so it is reliably dependency-free whatever the host has
        # installed. --without-pip keeps it cheap; the script never pips *this*
        # interpreter, only the environment it provisions.
        bare_root = cls.workspace / "dependency-free"
        proc = subprocess.run(
            [sys.executable, "-m", "venv", "--without-pip", str(bare_root)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if proc.returncode != 0:  # pragma: no cover - environment without venv
            raise unittest.SkipTest(f"cannot build a dependency-free venv: {proc.stderr}")
        cls.bare_python = bare_root / "bin" / "python3"

        # A synthetic checkout: enough for the script to accept --checkout, with
        # no environment beside it. `git init` makes it its own main worktree, so
        # the <main worktree> candidate cannot reach out to a real repository
        # that happens to enclose the temporary directory.
        cls.bare_checkout = cls.workspace / "synthetic-checkout"
        cls.bare_checkout.mkdir()
        (cls.bare_checkout / "pyproject.toml").write_text(
            PYPROJECT.read_text(encoding="utf-8"), encoding="utf-8"
        )
        subprocess.run(
            ["git", "init", "--quiet", str(cls.bare_checkout)],
            capture_output=True,
            text=True,
            timeout=60,
        )

    @classmethod
    def tearDownClass(cls):
        cls._workspace.cleanup()

    def _provision(self, *args: str, python: Path | None = None, **env: str):
        return subprocess.run(
            [str(python or self.bare_python), str(PROVISION_SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=900,
            env=_clean_env(**env),
        )

    def test_the_dependency_free_interpreter_really_has_no_pytest(self):
        # Control for every other test in this class: if this interpreter could
        # import pytest, none of the assertions below would mean anything.
        proc = subprocess.run(
            [str(self.bare_python), "-c", "import pytest"],
            capture_output=True,
            text=True,
            timeout=120,
            env=_clean_env(),
        )
        self.assertNotEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("No module named 'pytest'", proc.stderr, proc.stderr)

    def test_documented_bootstrap_from_a_dependency_free_interpreter_can_run_pytest(self):
        """The rejection, as a test: provision, then run the guide's next line."""
        try:
            import pytest  # noqa: F401
        except ImportError:  # pragma: no cover - no interpreter here has the deps
            self.skipTest("this interpreter has no pytest to supply as the dependency source")

        target = self.workspace / "provisioned"
        proc = self._provision(
            "--quiet",
            "--print-python",
            "--venv-dir",
            str(target),
            # The one thing the dependency-free child cannot discover on a bare
            # CI runner is *which* interpreter has the dependencies, so it is
            # named the way the guide documents. Discovery itself is fenced by
            # the candidate-derivation tests below.
            PANTHEON_DEPENDENCY_PYTHON=sys.executable,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        provisioned = Path(proc.stdout.strip())
        self.assertTrue(provisioned.exists(), proc.stdout + proc.stderr)

        # This is the command AI_COLLABORATION_GUIDE.md prints directly under
        # the provisioning line. It is what failed in the review dispatch.
        run = subprocess.run(
            [str(provisioned), "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=300,
            env=_clean_env(),
        )
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertNotIn("No module named pytest", run.stdout + run.stderr)

    def test_provisioning_fails_closed_when_no_candidate_has_the_dependencies(self):
        """Silent success is the defect; a named, actionable failure is the fix."""
        proc = self._provision("--quiet", "--checkout", str(self.bare_checkout))
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("pytest", proc.stderr, proc.stderr)
        self.assertIn("Candidates probed", proc.stderr, proc.stderr)
        self.assertIn("PANTHEON_DEPENDENCY_PYTHON", proc.stderr, proc.stderr)
        self.assertIn("AI_COLLABORATION_GUIDE.md", proc.stderr, proc.stderr)
        # And it must not have left a half-built environment behind: selection
        # runs before any install, so nothing is created.
        self.assertFalse((self.bare_checkout / ".venv-pantheon").exists())

    def test_an_explicit_dependency_interpreter_is_never_silently_replaced(self):
        """An operator's stated choice failing must not fall through to a guess."""
        proc = self._provision(
            "--quiet",
            "--checkout",
            str(self.bare_checkout),
            "--dependency-python",
            str(self.bare_python),
        )
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
        self.assertIn("cannot import pytest", proc.stderr, proc.stderr)
        self.assertIn("never replaced by a fallback", proc.stderr, proc.stderr)

    def test_selection_skips_candidates_that_lack_the_required_modules(self):
        module = provisioning_module()
        chosen, source = module.select_dependency_interpreter(
            [
                module.DependencyCandidate("dependency-free", self.bare_python),
                module.DependencyCandidate("this interpreter", Path(sys.executable)),
            ],
            required=("json",),
        )
        # Both can import json, so the first wins: order is honoured.
        self.assertEqual(chosen, self.bare_python)
        self.assertEqual(source, "dependency-free")

        # A candidate that does not exist is skipped, not fatal.
        chosen, source = module.select_dependency_interpreter(
            [
                module.DependencyCandidate("missing", self.workspace / "nonexistent-python"),
                module.DependencyCandidate("dependency-free", self.bare_python),
            ],
            required=("json",),
        )
        self.assertEqual(source, "dependency-free")

        # And a candidate that exists but lacks the required module is skipped
        # in favour of one that has it. This is the selection that had to happen
        # in the review dispatch and did not.
        try:
            import pytest  # noqa: F401
        except ImportError:  # pragma: no cover - no interpreter here has the deps
            self.skipTest("this interpreter has no pytest to supply as the dependency source")
        chosen, source = module.select_dependency_interpreter(
            [
                module.DependencyCandidate("dependency-free", self.bare_python),
                module.DependencyCandidate("this interpreter", Path(sys.executable)),
            ],
            required=("pytest",),
        )
        self.assertEqual(chosen, Path(sys.executable))
        self.assertEqual(source, "this interpreter")

        # Nothing left to fall back to must raise, never return a guess.
        with self.assertRaises(module.ProvisionError) as caught:
            module.select_dependency_interpreter(
                [module.DependencyCandidate("dependency-free", self.bare_python)],
                required=("pytest",),
            )
        self.assertIn("pytest", str(caught.exception))

    def test_candidate_list_reaches_the_main_worktree_environment(self):
        """The derivation that makes an auto-worker *worktree* provisionable.

        Every auto worker runs in a linked git worktree whose dependencies live
        in the main checkout's ``.venv``. Without this candidate the documented
        bootstrap has nothing to find, which is why the review dispatch failed.
        """
        module = provisioning_module()
        main = self.workspace / "main-checkout"
        main.mkdir()
        (main / "pyproject.toml").write_text("", encoding="utf-8")
        for command in (
            ["git", "-C", str(main), "init", "--quiet"],
            ["git", "-C", str(main), "config", "user.email", "test@example.invalid"],
            ["git", "-C", str(main), "config", "user.name", "test"],
            ["git", "-C", str(main), "add", "pyproject.toml"],
            ["git", "-C", str(main), "commit", "--quiet", "-m", "base"],
            ["git", "-C", str(main), "worktree", "add", "--quiet", "-b", "linked",
             str(self.workspace / "linked-worktree")],
        ):
            proc = subprocess.run(command, capture_output=True, text=True, timeout=120)
            if proc.returncode != 0:  # pragma: no cover - git unavailable
                self.skipTest(f"git worktree setup failed: {proc.stderr}")

        linked = self.workspace / "linked-worktree"
        candidates = module.dependency_candidates(
            linked, environ={}, invoking=Path(sys.executable)
        )
        sources = [candidate.source for candidate in candidates]
        pythons = [candidate.python for candidate in candidates]

        self.assertIn("<main worktree>/.venv", sources, sources)
        self.assertIn(main / ".venv" / "bin" / "python3", pythons, pythons)
        # Ordering: a checkout-local environment outranks the main checkout's.
        self.assertLess(
            sources.index("<checkout>/.venv"),
            sources.index("<main worktree>/.venv"),
            sources,
        )

    def test_venv_interpreters_are_not_deduplicated_into_their_base(self):
        """A venv python is a symlink to the interpreter that built it.

        Deduplicating candidates on the *resolved* path would therefore collapse
        every environment on the host into one entry and drop the only candidate
        that has the dependencies — a one-line regression that silently reopens
        the defect, so it gets its own fence.
        """
        module = provisioning_module()
        candidates = module.dependency_candidates(
            self.bare_checkout,
            environ={module.VIRTUAL_ENV_ENV: str(self.bare_python.parent.parent)},
            invoking=Path(sys.executable),
        )
        pythons = [candidate.python for candidate in candidates]
        self.assertIn(Path(sys.executable), pythons, pythons)
        self.assertIn(self.bare_python, pythons, pythons)


if __name__ == "__main__":
    unittest.main()

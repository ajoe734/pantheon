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
* the provisioning script's fail-closed behaviour actually fails closed.
"""

from __future__ import annotations

import fnmatch
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"
PROVISION_SCRIPT = REPO_ROOT / "scripts" / "dev" / "provision_python_distribution.py"
TELEMETRY_AC2_TEST = REPO_ROOT / "services" / "telemetry" / "test_discovery_imports.py"

EXPECTED_TOP_LEVEL = {"integrations", "scripts", "services"}


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
        sys.path.insert(0, str(PROVISION_SCRIPT.parent))
        try:
            import importlib

            module = importlib.import_module("provision_python_distribution")
        finally:
            sys.path.pop(0)

        with self.assertRaises(module.ProvisionError) as caught:
            module.verify_checkout_binding(Path(sys.executable), Path("/tmp/not-this-checkout"))
        message = str(caught.exception)
        self.assertTrue(
            "different checkout" in message or "cannot import" in message,
            message,
        )


if __name__ == "__main__":
    unittest.main()

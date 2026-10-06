from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ci_stage0


class ParseWave1InventoryTests(unittest.TestCase):
    def test_extracts_service_ids_from_wave1_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            doc_path = Path(tmpdir) / "deploy.md"
            doc_path.write_text(
                "\n".join(
                    [
                        "intro",
                        "### 4.3 Wave 1 core service inventory",
                        "| Compose service id | Repo path / source |",
                        "|---|---|",
                        "| `router` | `services/control-plane/router/` |",
                        "| `lean` / future `runtime-manager` | `lean/`, `services/execution/runtime-manager/` |",
                        "### 4.4 Canonical port and health registry",
                    ]
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                ci_stage0.parse_wave1_inventory_ids(doc_path),
                ["router", "lean", "runtime-manager"],
            )


class ChangedTargetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = {
            "global_paths": [".github/**"],
            "targets": [
                {
                    "id": "router",
                    "changed_paths": ["services/control-plane/router/**"],
                    "verify": {"commands": ["python3 -m py_compile services/control-plane/router/main.py"]},
                },
                {
                    "id": "persona",
                    "changed_paths": ["services/control-plane/persona/**"],
                    "build": {
                        "context": "services/control-plane/persona",
                        "dockerfile": "services/control-plane/persona/Dockerfile",
                        "tag": "pantheon-stage0/persona",
                    },
                },
            ],
        }

    def test_service_specific_match_only_selects_that_target(self) -> None:
        report = ci_stage0.compute_changed_targets(
            self.config,
            ["services/control-plane/router/main.py"],
        )
        self.assertFalse(report["global_changed"])
        self.assertEqual(report["target_ids"], ["router"])
        self.assertEqual(report["verify_ids"], ["router"])
        self.assertEqual(report["build_ids"], [])

    def test_global_match_selects_every_target(self) -> None:
        report = ci_stage0.compute_changed_targets(
            self.config,
            [".github/workflows/stage-0-ci.yml"],
        )
        self.assertTrue(report["global_changed"])
        self.assertEqual(report["target_ids"], ["router", "persona"])
        self.assertEqual(report["verify_ids"], ["router"])
        self.assertEqual(report["build_ids"], ["persona"])


class ValidateConfigTests(unittest.TestCase):
    def test_load_config_requires_doc_matrix_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            matrix_path = root / ".github" / "pantheon-stage0-matrix.json"
            matrix_path.parent.mkdir(parents=True, exist_ok=True)
            service_dir = root / "services" / "control-plane" / "router"
            service_dir.mkdir(parents=True, exist_ok=True)
            (service_dir / "main.py").write_text("print('ok')\n", encoding="utf-8")
            (service_dir / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")

            doc_path = root / "deploy.md"
            doc_path.write_text(
                "\n".join(
                    [
                        "### 4.3 Wave 1 core service inventory",
                        "| Compose service id | Repo path / source |",
                        "|---|---|",
                        "| `router` | `services/control-plane/router/` |",
                        "### 4.4 Canonical port and health registry",
                    ]
                ),
                encoding="utf-8",
            )

            matrix_path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "baseline": {"setup": [], "commands": ["python3 -m py_compile scripts/ci_stage0.py"]},
                        "global_paths": ["scripts/ci_stage0.py"],
                        "compose_services": ["router"],
                        "targets": [
                            {
                                "id": "router",
                                "family": "delivery-platform",
                                "profile": "core-vm",
                                "repo_paths": ["services/control-plane/router"],
                                "changed_paths": ["services/control-plane/router/**"],
                                "verify": {"commands": ["python3 -m py_compile services/control-plane/router/main.py"]},
                                "build": {
                                    "context": "services/control-plane/router",
                                    "dockerfile": "services/control-plane/router/Dockerfile",
                                    "tag": "pantheon-stage0/router",
                                },
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            original_root = ci_stage0.ROOT
            try:
                ci_stage0.ROOT = root
                with self.assertRaises(ci_stage0.Stage0ConfigError):
                    ci_stage0.load_config(matrix_path, doc_path)
            finally:
                ci_stage0.ROOT = original_root

    def test_load_config_requires_compose_services_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            matrix_path = root / ".github" / "pantheon-stage0-matrix.json"
            matrix_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path = root / "deploy.md"
            doc_path.write_text(
                "### 4.3 Wave 1 core service inventory\n| `router` | `services/router/` |\n.github/pantheon-stage0-matrix.json\n",
                encoding="utf-8",
            )
            compose_path = root / "docker-compose.yml"
            compose_path.write_text(
                "services:\n  router:\n    build:\n      context: services/router\n",
                encoding="utf-8",
            )
            matrix_path.write_text(
                json.dumps({
                    "schema_version": 1,
                    "baseline": {"setup": [], "commands": ["echo 1"]},
                    "global_paths": ["a"],
                    "targets": [{"id": "router", "family": "f", "profile": "p", "repo_paths": ["services/router"], "changed_paths": ["services/router/**"]}],
                    "compose_services": [],
                }),
                encoding="utf-8",
            )
            (root / "services" / "router").mkdir(parents=True, exist_ok=True)
            original_root = ci_stage0.ROOT
            try:
                ci_stage0.ROOT = root
                with self.assertRaises(ci_stage0.Stage0ConfigError) as ctx:
                    ci_stage0.load_config(matrix_path, doc_path, compose_path)
                self.assertIn("Compose project services missing from stage-0 matrix: router", str(ctx.exception))
            finally:
                ci_stage0.ROOT = original_root


class RunShellCommandTests(unittest.TestCase):
    def test_pip_preflight_raises_clear_error_when_module_missing(self) -> None:
        with mock.patch("ci_stage0.importlib.util.find_spec", return_value=None):
            with self.assertRaises(ci_stage0.Stage0ConfigError) as ctx:
                ci_stage0.run_shell_command("python3 -m pip install -r requirements.txt")

        self.assertIn("python3 -m pip is unavailable", str(ctx.exception))

    def test_docker_preflight_raises_clear_error_when_binary_missing(self) -> None:
        with mock.patch("ci_stage0.shutil.which", return_value=None):
            with self.assertRaises(ci_stage0.Stage0ConfigError) as ctx:
                ci_stage0.run_shell_command("docker build --file Dockerfile .")

        self.assertIn("docker is required", str(ctx.exception))


class RunTargetTests(unittest.TestCase):
    def test_build_mode_passes_declared_build_args(self) -> None:
        config = {
            "targets": [
                {
                    "id": "research-qlib",
                    "build": {
                        "context": ".",
                        "dockerfile": "services/research/qlib/Dockerfile",
                        "tag": "pantheon-stage0/research-qlib",
                        "args": ["PANTHEON_INSTALL_UPSTREAM_DEPS=false"],
                    },
                }
            ]
        }
        args = SimpleNamespace(
            matrix=Path("unused"),
            doc=Path("unused"),
            compose=Path("nonexistent"),
            target_id="research-qlib",
            mode="build",
            tag_suffix="sha123",
        )

        with (
            mock.patch("ci_stage0.load_config", return_value=config),
            mock.patch("ci_stage0.run_shell_command") as run_shell_command,
        ):
            self.assertEqual(ci_stage0.cmd_run_target(args), 0)

        run_shell_command.assert_called_once_with(
            "docker build --file services/research/qlib/Dockerfile "
            "--build-arg PANTHEON_INSTALL_UPSTREAM_DEPS=false "
            "--tag pantheon-stage0/research-qlib:sha123 ."
        )

    def test_build_mode_runs_compose_entrypoint_import_check(self) -> None:
        config = {
            "targets": [
                {
                    "id": "router",
                    "build": {
                        "context": "services/control-plane/router",
                        "dockerfile": "services/control-plane/router/Dockerfile",
                        "tag": "pantheon-stage0/router",
                    },
                }
            ]
        }
        args = SimpleNamespace(
            matrix=Path("unused"),
            doc=Path("unused"),
            compose=Path("docker-compose.yml"),
            target_id="router",
            mode="build",
            tag_suffix="sha123",
        )

        with (
            mock.patch("ci_stage0.load_config", return_value=config),
            mock.patch("ci_stage0.run_shell_command") as run_shell_command,
            mock.patch("ci_stage0.get_target_entrypoint_import_commands", return_value=["docker run --rm pantheon-stage0/router:sha123 python3 -c 'import main'"]),
        ):
            self.assertEqual(ci_stage0.cmd_run_target(args), 0)

        self.assertEqual(run_shell_command.call_count, 2)
        run_shell_command.assert_has_calls([
            mock.call("docker build --file services/control-plane/router/Dockerfile --tag pantheon-stage0/router:sha123 services/control-plane/router"),
            mock.call("docker run --rm pantheon-stage0/router:sha123 python3 -c 'import main'"),
        ])


if __name__ == "__main__":
    unittest.main()

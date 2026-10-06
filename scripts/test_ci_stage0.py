from __future__ import annotations

import json
import shutil
import subprocess
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


class ComposeParsingTests(unittest.TestCase):
    CONFIG = {
        "services": {
            "worker": {
                "build": {"context": "/repo", "dockerfile": "services/x/Dockerfile", "args": {"A": "b"}},
                "command": ["python", "-m", "services.x.worker"],
                "environment": {"DSN": "postgresql://u:p@postgres:5432/db", "UNSET": None},
            },
            "router": {"build": {"context": "/repo/services/router"}, "entrypoint": ["python", "/app/run.py"]},
            "openclaw-gateway": {"build": {"context": "/repo", "dockerfile": "integrations/openclaw/gateway/Dockerfile"}},
            "redis": {"image": "redis"},
        }
    }

    def _parse(self) -> dict[str, dict]:
        completed = subprocess.CompletedProcess([], 0, stdout=json.dumps(self.CONFIG), stderr="")
        with mock.patch("ci_stage0.subprocess.run", return_value=completed) as run:
            details = ci_stage0.parse_compose_services_details(Path("/repo/docker-compose.yml"))
        self.assertIn("--profile", run.call_args.args[0])
        return details

    def test_parses_resolved_compose_config_for_services_built_from_project_code(self) -> None:
        details = self._parse()
        self.assertEqual(list(details), ["worker", "router"])
        self.assertEqual(details["worker"]["command"], ["python", "-m", "services.x.worker"])
        self.assertEqual(details["worker"]["args"], ["A=b"])
        self.assertEqual(details["worker"]["dockerfile"], "services/x/Dockerfile")
        self.assertEqual(details["router"]["dockerfile"], "services/router/Dockerfile")
        self.assertEqual(details["router"]["entrypoint"], ["python", "/app/run.py"])

    def test_compose_failure_is_a_config_error(self) -> None:
        failed = subprocess.CompletedProcess([], 1, stdout="", stderr="bad compose")
        with mock.patch("ci_stage0.subprocess.run", return_value=failed):
            with self.assertRaises(ci_stage0.Stage0ConfigError):
                ci_stage0.parse_compose_services_details(Path("/repo/docker-compose.yml"))

    def test_images_group_services_sharing_a_dockerfile(self) -> None:
        details = {
            "a": {"context": ".", "dockerfile": "services/x/Dockerfile"},
            "b": {"context": ".", "dockerfile": "services/x/Dockerfile"},
        }
        with mock.patch("ci_stage0.parse_compose_services_details", return_value=details):
            images = ci_stage0.compose_images()
        self.assertEqual(list(images), ["services-x-Dockerfile"])
        self.assertEqual(list(images["services-x-Dockerfile"]["services"]), ["a", "b"])


class EntrypointResolutionTests(unittest.TestCase):
    def test_entrypoint_and_cmd_combine_and_compose_command_overrides_cmd(self) -> None:
        dockerfile = 'FROM x\nENTRYPOINT ["python", "/issuer/run.py"]\nCMD ["--serve"]\n'
        self.assertEqual(ci_stage0.effective_argv({}, dockerfile), ["python", "/issuer/run.py", "--serve"])
        self.assertEqual(ci_stage0.effective_argv({"command": ["--once"]}, dockerfile), ["python", "/issuer/run.py", "--once"])
        self.assertEqual(ci_stage0.effective_argv({"entrypoint": ["python", "other.py"]}, dockerfile), ["python", "other.py"])

    def test_shell_form_cmd_and_continuations_are_read(self) -> None:
        dockerfile = "FROM x\nCMD uvicorn main:app \\\n  --app-dir svc\n"
        argv = ci_stage0.effective_argv({}, dockerfile)
        self.assertEqual(argv[:2], ["sh", "-c"])
        self.assertIn("--app-dir svc", argv[2])

    def test_uvicorn_checks_module_and_app_attribute_in_app_dir(self) -> None:
        check = ci_stage0.resolve_import_check(["sh", "-c", "uvicorn main:app --app-dir svc/dir --port ${PORT:-8000}"])
        self.assertEqual(check[0], "python")
        self.assertIn("'svc/dir'", check[2])
        self.assertIn("importlib.import_module('main')", check[2])
        self.assertIn("'app'", check[2])
        python_m = ci_stage0.resolve_import_check(["python", "-m", "uvicorn", "pkg.mod:api"])
        self.assertIn("importlib.import_module('pkg.mod')", python_m[2])

    def test_module_script_shell_and_inline_entrypoints(self) -> None:
        self.assertIn("import_module('services.x.worker')", ci_stage0.resolve_import_check(["python", "-m", "services.x.worker", "--flag"])[2])
        self.assertIn("run_path('scripts/run.py'", ci_stage0.resolve_import_check(["python", "scripts/run.py"])[2])
        self.assertEqual(ci_stage0.resolve_import_check(["bash", "scripts/db_migrate.sh"]), ["bash", "-n", "scripts/db_migrate.sh"])
        self.assertIsNone(ci_stage0.resolve_import_check(["python", "-c", "print('deferred')"]))
        for unknown in (["node", "dist/index.js"], []):
            with self.assertRaises(ci_stage0.Stage0ConfigError):
                ci_stage0.resolve_import_check(unknown)

    def test_script_check_runs_dataclass_scripts_without_running_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "worker.py"
            script.write_text(
                "from dataclasses import dataclass\n\n@dataclass\nclass Job:\n    name: str = 'x'\n\n"
                "if __name__ == '__main__':\n    raise SystemExit('main must not run')\n",
                encoding="utf-8",
            )
            check = ci_stage0.resolve_import_check(["python", str(script)])
            result = subprocess.run([sys.executable, *check[1:]], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_script_check_fails_when_the_script_cannot_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            script = Path(tmpdir) / "broken.py"
            script.write_text("import module_that_is_not_installed\n", encoding="utf-8")
            check = ci_stage0.resolve_import_check(["python", str(script)])
            self.assertNotEqual(subprocess.run([sys.executable, *check[1:]], capture_output=True).returncode, 0)


class ImportSmokeTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("docker"), "docker compose config is needed to read docker-compose.yml")
    def test_every_compose_service_resolves_or_is_explicitly_inline(self) -> None:
        for image_id, image in ci_stage0.compose_images().items():
            dockerfile_text = (ci_stage0.ROOT / image["dockerfile"]).read_text(encoding="utf-8")
            for service, details in image["services"].items():
                with self.subTest(image=image_id, service=service):
                    argv = ci_stage0.effective_argv(details, dockerfile_text)
                    ci_stage0.resolve_import_check(argv)  # raises when the entrypoint shape is unknown

    def test_image_affected_by_dockerfile_lock_and_source_but_not_docs_or_tests(self) -> None:
        image = {"context": ".", "dockerfile": "services/telemetry/Dockerfile"}
        affected = lambda *files: ci_stage0.image_affected(image, list(files))  # noqa: E731
        self.assertTrue(affected("services/telemetry/Dockerfile"))
        self.assertTrue(affected("dependencies/locks/services-telemetry.txt"))
        self.assertTrue(affected("services/anything/module.py"))
        self.assertFalse(affected("docs/readme.md", "services/anything/test_module.py", "dependencies/locks/services-other.txt"))
        scoped = {"context": "services/research/mlflow", "dockerfile": "services/research/mlflow/Dockerfile"}
        self.assertTrue(ci_stage0.image_affected(scoped, ["services/research/mlflow/requirements.txt"]))
        self.assertFalse(ci_stage0.image_affected(scoped, ["services/telemetry/module.py"]))

    def test_run_import_smoke_reports_each_service_and_failures(self) -> None:
        image = {
            "dockerfile": "services/telemetry/Dockerfile",
            "context": ".",
            "args": ["A=b"],
            "services": {
                "api": {"command": None, "environment": {}},
                "worker": {"command": ["python", "-m", "services.telemetry.worker"], "environment": {"MODE": "dev"}},
                "inline": {"command": ["python", "-c", "print(1)"], "environment": {}},
            },
        }

        def fake_run(command: str) -> None:
            if "services.telemetry.worker" in command:
                raise subprocess.CalledProcessError(3, command)

        with mock.patch("ci_stage0.run_shell_command", side_effect=fake_run) as run:
            results = ci_stage0.run_import_smoke("services-telemetry-Dockerfile", image, "sha1")
        self.assertEqual(results["api"], "passed")
        self.assertEqual(results["worker"], "failed: exit 3")
        self.assertTrue(results["inline"].startswith("skipped"))
        self.assertIn("--build-arg A=b", run.call_args_list[0].args[0])
        self.assertIn("--entrypoint python pantheon-import-smoke/services-telemetry-dockerfile:sha1", run.call_args_list[1].args[0])
        self.assertIn("--env MODE=dev", run.call_args_list[2].args[0])

    def test_run_import_smoke_starts_compose_postgres_for_services_that_connect_on_import(self) -> None:
        image = {
            "dockerfile": "services/telemetry/Dockerfile",
            "context": ".",
            "args": [],
            "services": {"api": {"command": None, "environment": {"DSN": "postgresql://u:p@postgres:5432/db"}}},
        }
        with mock.patch("ci_stage0.run_shell_command") as run, mock.patch("ci_stage0.subprocess.run") as down:
            ci_stage0.run_import_smoke("img", image, "sha1")
        commands = [call.args[0] for call in run.call_args_list]
        self.assertTrue(any("up --detach --wait postgres" in command for command in commands))
        self.assertIn("--network pantheon-import-smoke-img_default", commands[-1])
        self.assertIn("down", down.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

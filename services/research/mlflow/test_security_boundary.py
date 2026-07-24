"""Tests for the fail-closed MLflow container entrypoint."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from entrypoint import MlflowSecurityBoundaryError, build_server_command


class MlflowSecurityBoundaryTests(unittest.TestCase):
    def test_default_command_is_loopback_and_job_execution_is_disabled(self) -> None:
        command = build_server_command({})
        self.assertEqual(command[:4], ["mlflow", "server", "--host", "127.0.0.1"])

    def test_non_loopback_bind_requires_basic_auth(self) -> None:
        with self.assertRaisesRegex(MlflowSecurityBoundaryError, "basic-auth"):
            build_server_command({"MLFLOW_HOST": "0.0.0.0"})

    def test_non_loopback_bind_requires_mounted_auth_config(self) -> None:
        with self.assertRaisesRegex(MlflowSecurityBoundaryError, "MLFLOW_AUTH_CONFIG_PATH"):
            build_server_command(
                {"MLFLOW_HOST": "0.0.0.0", "MLFLOW_APP_NAME": "basic-auth"}
            )

    def test_non_loopback_bind_accepts_explicit_non_default_auth(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "basic_auth.ini"
            auth_path.write_text(
                "[mlflow]\nadmin_username = operator\nadmin_password = test-non-default-value\n",
                encoding="utf-8",
            )
            command = build_server_command(
                {
                    "MLFLOW_HOST": "0.0.0.0",
                    "MLFLOW_APP_NAME": "basic-auth",
                    "MLFLOW_AUTH_CONFIG_PATH": str(auth_path),
                    "MLFLOW_SERVER_ALLOWED_HOSTS": "mlflow.internal.example",
                    "MLFLOW_SERVER_CORS_ALLOWED_ORIGINS": "https://mlflow.internal.example",
                }
            )
        self.assertIn("basic-auth", command)

    def test_default_admin_password_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "basic_auth.ini"
            auth_path.write_text(
                "[mlflow]\nadmin_username = admin\nadmin_password = password\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(MlflowSecurityBoundaryError, "default admin password"):
                build_server_command(
                    {
                        "MLFLOW_HOST": "0.0.0.0",
                        "MLFLOW_APP_NAME": "basic-auth",
                        "MLFLOW_AUTH_CONFIG_PATH": str(auth_path),
                    }
                )

    def test_missing_explicit_admin_credentials_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_path = Path(tmpdir) / "basic_auth.ini"
            auth_path.write_text("[mlflow]\nadmin_username = operator\n", encoding="utf-8")
            with self.assertRaisesRegex(MlflowSecurityBoundaryError, "credentials"):
                build_server_command(
                    {
                        "MLFLOW_HOST": "0.0.0.0",
                        "MLFLOW_APP_NAME": "basic-auth",
                        "MLFLOW_AUTH_CONFIG_PATH": str(auth_path),
                    }
                )

    def test_job_execution_and_wildcard_hosts_are_refused(self) -> None:
        with self.assertRaisesRegex(MlflowSecurityBoundaryError, "JOB_EXECUTION"):
            build_server_command({"MLFLOW_SERVER_ENABLE_JOB_EXECUTION": "true"})
        with self.assertRaisesRegex(MlflowSecurityBoundaryError, "non-wildcard"):
            build_server_command({"MLFLOW_SERVER_ALLOWED_HOSTS": "*"})
        with self.assertRaisesRegex(MlflowSecurityBoundaryError, "non-wildcard"):
            build_server_command({"MLFLOW_SERVER_ALLOWED_HOSTS": "*:*"})
        with self.assertRaisesRegex(MlflowSecurityBoundaryError, "non-wildcard"):
            build_server_command({"MLFLOW_SERVER_CORS_ALLOWED_ORIGINS": "http://*:*"})


if __name__ == "__main__":
    unittest.main()

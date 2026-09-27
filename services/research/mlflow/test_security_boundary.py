"""Tests for the fail-closed MLflow container entrypoint."""

from __future__ import annotations

import os
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


class MlflowRuntimeTrackingTests(unittest.TestCase):
    """Demonstrate actual local tracking and artifact round-trips against SQLite store."""

    def test_sqlite_tracking_and_artifact_roundtrip(self) -> None:
        import mlflow
        from mlflow.tracking import MlflowClient

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "mlflow.db"
            artifacts_dir = Path(tmpdir) / "artifacts"
            artifacts_dir.mkdir(parents=True, exist_ok=True)
            tracking_uri = f"sqlite:///{db_path}"

            mlflow.set_tracking_uri(tracking_uri)
            exp_id = mlflow.create_experiment(
                "governed-research-experiment",
                artifact_location=str(artifacts_dir),
            )
            with mlflow.start_run(experiment_id=exp_id, run_name="baseline-run") as run:
                mlflow.log_param("model_family", "quantlib_crr")
                mlflow.log_metric("eval_sharpe", 1.85)
                artifact_file = Path(tmpdir) / "metrics.json"
                artifact_file.write_text('{"status": "verified"}', encoding="utf-8")
                mlflow.log_artifact(str(artifact_file))
                run_id = run.info.run_id

            client = MlflowClient(tracking_uri=tracking_uri)
            fetched_run = client.get_run(run_id)
            self.assertEqual(fetched_run.data.params["model_family"], "quantlib_crr")
            self.assertEqual(fetched_run.data.metrics["eval_sharpe"], 1.85)

            downloaded_path = client.download_artifacts(run_id, "metrics.json")
            with open(downloaded_path, encoding="utf-8") as stream:
                content = stream.read()
            self.assertEqual(content, '{"status": "verified"}')


class MlflowBasicAuthSecurityBoundaryTests(unittest.TestCase):
    """Demonstrate fail-closed security boundary rejecting unauthenticated/unauthorized access."""

    def test_basic_auth_boundary_rejection_and_admission(self) -> None:
        import base64
        from mlflow.server import app as base_app
        from mlflow.server.auth import create_app

        with tempfile.TemporaryDirectory() as tmpdir:
            auth_ini = Path(tmpdir) / "basic_auth.ini"
            auth_db = Path(tmpdir) / "auth.db"
            auth_ini.write_text(
                f"[mlflow]\n"
                f"default_permission = READ\n"
                f"database_uri = sqlite:///{auth_db}\n"
                f"admin_username = testadmin\n"
                f"admin_password = testpassword123\n",
                encoding="utf-8",
            )
            orig_auth_path = os.environ.get("MLFLOW_AUTH_CONFIG_PATH")
            orig_secret = os.environ.get("MLFLOW_FLASK_SERVER_SECRET_KEY")
            orig_admin_user = os.environ.get("MLFLOW_AUTH_ADMIN_USERNAME")
            orig_admin_pass = os.environ.get("MLFLOW_AUTH_ADMIN_PASSWORD")
            orig_backend = os.environ.get("MLFLOW_BACKEND_STORE_URI")
            try:
                os.environ["MLFLOW_AUTH_CONFIG_PATH"] = str(auth_ini)
                os.environ["MLFLOW_FLASK_SERVER_SECRET_KEY"] = "supersecretkey1234567890"
                os.environ["MLFLOW_AUTH_ADMIN_USERNAME"] = "testadmin"
                os.environ["MLFLOW_AUTH_ADMIN_PASSWORD"] = "testpassword123"
                os.environ["MLFLOW_BACKEND_STORE_URI"] = f"sqlite:///{tmpdir}/backend.db"

                import mlflow.server.auth
                mlflow.server.auth.auth_config = mlflow.server.auth.read_auth_config()

                auth_app = create_app(base_app)
                client = auth_app.test_client()

                # 1. Unauthenticated request -> 401 Unauthorized
                resp_unauth = client.get("/api/2.0/mlflow/users/get?username=testadmin")
                self.assertEqual(resp_unauth.status_code, 401)

                # 2. Unauthorized request with invalid credentials -> 401 Unauthorized
                bad_auth = base64.b64encode(b"wronguser:wrongpass").decode("ascii")
                resp_bad = client.get(
                    "/api/2.0/mlflow/users/get?username=testadmin",
                    headers={"Authorization": f"Basic {bad_auth}"},
                )
                self.assertEqual(resp_bad.status_code, 401)

                # 3. Authorized request with valid admin credentials -> 200 OK
                good_auth = base64.b64encode(b"testadmin:testpassword123").decode("ascii")
                resp_good = client.get(
                    "/api/2.0/mlflow/users/get?username=testadmin",
                    headers={"Authorization": f"Basic {good_auth}"},
                )
                self.assertEqual(resp_good.status_code, 200)
            finally:
                for key, orig in [
                    ("MLFLOW_AUTH_CONFIG_PATH", orig_auth_path),
                    ("MLFLOW_FLASK_SERVER_SECRET_KEY", orig_secret),
                    ("MLFLOW_AUTH_ADMIN_USERNAME", orig_admin_user),
                    ("MLFLOW_AUTH_ADMIN_PASSWORD", orig_admin_pass),
                    ("MLFLOW_BACKEND_STORE_URI", orig_backend),
                ]:
                    if orig is not None:
                        os.environ[key] = orig
                    else:
                        os.environ.pop(key, None)


if __name__ == "__main__":
    unittest.main()

"""Tests for Ray token, network, and activation containment."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from security import (
    RaySecurityBoundaryError,
    require_secure_ray_runtime,
    secure_local_ray_init_kwargs,
)


TEST_TOKEN = "test-only-ray-auth-token-00000000000000000000"


class RaySecurityBoundaryTests(unittest.TestCase):
    def test_auth_mode_is_required(self) -> None:
        with self.assertRaisesRegex(RaySecurityBoundaryError, "RAY_AUTH_MODE=token"):
            require_secure_ray_runtime({"RAY_AUTH_TOKEN": TEST_TOKEN})

    def test_token_source_is_required(self) -> None:
        with self.assertRaisesRegex(RaySecurityBoundaryError, "RAY_AUTH_TOKEN"):
            require_secure_ray_runtime({"RAY_AUTH_MODE": "token"})

    def test_token_file_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            token_path = Path(tmpdir) / "ray-token"
            token_path.write_text(TEST_TOKEN, encoding="utf-8")
            require_secure_ray_runtime(
                {"RAY_AUTH_MODE": "token", "RAY_AUTH_TOKEN_PATH": str(token_path)}
            )

    def test_remote_cluster_and_non_loopback_dashboard_are_refused(self) -> None:
        base = {"RAY_AUTH_MODE": "token", "RAY_AUTH_TOKEN": TEST_TOKEN}
        with self.assertRaisesRegex(RaySecurityBoundaryError, "remote Ray cluster"):
            require_secure_ray_runtime({**base, "RAY_ADDRESS": "ray://ray.example:10001"})
        with self.assertRaisesRegex(RaySecurityBoundaryError, "loopback-only"):
            require_secure_ray_runtime({**base, "RAY_DASHBOARD_HOST": "0.0.0.0"})

    def test_init_kwargs_disable_dashboard_and_bind_loopback(self) -> None:
        with patch.dict(
            "os.environ",
            {"RAY_AUTH_MODE": "token", "RAY_AUTH_TOKEN": TEST_TOKEN},
            clear=True,
        ):
            kwargs = secure_local_ray_init_kwargs(num_cpus=1)
        self.assertFalse(kwargs["include_dashboard"])
        self.assertEqual(kwargs["dashboard_host"], "127.0.0.1")
        self.assertEqual(kwargs["_node_ip_address"], "127.0.0.1")
        self.assertNotIn("local_mode", kwargs)

    def test_security_owned_init_options_cannot_be_overridden(self) -> None:
        with patch.dict(
            "os.environ",
            {"RAY_AUTH_MODE": "token", "RAY_AUTH_TOKEN": TEST_TOKEN},
            clear=True,
        ):
            with self.assertRaisesRegex(RaySecurityBoundaryError, "may not be overridden"):
                secure_local_ray_init_kwargs(include_dashboard=True)


if __name__ == "__main__":
    unittest.main()

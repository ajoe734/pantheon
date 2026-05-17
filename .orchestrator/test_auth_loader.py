#!/usr/bin/env python3
from __future__ import annotations

import json
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import auth_loader


class AuthLoaderTests(unittest.TestCase):
    def test_missing_credential_fails_fast(self) -> None:
        with self.assertRaises(auth_loader.CredentialError) as ctx:
            auth_loader.load_git_credentials({})

        self.assertIn("No worker git credential found", str(ctx.exception))

    def test_present_ssh_key_returns_noninteractive_git_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "id_ed25519"
            key_path.write_text("sentinel-key-material", encoding="utf-8")
            key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

            credentials = auth_loader.load_git_credentials(
                {
                    "PANTHEON_WORKER_GIT_AUTH_MODE": "ssh",
                    "PANTHEON_WORKER_GIT_SSH_KEY_PATH": str(key_path),
                }
            )

        self.assertEqual(credentials.mode, "ssh")
        self.assertEqual(credentials.credential_path, str(key_path.resolve()))
        self.assertEqual(credentials.env["GIT_TERMINAL_PROMPT"], "0")
        self.assertIn("GIT_SSH_COMMAND", credentials.env)
        self.assertIn(str(key_path.resolve()), credentials.env["GIT_SSH_COMMAND"])
        self.assertNotIn("sentinel-key-material", json.dumps(credentials.safe_summary()))

    def test_empty_ssh_key_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            key_path = Path(tmpdir) / "id_ed25519"
            key_path.write_text("", encoding="utf-8")
            key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)

            with self.assertRaises(auth_loader.CredentialError) as ctx:
                auth_loader.load_git_credentials(
                    {
                        "PANTHEON_WORKER_GIT_AUTH_MODE": "ssh",
                        "PANTHEON_WORKER_GIT_SSH_KEY_PATH": str(key_path),
                    }
                )

        self.assertIn("is empty", str(ctx.exception))

    def test_present_pat_returns_askpass_env_without_token_value(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            askpass = Path(tmpdir) / "git-askpass.sh"
            askpass.write_text("#!/usr/bin/env sh\nexit 0\n", encoding="utf-8")
            askpass.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)

            credentials = auth_loader.load_git_credentials(
                {
                    "PANTHEON_WORKER_GIT_AUTH_MODE": "pat",
                    "PANTHEON_WORKER_GIT_ASKPASS": str(askpass),
                    "PANTHEON_WORKER_GIT_TOKEN": "sentinel-token-value",
                }
            )

        encoded = json.dumps({"env": credentials.env, "summary": credentials.safe_summary()})
        self.assertEqual(credentials.mode, "pat")
        self.assertEqual(credentials.env["GIT_ASKPASS"], str(askpass.resolve()))
        self.assertEqual(credentials.env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(credentials.secret_env_keys, ("PANTHEON_WORKER_GIT_TOKEN",))
        self.assertNotIn("sentinel-token-value", encoded)

    def test_env_file_values_are_loaded_before_explicit_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            key_path = root / "id_ed25519"
            key_path.write_text("secret", encoding="utf-8")
            key_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            env_file = root / "worker-git.env"
            env_file.write_text(
                "\n".join(
                    [
                        "PANTHEON_WORKER_GIT_AUTH_MODE=ssh",
                        f"PANTHEON_WORKER_GIT_SSH_KEY_PATH={key_path}",
                    ]
                ),
                encoding="utf-8",
            )

            credentials = auth_loader.load_git_credentials({"PANTHEON_WORKER_GIT_ENV_FILE": str(env_file)})

        self.assertEqual(credentials.mode, "ssh")
        self.assertEqual(credentials.source, str(env_file.resolve()))


if __name__ == "__main__":
    unittest.main()

"""Regression coverage for the supervised Codex wrapper's cache boundary."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


WRAPPER = Path(__file__).with_name("bin") / "codex"


class TestCodexWorkerHomeIsolation(unittest.TestCase):
    def _make_home(self, root: Path) -> tuple[Path, Path]:
        home = root / "home"
        source_home = home / ".codex"
        source_home.mkdir(parents=True)
        (source_home / "auth.json").write_text('{"account":"worker"}\n', encoding="utf-8")
        (source_home / "config.toml").write_text('model = "test"\n', encoding="utf-8")
        (source_home / "models_cache.json").write_text('{"schema":"new"}\n', encoding="utf-8")

        binary = home / ".npm-global" / "bin" / "codex"
        binary.parent.mkdir(parents=True)
        binary.write_text(
            "#!/usr/bin/env bash\nset -euo pipefail\nprintf '%s\\n' \"$CODEX_HOME\"\n",
            encoding="utf-8",
        )
        binary.chmod(0o755)
        return home, source_home

    def _run_wrapper(self, env: dict[str, str]) -> Path:
        result = subprocess.run(
            [str(WRAPPER), "exec", "test"],
            text=True,
            capture_output=True,
            env=env,
            check=True,
        )
        return Path(result.stdout.strip())

    def test_supervised_run_uses_private_home_without_shared_model_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home, source_home = self._make_home(Path(temp_dir))
            env = os.environ | {
                "HOME": str(home),
                "CODEX_HOME": str(source_home),
                "ORCH_RUN_ID": "codex-20260828T151937Z-bc776f6e",
            }

            actual_home = self._run_wrapper(env)

            self.assertNotEqual(actual_home, source_home)
            self.assertEqual(
                actual_home,
                home / ".cache" / "pantheon" / "codex-workers" / "codex-20260828T151937Z-bc776f6e",
            )
            self.assertEqual((actual_home / "auth.json").read_text(encoding="utf-8"), '{"account":"worker"}\n')
            self.assertEqual((actual_home / "config.toml").read_text(encoding="utf-8"), 'model = "test"\n')
            self.assertFalse((actual_home / "models_cache.json").exists())
            self.assertTrue((source_home / "models_cache.json").exists())

    def test_interactive_invocation_keeps_configured_home(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home, source_home = self._make_home(Path(temp_dir))
            env = os.environ | {"HOME": str(home), "CODEX_HOME": str(source_home)}

            self.assertEqual(self._run_wrapper(env), source_home)

    def test_run_id_is_normalized_below_worker_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home, source_home = self._make_home(Path(temp_dir))
            worker_root = home / "worker-cache"
            env = os.environ | {
                "HOME": str(home),
                "CODEX_HOME": str(source_home),
                "ORCH_RUN_ID": "../codex run///1",
                "PANTHEON_CODEX_WORKER_HOME_ROOT": str(worker_root),
            }

            actual_home = self._run_wrapper(env)

            self.assertEqual(actual_home.parent, worker_root)
            self.assertEqual(actual_home.name, "..-codex-run-1")


if __name__ == "__main__":
    unittest.main()

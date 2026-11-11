"""Focused tests for scripts/ci/provision_worker_sandbox.sh using temp fixtures only."""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "ci" / "provision_worker_sandbox.sh"


def _exe(path: Path, body: str) -> Path:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class ProvisionTests(unittest.TestCase):
    def run_script(self, *, primary_update=0, primary_install=0, fb_update=0,
                   fb_install=0, probe=0, os_id="ubuntu", fb_update_delay=0,
                   update_timeout="5", install_timeout="5"):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            log = d / "calls.log"
            real_timeout = shutil.which("timeout")
            _exe(d / "timeout", f'echo "timeout $*" >> {log}\nexec {real_timeout} "$@"\n')
            # fake apt-get: fallback calls carry Dir::Etc::sourcelist.
            _exe(d / "apt-get", f'''
echo "$*" >> {log}
fb=0; case "$*" in *Dir::Etc::sourcelist*) fb=1;; esac
for a in "$@"; do
  [ "$a" = update ] && cmd=update
  [ "$a" = install ] && cmd=install
  case "$a" in Dir::Etc::sourcelist=*) cat "${{a#*=}}" >> {log};; esac
done
if [ $fb = 1 ]; then
  if [ $cmd = update ]; then sleep {fb_update_delay}; echo "fb-update-diag" >&2; exit {fb_update}; fi
  echo "fb-install-diag" >&2; exit {fb_install}
fi
if [ $cmd = update ]; then echo "Temporary failure resolving azure.archive.ubuntu.com" >&2; exit {primary_update}; fi
exit {primary_install}
''')
            _exe(d / "dpkg-query", "echo bubblewrap 1.0\n")
            _exe(d / "parser", f'echo "$*" >> {log}\n')
            _exe(d / "bwrap", f'[ "$1" = --version ] && {{ echo bwrap 0; exit 0; }}\nexit {probe}\n')
            (d / "profile").write_text("profile")
            (d / "os-release").write_text(f"ID={os_id}\nVERSION_CODENAME=noble\n")
            env = dict(os.environ,
                       PATH=str(d) + os.pathsep + os.environ["PATH"],
                       SANDBOX_SUDO="", SANDBOX_APT_GET=str(d / "apt-get"),
                       SANDBOX_DPKG_QUERY=str(d / "dpkg-query"),
                       SANDBOX_APPARMOR_PARSER=str(d / "parser"),
                       SANDBOX_BWRAP=str(d / "bwrap"), SANDBOX_PROFILE=str(d / "profile"),
                       SANDBOX_OS_RELEASE=str(d / "os-release"))
            for name, value in (("SANDBOX_UPDATE_TIMEOUT", update_timeout),
                                ("SANDBOX_INSTALL_TIMEOUT", install_timeout)):
                env.pop(name, None)
                if value is not None:
                    env[name] = value
            r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True,
                               timeout=20)
            return r, log.read_text() if log.exists() else ""

    def test_success_uses_primary_only(self):
        r, log = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Dir::Etc::sourcelist", log)
        self.assertIn("capability probe passed", r.stdout)
        self.assertIn("--replace", log)

    def test_temporary_primary_failure_uses_registered_mirror_with_ubuntu_keyring(self):
        r, log = self.run_script(primary_update=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Temporary failure resolving", r.stderr)  # original diagnostics kept
        self.assertIn("Dir::Etc::sourcelist", log)
        self.assertIn("Dir::Etc::sourceparts=/dev/null", log)
        for suite in ("noble", "noble-updates", "noble-security"):
            self.assertIn(
                "deb [signed-by=/usr/share/keyrings/ubuntu-archive-keyring.gpg] "
                f"https://mirrors.mit.edu/ubuntu {suite} main universe", log)
        self.assertNotIn("allow-unauthenticated", log)
        self.assertNotIn("trusted=yes", log)

    def test_persistent_failure_is_metadata_exit_10(self):
        r, log = self.run_script(primary_update=100, fb_update=100)
        self.assertEqual(r.returncode, 10)
        self.assertIn("Temporary failure resolving", r.stderr)
        self.assertIn("fb-update-diag", r.stderr)
        self.assertNotIn(" install ", log)
        self.assertNotIn("--replace", log)

    def test_all_metadata_errors_are_fatal_and_attempts_are_bounded(self):
        r, log = self.run_script(primary_update=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = [line for line in log.splitlines() if line.startswith("-o ")]
        self.assertEqual(len(calls), 3)  # primary update, mirror update + install
        for call in calls:
            self.assertIn("APT::Update::Error-Mode=any", call)
            self.assertIn("Acquire::Retries=1", call)
            self.assertIn("Acquire::https::Timeout=15", call)

    def test_install_failure_is_exit_11(self):
        r, _ = self.run_script(primary_install=100, fb_install=100)
        self.assertEqual(r.returncode, 11)

    def test_fresh_fallback_metadata_can_outlast_primary_update_budget(self):
        r, log = self.run_script(primary_update=100, update_timeout="0.1",
                                 fb_update_delay=0.3)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("timeout --kill-after=5s 0.1s", log)
        self.assertIn("timeout --kill-after=5s 90s", log)
        self.assertIn("capability probe passed", r.stdout)

    def test_default_attempt_bounds_fit_unchanged_five_minute_step(self):
        # Force both installs to run, covering the longest permitted path.
        r, log = self.run_script(primary_install=100,
                                 update_timeout=None, install_timeout=None)
        self.assertEqual(r.returncode, 0, r.stderr)
        calls = [line.split() for line in log.splitlines()
                 if line.startswith("timeout --kill-after=5s")]
        seconds = [int(call[2].removesuffix("s")) for call in calls]
        self.assertEqual(seconds, [45, 60, 90, 60])
        self.assertLess(sum(seconds) + len(calls) * 5 + 5, 300)

    def test_fallback_metadata_timeout_remains_fatal(self):
        r, log = self.run_script(primary_update=124, fb_update=124)
        self.assertEqual(r.returncode, 10)
        self.assertIn("primary apt update failed (exit 124)", r.stdout)
        self.assertIn("fallback apt update failed (exit 124)", r.stdout)
        self.assertNotIn(" install ", log)
        self.assertNotIn("--replace", log)

    def test_capability_probe_failure_is_exit_12(self):
        r, _ = self.run_script(probe=1)
        self.assertEqual(r.returncode, 12)
        self.assertNotIn("probe passed", r.stdout)

    def test_non_ubuntu_refuses_fallback_and_keeps_primary_code(self):
        r, log = self.run_script(primary_update=100, os_id="debian")
        self.assertEqual(r.returncode, 10)
        self.assertNotIn("Dir::Etc::sourcelist", log)

    def test_script_has_no_unsafe_options(self):
        text = SCRIPT.read_text()
        for bad in ("trusted=yes", "--allow-unauthenticated", "AllowInsecure", "| sh", "| bash"):
            self.assertNotIn(bad, text)
        self.assertIn("signed-by=", text)


if __name__ == "__main__":
    unittest.main()

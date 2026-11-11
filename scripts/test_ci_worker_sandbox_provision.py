"""Focused tests for scripts/ci/provision_worker_sandbox.sh using temp fixtures only."""
from __future__ import annotations

import os
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
                   fb_install=0, probe=0, os_id="ubuntu"):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            log = d / "calls.log"
            # fake apt-get: fallback calls carry Dir::Etc::sourcelist.
            _exe(d / "apt-get", f'''
echo "$*" >> {log}
fb=0; case "$*" in *Dir::Etc::sourcelist*) fb=1;; esac
for a in "$@"; do [ "$a" = update ] && cmd=update; [ "$a" = install ] && cmd=install; done
if [ $fb = 1 ]; then
  if [ $cmd = update ]; then echo "fb-update-diag" >&2; exit {fb_update}; fi
  echo "fb-install-diag" >&2; exit {fb_install}
fi
if [ $cmd = update ]; then echo "Temporary failure resolving azure.archive.ubuntu.com" >&2; exit {primary_update}; fi
exit {primary_install}
''')
            _exe(d / "dpkg-query", "echo bubblewrap 1.0\n")
            _exe(d / "parser", f'echo "$*" >> {log}\n')
            _exe(d / "bwrap", f'[ "$1" = --version ] && {{ echo bwrap 0; exit 0; }}\nexit {probe}\n')
            _exe(d / "getent", "exit 2\n")
            (d / "hosts").write_text("127.0.0.1 localhost\n")
            (d / "resolv.conf").write_text("nameserver 10.0.0.1\n")
            (d / "profile").write_text("profile")
            (d / "os-release").write_text(f"ID={os_id}\nVERSION_CODENAME=noble\n")
            env = dict(os.environ,
                       SANDBOX_SUDO="", SANDBOX_APT_GET=str(d / "apt-get"),
                       SANDBOX_DPKG_QUERY=str(d / "dpkg-query"),
                       SANDBOX_APPARMOR_PARSER=str(d / "parser"),
                       SANDBOX_BWRAP=str(d / "bwrap"), SANDBOX_PROFILE=str(d / "profile"),
                       SANDBOX_OS_RELEASE=str(d / "os-release"),
                       SANDBOX_GETENT=str(d / "getent"), SANDBOX_HOSTS=str(d / "hosts"),
                       SANDBOX_RESOLV=str(d / "resolv.conf"),
                       SANDBOX_UPDATE_TIMEOUT="5", SANDBOX_INSTALL_TIMEOUT="5")
            r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)
            return r, log.read_text() if log.exists() else ""

    def test_success_uses_primary_only(self):
        r, log = self.run_script()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Dir::Etc::sourcelist", log)
        self.assertIn("capability probe passed", r.stdout)
        self.assertIn("--replace", log)

    def test_temporary_primary_failure_falls_back_to_official_signed_source(self):
        r, log = self.run_script(primary_update=100)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Temporary failure resolving", r.stderr)  # original diagnostics kept
        self.assertIn("Dir::Etc::sourcelist", log)
        self.assertNotIn("allow-unauthenticated", log)
        self.assertNotIn("trusted=yes", log)

    def test_persistent_failure_is_metadata_exit_10(self):
        r, _ = self.run_script(primary_update=100, fb_update=100)
        self.assertEqual(r.returncode, 10)
        self.assertIn("Temporary failure resolving", r.stderr)

    def test_persistent_failure_emits_counts_only_dns_diagnostics(self):
        r, _ = self.run_script(primary_update=100, fb_update=100)
        self.assertEqual(r.returncode, 10)
        self.assertIn("diag getent-dns host=archive.ubuntu.com exit=2 answer_lines=0", r.stdout)
        self.assertIn("diag getent-dns-tcp host=github.com", r.stdout)
        self.assertIn("diag hosts-file-entries host=security.ubuntu.com count=0", r.stdout)
        self.assertIn("diag resolv-conf readable=yes nameserver_count=1", r.stdout)
        self.assertNotIn("10.0.0.1", r.stdout + r.stderr)

    def test_success_emits_no_dns_diagnostics(self):
        r, _ = self.run_script()
        self.assertNotIn("diag getent", r.stdout)

    def test_install_failure_is_exit_11(self):
        r, _ = self.run_script(primary_install=100, fb_install=100)
        self.assertEqual(r.returncode, 11)

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

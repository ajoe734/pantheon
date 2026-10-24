"""Local controller and contract tests for production blue/green delivery (`ENV-PROD-CONTROL-IMPL-001`).

NOTE: These tests use a fake ops adapter and a temporary state tree. They prove controller
behavior (preflight, atomic switch, rollback, failure injection) only. They do NOT constitute
hosted production drill evidence, and no cloud or GitHub resource is touched.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

import yaml

from scripts.deploy import prod_preflight, release_manifest
from tests.deployment.test_staging_release import _sample_manifest

REPO_ROOT = Path(__file__).resolve().parents[2]
SWITCH = REPO_ROOT / "scripts" / "deploy" / "prod_atomic_switch.sh"
ROLLBACK = REPO_ROOT / "scripts" / "deploy" / "prod_rollback.sh"
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "production-release.yml"
COMPOSE = REPO_ROOT / "deploy" / "production" / "docker-compose.prod-control.yml"
CADDY = REPO_ROOT / "deploy" / "production" / "Caddyfile"

FAKE_OPS = """#!/usr/bin/env bash
step="$1"; shift
echo "$step $*" >> "$OPS_LOG"
if [[ "${FAIL_STEP:-}" == "$step" || "${FAIL_STEP:-}" == "$step:${2:-}" ]]; then
  # fail once per run, but not during rollback for steps in FAIL_ALWAYS
  if [[ -z "${FAIL_ALWAYS:-}" && -f "$OPS_LOG.failed" ]]; then :; else
    touch "$OPS_LOG.failed"; exit 1
  fi
fi
if [[ "$step" == "post_identity" ]]; then
  if [[ -n "${IDENTITY_OVERRIDE:-}" ]]; then echo "$IDENTITY_OVERRIDE"; else cat "$IDENTITY_FILE"; fi
fi
exit 0
"""

OPS = REPO_ROOT / "scripts" / "deploy" / "prod_ops.sh"
BOOTSTRAP = REPO_ROOT / "deploy" / "production" / "bootstrap.sh"

# Fake docker/curl/mountpoint binaries exercise the REAL prod_ops.sh/bootstrap.sh code paths.
FAKE_BIN = r"""#!/usr/bin/env bash
tool="$(basename "$0")"
echo "$tool $*" >> "$BIN_LOG"
case "$tool" in
  mountpoint) [[ "${FAKE_MOUNTED:-yes}" == "yes" ]]; exit $? ;;
  docker)
    args="$*"
    if [[ "$args" == *"compose"*" ps -q "* ]]; then
      svc="${args##* ps -q }"
      if [[ "$svc" == lifecycle-projector ]]; then
        if [[ -f "$FAKE_STATE/singleton_up" ]]; then for _ in $(seq 1 "${FAKE_SINGLETON_COUNT:-1}"); do echo "cid-$_"; done; fi
      else echo "cid-$svc"; fi
      exit 0
    fi
    if [[ "$args" == *"compose"*"up -d"*lifecycle-projector* ]]; then touch "$FAKE_STATE/singleton_up"; fi
    if [[ "$args" == *"compose"*"rm -fsv lifecycle-projector"* ]]; then rm -f "$FAKE_STATE/singleton_up"; fi
    if [[ "$args" == "inspect -f {{range .Config.Env}}"* ]]; then cat "$FAKE_STATE/container_env"; exit 0; fi
    if [[ "$args" == "inspect -f {{.Config.Image}}"* ]]; then cat "$FAKE_STATE/container_image"; exit 0; fi
    if [[ "$args" == "image inspect"*migration* ]]; then printf '%s' "${FAKE_MIGRATION_LABEL:-}"; exit 0; fi
    if [[ "$args" == "image inspect"*compat* ]]; then printf '%s' "${FAKE_COMPAT_LABEL:-}"; exit 0; fi
    [[ "${FAKE_DOCKER_FAIL:-}" == *"$(echo "$args" | awk '{print $1}')"* ]] && exit 1
    exit 0 ;;
  curl)
    url="${@: -1}"; all="$*"; port="$(sed -n 's#.*127.0.0.1:\([0-9]*\)/.*#\1#p' <<<"$url")"
    out=""; prev=""; for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
    if [[ "$url" == */bff/readyz ]]; then [[ " ${FAKE_READY_PORTS:-} " == *" $port "* ]] && printf 200 || printf 503; exit 0; fi
    if [[ "$url" == */runtime-config.js ]]; then
      [[ -n "$out" && "$out" != /dev/null ]] && cat "$FAKE_STATE/served_runtime_config" > "$out"
      printf 'HTTP/2 200\r\ncache-control: %s\r\n\r\n' "${FAKE_RC_CACHE:-no-store, no-cache}"; exit 0
    fi
    if [[ "$all" == *"-D -"* ]]; then
      if [[ "$all" == *"evil.invalid"* ]]; then printf 'HTTP/1.1 400\r\n\r\n'; else printf 'HTTP/1.1 200\r\naccess-control-allow-origin: %s\r\n\r\n' "${FAKE_CORS_ALLOW:-https://app.trading.acme-prod.net}"; fi
      exit 0
    fi
    if [[ "$all" == *"-X POST"* ]]; then printf '%s' "${FAKE_WRITE_CODE:-405}"; exit 0; fi
    if [[ "$all" == *"Authorization"* ]]; then printf '%s' "${FAKE_TENANT_CODE:-403}"; exit 0; fi
    printf '%s' "${FAKE_AUTH_CODE:-401}"; exit 0 ;;
esac
"""


class OpsHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in ("docker", "curl", "mountpoint"):
            (self.bin / tool).write_text(FAKE_BIN)
            (self.bin / tool).chmod(0o755)
        self.state = self.tmp / "fake_state"
        self.state.mkdir()
        self.root = self.tmp / "prod"
        for d in ("state/snapshots", "config", "fe/releases"):
            (self.root / d).mkdir(parents=True)
        self.manifest = _sample_manifest(state="staging_verified")
        for owner in ("runtime_manager", "governance", "deployment", "lifecycle_projector"):
            self.manifest["backend"]["images"][owner] = f"r/{owner}@sha256:" + "a" * 64
        self.mpath = self.tmp / "manifest.json"
        self.mpath.write_text(json.dumps(self.manifest))
        self.log = self.tmp / "bin.log"
        self.rc = self.root / "config" / "runtime-config.js"
        self.rc.write_text('window.__PANTHEON_RUNTIME_CONFIG__ = {"bffBaseUrl":"https://api.trading.acme-prod.net"};\n')
        (self.state / "served_runtime_config").write_text(self.rc.read_text())
        (self.state / "container_env").write_text("\n".join([
            "PANTHEON_LIVE_BROKER_ENABLED=false", "PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED=false",
            "PANTHEON_BFF_AUTH_STUB=false", "PANTHEON_BFF_AUTH_MODE=strict"]) + "\n")
        (self.state / "container_image").write_text(self.manifest["backend"]["images"]["operator_bff"])
        self.token = self.tmp / "token"
        self.token.write_text("synthetic-readonly-token")

    def env(self, **extra):
        env = {**os.environ, **PROD_ENV, "PATH": f"{self.bin}:{os.environ['PATH']}", "BIN_LOG": str(self.log),
               "FAKE_STATE": str(self.state), "PROD_ROOT": str(self.root), "PROD_DATA_MOUNT": str(self.tmp / "data"),
               "PROD_BLUE_PORT": "8001", "PROD_GREEN_PORT": "8002", "PROD_ACTIVE_PORT": "8001",
               "PROD_TARGET_PORT": "8002", "RELEASE_MANIFEST_PATH": str(self.mpath),
               "PROD_COMPOSE_FILE": str(COMPOSE), "PROD_COMPOSE_ENV_FILE": str(self.tmp / "compose.env"),
               "PROD_GATE_TOKEN_FILE": str(self.token), "FAKE_READY_PORTS": "8001 8002",
               "FAKE_MIGRATION_LABEL": self.manifest["migration_set_sha256"],
               "FAKE_COMPAT_LABEL": self.manifest["compatibility_manifest_sha256"]}
        env.update(extra)
        return env

    def ops(self, *args, **extra):
        return subprocess.run([str(OPS), *args], env=self.env(**extra), capture_output=True, text=True, timeout=60)

    def snapshot(self, **over):
        rec = {"release_id": "rel-1", "status": "READY", "disk": PROD_ENV["PROD_DATA_DISK"],
               "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
        rec.update(over)
        (self.root / "state" / "snapshots" / "rel-1.json").write_text(json.dumps(rec))


class ProdOpsTests(OpsHarness):
    def test_snapshot_preflight_requires_fresh_matching_ready_snapshot(self):
        self.assertNotEqual(self.ops("snapshot_preflight", "rel-1").returncode, 0)  # no record
        self.snapshot()
        self.assertEqual(self.ops("snapshot_preflight", "rel-1").returncode, 0)
        for bad in ({"status": "CREATING"}, {"disk": "other-disk"}, {"release_id": "rel-0"},
                    {"created_at": "2020-01-01T00:00:00Z"}):
            self.snapshot(**bad)
            r = self.ops("snapshot_preflight", "rel-1")
            self.assertNotEqual(r.returncode, 0, bad)
            self.assertIn("SNAPSHOT_INVALID", r.stderr)
        self.snapshot()
        r = self.ops("snapshot_preflight", "rel-1", FAKE_MOUNTED="no")
        self.assertIn("DATA_DISK_NOT_MOUNTED", r.stderr)

    def test_baseline_health_and_green_start_are_real_readbacks(self):
        self.assertEqual(self.ops("baseline_health", "rel-1").returncode, 0)
        self.assertNotEqual(self.ops("baseline_health", "rel-1", FAKE_READY_PORTS="8002").returncode, 0)
        ok = self.ops("start_green", "rel-1", "8002", self.manifest["backend"]["images"]["operator_bff"])
        self.assertEqual(ok.returncode, 0, ok.stderr)
        log = self.log.read_text()
        self.assertIn("--no-build", log)
        self.assertIn("operator-bff-green", log)
        self.assertNotIn("docker build", log)
        self.assertNotIn(" pull", log)

    def test_start_green_refuses_active_port_wrong_image_and_missing_owner_images(self):
        img = self.manifest["backend"]["images"]["operator_bff"]
        self.assertIn("PORT_IN_USE", self.ops("start_green", "rel-1", "8001", img).stderr)
        self.assertIn("IMAGE_MISMATCH", self.ops("start_green", "rel-1", "8002", "x@sha256:" + "9" * 64).stderr)
        del self.manifest["backend"]["images"]["lifecycle_projector"]
        self.mpath.write_text(json.dumps(self.manifest))
        self.assertIn("OWNER_IMAGES", self.ops("start_green", "rel-1", "8002", img).stderr)

    def test_start_green_not_ready_fails(self):
        img = self.manifest["backend"]["images"]["operator_bff"]
        r = self.ops("start_green", "rel-1", "8002", img, FAKE_READY_PORTS="8001",
                     PROD_GREEN_READY_TRIES="2", PROD_GREEN_READY_INTERVAL="0")
        self.assertIn("GREEN_NOT_READY", r.stderr)

    def test_gates_pass_and_fail_closed(self):
        for gate in ("health", "auth", "cors", "tenant", "read_only", "schema", "old_new_compat"):
            r = self.ops("gate", "rel-1", gate, "8002")
            self.assertEqual(r.returncode, 0, f"{gate}: {r.stderr}")
        cases = {
            "health": {"FAKE_READY_PORTS": "8001"},
            "auth": {"FAKE_AUTH_CODE": "200"},
            "cors": {"FAKE_CORS_ALLOW": "*"},
            "tenant": {"FAKE_TENANT_CODE": "200"},
            "read_only": {"FAKE_WRITE_CODE": "200"},
            "schema": {"FAKE_MIGRATION_LABEL": "sha256:" + "0" * 64},
            "old_new_compat": {"FAKE_COMPAT_LABEL": ""},
        }
        for gate, extra in cases.items():
            r = self.ops("gate", "rel-1", gate, "8002", **extra)
            self.assertNotEqual(r.returncode, 0, gate)
        self.assertNotEqual(self.ops("gate", "rel-1", "old_new_compat", "8002", FAKE_READY_PORTS="8002").returncode, 0)
        self.assertIn("UNKNOWN_GATE", self.ops("gate", "rel-1", "bogus", "8002").stderr)

    def test_tenant_gate_is_never_skipped_without_identity(self):
        self.token.unlink()
        self.assertNotEqual(self.ops("gate", "rel-1", "tenant", "8002").returncode, 0)

    def test_read_only_gate_rejects_unsafe_runtime_flags(self):
        (self.state / "container_env").write_text("PANTHEON_LIVE_BROKER_ENABLED=true\n")
        self.assertIn("GATE_READ_ONLY", self.ops("gate", "rel-1", "read_only", "8002").stderr)

    def test_singleton_handoff_keeps_exactly_one_owner(self):
        self.assertEqual(self.ops("activate_singletons", "rel-1").returncode, 0)
        self.assertEqual(self.ops("drain_singletons", "rel-1").returncode, 0)
        self.assertFalse((self.state / "singleton_up").exists())
        self.assertEqual(self.ops("resume_baseline_singletons", "rel-1").returncode, 0)
        dup = self.ops("activate_singletons", "rel-1", FAKE_SINGLETON_COUNT="2")
        self.assertNotEqual(dup.returncode, 0)
        self.assertIn("expected exactly 1", dup.stderr)

    def test_runtime_config_is_external_no_store_and_matches(self):
        (self.root / "fe" / "releases" / "r1").mkdir()
        os.symlink(self.root / "fe" / "releases" / "r1", self.root / "fe" / "current")
        self.assertEqual(self.ops("verify_runtime_config", "rel-1").returncode, 0)
        self.assertIn("RUNTIME_CONFIG_CACHEABLE", self.ops("verify_runtime_config", "rel-1", FAKE_RC_CACHE="public, max-age=60").stderr)
        (self.state / "served_runtime_config").write_text("tampered")
        self.assertIn("RUNTIME_CONFIG_DRIFT", self.ops("verify_runtime_config", "rel-1").stderr)
        self.rc.unlink()
        self.assertIn("RUNTIME_CONFIG_MISSING", self.ops("verify_runtime_config", "rel-1").stderr)

    def test_post_identity_reads_back_real_values_and_missing_fe_fails(self):
        cur = self.root / "fe" / "releases" / "r1"
        cur.mkdir()
        (cur / ".artifact_sha256").write_text("sha256:" + "1" * 64)
        os.symlink(cur, self.root / "fe" / "current")
        r = self.ops("post_identity", "rel-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["frontend_artifact_sha256"], "sha256:" + "1" * 64)
        self.assertEqual(got["operator_bff_image"], self.manifest["backend"]["images"]["operator_bff"])
        (cur / ".artifact_sha256").unlink()
        self.assertNotEqual(self.ops("post_identity", "rel-1").returncode, 0)

    def test_missing_input_unknown_step_and_proxy_failures(self):
        self.assertNotEqual(self.ops("bogus", "rel-1").returncode, 0)
        r = subprocess.run([str(OPS), "baseline_health", "rel-1"], env={**self.env(), "PROD_ROOT": ""}, capture_output=True, text=True)
        self.assertIn("MISSING_INPUT", r.stderr)
        self.assertEqual(self.ops("reload_proxy", "rel-1").returncode, 0)
        self.assertIn("PROXY_VALIDATE", self.ops("reload_proxy", "rel-1", FAKE_DOCKER_FAIL="exec").stderr)


class BootstrapTests(OpsHarness):
    def _env(self, **extra):
        secrets = self.tmp / "secrets.env"; pw = self.tmp / "pgpw"
        for f in (secrets, pw):
            f.write_text("x"); f.chmod(0o600)
        (self.tmp / "data").mkdir(exist_ok=True)
        return self.env(PROD_SECRETS_ENV_FILE=str(secrets), PROD_POSTGRES_PASSWORD_FILE=str(pw), **extra)

    def _run(self, **extra):
        return subprocess.run([str(BOOTSTRAP)], env=self._env(**extra), capture_output=True, text=True, timeout=60)

    def test_bootstrap_installs_503_placeholder_and_no_invented_release(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(json.loads(r.stdout)["rollback_target"])
        self.assertIn("503", (self.root / "proxy" / "bff_upstream.caddy").read_text())
        self.assertFalse((self.root / "fe" / "current").exists())
        rc = (self.root / "config" / "runtime-config.js").read_text()
        self.assertIn("https://api.trading.acme-prod.net", rc)
        self.assertIn("--profile stateful", self.log.read_text())

    def test_bootstrap_never_mounts_or_formats_and_rejects_bad_secrets(self):
        r = self._run(FAKE_MOUNTED="no")
        self.assertIn("DATA_DISK_NOT_MOUNTED", r.stderr)
        text = BOOTSTRAP.read_text()
        for forbidden in ("mkfs", "parted", "mount -", "wipefs"):
            self.assertNotIn(forbidden, text)
        pw = self.tmp / "pgpw"
        self._env()
        pw.chmod(0o644)
        self.assertIn("SECRET_MODE", subprocess.run([str(BOOTSTRAP)], env=self.env(
            PROD_SECRETS_ENV_FILE=str(self.tmp / "secrets.env"), PROD_POSTGRES_PASSWORD_FILE=str(pw)),
            capture_output=True, text=True).stderr)
        pw.write_text("")
        self.assertIn("SECRET_MISSING", subprocess.run([str(BOOTSTRAP)], env=self.env(
            PROD_SECRETS_ENV_FILE=str(self.tmp / "secrets.env"), PROD_POSTGRES_PASSWORD_FILE=str(pw)),
            capture_output=True, text=True).stderr)

    def test_bootstrap_does_not_clobber_existing_upstream(self):
        (self.root / "proxy").mkdir()
        (self.root / "proxy" / "bff_upstream.caddy").write_text("reverse_proxy 127.0.0.1:8001\n")
        self.assertEqual(self._run().returncode, 0)
        self.assertIn("8001", (self.root / "proxy" / "bff_upstream.caddy").read_text())

    def test_stateful_start_failure_is_reported(self):
        self.assertIn("STATEFUL_START", self._run(FAKE_DOCKER_FAIL="compose").stderr)


PROD_ENV = {
    "PROD_GCP_PROJECT_ID": "acme-trading-prod-01",
    "PROD_GCP_ZONE": "asia-east1-b",
    "PROD_WIF_PROVIDER": "projects/1/locations/global/workloadIdentityPools/p/providers/prod",
    "PROD_SERVICE_ACCOUNT": "release@acme-trading-prod-01.iam.gserviceaccount.com",
    "PROD_IAM_POLICY_REF": "iam-policy-v1",
    "PROD_SECRETS_REF": "projects/1/secrets/prod-config",
    "PROD_DATA_DISK": "pantheon-prod-data",
    "PROD_NETWORK": "pantheon-prod-net",
    "PROD_BFF_HOST": "api.trading.acme-prod.net",
    "PROD_FE_HOST": "app.trading.acme-prod.net",
    "PROD_IDP_ISSUER": "https://idp.acme-prod.net",
    "PROD_RPO_SECONDS": "300",
    "PROD_RTO_SECONDS": "1800",
    "PROD_RETENTION_DAYS": "30",
    "PROD_ONCALL_OWNER": "oncall-team",
    "PROD_ROLLBACK_OWNER": "release-owner",
}


def _evidence(manifest: dict, sha: str) -> dict:
    return {
        "status": "staging_verified",
        "release_id": manifest["release_id"],
        "workflow_run_id": "123456",
        "manifest": {
            "manifest_sha256": sha,
            "operator_bff_image": manifest["backend"]["images"]["operator_bff"],
            "frontend_artifact_sha256": manifest["frontend"]["artifact_sha256"],
        },
        "gates": {k: {} for k in (
            "sanitized_data", "migration_and_compatibility", "backup_restore_readability",
            "exact_pair_rollback", "security_and_safety_readbacks", "cleanup_dry_run")},
    }


APPROVALS = [{"state": "approved", "user": {"login": "reviewer-b"}, "environments": [{"name": "production"}]}]


def _fe_tarball(path: Path, marker: str) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = marker.encode()
        info = tarfile.TarInfo("index.html")
        info.size = len(data)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(data))
    path.write_bytes(buf.getvalue())
    return "sha256:" + hashlib.sha256(buf.getvalue()).hexdigest()


class PreflightTests(unittest.TestCase):
    def test_complete_config_passes(self):
        self.assertEqual(prod_preflight.check_config(PROD_ENV)["PROD_GCP_PROJECT_ID"], "acme-trading-prod-01")

    def test_each_missing_value_fails_closed(self):
        for key in prod_preflight.REQUIRED_CONFIG:
            env = {k: v for k, v in PROD_ENV.items() if k != key}
            with self.assertRaises(prod_preflight.PreflightError, msg=key):
                prod_preflight.check_config(env)

    def test_placeholder_retired_and_shared_project_rejected(self):
        for key, bad in (
            ("PROD_ONCALL_OWNER", "TBD"),
            ("PROD_BFF_HOST", "api.example.com"),
            ("PROD_GCP_PROJECT_ID", "pantheon-lupin-dev-20260719"),
            ("PROD_RPO_SECONDS", "0"),
            ("PROD_IDP_ISSUER", "http://idp.acme-prod.net"),
        ):
            with self.assertRaises(prod_preflight.PreflightError, msg=key):
                prod_preflight.check_config({**PROD_ENV, key: bad})
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_config({**PROD_ENV, "STAGING_GCP_PROJECT_ID": "acme-trading-prod-01"})
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_config({**PROD_ENV, "PANTHEON_LIVE_BROKER_ENABLED": "true"})

    def test_same_account_approval_follows_actual_environment_policy(self):
        def policy(prevent, reviewers=("ajoe734",)):
            return {"name": "production", "protection_rules": [{"type": "required_reviewers", "prevent_self_review": prevent,
                    "reviewers": [{"type": "User", "reviewer": {"login": r}} for r in reviewers]}]}
        same = [{"state": "approved", "user": {"login": "ajoe734"}, "environments": [{"name": "production"}]}]
        self.assertEqual(prod_preflight.check_approval(same, "ajoe734", policy(False)), "ajoe734")
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_approval(same, "ajoe734", policy(True))
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_approval(same, "ajoe734")  # strict default without policy
        with self.assertRaises(prod_preflight.PreflightError):  # approver not a required reviewer
            prod_preflight.check_approval(same, "ajoe734", policy(False, ("someone-else",)))
        with self.assertRaises(prod_preflight.PreflightError):  # unapproved state is never accepted
            prod_preflight.check_approval([{**same[0], "state": "rejected"}], "ajoe734", policy(False))
        with self.assertRaises(prod_preflight.PreflightError):  # wrong environment binding
            prod_preflight.check_approval([{**same[0], "environments": [{"name": "staging"}]}], "ajoe734", policy(False))
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_approval(same, "ajoe734", {"name": "production", "protection_rules": []})
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_approval(same, "ajoe734", {"name": "staging"})

    def test_approval_requires_distinct_production_reviewer(self):
        self.assertEqual(prod_preflight.check_approval(APPROVALS, "initiator-a"), "reviewer-b")
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_approval(APPROVALS, "Reviewer-B")
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_approval([{**APPROVALS[0], "state": "rejected"}], "initiator-a")
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_approval([{**APPROVALS[0], "environments": [{"name": "staging"}]}], "x")
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_approval(APPROVALS, "")

    def test_staging_evidence_must_bind_exact_manifest(self):
        manifest = _sample_manifest(state="staging_verified")
        sha = release_manifest.compute_manifest_sha256(manifest)
        prod_preflight.check_staging_evidence(_evidence(manifest, sha), manifest, sha)
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_staging_evidence(_evidence(manifest, "sha256:" + "0" * 64), manifest, sha)
        bad = _evidence(manifest, sha)
        bad["status"] = "staging_failed"
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_staging_evidence(bad, manifest, sha)
        bad = _evidence(manifest, sha)
        del bad["gates"]["exact_pair_rollback"]
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_staging_evidence(bad, manifest, sha)

    def test_baseline_and_bootstrap_semantics(self):
        manifest = _sample_manifest(state="staging_verified")
        rb = manifest["rollback_baseline"]
        good = {"healthy": True, "frontend_artifact_sha256": rb["frontend_artifact_sha256"],
                "operator_bff_image": rb["backend_operator_bff_image"]}
        self.assertEqual(prod_preflight.check_baseline(manifest, bootstrap=False, baseline_state=good), "baseline")
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_baseline(manifest, bootstrap=False, baseline_state=None)
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_baseline(manifest, bootstrap=False, baseline_state={**good, "healthy": False})
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_baseline(manifest, bootstrap=False, baseline_state={**good, "operator_bff_image": "x"})
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_baseline(manifest, bootstrap=True, baseline_state=good)
        no_rb = {k: v for k, v in manifest.items() if k != "rollback_baseline"}
        self.assertEqual(prod_preflight.check_baseline(no_rb, bootstrap=True, baseline_state=None), "bootstrap")
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_baseline(manifest, bootstrap=True, baseline_state=None)


class SwitchHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "prod"
        (self.root / "fe" / "releases").mkdir(parents=True)
        (self.root / "proxy").mkdir()
        (self.root / "state").mkdir()
        self.ops = self.tmp / "ops.sh"
        self.ops.write_text(FAKE_OPS)
        self.ops.chmod(0o755)
        self.log = self.tmp / "ops.log"
        self.identity_file = self.tmp / "identity.json"

    def _prepare(self, *, bootstrap=False, mutate_manifest=None):
        manifest = _sample_manifest(state="staging_verified")
        if bootstrap:
            manifest = {k: v for k, v in manifest.items() if k != "rollback_baseline"}
        artifact = self.tmp / "fe.tar.gz"
        fe_sha = _fe_tarball(artifact, "new")
        manifest["frontend"]["artifact_sha256"] = fe_sha
        if mutate_manifest:
            mutate_manifest(manifest)
        sha = release_manifest.compute_manifest_sha256(manifest)
        mpath = self.tmp / "manifest.json"
        mpath.write_text(json.dumps(manifest))
        epath = self.tmp / "evidence.json"
        epath.write_text(json.dumps(_evidence(manifest, sha)))
        apath = self.tmp / "approvals.json"
        apath.write_text(json.dumps(APPROVALS))
        self.identity_file.write_text(json.dumps({
            "frontend_artifact_sha256": fe_sha,
            "operator_bff_image": manifest["backend"]["images"]["operator_bff"]}))
        if not bootstrap:
            old = self.root / "fe" / "releases" / "old"
            old.mkdir(parents=True, exist_ok=True)
            (old / "index.html").write_text("old")
            os.symlink(old, self.root / "fe" / "current")
            (self.root / "proxy" / "bff_upstream.caddy").write_text("reverse_proxy 127.0.0.1:8001\n")
            rb = manifest["rollback_baseline"]
            (self.root / "state" / "baseline.json").write_text(json.dumps({
                "healthy": True, "frontend_artifact_sha256": rb["frontend_artifact_sha256"],
                "operator_bff_image": rb["backend_operator_bff_image"]}))
        self.manifest, self.sha, self.artifact = manifest, sha, artifact
        return manifest

    def _run(self, *, bootstrap=False, extra_env=None, sha=None, env_override=None):
        env = {**os.environ, **PROD_ENV, "PROD_ROOT": str(self.root), "PROD_OPS_CMD": str(self.ops),
               "RELEASE_ID": self.manifest["release_id"], "RELEASE_MANIFEST_PATH": str(self.tmp / "manifest.json"),
               "EXPECTED_MANIFEST_SHA256": sha or self.sha, "STAGING_EVIDENCE_PATH": str(self.tmp / "evidence.json"),
               "APPROVALS_PATH": str(self.tmp / "approvals.json"), "WORKFLOW_INITIATOR": "initiator-a",
               "FE_ARTIFACT_PATH": str(self.artifact), "PROD_BLUE_PORT": "8001", "PROD_GREEN_PORT": "8002",
               "OPS_LOG": str(self.log), "IDENTITY_FILE": str(self.identity_file),
               "PROD_BOOTSTRAP_FIRST_RELEASE": "true" if bootstrap else "false"}
        env.update(extra_env or {})
        return subprocess.run([str(SWITCH)], env=env, capture_output=True, text=True, timeout=60)

    def _steps(self):
        return [l.split()[0] for l in self.log.read_text().splitlines()] if self.log.exists() else []

    def _current(self):
        return os.readlink(self.root / "fe" / "current")

    def _upstream(self):
        return (self.root / "proxy" / "bff_upstream.caddy").read_text()

    def _assert_baseline_restored(self):
        self.assertTrue(self._current().endswith("/old"))
        self.assertIn("8001", self._upstream())


class SwitchTests(SwitchHarness):
    def test_successful_switch_order_and_state(self):
        rid = self._prepare()["release_id"]
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(self._current().endswith(f"/{rid}"))
        self.assertIn("8002", self._upstream())
        steps = self._steps()
        self.assertEqual(steps.index("snapshot_preflight") < steps.index("start_green") < steps.index("drain_singletons")
                         < steps.index("reload_proxy") < steps.index("post_identity"), True)
        self.assertEqual(steps.count("gate"), 7)
        state = json.loads((self.root / "state" / f"{rid}.json").read_text())
        self.assertEqual(state["release_state"], "prod_active")
        self.assertNotIn("release_complete", json.dumps(state))

    def test_preflight_failures_mutate_nothing(self):
        self._prepare()
        r = self._run(sha="sha256:" + "0" * 64)
        self.assertEqual(r.returncode, 1)
        self.assertIn("PREFLIGHT_FAILED", r.stderr)
        self.assertEqual(self._steps(), [])
        self._assert_baseline_restored()
        (self.tmp / "approvals.json").write_text(json.dumps([{**APPROVALS[0], "user": {"login": "initiator-a"}}]))
        self.assertEqual(self._run().returncode, 1)
        self.assertEqual(self._steps(), [])
        r = self._run(extra_env={"PROD_RPO_SECONDS": ""})
        self.assertEqual(r.returncode, 1)

    def test_fe_artifact_mismatch_and_immutable_dir(self):
        self._prepare()
        self.artifact.write_bytes(b"tampered")
        r = self._run()
        self.assertIn("FE_ARTIFACT_MISMATCH", r.stderr)
        self.assertEqual(self._steps(), [])
        # existing release dir with different content is never overwritten
        self.setUp()
        self._prepare()
        d = self.root / "fe" / "releases" / self.manifest["release_id"]
        d.mkdir()
        (d / ".artifact_sha256").write_text("sha256:" + "9" * 64)
        r = self._run()
        self.assertIn("IMMUTABLE_RELEASE_DIR", r.stderr)
        self._assert_baseline_restored()

    def test_unhealthy_baseline_or_snapshot_blocks_switch(self):
        self._prepare()
        r = self._run(extra_env={"FAIL_STEP": "baseline_health"})
        self.assertIn("BASELINE_UNHEALTHY", r.stderr)
        self.assertNotIn("start_green", self._steps())
        self.log.unlink(missing_ok=True)
        Path(str(self.log) + ".failed").unlink(missing_ok=True)
        r = self._run(extra_env={"FAIL_STEP": "snapshot_preflight"})
        self.assertIn("SNAPSHOT_FAILED", r.stderr)

    def test_every_gate_failure_aborts_before_traffic(self):
        for gate in ("health", "auth", "cors", "tenant", "read_only", "schema", "old_new_compat"):
            with self.subTest(gate=gate):
                self.setUp()
                self._prepare()
                r = self._run(extra_env={"FAIL_STEP": f"gate:{gate}"})
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertIn("GATE_FAILED", r.stderr)
                self.assertNotIn("drain_singletons", self._steps())
                self.assertIn("stop_green", self._steps())
                self._assert_baseline_restored()

    def test_partial_switch_failures_roll_back_fe_bff_and_config(self):
        for fail in ("drain_singletons", "reload_proxy", "activate_singletons", "post_identity"):
            with self.subTest(fail=fail):
                self.setUp()
                self._prepare()
                r = self._run(extra_env={"FAIL_STEP": fail})
                self.assertEqual(r.returncode, 2, r.stderr)
                self._assert_baseline_restored()
                steps = self._steps()
                self.assertIn("resume_baseline_singletons", steps)
                self.assertLess(steps.index("stop_green", steps.index(fail)), steps.index("resume_baseline_singletons"))
                state = json.loads((self.root / "state" / f"{self.manifest['release_id']}.json").read_text())
                self.assertEqual(state["release_state"], "prod_rolled_back")

    def test_post_switch_identity_mismatch_rolls_back(self):
        self._prepare()
        wrong = json.dumps({"frontend_artifact_sha256": "sha256:" + "8" * 64,
                            "operator_bff_image": self.manifest["backend"]["images"]["operator_bff"]})
        r = self._run(extra_env={"IDENTITY_OVERRIDE": wrong})
        self.assertEqual(r.returncode, 2)
        self._assert_baseline_restored()

    def test_runtime_config_failure_after_switch_rolls_back(self):
        self._prepare()
        r = self._run(extra_env={"FAIL_STEP": "verify_runtime_config"})
        self.assertEqual(r.returncode, 2, r.stderr)
        self._assert_baseline_restored()

    def test_colors_alternate_between_releases(self):
        self._prepare()
        self.assertEqual(self._run().returncode, 0)
        baseline = json.loads((self.root / "state" / "baseline.json").read_text())
        self.assertEqual(baseline["active_port"], "8002")
        # Next release must target the other color, never the active port.
        self.log.unlink()
        (self.root / "state" / "baseline.json").write_text(json.dumps({**baseline, "healthy": True,
            "frontend_artifact_sha256": self.manifest["rollback_baseline"]["frontend_artifact_sha256"],
            "operator_bff_image": self.manifest["rollback_baseline"]["backend_operator_bff_image"]}))
        self.assertEqual(self._run().returncode, 0)
        self.assertIn("start_green pantheon-20261024-001 8001", self.log.read_text())

    def test_restoration_failure_is_reported_not_swallowed(self):
        self._prepare()
        r = self._run(extra_env={"FAIL_STEP": "resume_baseline_singletons", "FAIL_ALWAYS": "1"} | {"IDENTITY_OVERRIDE": "{}"})
        self.assertEqual(r.returncode, 3)
        self.assertIn("rollback_failed", r.stdout)
        state = json.loads((self.root / "state" / f"{self.manifest['release_id']}.json").read_text())
        self.assertEqual(state["release_state"], "rollback_failed")

    def test_missing_journal_target_rollback_fails_closed(self):
        self._prepare()
        r = subprocess.run([str(ROLLBACK), "pantheon-20261024-001"], capture_output=True, text=True,
                           env={**os.environ, "PROD_ROOT": str(self.root), "PROD_OPS_CMD": str(self.ops), "OPS_LOG": str(self.log)})
        self.assertEqual(r.returncode, 3)

    def test_bootstrap_has_no_invented_rollback_target(self):
        self._prepare(bootstrap=True)
        r = self._run(bootstrap=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("baseline_health", self._steps())
        self.setUp()
        self._prepare(bootstrap=True)
        r = self._run(bootstrap=True, extra_env={"IDENTITY_OVERRIDE": "{}"})
        self.assertEqual(r.returncode, 4)
        self.assertIn("no_rollback_target", r.stdout)
        self.assertNotIn("resume_baseline_singletons", self._steps())
        state = json.loads((self.root / "state" / f"{self.manifest['release_id']}.json").read_text())
        self.assertEqual(state["release_state"], "prod_aborted")

    def test_first_release_without_bootstrap_declaration_refused(self):
        self._prepare(bootstrap=True)
        r = self._run(bootstrap=False)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._steps(), [])

    def test_port_collision_refused(self):
        self._prepare()
        r = self._run(extra_env={"PROD_GREEN_PORT": "8001"})
        self.assertIn("PORT_COLLISION", r.stderr)


class StaticContractTests(unittest.TestCase):
    def test_workflow_is_inert_and_gated(self):
        text = WORKFLOW.read_text()
        wf = yaml.safe_load(text)
        on = wf.get("on") or wf.get(True)
        self.assertEqual(list(on.keys()), ["workflow_dispatch"])
        self.assertEqual(wf["jobs"]["deploy"]["environment"], "production")
        self.assertEqual(wf["jobs"]["deploy"]["needs"], "preflight")
        self.assertIn("PRODUCTION_RELEASE_ENABLED", text)
        self.assertNotIn("docker build", text)
        self.assertNotIn("npm run build", text)

    def test_compose_has_single_stateful_owner_and_safe_defaults(self):
        compose = yaml.safe_load(COMPOSE.read_text())
        svc = compose["services"]
        self.assertEqual(svc["postgres"]["profiles"], ["stateful"])
        self.assertEqual(svc["nats"]["profiles"], ["stateful"])
        for color in ("blue", "green"):
            s = svc[f"operator-bff-{color}"]
            self.assertEqual(s["pull_policy"], "never")
            self.assertEqual(s["environment"]["PANTHEON_LIVE_BROKER_ENABLED"], "false")
            self.assertEqual(s["environment"]["PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED"], "false")
            self.assertEqual(s["environment"]["PANTHEON_BFF_AUTH_STUB"], "false")
            self.assertEqual(s["environment"]["PANTHEON_BFF_AUTH_MODE"], "strict")
            for unused in ("PANTHEON_REAL_WRITES_ENABLED", "PANTHEON_PROVIDER_ENABLED", "PANTHEON_DEV_STUB_WRITES_ENABLED"):
                self.assertNotIn(unused, s["environment"])
            self.assertNotIn("build", s)
        self.assertNotEqual(svc["operator-bff-blue"]["ports"], svc["operator-bff-green"]["ports"])

    def test_workflow_scopes_prod_vars_to_environment_and_transports_over_iap(self):
        wf = yaml.safe_load(WORKFLOW.read_text())
        self.assertNotIn("env", wf)  # no workflow-global PROD_* values
        pre = wf["jobs"]["preflight"]
        self.assertNotIn("environment", pre)
        self.assertNotIn("vars.PROD_", json.dumps(pre))
        deploy = wf["jobs"]["deploy"]
        self.assertEqual(deploy["environment"], "production")
        self.assertIn("PROD_GCP_PROJECT_ID", deploy["env"])
        names = [st.get("name", "") for st in deploy["steps"]]
        idx = {k: next(i for i, n in enumerate(names) if k in n) for k in ("Full production preflight", "Authenticate", "Transport", "Run atomic")}
        self.assertLess(idx["Full production preflight"], idx["Authenticate"])
        self.assertLess(idx["Authenticate"], idx["Transport"])
        self.assertLess(idx["Transport"], idx["Run atomic"])
        text = WORKFLOW.read_text()
        self.assertIn("compute scp", text)
        self.assertIn("--tunnel-through-iap", text)
        for needed in ("PROD_OPS_CMD", "PROD_ROOT", "PROD_BLUE_PORT", "PROD_GREEN_PORT", "FE_ARTIFACT_PATH",
                       "staging_evidence.json", "approvals.json", "env-policy.json", "prod_ops.sh"):
            self.assertIn(needed, text)
        self.assertNotIn("./scripts/deploy/prod_atomic_switch.sh", text)  # never on the GitHub runner
        self.assertNotIn("PROD_OPS_CMD }}", text)

    def test_compose_core_owner_closure_and_secrets_policy(self):
        compose = yaml.safe_load(COMPOSE.read_text())
        svc = compose["services"]
        for owner in ("runtime-manager", "governance", "deployment", "lifecycle-projector"):
            self.assertEqual(svc[owner]["pull_policy"], "never")
        self.assertEqual(svc["lifecycle-projector"]["profiles"], ["singleton"])
        pg = svc["postgres"]["environment"]
        self.assertIn("POSTGRES_PASSWORD_FILE", pg)
        self.assertNotIn("POSTGRES_PASSWORD", pg)
        self.assertNotIn("trust", COMPOSE.read_text().lower().replace("trust auth", "").replace("never trust", ""))
        self.assertIn("postgres_password", compose["secrets"])

    def test_caddy_serves_external_no_store_runtime_config(self):
        text = CADDY.read_text()
        self.assertIn("/runtime-config.js", text)
        self.assertIn("{$PROD_ROOT}/config", text)
        block = text.split("handle /runtime-config.js", 1)[1].split("handle /deployment.json", 1)[0]
        self.assertIn("no-store", block)

    def test_caddy_uses_replaceable_upstream_and_current_symlink(self):
        text = CADDY.read_text()
        self.assertIn("bff_upstream.caddy", text)
        self.assertIn("fe/current", text)
        for retired in release_manifest.RETIRED_IDENTIFIERS:
            self.assertNotIn(retired, text)


if __name__ == "__main__":
    unittest.main()

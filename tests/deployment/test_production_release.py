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
            self.assertEqual(s["environment"]["PANTHEON_REAL_WRITES_ENABLED"], "false")
            self.assertNotIn("build", s)
        self.assertNotEqual(svc["operator-bff-blue"]["ports"], svc["operator-bff-green"]["ports"])

    def test_caddy_uses_replaceable_upstream_and_current_symlink(self):
        text = CADDY.read_text()
        self.assertIn("bff_upstream.caddy", text)
        self.assertIn("fe/current", text)
        for retired in release_manifest.RETIRED_IDENTIFIERS:
            self.assertNotIn(retired, text)


if __name__ == "__main__":
    unittest.main()

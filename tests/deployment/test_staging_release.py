"""Local controller and contract tests for Ephemeral Staging (`ENV-STG-EPHEMERAL-IMPL-001`).

NOTE: These tests validate local controller behavior, schema/manifest contracts,
sanitization logic, partial-failure compensation, and orphan cleanup protection
only. They do NOT constitute hosted GCP/GitHub Environment drill evidence.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import re
import subprocess
import unittest

import jsonschema
import yaml

from scripts.deploy import release_manifest, staging_sanitize


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "release-manifest.schema.json"
PROVISION_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "staging_provision.sh"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "staging_verify.sh"
DESTROY_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "staging_destroy.sh"
EPHEMERAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "staging-ephemeral-deploy.yml"
ORPHAN_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "staging-orphan-cleanup.yml"
INSTANCE_TEMPLATE = REPO_ROOT / "deploy" / "staging" / "instance-template.json"
COMPOSE_STAGING = REPO_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"


def _sample_manifest(*, release_id: str = "pantheon-20261024-001", state: str = "dev_verified") -> dict:
    fe_commit = "a" * 40
    bff_commit = "b" * 40
    fe_sha = "sha256:" + ("1" * 64)
    bff_img = "asia-east1-docker.pkg.dev/pantheon-stg/containers/operator-bff@sha256:" + ("2" * 64)
    compat_sha = "sha256:" + ("3" * 64)
    mig_sha = "sha256:" + ("4" * 64)
    prev_fe_sha = "sha256:" + ("5" * 64)
    prev_bff_img = "asia-east1-docker.pkg.dev/pantheon-stg/containers/operator-bff@sha256:" + ("6" * 64)
    return release_manifest.build_manifest(
        release_id=release_id,
        release_state=state,
        frontend_commit=fe_commit,
        frontend_artifact_sha256=fe_sha,
        backend_commit=bff_commit,
        operator_bff_image=bff_img,
        compatibility_manifest_sha256=compat_sha,
        migration_set_sha256=mig_sha,
        config_schema_version="pantheon.env-config.v1",
        created_by_workflow="ajoe734/pantheon/actions/runs/40000001",
        created_at="2026-10-24T07:00:00Z",
        dev_verification={
            "status": "dev_verified",
            "verified_at": "2026-10-24T07:30:00Z",
            "workflow_run": "ajoe734/pantheon/actions/runs/40000002",
            "frontend_commit": fe_commit,
            "bff_version_commit": bff_commit,
            "fe_deployment_manifest_sha256": "sha256:" + ("7" * 64),
            "all12_chain_id": "h12-verified-fixture-0001",
            "all12_status": "passed",
        },
        rollback_baseline={
            "previous_release_id": "pantheon-20261023-001",
            "frontend_commit": "c" * 40,
            "frontend_artifact_sha256": prev_fe_sha,
            "backend_commit": "d" * 40,
            "backend_operator_bff_image": prev_bff_img,
        },
    )


def _sample_raw_dataset(mig_sha: str = "sha256:" + ("4" * 64)) -> dict:
    return {
        "metadata": {
            "snapshot_name": "pantheon-nonprod-sanitized-20261024",
            "source_environment": "dev_sanitized_seed",
            "schema_migration_sha256": mig_sha,
            "retention_days": 7,
            "pii_masked": False,
            "secrets_excluded": False,
        },
        "tables": {
            "users": [
                {
                    "id": "usr-101",
                    "email": "alice.operator@real-domain.example.com",
                    "full_name": "Alice Realname",
                    "password_hash": "$2b$12$realProductionOrDevHashValue",
                    "phone": "+886-912-345-678",
                    "national_id": "A123456789",
                    "tenant_id": "tenant-alpha",
                    "role": "operator",
                }
            ],
            "broker_credentials": [
                {
                    "id": "cred-1",
                    "account_id": "DU123456",
                    "broker_name": "ibkr",
                    "encrypted_secret": "sk-secretbrokercredential1234567890",
                    "api_key": "AIzaSySecretKey12345678901234567890",
                }
            ],
            "exchange_api_keys": [
                {
                    "id": "ex-1",
                    "exchange": "binance",
                    "key_id": "k1",
                    "secret_ciphertext": "cipher-secret",
                }
            ],
            "user_sessions": [
                {
                    "id": "sess-1",
                    "user_id": "usr-101",
                    "session_token": "ghp_1234567890abcdefghijklmnopqrstuv",
                    "refresh_token": "refresh-tok",
                    "expires_at": "2026-10-25T00:00:00Z",
                }
            ],
            "system_settings": [
                {
                    "id": "default",
                    "live_broker_enabled": True,
                    "provider_enabled": True,
                    "real_writes_enabled": True,
                    "safe_write_strict": False,
                    "execution_mode": "live",
                }
            ],
            "telemetry_events": [
                {
                    "id": "evt-1",
                    "event_type": "loop_tick",
                    "tenant_id": "tenant-alpha",
                    "raw_payload": {
                        "correlation_id": "corr-001",
                        "token": "secret-jwt-token",
                        "auth": "Bearer secret",
                        "nested": {"broker_secret": "drop-me", "latency_ms": 42},
                    },
                    "created_at": "2026-10-24T07:00:00Z",
                }
            ],
            "tenants": [{"id": "tenant-alpha", "name": "Alpha Tenant", "status": "active"}],
            "rbac_bindings": [{"id": "rb-1", "user_id": "usr-101", "tenant_id": "tenant-alpha", "role": "operator"}],
            "schema_migrations": [{"version": "20261024_01", "applied_at": "2026-10-24T06:00:00Z", "checksum_sha256": mig_sha}],
        },
    }


class ReleaseManifestContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def test_valid_manifest_matches_json_schema_and_promotion_gate(self) -> None:
        manifest = _sample_manifest()
        jsonschema.validate(instance=manifest, schema=self.schema)
        outer_sha = release_manifest.compute_manifest_sha256(manifest)
        res = release_manifest.verify_promotion_eligibility(
            manifest,
            expected_manifest_sha256=outer_sha,
            observed_fe_sha256=manifest["frontend"]["artifact_sha256"],
            observed_bff_digest=manifest["backend"]["images"]["operator_bff"],
        )
        self.assertEqual(res["release_id"], "pantheon-20261024-001")
        self.assertEqual(res["manifest_sha256"], outer_sha)

    def test_rejects_immutable_artifact_mismatch_and_mutable_tags(self) -> None:
        manifest = _sample_manifest()
        outer_sha = release_manifest.compute_manifest_sha256(manifest)

        # Outer manifest checksum mismatch
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable manifest checksum mismatch"):
            release_manifest.verify_promotion_eligibility(
                manifest,
                expected_manifest_sha256="sha256:" + ("9" * 64),
            )

        # Observed FE artifact checksum mismatch
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable FE artifact checksum mismatch"):
            release_manifest.verify_promotion_eligibility(
                manifest,
                expected_manifest_sha256=outer_sha,
                observed_fe_sha256="sha256:" + ("f" * 64),
            )

        # Observed BFF image digest mismatch
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable BFF image digest mismatch"):
            release_manifest.verify_promotion_eligibility(
                manifest,
                expected_manifest_sha256=outer_sha,
                observed_bff_digest="asia-east1-docker.pkg.dev/pantheon-stg/containers/operator-bff@sha256:" + ("e" * 64),
            )

        # Mutable tag (:latest) without @sha256 digest is rejected
        bad_tag = copy.deepcopy(manifest)
        bad_tag["backend"]["images"]["operator_bff"] = "asia-east1-docker.pkg.dev/pantheon-stg/containers/operator-bff:latest"
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable digest reference"):
            release_manifest.validate_manifest(bad_tag)

    def test_rejects_unverified_release_and_unaccepted_all12_chain_relabeling(self) -> None:
        unverified = _sample_manifest(state="candidate_built")
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "only 'dev_verified' releases"):
            release_manifest.verify_promotion_eligibility(unverified)

        # Relabeling current unaccepted all12 chain h12-01a11-20261023-9aeb as dev_verified must fail closed
        relabeled = _sample_manifest()
        relabeled["dev_verification"]["all12_chain_id"] = "h12-01a11-20261023-9aeb"
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "h12-01a11-20261023-9aeb"):
            release_manifest.validate_manifest(relabeled)

        # Non-passed all12_status must fail closed
        failed_all12 = _sample_manifest()
        failed_all12["dev_verification"]["all12_status"] = "failed"
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "all12_status must be 'passed'"):
            release_manifest.validate_manifest(failed_all12)

    def test_rejects_unsafe_defaults_and_retired_identifiers(self) -> None:
        for flag in (
            "provider_enabled",
            "live_broker_enabled",
            "real_writes_enabled",
            "control_vm_broker_credentials_present",
        ):
            unsafe = _sample_manifest()
            unsafe["safety_defaults"][flag] = True
            with self.assertRaisesRegex(release_manifest.ReleaseManifestError, f"safety_defaults.{flag}"):
                release_manifest.validate_manifest(unsafe)

        for retired in ("pantheon-lupin-dev-20260719", "pantheon-benjamin-20260528", "104.155.223.192"):
            bad = _sample_manifest()
            bad["config_schema_version"] = f"v1-{retired}"
            with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "retired"):
                release_manifest.validate_manifest(bad)


class StagingSanitizeAndGateTests(unittest.TestCase):
    def test_sanitize_dataset_masks_pii_strips_secrets_and_verifies(self) -> None:
        raw = _sample_raw_dataset()
        sanitized = staging_sanitize.sanitize_dataset(raw)
        result = staging_sanitize.verify_sanitized_dataset(sanitized)
        self.assertTrue(result["sanitized_verified"])
        self.assertEqual(sanitized["tables"]["broker_credentials"], [])
        self.assertEqual(sanitized["tables"]["exchange_api_keys"], [])
        self.assertEqual(sanitized["tables"]["user_sessions"], [])
        user = sanitized["tables"]["users"][0]
        self.assertEqual(user["email"], "staging-user-usr-101@pantheon.internal")
        self.assertEqual(user["full_name"], "Staging Persona usr-101")
        self.assertIsNone(user["phone"])
        self.assertIsNone(user["national_id"])
        settings = sanitized["tables"]["system_settings"][0]
        self.assertFalse(settings["live_broker_enabled"])
        self.assertFalse(settings["provider_enabled"])
        self.assertFalse(settings["real_writes_enabled"])
        self.assertEqual(settings["execution_mode"], "paper")
        payload = sanitized["tables"]["telemetry_events"][0]["raw_payload"]
        self.assertNotIn("token", payload)
        self.assertNotIn("auth", payload)
        self.assertNotIn("broker_secret", payload["nested"])
        self.assertEqual(payload["nested"]["latency_ms"], 42)

    def test_rejects_production_data_and_unknown_schema_inputs_fail_closed(self) -> None:
        prod_input = _sample_raw_dataset()
        prod_input["metadata"]["source_environment"] = "production"
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "production data is strictly forbidden"):
            staging_sanitize.sanitize_dataset(prod_input)

        unknown_table = _sample_raw_dataset()
        unknown_table["tables"]["secret_live_orders"] = [{"id": "1"}]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "unknown table"):
            staging_sanitize.sanitize_dataset(unknown_table)

        unknown_col = _sample_raw_dataset()
        unknown_col["tables"]["users"][0]["ssn_raw"] = "000-11-2222"
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "unknown column"):
            staging_sanitize.sanitize_dataset(unknown_col)

    def test_migration_backup_rollback_and_security_readback_verifiers(self) -> None:
        mig_sha = "sha256:" + ("4" * 64)
        valid_plan = {
            "expected_migration_set_sha256": mig_sha,
            "observed_migration_set_sha256": mig_sha,
            "phase": "expand",
            "statements": ["ALTER TABLE users ADD COLUMN locale TEXT NULL;"],
            "old_app_schema": {
                "users": {"id": {"type": "text"}, "email": {"type": "text"}},
            },
            "new_schema": {
                "users": {
                    "id": {"type": "text"},
                    "email": {"type": "text"},
                    "locale": {"type": "text", "nullable": True, "has_default": False},
                },
            },
            "forward_migration_applied": True,
            "old_app_smoke_against_new_schema_passed": True,
        }
        self.assertTrue(
            staging_sanitize.verify_migration_and_compatibility(valid_plan)["migration_verified"]
        )

        # Destructive SQL in expand phase fails closed
        destructive_plan = copy.deepcopy(valid_plan)
        destructive_plan["statements"] = ["ALTER TABLE users DROP COLUMN email;"]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "destructive SQL forbidden"):
            staging_sanitize.verify_migration_and_compatibility(destructive_plan)

        # Non-nullable new column without default breaks old-app compatibility
        incompatible_plan = copy.deepcopy(valid_plan)
        incompatible_plan["new_schema"]["users"]["locale"] = {
            "type": "text",
            "nullable": False,
            "has_default": False,
        }
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "neither nullable nor defaulted"):
            staging_sanitize.verify_migration_and_compatibility(incompatible_plan)

        # Backup restore readability
        backup_record = {
            "backup_id": "stg-backup-20261024-001",
            "backup_sha256": "sha256:" + ("8" * 64),
            "expected_migration_set_sha256": mig_sha,
            "restored_migration_set_sha256": mig_sha,
            "restore_completed": True,
            "read_query_verified": True,
            "restored_row_counts": {
                "users": 1,
                "tenants": 1,
                "broker_credentials": 0,
                "exchange_api_keys": 0,
                "user_sessions": 0,
            },
        }
        self.assertTrue(
            staging_sanitize.verify_backup_restore_readability(backup_record)["backup_restore_verified"]
        )

        # Exact-pair rollback
        manifest = _sample_manifest()
        baseline = manifest["rollback_baseline"]
        rollback_readback = {
            "served_release_id": baseline["previous_release_id"],
            "served_frontend_commit": baseline["frontend_commit"],
            "served_frontend_artifact_sha256": baseline["frontend_artifact_sha256"],
            "served_backend_commit": baseline["backend_commit"],
            "fe_manifest_paired_backend_commit": baseline["backend_commit"],
            "served_operator_bff_image": baseline["backend_operator_bff_image"],
            "bff_healthy": True,
            "singleton_worker_owner_unique": True,
            "rollback_duration_seconds": 38,
        }
        self.assertTrue(
            staging_sanitize.verify_exact_pair_rollback(baseline, rollback_readback)["exact_pair_rollback_verified"]
        )

        # HybridFE/BFF rollback fails closed
        hybrid_readback = dict(rollback_readback, fe_manifest_paired_backend_commit="f" * 40)
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "hybrid state"):
            staging_sanitize.verify_exact_pair_rollback(baseline, hybrid_readback)

        # Security and safety readbacks
        sec_readback = {
            "tenant_isolation_enforced": True,
            "cross_tenant_read_rejected": True,
            "cross_tenant_write_rejected": True,
            "unauthenticated_rejected": True,
            "viewer_mutation_rejected": True,
            "role_map_strict": True,
            "provider_enabled": False,
            "real_writes_enabled": False,
            "dev_stub_writes_enabled": False,
            "live_broker_enabled": False,
            "control_vm_broker_credentials_present": False,
            "vite_bff_mode": "live",
            "vite_bff_fallback": "strict",
            "execution_mode": "paper",
        }
        self.assertTrue(
            staging_sanitize.verify_security_and_safety_readbacks(sec_readback)["security_and_safety_readback_verified"]
        )


class StagingControllerLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.state_dir = self.tmp_path / "state"
        self.manifest = _sample_manifest()
        self.manifest_path = self.tmp_path / "release-manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.manifest_sha = release_manifest.compute_manifest_sha256(self.manifest)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _base_env(self, **overrides: str) -> dict[str, str]:
        env = {
            **os.environ,
            "STAGING_GCP_PROJECT_ID": "pantheon-stg-nonprod-fixture",
            "STAGING_GCP_ZONE": "asia-east1-b",
            "STAGING_INSTANCE_TEMPLATE": "pantheon-stg-template-v1",
            "STAGING_SERVICE_ACCOUNT": "pantheon-stg-deployer@pantheon-stg-nonprod-fixture.iam.gserviceaccount.com",
            "STAGING_SANITIZED_SNAPSHOT": "pantheon-nonprod-sanitized-20261024",
            "RELEASE_MANIFEST_PATH": str(self.manifest_path),
            "EXPECTED_MANIFEST_SHA256": self.manifest_sha,
            "WORKFLOW_RUN_ID": "500001",
            "STAGING_STATE_DIR": str(self.state_dir),
            "STAGING_DRY_RUN": "false",
            "STAGING_NOW_EPOCH": "1792828800",
        }
        env.update(overrides)
        return env

    def test_configuration_and_retired_project_rejection(self) -> None:
        # Missing project ID fails closed
        res = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_GCP_PROJECT_ID=""),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("MISSING_REQUIRED_CONFIG", res.stderr)

        # Retired project ID fails closed
        res_retired = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_GCP_PROJECT_ID="pantheon-lupin-dev-20260719"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_retired.returncode, 0)
        self.assertIn("RETIRED_IDENTIFIER_FORBIDDEN", res_retired.stderr)

        # Excessive TTL (> 86400s) fails closed
        res_ttl = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_TTL_SECONDS="90000"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_ttl.returncode, 0)
        self.assertIn("INVALID_TTL", res_ttl.stderr)

    def test_idempotency_and_conflicting_lease_rejection(self) -> None:
        env = self._base_env()
        first = subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        first_json = json.loads(first.stdout)
        self.assertEqual(first_json["status"], "active")
        self.assertEqual(first_json["resource_count"], 5)

        # Same release_id + same workflow_run_id -> idempotent re-entry, no duplicate resources
        second = subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        second_json = json.loads(second.stdout)
        self.assertEqual(second_json["status"], "already_provisioned")
        self.assertTrue(second_json["idempotent_reentry"])
        inv = json.loads((self.state_dir / "inventory" / "pantheon-20261024-001.json").read_text(encoding="utf-8"))
        self.assertEqual(len(inv["resources"]), 5)

        # Same release_id + different workflow_run_id -> conflicting lease error
        conflict = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(WORKFLOW_RUN_ID="500002"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("CONFLICTING_RELEASE_LEASE", conflict.stderr)

    def test_partial_create_failure_cleans_up_allocated_resources_and_preserves_evidence(self) -> None:
        fail_env = self._base_env(STAGING_INJECT_FAIL_STAGE="create_vm")
        failed = subprocess.run([str(PROVISION_SCRIPT)], env=fail_env, capture_output=True, text=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", failed.stderr)

        inv_path = self.state_dir / "inventory" / "pantheon-20261024-001.json"
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        # IP, data disk, and boot disk were created prior to VM failure and must all be cleaned up (0 remaining)
        self.assertEqual(inv["resources"], [])
        ev_path = self.state_dir / "evidence" / "pantheon-20261024-001-teardown-evidence.json"
        self.assertTrue(ev_path.exists())
        ev = json.loads(ev_path.read_text(encoding="utf-8"))
        self.assertEqual(ev["reason"], "partial_create_failure")
        self.assertEqual(len(ev["inventory_snapshot"]), 3)

    def test_inventory_ownership_protection_and_orphan_expiration(self) -> None:
        # 1. Orphan sweep is inert when STAGING_ORPHAN_CLEANUP_ENABLED != true
        inert = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "orphan-sweep"],
            env=self._base_env(STAGING_ORPHAN_CLEANUP_ENABLED="false"),
            capture_output=True,
            text=True,
            check=True,
        )
        inert_json = json.loads(inert.stdout)
        self.assertEqual(inert_json["status"], "inert_disabled")
        self.assertEqual(inert_json["mutated_resources"], 0)

        # 2. Provision staging release and inject an unlabeled/dev resource into the inventory
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        inv_path = self.state_dir / "inventory" / "pantheon-20261024-001.json"
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        inv["resources"].append({
            "type": "instance",
            "name": "pantheon-dev-deploy",
            "project_id": "pantheon-dev-20260902",
            "zone": "asia-east1-b",
            "labels": {"environment": "dev"},
            "state": "provisioned",
        })
        inv_path.write_text(json.dumps(inv, indent=2), encoding="utf-8")

        # 3. Before TTL expiration (now=1792828900 < expires=1792836000), orphan sweep retains active staging resources
        before_exp = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "orphan-sweep"],
            env=self._base_env(STAGING_ORPHAN_CLEANUP_ENABLED="true", STAGING_NOW_EPOCH="1792828900"),
            capture_output=True,
            text=True,
            check=True,
        )
        before_json = json.loads(before_exp.stdout)
        self.assertEqual(before_json["deleted_resources"], [])
        self.assertEqual(len(before_json["retained_active_resources"]), 5)
        self.assertEqual(len(before_json["skipped_unowned_resources"]), 1)
        self.assertEqual(before_json["skipped_unowned_resources"][0]["name"], "pantheon-dev-deploy")

        # 4. After TTL expiration (now=1792836001 > expires=1792836000), orphan sweep deletes ONLY owned staging resources
        after_exp = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "orphan-sweep"],
            env=self._base_env(STAGING_ORPHAN_CLEANUP_ENABLED="true", STAGING_NOW_EPOCH="1792836001"),
            capture_output=True,
            text=True,
            check=True,
        )
        after_json = json.loads(after_exp.stdout)
        self.assertEqual(len(after_json["deleted_resources"]), 5)
        self.assertEqual(len(after_json["skipped_unowned_resources"]), 1)
        remaining_inv = json.loads(inv_path.read_text(encoding="utf-8"))
        self.assertEqual(len(remaining_inv["resources"]), 1)
        self.assertEqual(remaining_inv["resources"][0]["name"], "pantheon-dev-deploy")

    def test_two_consecutive_simulated_releases_zero_to_zero_and_delayed_upload(self) -> None:
        mig_sha = self.manifest["migration_set_sha256"]
        sanitized = staging_sanitize.sanitize_dataset(_sample_raw_dataset(mig_sha))
        sanitized_path = self.tmp_path / "sanitized.json"
        sanitized_path.write_text(json.dumps(sanitized), encoding="utf-8")

        mig_plan_path = self.tmp_path / "migration-plan.json"
        mig_plan_path.write_text(
            json.dumps({
                "expected_migration_set_sha256": mig_sha,
                "observed_migration_set_sha256": mig_sha,
                "phase": "expand",
                "statements": ["ALTER TABLE users ADD COLUMN staging_note TEXT NULL;"],
                "old_app_schema": {"users": {"id": {"type": "text"}}},
                "new_schema": {
                    "users": {
                        "id": {"type": "text"},
                        "staging_note": {"type": "text", "nullable": True, "has_default": False},
                    }
                },
                "forward_migration_applied": True,
                "old_app_smoke_against_new_schema_passed": True,
            }),
            encoding="utf-8",
        )

        backup_path = self.tmp_path / "backup.json"
        backup_path.write_text(
            json.dumps({
                "backup_id": "stg-bak-001",
                "backup_sha256": "sha256:" + ("8" * 64),
                "expected_migration_set_sha256": mig_sha,
                "restored_migration_set_sha256": mig_sha,
                "restore_completed": True,
                "read_query_verified": True,
                "restored_row_counts": {"users": 1, "tenants": 1, "broker_credentials": 0},
            }),
            encoding="utf-8",
        )

        sec_path = self.tmp_path / "security.json"
        sec_path.write_text(
            json.dumps({
                "tenant_isolation_enforced": True,
                "cross_tenant_read_rejected": True,
                "cross_tenant_write_rejected": True,
                "unauthenticated_rejected": True,
                "viewer_mutation_rejected": True,
                "role_map_strict": True,
                "provider_enabled": False,
                "real_writes_enabled": False,
                "dev_stub_writes_enabled": False,
                "live_broker_enabled": False,
                "control_vm_broker_credentials_present": False,
                "vite_bff_mode": "live",
                "vite_bff_fallback": "strict",
                "execution_mode": "paper",
            }),
            encoding="utf-8",
        )

        for idx, rel_id in enumerate(("pantheon-20261024-001", "pantheon-20261024-002"), start=1):
            manifest = _sample_manifest(release_id=rel_id)
            m_path = self.tmp_path / f"{rel_id}.json"
            m_path.write_text(json.dumps(manifest), encoding="utf-8")
            m_sha = release_manifest.compute_manifest_sha256(manifest)
            baseline = manifest["rollback_baseline"]
            rb_path = self.tmp_path / f"{rel_id}-rb.json"
            rb_path.write_text(
                json.dumps({
                    "served_release_id": baseline["previous_release_id"],
                    "served_frontend_commit": baseline["frontend_commit"],
                    "served_frontend_artifact_sha256": baseline["frontend_artifact_sha256"],
                    "served_backend_commit": baseline["backend_commit"],
                    "fe_manifest_paired_backend_commit": baseline["backend_commit"],
                    "served_operator_bff_image": baseline["backend_operator_bff_image"],
                    "bff_healthy": True,
                    "singleton_worker_owner_unique": True,
                    "rollback_duration_seconds": 25,
                }),
                encoding="utf-8",
            )
            env = self._base_env(
                RELEASE_ID=rel_id,
                RELEASE_MANIFEST_PATH=str(m_path),
                EXPECTED_MANIFEST_SHA256=m_sha,
                WORKFLOW_RUN_ID=f"60000{idx}",
                SANITIZED_DATASET_PATH=str(sanitized_path),
                MIGRATION_PLAN_PATH=str(mig_plan_path),
                BACKUP_RESTORE_RECORD_PATH=str(backup_path),
                ROLLBACK_READBACK_PATH=str(rb_path),
                SECURITY_READBACK_PATH=str(sec_path),
                STAGING_DELAYED_UPLOAD_SIMULATE="true" if idx == 2 else "false",
            )
            subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
            ver = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(ver.stdout)["status"], "staging_verified")
            destroy = subprocess.run(
                [str(DESTROY_SCRIPT), "--mode", "teardown", "--result", "success", "--reason", "completed"],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            d_json = json.loads(destroy.stdout)
            self.assertEqual(d_json["status"], "destroyed")
            self.assertEqual(d_json["lingering_owned_resources"], 0)
            if idx == 2:
                self.assertEqual(d_json["upload_status"], "recovered_after_retry")


class WorkflowAndTemplateContractTests(unittest.TestCase):
    def test_workflows_and_templates_parse_and_have_no_retired_identifiers(self) -> None:
        for wf_path in (EPHEMERAL_WORKFLOW, ORPHAN_WORKFLOW):
            raw = wf_path.read_text(encoding="utf-8")
            for retired in (
                "pantheon-lupin-dev-20260719",
                "pantheon-benjamin-20260528",
                "35.201.204.12",
                "35.201.239.38",
                "34.81.75.241",
                "104.155.223.192",
                "/home/lupin",
            ):
                self.assertNotIn(retired, raw)
            parsed = yaml.safe_load(raw)
            triggers = parsed.get("on", parsed.get(True))
            self.assertNotIn("push", triggers)
            self.assertNotIn("pull_request", triggers)
            for job in parsed["jobs"].values():
                for step in job.get("steps", []):
                    if "run" in step:
                        body = re.sub(r"\$\{\{.*?\}\}", "fixture_val", step["run"])
                        chk = subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True)
                        self.assertEqual(chk.returncode, 0, chk.stderr)

        tpl = json.loads(INSTANCE_TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(tpl["properties"]["machineType"], "e2-standard-2")
        self.assertFalse(tpl["properties"]["scheduling"]["preemptible"])
        self.assertEqual(tpl["properties"]["scheduling"]["provisioningModel"], "STANDARD")

        compose = yaml.safe_load(COMPOSE_STAGING.read_text(encoding="utf-8"))
        bff = compose["services"]["operator-bff"]
        self.assertEqual(bff["pull_policy"], "never")
        self.assertNotIn("build", bff)
        self.assertEqual(bff["environment"]["PANTHEON_LIVE_BROKER_ENABLED"], "false")
        self.assertEqual(bff["environment"]["PANTHEON_PROVIDER_ENABLED"], "false")
        self.assertEqual(bff["environment"]["PANTHEON_REAL_WRITES_ENABLED"], "false")


if __name__ == "__main__":
    unittest.main()

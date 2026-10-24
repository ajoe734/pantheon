"""Local controller and contract tests for Ephemeral Staging (`ENV-STG-EPHEMERAL-IMPL-001`).

NOTE: These tests validate local controller behavior, schema/manifest contracts,
sanitization logic, live `gcloud` CLI invocation/error propagation, cross-run
cloud orphan discovery, valid GCP labels, artifact checksum verification,
no-live-success-from-dryrun enforcement, and workflow `actionlint` compliance.
They do NOT constitute hosted GCP/GitHub Environment drill evidence.
"""
from __future__ import annotations

import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile
import tempfile
import unittest

import jsonschema
import yaml

from scripts.deploy import prod_preflight, release_manifest, staging_sanitize


REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = REPO_ROOT / "schemas" / "release-manifest.schema.json"
PROVISION_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "staging_provision.sh"
OPS_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "staging_ops.sh"
VERIFY_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "staging_verify.sh"
DESTROY_SCRIPT = REPO_ROOT / "scripts" / "deploy" / "staging_destroy.sh"
EPHEMERAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "staging-ephemeral-deploy.yml"
ORPHAN_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "staging-orphan-cleanup.yml"
INSTANCE_TEMPLATE = REPO_ROOT / "deploy" / "staging" / "instance-template.json"
COMPOSE_STAGING = REPO_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
ACTIONLINT_CANDIDATES = (
    Path("/tmp/pantheon-env-current-20261024/tools/actionlint"),
)


FAKE_GCLOUD_SCRIPT = r"""#!/usr/bin/env python3
import hashlib
import json
import os
import shutil
import sys
from pathlib import Path

state_file = Path(os.environ["GCLOUD_STATE_FILE"])
log_file = Path(os.environ["GCLOUD_LOG_FILE"])

args = [a for a in sys.argv[1:] if a != "--format=json" and a != "--quiet"]
log_file.parent.mkdir(parents=True, exist_ok=True)
with log_file.open("a", encoding="utf-8") as lf:
    lf.write(" ".join(sys.argv[1:]) + "\n")

fail_on = os.environ.get("GCLOUD_FAIL_ON", "").strip()
if fail_on and fail_on in " ".join(sys.argv[1:]):
    print(f"simulated gcloud failure on {fail_on}", file=sys.stderr)
    raise SystemExit(1)

if os.environ.get("GCLOUD_RETURN_EMPTY_IDENTITY") == "true" and "create" in args:
    print("{}")
    raise SystemExit(0)

if state_file.exists():
    state = json.loads(state_file.read_text(encoding="utf-8"))
else:
    state = {
        "addresses": [],
        "disks": [],
        "instances": [],
        "iam_bindings": [],
        "images": {},
        "gcs_blobs": {},
        "vm_Verify_bundles": {},
    }


def save_state() -> None:
    state_file.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def parse_flag(prefix: str, default: str = "") -> str:
    for idx, tok in enumerate(args):
        if tok.startswith(prefix + "="):
            return tok.split("=", 1)[1]
        if tok == prefix and idx + 1 < len(args):
            return args[idx + 1]
    return default


def parse_multi_flag(prefix: str) -> list[str]:
    vals = []
    for idx, tok in enumerate(args):
        if tok.startswith(prefix + "="):
            vals.append(tok.split("=", 1)[1])
        elif tok == prefix and idx + 1 < len(args):
            vals.append(args[idx + 1])
    return vals


def parse_labels(raw: str) -> dict[str, str]:
    if not raw:
        return {}
    out = {}
    for pair in raw.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            out[k] = v
    return out


if args[:3] == ["artifacts", "docker", "images"] and args[3] == "describe":
    img_ref = args[4]
    override_digest = os.environ.get("GCLOUD_OVERRIDE_BFF_DIGEST", "").strip()
    if override_digest:
        digest = override_digest
    elif "@sha256:" in img_ref:
        digest = "sha256:" + img_ref.split("@sha256:", 1)[1]
    else:
        digest = state.get("images", {}).get(img_ref, "")
    print(json.dumps({"image_summary": {"digest": digest}, "name": img_ref}))
    raise SystemExit(0)

if args[:2] == ["storage", "cp"]:
    src, dst = args[2], args[3]
    blob_path = state.get("gcs_blobs", {}).get(src)
    if not blob_path or not Path(blob_path).exists():
        print(f"GCS object not found: {src}", file=sys.stderr)
        raise SystemExit(1)
    Path(dst).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(blob_path, dst)
    print(json.dumps({"copied": src, "to": dst}))
    raise SystemExit(0)

if args[:3] == ["compute", "addresses", "create"]:
    name = args[3]
    project = parse_flag("--project")
    region = parse_flag("--region")
    labels = parse_labels(parse_flag("--labels"))
    ip_val = f"203.0.113.{10 + len(state['addresses'])}"
    obj = {
        "id": str(1000 + len(state["addresses"])),
        "name": name,
        "project_id": project,
        "region": region,
        "address": ip_val,
        "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/addresses/{name}",
        "labels": labels,
        "status": "RESERVED",
    }
    state["addresses"] = [a for a in state["addresses"] if a["name"] != name] + [obj]
    save_state()
    print(json.dumps(obj))
    raise SystemExit(0)

if args[:3] == ["compute", "addresses", "list"]:
    print(json.dumps(state["addresses"]))
    raise SystemExit(0)

if args[:3] == ["compute", "addresses", "delete"]:
    name = args[3]
    if os.environ.get("GCLOUD_SIMULATE_LINGERING_DELETE") != "true":
        state["addresses"] = [a for a in state["addresses"] if a["name"] != name]
        save_state()
    print(json.dumps({"deleted": name}))
    raise SystemExit(0)

if args[:3] == ["compute", "disks", "create"]:
    name = args[3]
    project = parse_flag("--project")
    zone = parse_flag("--zone")
    labels = parse_labels(parse_flag("--labels"))
    obj = {
        "id": str(2000 + len(state["disks"])),
        "name": name,
        "project_id": project,
        "zone": zone,
        "sourceSnapshot": parse_flag("--source-snapshot"),
        "sizeGb": parse_flag("--size"),
        "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/zones/{zone}/disks/{name}",
        "labels": labels,
        "status": "READY",
    }
    state["disks"] = [d for d in state["disks"] if d["name"] != name] + [obj]
    save_state()
    print(json.dumps(obj))
    raise SystemExit(0)

if args[:3] == ["compute", "disks", "list"]:
    print(json.dumps(state["disks"]))
    raise SystemExit(0)

if args[:3] == ["compute", "disks", "delete"]:
    name = args[3]
    if os.environ.get("GCLOUD_SIMULATE_LINGERING_DELETE") != "true":
        state["disks"] = [d for d in state["disks"] if d["name"] != name]
        save_state()
    print(json.dumps({"deleted": name}))
    raise SystemExit(0)

if args[:3] == ["compute", "instances", "create"]:
    name = args[3]
    project = parse_flag("--project")
    zone = parse_flag("--zone")
    address = parse_flag("--address")
    labels = parse_labels(parse_flag("--labels"))
    disk_specs = parse_multi_flag("--disk")
    disks_attached = []
    for spec in disk_specs:
        kv = dict(item.split("=", 1) for item in spec.split(",") if "=" in item)
        dname = kv.get("name", "")
        disks_attached.append({
            "name": dname,
            "deviceName": kv.get("device-name", dname),
            "boot": kv.get("boot") == "yes",
            "autoDelete": kv.get("auto-delete") == "yes",
            "source": f"https://www.googleapis.com/compute/v1/projects/{project}/zones/{zone}/disks/{dname}",
        })
    obj = {
        "id": str(3000 + len(state["instances"])),
        "name": name,
        "project_id": project,
        "zone": zone,
        "machineType": parse_flag("--machine-type", "e2-standard-2"),
        "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/zones/{zone}/instances/{name}",
        "labels": labels,
        "disks": disks_attached,
        "networkInterfaces": [
            {
                "accessConfigs": [{"natIP": address}],
            }
        ],
        "status": "RUNNING",
    }
    state["instances"] = [i for i in state["instances"] if i["name"] != name] + [obj]
    save_state()
    print(json.dumps(obj))
    raise SystemExit(0)

if args[:3] == ["compute", "instances", "list"]:
    print(json.dumps(state["instances"]))
    raise SystemExit(0)

if args[:3] == ["compute", "instances", "delete"]:
    name = args[3]
    if os.environ.get("GCLOUD_SIMULATE_LINGERING_DELETE") != "true":
        state["instances"] = [i for i in state["instances"] if i["name"] != name]
        save_state()
    print(json.dumps({"deleted": name}))
    raise SystemExit(0)

if args[:2] == ["projects", "get-iam-policy"]:
    print(json.dumps({"bindings": state.get("iam_bindings", [])}))
    raise SystemExit(0)

if args[:2] == ["projects", "add-iam-policy-binding"]:
    member = parse_flag("--member")
    role = parse_flag("--role")
    bindings = state.setdefault("iam_bindings", [])
    found = False
    for b in bindings:
        if b.get("role") == role:
            if member not in b.setdefault("members", []):
                b["members"].append(member)
            found = True
            break
    if not found:
        bindings.append({"role": role, "members": [member]})
    save_state()
    print(json.dumps({"bindings": bindings}))
    raise SystemExit(0)

if args[:2] == ["projects", "remove-iam-policy-binding"]:
    member = parse_flag("--member")
    role = parse_flag("--role")
    bindings = state.setdefault("iam_bindings", [])
    for b in bindings:
        if b.get("role") == role and member in b.get("members", []):
            b["members"].remove(member)
    state["iam_bindings"] = [b for b in bindings if b.get("members")]
    save_state()
    print(json.dumps({"bindings": state["iam_bindings"]}))
    raise SystemExit(0)

if args[:2] == ["compute", "scp"]:
    # Record transferred files on the target VM state
    target = args[-1]
    vm_name = target.split(":", 1)[0]
    files = [a for a in args[2:-1] if not a.startswith("--")]
    manifest_file = next((f for f in files if f.endswith(".json")), None)
    fe_tar = next((f for f in files if f.endswith(".tar.gz")), None)
    if manifest_file and fe_tar:
        m_data = json.loads(Path(manifest_file).read_text(encoding="utf-8"))
        fe_sha = "sha256:" + hashlib.sha256(Path(fe_tar).read_bytes()).hexdigest()
        rel_id = m_data["release_id"]
        mig_sha = m_data["migration_set_sha256"]
        rb = m_data["rollback_baseline"]
        observed_deploy = {
            "observed_from_live_vm": True,
            "dry_run": False,
            "rebuilt_during_promotion": False,
            "release_id": rel_id,
            "frontend_commit": m_data["frontend"]["commit"],
            "frontend_artifact_sha256": fe_sha,
            "backend_commit": m_data["backend"]["commit"],
            "operator_bff_image": m_data["backend"]["images"]["operator_bff"],
            "data_disk_mounted": True,
            "bff_healthy": True,
        }
        state.setdefault("vm_verify_bundles", {})[vm_name] = {
            "observed_from_live_vm": True,
            "dry_run": False,
            "observed_deploy": observed_deploy,
            "sanitized_dataset": state.get("default_sanitized_dataset", {}),
            "migration_plan": {
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
            },
            "backup_restore_record": {
                "backup_id": f"stg-bak-{rel_id}",
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
            },
            "rollback_readback": {
                "served_release_id": rb["previous_release_id"],
                "served_frontend_commit": rb["frontend_commit"],
                "served_frontend_artifact_sha256": rb["frontend_artifact_sha256"],
                "served_backend_commit": rb["backend_commit"],
                "fe_manifest_paired_backend_commit": rb["backend_commit"],
                "served_operator_bff_image": rb["backend_operator_bff_image"],
                "bff_healthy": True,
                "singleton_worker_owner_unique": True,
                "rollback_duration_seconds": 24,
            },
            "security_readback": {
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
            },
        }
        save_state()
    print(json.dumps({"scp": "ok"}))
    raise SystemExit(0)

if args[:2] == ["compute", "ssh"]:
    vm_name = args[2]
    cmd = parse_flag("--command")
    bundle = state.get("vm_verify_bundles", {}).get(vm_name)
    if not bundle:
        print(f"No deployed bundle on VM {vm_name}", file=sys.stderr)
        raise SystemExit(1)
    if "pantheon-staging-collect-gates" in cmd:
        print(json.dumps(bundle, sort_keys=True))
    else:
        print(json.dumps(bundle["observed_deploy"], sort_keys=True))
    raise SystemExit(0)

print(f"Unsupported fake gcloud command: {args}", file=sys.stderr)
raise SystemExit(1)
"""


def _build_fe_tarball(path: Path, content: str = "<!doctype html><title>Pantheon Staging</title>") -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        raw = content.encode("utf-8")
        info = tarfile.TarInfo("index.html")
        info.size = len(raw)
        info.mtime = 0
        tar.addfile(info, io.BytesIO(raw))
    data = buf.getvalue()
    path.write_bytes(data)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sample_manifest(
    *,
    release_id: str = "pantheon-20261024-001",
    state: str = "dev_verified",
    fe_sha: str | None = None,
) -> dict:
    fe_commit = "a" * 40
    bff_commit = "b" * 40
    fe_sha_val = fe_sha or ("sha256:" + ("1" * 64))
    bff_img = "asia-east1-docker.pkg.dev/pantheon-stg-20260902/containers/operator-bff@sha256:" + ("2" * 64)
    compat_sha = "sha256:" + ("3" * 64)
    mig_sha = "sha256:" + ("4" * 64)
    prev_fe_sha = "sha256:" + ("5" * 64)
    prev_bff_img = "asia-east1-docker.pkg.dev/pantheon-stg-20260902/containers/operator-bff@sha256:" + ("6" * 64)
    return release_manifest.build_manifest(
        release_id=release_id,
        release_state=state,
        frontend_commit=fe_commit,
        frontend_artifact_sha256=fe_sha_val,
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

        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable manifest checksum mismatch"):
            release_manifest.verify_promotion_eligibility(
                manifest,
                expected_manifest_sha256="sha256:" + ("9" * 64),
            )

        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable FE artifact checksum mismatch"):
            release_manifest.verify_promotion_eligibility(
                manifest,
                expected_manifest_sha256=outer_sha,
                observed_fe_sha256="sha256:" + ("f" * 64),
            )

        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable BFF image digest mismatch"):
            release_manifest.verify_promotion_eligibility(
                manifest,
                expected_manifest_sha256=outer_sha,
                observed_bff_digest="asia-east1-docker.pkg.dev/pantheon-stg-20260902/containers/operator-bff@sha256:" + ("e" * 64),
            )

        bad_tag = copy.deepcopy(manifest)
        bad_tag["backend"]["images"]["operator_bff"] = "asia-east1-docker.pkg.dev/pantheon-stg-20260902/containers/operator-bff:latest"
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable digest reference"):
            release_manifest.validate_manifest(bad_tag)

    def test_rejects_unverified_release_and_unaccepted_all12_chain_relabeling(self) -> None:
        unverified = _sample_manifest(state="candidate_built")
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "only 'dev_verified' releases"):
            release_manifest.verify_promotion_eligibility(unverified)

        relabeled = _sample_manifest()
        relabeled["dev_verification"]["all12_chain_id"] = "h12-01a11-20261023-9aeb"
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "h12-01a11-20261023-9aeb"):
            release_manifest.validate_manifest(relabeled)

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

        destructive_plan = copy.deepcopy(valid_plan)
        destructive_plan["statements"] = ["ALTER TABLE users DROP COLUMN email;"]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "destructive SQL forbidden"):
            staging_sanitize.verify_migration_and_compatibility(destructive_plan)

        incompatible_plan = copy.deepcopy(valid_plan)
        incompatible_plan["new_schema"]["users"]["locale"] = {
            "type": "text",
            "nullable": False,
            "has_default": False,
        }
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "neither nullable nor defaulted"):
            staging_sanitize.verify_migration_and_compatibility(incompatible_plan)

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

        hybrid_readback = dict(rollback_readback, fe_manifest_paired_backend_commit="f" * 40)
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "hybrid state"):
            staging_sanitize.verify_exact_pair_rollback(baseline, hybrid_readback)

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
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.state_dir = self.tmp_path / "state"
        self.fe_tarball = self.tmp_path / "fe-bundle.tar.gz"
        self.fe_sha = _build_fe_tarball(self.fe_tarball)
        self.manifest = _sample_manifest(fe_sha=self.fe_sha)
        self.manifest_path = self.tmp_path / "release-manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.manifest_sha = release_manifest.compute_manifest_sha256(self.manifest)

        self.gcloud_bin = self.tmp_path / "fake-gcloud.py"
        self.gcloud_bin.write_text(FAKE_GCLOUD_SCRIPT, encoding="utf-8")
        self.gcloud_bin.chmod(0o755)
        self.gcloud_state_file = self.tmp_path / "gcloud-state.json"
        self.gcloud_log_file = self.tmp_path / "gcloud-commands.log"

        sanitized = staging_sanitize.sanitize_dataset(_sample_raw_dataset(self.manifest["migration_set_sha256"]))
        self.gcloud_state_file.write_text(
            json.dumps({
                "addresses": [],
                "disks": [],
                "instances": [],
                "iam_bindings": [],
                "images": {},
                "gcs_blobs": {"gs://pantheon-stg-artifacts/fe-bundle.tar.gz": str(self.fe_tarball)},
                "vm_verify_bundles": {},
                "default_sanitized_dataset": sanitized,
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _base_env(self, **overrides: str) -> dict[str, str]:
        env = {
            **os.environ,
            "STAGING_GCP_PROJECT_ID": "pantheon-stg-20260902",
            "STAGING_GCP_ZONE": "asia-east1-b",
            "STAGING_INSTANCE_TEMPLATE": "pantheon-stg-template-v1",
            "STAGING_SERVICE_ACCOUNT": "pantheon-stg-deployer@pantheon-stg-20260902.iam.gserviceaccount.com",
            "STAGING_SANITIZED_SNAPSHOT": "pantheon-nonprod-sanitized-20261024",
            "RELEASE_MANIFEST_PATH": str(self.manifest_path),
            "EXPECTED_MANIFEST_SHA256": self.manifest_sha,
            "STAGING_FE_ARTIFACT_PATH": str(self.fe_tarball),
            "WORKFLOW_RUN_ID": "500001",
            "STAGING_STATE_DIR": str(self.state_dir),
            "STAGING_DRY_RUN": "false",
            "STAGING_NOW_EPOCH": "1792828800",
            "GCLOUD_BIN": str(self.gcloud_bin),
            "GCLOUD_STATE_FILE": str(self.gcloud_state_file),
            "GCLOUD_LOG_FILE": str(self.gcloud_log_file),
        }
        env.update(overrides)
        return env

    def test_configuration_and_retired_project_rejection(self) -> None:
        res = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_GCP_PROJECT_ID=""),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("MISSING_REQUIRED_CONFIG", res.stderr)

        res_retired = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_GCP_PROJECT_ID="pantheon-lupin-dev-20260719"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_retired.returncode, 0)
        self.assertIn("RETIRED_IDENTIFIER_FORBIDDEN", res_retired.stderr)

        res_ttl = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_TTL_SECONDS="90000"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_ttl.returncode, 0)
        self.assertIn("INVALID_TTL", res_ttl.stderr)

    def test_no_gcloud_false_success_rejected_fail_closed(self) -> None:
        # 1. STAGING_DRY_RUN=false with unset/empty GCLOUD_BIN must fail closed and record 0 resources
        no_gcloud_env = self._base_env(GCLOUD_BIN="")
        res_prov = subprocess.run([str(PROVISION_SCRIPT)], env=no_gcloud_env, capture_output=True, text=True)
        self.assertNotEqual(res_prov.returncode, 0)
        self.assertIn("GCLOUD_BIN_REQUIRED", res_prov.stderr)
        inv_file = self.state_dir / "inventory" / "pantheon-20261024-001.json"
        self.assertFalse(inv_file.exists())

        # 2. STAGING_DRY_RUN=false with GCLOUD_BIN returning empty `{}` (no observed identity) fails closed
        empty_id_env = self._base_env(GCLOUD_RETURN_EMPTY_IDENTITY="true")
        res_empty = subprocess.run([str(PROVISION_SCRIPT)], env=empty_id_env, capture_output=True, text=True)
        self.assertNotEqual(res_empty.returncode, 0)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", res_empty.stderr)

        # 3. Verify and Destroy also fail closed when STAGING_DRY_RUN=false and GCLOUD_BIN is unset
        res_ver = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", GCLOUD_BIN=""),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_ver.returncode, 0)
        self.assertIn("GCLOUD_BIN_REQUIRED", res_ver.stderr)

        res_teardown = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "teardown"],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", GCLOUD_BIN=""),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_teardown.returncode, 0)
        self.assertIn("GCLOUD_BIN_REQUIRED", res_teardown.stderr)

        res_sweep = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "orphan-sweep"],
            env=self._base_env(STAGING_ORPHAN_CLEANUP_ENABLED="true", GCLOUD_BIN=""),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_sweep.returncode, 0)
        self.assertIn("GCLOUD_BIN_REQUIRED", res_sweep.stderr)

    def test_real_gcloud_invocation_attachment_and_error_propagation(self) -> None:
        env = self._base_env()
        prov = subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        lease = json.loads(prov.stdout)
        self.assertEqual(lease["status"], "active")
        self.assertFalse(lease["dry_run"])
        self.assertTrue(lease["observed"])
        self.assertTrue(lease["deployed_artifacts"]["observed_from_live_vm"])

        # Inspect actual gcloud commands invoked
        cmds = self.gcloud_log_file.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any("artifacts docker images describe" in c for c in cmds))
        self.assertTrue(any("compute addresses create pantheon-stg-ip-pantheon-20261024-001" in c for c in cmds))
        self.assertTrue(any("compute disks create pantheon-stg-data-pantheon-20261024-001" in c for c in cmds))
        self.assertTrue(any("compute disks create pantheon-stg-pantheon-20261024-001-boot" in c for c in cmds))
        inst_create_cmd = next(c for c in cmds if "compute instances create pantheon-stg-pantheon-20261024-001" in c)
        self.assertIn("--address=203.0.113.10", inst_create_cmd)
        self.assertIn("name=pantheon-stg-pantheon-20261024-001-boot,device-name=pantheon-stg-pantheon-20261024-001-boot,boot=yes,mode=rw,auto-delete=no", inst_create_cmd)
        self.assertIn("name=pantheon-stg-data-pantheon-20261024-001,device-name=staging-data,mode=rw,auto-delete=no", inst_create_cmd)
        self.assertTrue(any("compute scp" in c for c in cmds))
        self.assertTrue(any("compute ssh pantheon-stg-pantheon-20261024-001" in c for c in cmds))

        # Test gcloud error propagation during provisioning (e.g. SCP failure triggers partial-create compensation)
        for fail_token in ("compute instances create", "compute scp", "compute ssh"):
            with self.subTest(fail_token=fail_token):
                self.setUp()
                fail_res = subprocess.run(
                    [str(PROVISION_SCRIPT)],
                    env=self._base_env(GCLOUD_FAIL_ON=fail_token),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(fail_res.returncode, 0)
                self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", fail_res.stderr)
                cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
                self.assertEqual(cloud_state["instances"], [])
                self.assertEqual(cloud_state["disks"], [])
                self.assertEqual(cloud_state["addresses"], [])

        # Test post-delete readback failure propagation when cloud resource lingers after delete
        self.setUp()
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        lingering_res = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "teardown", "--result", "success", "--reason", "completed"],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", GCLOUD_SIMULATE_LINGERING_DELETE="true"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(lingering_res.returncode, 0)
        self.assertIn("POST_DELETE_READBACK_FAILED", lingering_res.stderr)

    def test_valid_gcp_cloud_labels_and_ttl_epochs(self) -> None:
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        inv = json.loads((self.state_dir / "inventory" / "pantheon-20261024-001.json").read_text(encoding="utf-8"))
        key_re = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
        val_re = re.compile(r"^[a-z0-9_-]{1,63}$")

        required_label_keys = {
            "environment",
            "release_id",
            "owner",
            "workflow_run_id",
            "created_at",
            "expires_at",
            "created_at_epoch",
            "expires_at_epoch",
            "cleanup_policy",
        }
        self.assertEqual(set(inv["labels"].keys()), required_label_keys)
        for k, v in inv["labels"].items():
            self.assertRegex(k, key_re)
            self.assertRegex(v, val_re)

        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        for collection in ("addresses", "disks", "instances"):
            for item in cloud_state[collection]:
                self.assertEqual(set(item["labels"].keys()), required_label_keys)
                for k, v in item["labels"].items():
                    self.assertRegex(k, key_re)
                    self.assertRegex(v, val_re)
                self.assertEqual(item["labels"]["created_at_epoch"], "1792828800")
                self.assertEqual(item["labels"]["expires_at_epoch"], "1792836000")

    def test_source_artifact_retrieval_and_checksum_mismatch_rejection(self) -> None:
        # 1. Missing FE artifact file in live mode fails closed
        missing_fe = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_FE_ARTIFACT_PATH=str(self.tmp_path / "nonexistent.tar.gz")),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(missing_fe.returncode, 0)
        self.assertIn("FE_ARTIFACT_MISSING", missing_fe.stderr)

        # 2. Tampered FE tarball bytes fail closed on SHA-256 mismatch
        tampered_tar = self.tmp_path / "tampered-fe.tar.gz"
        _build_fe_tarball(tampered_tar, content="tampered-frontend-bundle")
        mismatch_fe = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_FE_ARTIFACT_PATH=str(tampered_tar)),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(mismatch_fe.returncode, 0)
        self.assertIn("immutable FE artifact checksum mismatch", mismatch_fe.stderr)

        # 3. Mismatched BFF image digest from Artifact Registry fails closed
        mismatch_bff = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(GCLOUD_OVERRIDE_BFF_DIGEST="sha256:" + ("f" * 64)),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(mismatch_bff.returncode, 0)
        self.assertIn("immutable BFF image digest mismatch", mismatch_bff.stderr)

        # 4. Retrieving FE artifact from STAGING_FE_ARTIFACT_URI via gcloud storage cp succeeds when SHA matches
        uri_ok = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(
                STAGING_FE_ARTIFACT_PATH="",
                STAGING_FE_ARTIFACT_URI="gs://pantheon-stg-artifacts/fe-bundle.tar.gz",
            ),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(json.loads(uri_ok.stdout)["status"], "active")

    def test_idempotency_and_global_cloud_conflicting_lease_rejection(self) -> None:
        env = self._base_env()
        first = subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        first_json = json.loads(first.stdout)
        self.assertEqual(first_json["status"], "active")
        self.assertEqual(first_json["resource_count"], 5)

        # Same release_id + same workflow_run_id -> idempotent re-entry
        second = subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        second_json = json.loads(second.stdout)
        self.assertEqual(second_json["status"], "already_provisioned")
        self.assertTrue(second_json["idempotent_reentry"])

        # Cross-runner global cloud lease conflict: even with a fresh empty STAGING_STATE_DIR,
        # active cloud resources in GCP block a conflicting workflow run!
        fresh_runner_state = self.tmp_path / "fresh-runner-state"
        conflict = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(WORKFLOW_RUN_ID="500002", STAGING_STATE_DIR=str(fresh_runner_state)),
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
        self.assertEqual(inv["resources"], [])
        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        self.assertEqual(cloud_state["addresses"], [])
        self.assertEqual(cloud_state["disks"], [])
        self.assertEqual(cloud_state["instances"], [])

        ev_path = self.state_dir / "evidence" / "pantheon-20261024-001-teardown-evidence.json"
        self.assertTrue(ev_path.exists())
        ev = json.loads(ev_path.read_text(encoding="utf-8"))
        self.assertEqual(ev["reason"], "partial_create_failure")
        self.assertTrue(ev["post_delete_readback_verified"])
        self.assertEqual(len(ev["inventory_snapshot"]), 3)

    def test_cross_run_orphan_discovery_and_preexisting_iam_preservation(self) -> None:
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

        # 2. Seed preexisting IAM binding and unowned Dev VM in cloud state before provisioning
        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        sa_member = "serviceAccount:pantheon-stg-deployer@pantheon-stg-20260902.iam.gserviceaccount.com"
        cloud_state["iam_bindings"] = [
            {"role": "roles/compute.viewer", "members": [sa_member]},
            {"role": "roles/owner", "members": ["user:bryan@example.com"]},
        ]
        cloud_state["instances"].append({
            "id": "9999",
            "name": "pantheon-dev-deploy",
            "project_id": "pantheon-dev-20260902",
            "zone": "asia-east1-b",
            "labels": {"environment": "dev"},
            "status": "RUNNING",
        })
        self.gcloud_state_file.write_text(json.dumps(cloud_state), encoding="utf-8")

        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        inv = json.loads((self.state_dir / "inventory" / "pantheon-20261024-001.json").read_text(encoding="utf-8"))
        iam_res = next(r for r in inv["resources"] if r["type"] == "iam_binding")
        self.assertTrue(iam_res["preexisting"])

        # 3. Wipe local runner state completely to prove stateless cross-run cloud orphan discovery!
        shutil.rmtree(self.state_dir)
        fresh_state_dir = self.tmp_path / "fresh-cron-runner-state"

        # Before TTL expiration (now=1792828900 < expires=1792836000): retains active cloud resources
        before_exp = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "orphan-sweep"],
            env=self._base_env(
                STAGING_STATE_DIR=str(fresh_state_dir),
                STAGING_ORPHAN_CLEANUP_ENABLED="true",
                STAGING_NOW_EPOCH="1792828900",
            ),
            capture_output=True,
            text=True,
            check=True,
        )
        before_json = json.loads(before_exp.stdout)
        self.assertEqual(before_json["deleted_resources"], [])
        self.assertEqual(len(before_json["retained_active_resources"]), 4)
        self.assertEqual(len(before_json["skipped_unowned_resources"]), 1)
        self.assertEqual(before_json["skipped_unowned_resources"][0]["name"], "pantheon-dev-deploy")

        # After TTL expiration (now=1792836001 > expires=1792836000): sweeps expired cloud resources from GCP
        after_exp = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "orphan-sweep"],
            env=self._base_env(
                STAGING_STATE_DIR=str(fresh_state_dir),
                STAGING_ORPHAN_CLEANUP_ENABLED="true",
                STAGING_NOW_EPOCH="1792836001",
            ),
            capture_output=True,
            text=True,
            check=True,
        )
        after_json = json.loads(after_exp.stdout)
        self.assertTrue(after_json["post_delete_readback_verified"])
        self.assertEqual(len(after_json["deleted_resources"]), 4)
        self.assertEqual(len(after_json["skipped_unowned_resources"]), 1)

        # Verify cloud state after sweep: staging VM/disks/IP deleted, dev VM and preexisting IAM bindings intact!
        final_cloud = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        self.assertEqual([i["name"] for i in final_cloud["instances"]], ["pantheon-dev-deploy"])
        self.assertEqual(final_cloud["disks"], [])
        self.assertEqual(final_cloud["addresses"], [])
        self.assertIn(
            {"role": "roles/compute.viewer", "members": [sa_member]},
            final_cloud["iam_bindings"],
        )

    def test_no_live_success_from_dryrun_and_prod_preflight_alignment(self) -> None:
        # 1. Provision in dry-run mode
        dry_env = self._base_env(STAGING_DRY_RUN="true", GCLOUD_BIN="")
        subprocess.run([str(PROVISION_SCRIPT), "--dry-run"], env=dry_env, capture_output=True, text=True, check=True)

        # 2. Attempting live verification (STAGING_DRY_RUN=false) against a dry-run lease MUST fail closed
        live_ver_on_dry = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", STAGING_DRY_RUN="false"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(live_ver_on_dry.returncode, 0)
        self.assertIn("DRY_RUN_LEASE_REJECTED", live_ver_on_dry.stderr)

        # 3. Running verify with --dry-run produces dry_run_validated (NEVER staging_verified)
        #    and is rejected by prod_preflight.check_staging_evidence
        mig_sha = self.manifest["migration_set_sha256"]
        san_path = self.tmp_path / "san.json"
        san_path.write_text(
            json.dumps(staging_sanitize.sanitize_dataset(_sample_raw_dataset(mig_sha))),
            encoding="utf-8",
        )
        mig_path = self.tmp_path / "mig.json"
        mig_path.write_text(
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
        bak_path = self.tmp_path / "bak.json"
        bak_path.write_text(
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
        rb = self.manifest["rollback_baseline"]
        rb_path = self.tmp_path / "rb.json"
        rb_path.write_text(
            json.dumps({
                "served_release_id": rb["previous_release_id"],
                "served_frontend_commit": rb["frontend_commit"],
                "served_frontend_artifact_sha256": rb["frontend_artifact_sha256"],
                "served_backend_commit": rb["backend_commit"],
                "fe_manifest_paired_backend_commit": rb["backend_commit"],
                "served_operator_bff_image": rb["backend_operator_bff_image"],
                "bff_healthy": True,
                "singleton_worker_owner_unique": True,
                "rollback_duration_seconds": 25,
            }),
            encoding="utf-8",
        )
        sec_path = self.tmp_path / "sec.json"
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

        dry_ver = subprocess.run(
            [str(VERIFY_SCRIPT), "--dry-run"],
            env=self._base_env(
                RELEASE_ID="pantheon-20261024-001",
                STAGING_DRY_RUN="true",
                GCLOUD_BIN="",
                SANITIZED_DATASET_PATH=str(san_path),
                MIGRATION_PLAN_PATH=str(mig_path),
                BACKUP_RESTORE_RECORD_PATH=str(bak_path),
                ROLLBACK_READBACK_PATH=str(rb_path),
                SECURITY_READBACK_PATH=str(sec_path),
            ),
            capture_output=True,
            text=True,
            check=True,
        )
        dry_ver_json = json.loads(dry_ver.stdout)
        self.assertEqual(dry_ver_json["status"], "dry_run_validated")
        self.assertTrue(dry_ver_json["dry_run"])
        self.assertFalse(dry_ver_json["observed_from_live_vm"])
        with self.assertRaisesRegex(prod_preflight.PreflightError, "staging_verified"):
            prod_preflight.check_staging_evidence(dry_ver_json, self.manifest, self.manifest_sha)

        # 4. Provision in live mode and verify that passing an unobserved static fixture is rejected
        self.setUp()
        san_path = self.tmp_path / "san.json"
        san_path.write_text(
            json.dumps(staging_sanitize.sanitize_dataset(_sample_raw_dataset(mig_sha))),
            encoding="utf-8",
        )
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        unobserved_fixture_res = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(
                RELEASE_ID="pantheon-20261024-001",
                SANITIZED_DATASET_PATH=str(san_path),
            ),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(unobserved_fixture_res.returncode, 0)
        self.assertIn("UNOBSERVED_FIXTURE_REJECTED", unobserved_fixture_res.stderr)

    def test_two_consecutive_simulated_releases_zero_to_zero_and_prod_preflight_alignment(self) -> None:
        for idx, rel_id in enumerate(("pantheon-20261024-001", "pantheon-20261024-002"), start=1):
            manifest = _sample_manifest(release_id=rel_id, fe_sha=self.fe_sha)
            m_path = self.tmp_path / f"{rel_id}.json"
            m_path.write_text(json.dumps(manifest), encoding="utf-8")
            m_sha = release_manifest.compute_manifest_sha256(manifest)
            env = self._base_env(
                RELEASE_ID=rel_id,
                RELEASE_MANIFEST_PATH=str(m_path),
                EXPECTED_MANIFEST_SHA256=m_sha,
                WORKFLOW_RUN_ID=f"60000{idx}",
                STAGING_DELAYED_UPLOAD_SIMULATE="true" if idx == 2 else "false",
            )
            subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
            ver = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True, check=True)
            ver_json = json.loads(ver.stdout)
            self.assertEqual(ver_json["status"], "staging_verified")
            self.assertFalse(ver_json["dry_run"])
            self.assertTrue(ver_json["observed_from_live_vm"])

            # Verify evidence shape alignment with prod_preflight consumer
            prod_preflight.check_staging_evidence(ver_json, manifest, m_sha)

            destroy = subprocess.run(
                [str(DESTROY_SCRIPT), "--mode", "teardown", "--result", "success", "--reason", "completed"],
                env=env,
                capture_output=True,
                text=True,
                check=True,
            )
            d_json = json.loads(destroy.stdout)
            self.assertEqual(d_json["status"], "destroyed")
            self.assertTrue(d_json["post_delete_readback_verified"])
            self.assertEqual(d_json["lingering_owned_resources"], 0)
            if idx == 2:
                self.assertEqual(d_json["upload_status"], "recovered_after_retry")

            cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
            self.assertEqual(cloud_state["instances"], [])
            self.assertEqual(cloud_state["disks"], [])
            self.assertEqual(cloud_state["addresses"], [])


class WorkflowAndTemplateContractTests(unittest.TestCase):
    def test_workflows_and_templates_parse_and_pass_actionlint(self) -> None:
        disallowed_job_env_ctx = re.compile(r"\$\{\{\s*(?:runner|steps|job)\.")
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

            # Workflow-level env must never reference runner/steps/job contexts
            for k, v in (parsed.get("env") or {}).items():
                self.assertIsNone(
                    disallowed_job_env_ctx.search(str(v)),
                    f"{wf_path.name} workflow env.{k} uses forbidden context: {v}",
                )

            has_staging_env = False
            has_wif_auth = False
            has_setup_gcloud = False
            for job_id, job in parsed["jobs"].items():
                if job.get("environment") == "staging":
                    has_staging_env = True
                # Job-level env must never reference runner/steps/job contexts (actionlint rule)
                for k, v in (job.get("env") or {}).items():
                    self.assertIsNone(
                        disallowed_job_env_ctx.search(str(v)),
                        f"{wf_path.name} jobs.{job_id}.env.{k} uses forbidden context: {v}",
                    )
                for step in job.get("steps", []):
                    uses = str(step.get("uses") or "")
                    if uses.startswith("google-github-actions/auth@"):
                        has_wif_auth = True
                    if uses.startswith("google-github-actions/setup-gcloud@"):
                        has_setup_gcloud = True
                    if "run" in step:
                        body = re.sub(r"\$\{\{.*?\}\}", "fixture_val", step["run"])
                        chk = subprocess.run(["bash", "-n"], input=body, text=True, capture_output=True)
                        self.assertEqual(chk.returncode, 0, chk.stderr)

            self.assertTrue(has_staging_env, f"{wf_path.name} must bind environment: staging")
            self.assertTrue(has_wif_auth, f"{wf_path.name} must configure WIF google-github-actions/auth")
            self.assertTrue(has_setup_gcloud, f"{wf_path.name} must configure google-github-actions/setup-gcloud")

        actionlint_bin = shutil.which("actionlint")
        if not actionlint_bin:
            for cand in ACTIONLINT_CANDIDATES:
                if cand.exists() and os.access(cand, os.X_OK):
                    actionlint_bin = str(cand)
                    break
        if actionlint_bin:
            lint = subprocess.run(
                [actionlint_bin, str(EPHEMERAL_WORKFLOW), str(ORPHAN_WORKFLOW)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(lint.returncode, 0, f"actionlint failed:\n{lint.stdout}\n{lint.stderr}")

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

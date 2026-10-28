"""Local controller and contract tests for Ephemeral Staging (`ENV-STG-EPHEMERAL-IMPL-001`).

NOTE: These tests validate local controller behavior, schema/manifest contracts,
sanitization logic, live `gcloud` CLI invocation/error propagation, IAP-only SSH/SCP
enforcement, bare-Debian bootstrap (`deploy/staging/bootstrap.sh`), real on-VM gate
collection (`deploy/staging/pantheon-staging-collect-gates`) under constrained host
binaries (`docker`, `curl`, `caddy`, `mountpoint`, `psql`, `pg_dump`, `pg_restore`, `sudo`),
cross-run cloud orphan discovery, valid GCP labels, artifact checksum verification,
deployer/runtime SA separation, and workflow `actionlint` compliance.
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
BOOTSTRAP_SCRIPT = REPO_ROOT / "deploy" / "staging" / "bootstrap.sh"
COLLECTOR_SCRIPT = REPO_ROOT / "deploy" / "staging" / "pantheon-staging-collect-gates"
EPHEMERAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "staging-ephemeral-deploy.yml"
ORPHAN_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "staging-orphan-cleanup.yml"
INSTANCE_TEMPLATE = REPO_ROOT / "deploy" / "staging" / "instance-template.json"
COMPOSE_STAGING = REPO_ROOT / "deploy" / "staging" / "docker-compose.staging.yml"
CADDY_TEMPLATE = REPO_ROOT / "deploy" / "staging" / "Caddyfile.staging.tmpl"
PRODUCT_DSN_KEYS = (
    "DATABASE_URL",
    "PERSONA_STORE_DSN",
    "PERSONA_CAPABILITY_STORE_DSN",
    "AGORA_WORKSHOP_STORE_DSN",
    "AGORA_DATASET_STORE_DSN",
    "AGORA_GOVERNANCE_STORE_DSN",
    "AGORA_RESEARCH_STORE_DSN",
)
PRODUCT_SECRET_KEYS = (
    "PANTHEON_RUNTIME_MANAGER_TOKEN",
    "CONSULTATION_SERVICE_TOKEN",
    "CONSULTATION_HANDOFF_TOKEN",
    "PANTHEON_TELEMETRY_SERVICE_TOKEN",
    "EVOLUTION_AUTH_TOKEN",
    "RECONCILIATION_DRIFT_AUTH_TOKEN",
    "POLICY_LEARNING_SERVICE_TOKEN",
    "PANTHEON_PERSONA_SERVICE_TOKEN",
    "PANTHEON_RESEARCH_JWT_SECRET",
    "PANTHEON_REGISTRY_JWT_SECRET",
    "PANTHEON_TELEMETRY_JWT_SECRET",
    "PERSONA_JWT_SECRET",
    "TRAINING_SESSION_JWT_SECRET",
    "CAPITAL_JWT_SECRET",
    "PANTHEON_CAPITAL_JWT_SECRET",
    "PANTHEON_OPENCLAW_ADAPTER_SERVICE_TOKEN",
)
ACTIONLINT_CANDIDATES = (
    Path("/tmp/pantheon-env-current-20261024/tools/actionlint"),
)


FAKE_HOST_BIN_SCRIPT = r"""#!/usr/bin/env python3
FAKE_NATIVE_ENV = {
    "runtime-manager": {
        "PANTHEON_RUNTIME_BINDING_STORE_PATH": "/data/runtime/runtime_bindings.json",
        "PANTHEON_COMMAND_STATE_FILE": "/data/runtime/internal_api_commands.json",
    },
    "governance": {"GOVERNANCE_DATA_DIR": "/data/governance"},
    "operator-bff": {"BFF_DATA_DIR": "/data/bff"},
}
import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import subprocess
import sys

tool = Path(sys.argv[0]).name
args = sys.argv[1:]

vm_state_file = Path(os.environ["VM_RUNTIME_STATE_FILE"])
vm_log_file = Path(os.environ["VM_BIN_LOG_FILE"])
vm_log_file.parent.mkdir(parents=True, exist_ok=True)
with vm_log_file.open("a", encoding="utf-8") as lf:
    lf.write(f"{tool} {' '.join(args)}\n")


def load_state() -> dict:
    if vm_state_file.exists():
        return json.loads(vm_state_file.read_text(encoding="utf-8"))
    return {
        "pulled_images": [],
        "logged_in_registries": [],
        "containers": {},
        "databases": {},
        "caddy_validated": False,
        "caddy_reloaded": False,
    }


def save_state(st: dict) -> None:
    vm_state_file.parent.mkdir(parents=True, exist_ok=True)
    vm_state_file.write_text(json.dumps(st, indent=2, sort_keys=True), encoding="utf-8")


def parse_env_file(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _root_owned_dirs() -> list[Path]:
    dirs = []
    for env_key in ("STAGING_ROOT", "STAGING_FE_ROOT", "STAGING_BIN_DIR", "STAGING_DATA_MOUNT"):
        val = os.environ.get(env_key, "").strip()
        if val:
            b = Path(val)
            dirs.append(b)
            for sub in ("secrets", "state", "config", "lib", "releases", "backups", "sanitized", "bff", "runtime", "governance", "product"):
                sd = b / sub
                if sd.exists() and sd.is_dir():
                    dirs.append(sd)
                    for child in sd.iterdir():
                        if not child.is_symlink() and child.is_dir():
                            dirs.append(child)
    caddy_p = os.environ.get("STAGING_CADDYFILE_PATH", "").strip()
    if caddy_p:
        dirs.append(Path(caddy_p).parent)
    return dirs


def _unlock_privileged_paths() -> list[tuple[Path, int]]:
    restores: list[tuple[Path, int]] = []
    for p in _root_owned_dirs():
        if p.exists() and p.is_dir():
            if (p.stat().st_mode & 0o777) == 0o555:
                p.chmod(0o755)
    for sec_key in ("STAGING_SECRETS_ENV_FILE", "STAGING_POSTGRES_PASSWORD_FILE"):
        s_val = os.environ.get(sec_key, "").strip()
        if s_val and Path(s_val).exists():
            sp = Path(s_val)
            smode = sp.stat().st_mode & 0o777
            if smode == 0o000:
                sp.chmod(0o600)
                restores.append((sp, 0o000))
    return restores


def _relock_privileged_paths(secret_restores: list[tuple[Path, int]]) -> None:
    if os.environ.get("FAKE_OSLOGIN_NONROOT") != "true":
        return
    for sp, mode in secret_restores:
        if sp.exists():
            sp.chmod(mode)
    for p in reversed(_root_owned_dirs()):
        if p.exists() and p.is_dir():
            p.chmod(0o555)


if tool == "sudo":
    cmd_args = list(args)
    while cmd_args and cmd_args[0] in ("-E", "--preserve-env"):
        cmd_args.pop(0)
    if not cmd_args:
        raise SystemExit(0)
    child_env = os.environ.copy()
    child_env["PANTHEON_PRIVILEGED"] = "1"
    secret_restores = _unlock_privileged_paths()
    try:
        proc = subprocess.run(cmd_args, env=child_env)
        raise SystemExit(proc.returncode)
    finally:
        _relock_privileged_paths(secret_restores)

if os.environ.get("FAKE_OSLOGIN_NONROOT") == "true" and os.environ.get("PANTHEON_PRIVILEGED") != "1":
    if tool in ("docker", "caddy", "mountpoint"):
        print(f"{tool}: permission denied (non-root OS Login user must invoke via sudo)", file=sys.stderr)
        raise SystemExit(1)

if tool == "mountpoint":
    if os.environ.get("FAKE_DATA_MOUNTED", "true") != "true":
        raise SystemExit(1)
    target = args[-1] if args else ""
    if not target or not Path(target).is_dir():
        raise SystemExit(1)
    raise SystemExit(0)

if tool == "caddy":
    if not args:
        raise SystemExit(1)
    sub = args[0]
    cfg_path = ""
    for i, a in enumerate(args):
        if a == "--config" and i + 1 < len(args):
            cfg_path = args[i + 1]
    if not cfg_path or not Path(cfg_path).exists():
        print("caddy config file missing", file=sys.stderr)
        raise SystemExit(1)
    text = Path(cfg_path).read_text(encoding="utf-8")
    if "__STAGING_" in text or "/runtime-config.js" not in text:
        print("caddy config has unrendered placeholders or lacks /runtime-config.js route", file=sys.stderr)
        raise SystemExit(1)
    st = load_state()
    if sub == "validate":
        st["caddy_validated"] = True
    elif sub in ("reload", "start"):
        st["caddy_reloaded"] = True
    save_state(st)
    raise SystemExit(0)

if tool == "docker":
    st = load_state()
    if args[:2] == ["compose", "version"]:
        print("Docker Compose version v2.29.7")
        raise SystemExit(0)

    if args[:1] == ["login"]:
        if "-u" not in args or "oauth2accesstoken" not in args or "--password-stdin" not in args:
            print("docker login requires -u oauth2accesstoken --password-stdin", file=sys.stderr)
            raise SystemExit(1)
        token_in = sys.stdin.read().strip()
        expected_tok = os.environ.get("FAKE_EXPECTED_METADATA_TOKEN", "ya29.fake-stg-runtime-sa-token")
        if not token_in or token_in != expected_tok:
            print("unauthorized: invalid or empty Artifact Registry access token", file=sys.stderr)
            raise SystemExit(1)
        reg_url = args[-1].rstrip("/")
        if reg_url not in st.setdefault("logged_in_registries", []):
            st["logged_in_registries"].append(reg_url)
        save_state(st)
        print("Login Succeeded")
        raise SystemExit(0)

    if args[:1] == ["pull"]:
        img = args[1]
        if "@sha256:" not in img:
            print(f"refusing unpinned image pull: {img}", file=sys.stderr)
            raise SystemExit(1)
        if img.startswith("asia-east1-docker.pkg.dev/"):
            reg_host = "https://" + img.split("/", 1)[0]
            if reg_host not in st.get("logged_in_registries", []):
                print(
                    f"Error response from daemon: Head {img}: denied: unauthenticated pull against {reg_host}",
                    file=sys.stderr,
                )
                raise SystemExit(1)
        fail_pull = os.environ.get("FAKE_DOCKER_PULL_FAIL", "").strip()
        if fail_pull and fail_pull in img:
            print(f"simulated docker pull failure for {img}", file=sys.stderr)
            raise SystemExit(1)
        if img not in st.setdefault("pulled_images", []):
            st["pulled_images"].append(img)
        save_state(st)
        print(f"Pulled {img}")
        raise SystemExit(0)

    if args[:1] == ["exec"]:
        # Runs the collector's native-store snippet against the real repo store classes, with the
        # container's own env and its /data/<owner> mounts mapped onto the fake data disk.
        cinfo = next((i for i in st.get("containers", {}).values() if i.get("cid") == args[1]), None)
        if not cinfo or not cinfo.get("running"):
            print(f"Error response from daemon: container {args[1]} is not running", file=sys.stderr)
            raise SystemExit(1)
        c_idx = args.index("-c")
        code, rest = args[c_idx + 1], args[c_idx + 2:]
        data_mount = os.environ["STAGING_DATA_MOUNT"]
        exec_env = {k: v for k, v in os.environ.items() if not k.startswith(("GOVERNANCE_", "BFF_", "DATABASE_URL"))}
        exec_env.update({
            k: (data_mount + v[len("/data"):] if v.startswith("/data/") else v)
            for k, v in cinfo.get("env", {}).items()
        })
        exec_env["PYTHONPATH"] = os.environ["FAKE_REPO_ROOT"]
        if cinfo["env"].get("GOVERNANCE_STORE_BACKEND") == "postgres":
            # No PostgreSQL in unit tests: the PG store class persists to a sim dir outside the data disk.
            code = (
                "import os,sys,pathlib,services.governance as _g\n"
                "sys.path.insert(0,str(pathlib.Path(_g.__file__).resolve().parent.parent/'control-plane'/'governance'))\n"
                "from approval_decision import ApprovalDecisionStore as _S\n"
                "import services.governance.pg_store as _pg\n"
                "class _Sim(_S):\n"
                "    def __init__(self, dsn, table, bootstrap):\n"
                "        super().__init__(os.path.join(os.environ['FAKE_PG_SIM_DIR'], 'approval_decisions.json'))\n"
                "_pg.PostgresApprovalDecisionStore=_Sim\n"
            ) + code
        proc = subprocess.run(
            [sys.executable, "-c", code, *rest], env=exec_env, cwd=os.environ["FAKE_REPO_ROOT"],
            capture_output=True, text=True,
        )
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        raise SystemExit(proc.returncode)

    if args[:2] == ["image", "inspect"]:
        img = args[-1]
        if img not in st.get("pulled_images", []):
            print(f"image not pulled locally: {img}", file=sys.stderr)
            raise SystemExit(1)
        print(json.dumps([{"Id": img}]))
        raise SystemExit(0)

    if args[:1] == ["inspect"]:
        cid = args[-1]
        fmt = ""
        for i, a in enumerate(args):
            if a == "-f" and i + 1 < len(args):
                fmt = args[i + 1]
        cinfo = None
        for svc, info in st.get("containers", {}).items():
            if info.get("cid") == cid:
                cinfo = info
                break
        if not cinfo:
            print(f"No such container: {cid}", file=sys.stderr)
            raise SystemExit(1)
        if fmt == "{{.Config.Image}}":
            print(cinfo["image"])
        elif "State.Running" in fmt:
            running = "true" if cinfo.get("running", True) else "false"
            mode = os.environ.get(
                "FAKE_BASELINE_OWNER_HEALTH" if cinfo.get("baseline") else "FAKE_OWNER_HEALTH", ""
            )
            health = ""
            if mode:
                counter = vm_state_file.with_name("health-" + cid + ".count")
                n = int(counter.read_text()) if counter.exists() else 0
                counter.write_text(str(n + 1))
                health = "unhealthy" if mode == "unhealthy" else ("healthy" if n >= 2 else "starting")
            print(running + (" " + health if health else ""))
        elif "Config.Env" in fmt:
            for k, v in sorted(cinfo.get("env", {}).items()):
                print(f"{k}={v}")
        else:
            print(json.dumps(cinfo))
        raise SystemExit(0)

    if args[:1] == ["compose"]:
        env_file = None
        for i, a in enumerate(args):
            if a == "--env-file" and i + 1 < len(args):
                env_file = Path(args[i + 1])
        c_env = parse_env_file(env_file) if env_file else {}

        if "ps" in args and "-q" in args:
            svc = args[-1]
            cinfo = st.get("containers", {}).get(svc)
            if cinfo and cinfo.get("running"):
                count = int(os.environ.get("FAKE_SINGLETON_COUNT", "1")) if svc == "lifecycle-projector" else 1
                for idx in range(count):
                    print(cinfo["cid"] if idx == 0 else f"{cinfo['cid']}-{idx}")
            raise SystemExit(0)

        if "rm" in args and "-fsv" in args:
            svc = args[-1]
            if os.environ.get("FAKE_WIPE_OWNER_ON_RECREATE") == "true" and svc == "operator-bff":
                (Path(os.environ["STAGING_DATA_MOUNT"]) / "bff" / "session_lifecycle.json").unlink(missing_ok=True)
            if svc in st.get("containers", {}):
                st["containers"][svc]["running"] = False
                save_state(st)
            print(f"Removed {svc}")
            raise SystemExit(0)

        if "up" in args and "-d" in args:
            if "--no-build" not in args:
                print("compose up without --no-build is forbidden", file=sys.stderr)
                raise SystemExit(1)
            svc_image_keys = {
                "postgres": "PANTHEON_POSTGRES_IMAGE",
                "nats": "PANTHEON_NATS_IMAGE",
                "runtime-manager": "PANTHEON_RUNTIME_MANAGER_IMAGE",
                "governance": "PANTHEON_GOVERNANCE_IMAGE",
                "deployment": "PANTHEON_DEPLOYMENT_IMAGE",
                "lifecycle-projector": "PANTHEON_LIFECYCLE_PROJECTOR_IMAGE",
                "operator-bff": "PANTHEON_OPERATOR_BFF_IMAGE",
            }
            for product_svc in (
                "registry telemetry incidents postmortems evolution lineage-read reconciliation-drift-svc persona "
                "consultation-svc source-ingest training-session-svc policy-learning-svc "
                "research-orchestrator-svc agora-interaction-worker memory capital openclaw-gateway-adapter"
            ).split():
                svc_image_keys[product_svc] = "PANTHEON_" + product_svc.upper().replace("-", "_") + "_IMAGE"
            explicit_svcs = [a for a in args if a in svc_image_keys]
            target_svcs = explicit_svcs if explicit_svcs else list(svc_image_keys.keys())
            # Compose reconciles an explicit service's dependencies too; runtime-manager's
            # PANTHEON_RELEASE_ID changes with the release, so it is recreated with a new ID.
            if "operator-bff" in target_svcs and "runtime-manager" not in target_svcs:
                target_svcs.append("runtime-manager")
            omit_svc = os.environ.get("FAKE_OMIT_SERVICE", "").strip()

            secrets_env = {}
            if c_env.get("STAGING_SECRETS_ENV_FILE"):
                secrets_env = parse_env_file(Path(c_env["STAGING_SECRETS_ENV_FILE"]))

            for svc in target_svcs:
                if omit_svc and svc == omit_svc:
                    continue
                img_key = svc_image_keys[svc]
                img_ref = c_env.get(img_key, "")
                if not img_ref or img_ref not in st.get("pulled_images", []):
                    print(f"pull_policy:never violation — image for {svc} ({img_ref!r}) not pre-pulled", file=sys.stderr)
                    raise SystemExit(1)
                merged_svc_env = {
                    "PANTHEON_ENV": "staging",
                    "PANTHEON_RELEASE_ID": c_env.get("PANTHEON_RELEASE_ID", ""),
                    "PANTHEON_PROVIDER_ENABLED": "false",
                    "PANTHEON_LIVE_BROKER_ENABLED": os.environ.get("FAKE_CONTAINER_LIVE_BROKER", "false"),
                    "PANTHEON_REAL_WRITES_ENABLED": "false",
                    "PANTHEON_DEV_STUB_WRITES_ENABLED": "false",
                    "PANTHEON_CONTROL_VM_BROKER_CREDENTIALS_PRESENT": "false",
                    "PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED": "false",
                    "PANTHEON_MGMT_NL_ASSISTANT_PROVIDER_ENABLED": "false",
                    "PANTHEON_ASSISTANT_PROVIDER": "disabled",
                    "PANTHEON_EXECUTION_MODE": "paper",
                    "PANTHEON_BFF_AUTH_MODE": "strict",
                    "PANTHEON_BFF_AUTH_STUB": "false",
                    "PANTHEON_BFF_JWT_ISSUER": c_env.get("PANTHEON_BFF_JWT_ISSUER", "https://auth.pantheon.internal"),
                    "PANTHEON_BFF_JWT_AUDIENCE": c_env.get("PANTHEON_BFF_JWT_AUDIENCE", "pantheon-operator-bff"),
                    "BFF_COMMIT": c_env.get("PANTHEON_BACKEND_COMMIT", ""),
                    "BFF_IMAGE_DIGEST": c_env.get("PANTHEON_OPERATOR_BFF_DIGEST", ""),
                    **FAKE_NATIVE_ENV.get(svc, {}),
                    **secrets_env,
                }
                if svc == "governance" and os.environ.get("FAKE_GOVERNANCE_BACKEND") == "postgres":
                    merged_svc_env.update({
                        "GOVERNANCE_STORE_BACKEND": "postgres",
                        "GOVERNANCE_AUDIT_BACKEND": "postgres",
                        "DATABASE_URL": "postgresql://pantheon_stg@postgres:5432/pantheon_staging",
                    })
                st.setdefault("containers", {})[svc] = {
                    "cid": f"cid-{svc}-{st.setdefault('cid_gen', 0) + 1}",
                    "image": img_ref,
                    "running": True,
                    "baseline": bool(env_file and env_file.name == "compose.baseline.env"),
                    "env": merged_svc_env,
                }
            st["cid_gen"] = st.get("cid_gen", 0) + 1
            save_state(st)
            # Emit ordinary Docker Compose stdout to prove deploy-host does not rely on compose printing JSON.
            print("Container pantheon-staging-operator-bff-1 Started")
            raise SystemExit(0)

    print(f"Unsupported docker args: {args}", file=sys.stderr)
    raise SystemExit(1)

if tool == "curl":
    st = load_state()
    url = args[-1]
    write_status_only = "-w" in args and "%{http_code}" in args
    dump_headers = "-D" in args
    out_file = None
    method = "GET"
    headers = {}
    req_body = ""
    for i, a in enumerate(args):
        if a == "-o" and i + 1 < len(args):
            out_file = args[i + 1]
        elif a == "-X" and i + 1 < len(args):
            method = args[i + 1].upper()
        elif a == "-H" and i + 1 < len(args) and ":" in args[i + 1]:
            hk, hv = args[i + 1].split(":", 1)
            headers[hk.strip().lower()] = hv.strip()
        elif a in ("-d", "--data", "--data-raw") and i + 1 < len(args):
            req_body = args[i + 1]

    def _emit_http(code: int, body_obj: dict) -> "NoReturn":
        body_str = json.dumps(body_obj, sort_keys=True)
        if out_file and out_file != "/dev/null":
            Path(out_file).write_text(body_str, encoding="utf-8")
        if write_status_only:
            print(str(code), end="")
            raise SystemExit(0)
        if code >= 400 and "-f" in "".join(a for a in args if a.startswith("-")):
            raise SystemExit(22)
        print(body_str)
        raise SystemExit(0)

    if "computeMetadata/v1/instance/service-accounts/default/token" in url:
        if headers.get("metadata-flavor") != "Google" or os.environ.get("FAKE_METADATA_TOKEN_FAIL") == "true":
            raise SystemExit(22)
        tok = os.environ.get("FAKE_METADATA_TOKEN_VALUE", "ya29.fake-stg-runtime-sa-token")
        print(json.dumps({"access_token": tok, "expires_in": 3599, "token_type": "Bearer"}))
        raise SystemExit(0)

    if url.endswith("/bff/readyz"):
        required_up = ("postgres", "nats", "runtime-manager", "governance", "deployment", "lifecycle-projector", "operator-bff")
        all_up = all(st.get("containers", {}).get(s, {}).get("running") is True for s in required_up)
        if os.environ.get("FAKE_BFF_UNHEALTHY") == "true":
            all_up = False
        code = 200 if all_up else 503
        _emit_http(code, {"status": "ok" if all_up else "unavailable", "ready": all_up})

    if url.endswith("/bff/version"):
        bff = st.get("containers", {}).get("operator-bff", {})
        b_env = bff.get("env", {})
        _emit_http(200, {
            "environment": b_env.get("PANTHEON_ENV", "staging"),
            "source_commit_sha": b_env.get("BFF_COMMIT", ""),
            "image_digest": b_env.get("BFF_IMAGE_DIGEST", ""),
        })

    if url.endswith("/runtime-config.js"):
        cfg_path = Path(os.environ.get("STAGING_ROOT", "")) / "config" / "runtime-config.js"
        if not cfg_path.exists():
            raise SystemExit(22)
        content = cfg_path.read_text(encoding="utf-8")
        if out_file and out_file != "/dev/null":
            Path(out_file).write_text(content, encoding="utf-8")
        cc = os.environ.get("FAKE_RUNTIME_CONFIG_CACHE_CONTROL", "no-store, no-cache, must-revalidate, max-age=0")
        if dump_headers:
            print(f"HTTP/1.1 200 OK\r\nCache-Control: {cc}\r\nContent-Type: application/javascript\r\n\r\n", end="")
        else:
            print(content, end="")
        raise SystemExit(0)

    def _verify_jwt() -> tuple[dict | None, str | None]:
        auth = headers.get("authorization", "")
        if not auth:
            return None, "AUTH_REQUIRED"
        if not auth.startswith("Bearer "):
            return None, "AUTH_INVALID"
        token = auth.split(" ", 1)[1].strip()
        parts = token.split(".")
        if len(parts) != 3:
            return None, "AUTH_INVALID"
        h_b64, p_b64, s_b64 = parts
        bff_env = st.get("containers", {}).get("operator-bff", {}).get("env", {})
        secret = bff_env.get("PANTHEON_BFF_JWT_SECRET", "")
        if not secret:
            return None, "AUTH_INVALID"
        signing_input = f"{h_b64}.{p_b64}".encode("ascii")
        expected_sig = base64.urlsafe_b64encode(
            hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
        ).rstrip(b"=").decode("ascii")
        if not hmac.compare_digest(s_b64, expected_sig):
            return None, "AUTH_INVALID"
        padded = p_b64 + "=" * (-len(p_b64) % 4)
        try:
            claims = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except Exception:
            return None, "AUTH_INVALID"
        if claims.get("iss") != bff_env.get("PANTHEON_BFF_JWT_ISSUER", "https://auth.pantheon.internal"):
            return None, "AUTH_INVALID"
        if claims.get("aud") != bff_env.get("PANTHEON_BFF_JWT_AUDIENCE", "pantheon-operator-bff"):
            return None, "AUTH_INVALID"
        return claims, None

    if url.endswith("/bff/me"):
        if method != "GET":
            _emit_http(405, {"detail": "Method Not Allowed"})
        # Also verify the migrated database works when the old-app BFF handles /bff/me
        bff_img = st.get("containers", {}).get("operator-bff", {}).get("image", "")
        if os.environ.get("FAKE_OLD_APP_INCOMPATIBLE") == "true" and ("6" * 64) in bff_img:
            _emit_http(500, {"error": {"code": "INTERNAL_ERROR", "message": "old app incompatible with new schema"}})
        claims, err_code = _verify_jwt()
        if err_code:
            code = int(os.environ.get("FAKE_UNAUTH_STATUS", "401"))
            _emit_http(code, {"error": {"code": err_code, "message": "Authentication required or invalid"}})
        req_tenant = headers.get("x-tenant-id") or claims.get("tenant_id", "")
        if req_tenant not in (claims.get("allowed_tenants") or []):
            code = int(os.environ.get("FAKE_CROSS_TENANT_STATUS", "403"))
            _emit_http(code, {
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Requested tenant is outside authenticated tenant scope.",
                    "details": {"precondition_failed": "tenant_scope"},
                }
            })
        _emit_http(200, {
            "data": {
                "user_id": claims["sub"],
                "email": claims.get("email"),
                "tenant_id": req_tenant,
                "allowed_tenants": claims["allowed_tenants"],
                "roles": claims["roles"],
            }
        })

    if url.endswith("/bff/switch-tenant"):
        if method != "POST":
            _emit_http(405, {"detail": "Method Not Allowed"})
        claims, err_code = _verify_jwt()
        if err_code:
            _emit_http(401, {"error": {"code": err_code, "message": "Authentication required or invalid"}})
        try:
            payload = json.loads(req_body) if req_body else {}
        except Exception:
            payload = {}
        target_tenant = payload.get("tenant_id", "")
        if target_tenant not in (claims.get("allowed_tenants") or []):
            code = int(os.environ.get("FAKE_CROSS_TENANT_STATUS", "403"))
            _emit_http(code, {
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Requested tenant is outside authenticated tenant scope.",
                    "details": {"precondition_failed": "tenant_scope"},
                }
            })
        _emit_http(200, {"data": {"tenant_id": target_tenant, "allowed_tenants": claims["allowed_tenants"]}})

    if url.endswith("/bff/command-confirmations"):
        if method != "POST":
            _emit_http(405, {"detail": "Method Not Allowed"})
        claims, err_code = _verify_jwt()
        if err_code:
            _emit_http(401, {"error": {"code": err_code, "message": "Authentication required or invalid"}})
        req_tenant = headers.get("x-tenant-id") or claims.get("tenant_id", "")
        if req_tenant not in (claims.get("allowed_tenants") or []):
            _emit_http(403, {
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Requested tenant is outside authenticated tenant scope.",
                    "details": {"precondition_failed": "tenant_scope"},
                }
            })
        roles = claims.get("roles") or []
        if not any(r in ("operator", "admin") for r in roles):
            code = int(os.environ.get("FAKE_VIEWER_MUTATION_STATUS", "403"))
            _emit_http(code, {
                "error": {
                    "code": "FORBIDDEN",
                    "message": "Role is not permitted to confirm commands.",
                    "details": {"precondition_failed": "role_check"},
                }
            })
        _emit_http(200, {"data": {"status": "confirmed"}})

    if url.endswith("/bff/capabilities"):
        if method != "GET":
            _emit_http(405, {"detail": "Method Not Allowed"})
        claims, err_code = _verify_jwt()
        if err_code:
            _emit_http(401, {"error": {"code": err_code}})
        _emit_http(200, {"data": {"roles": claims["roles"]}})

    print(f"Unsupported curl URL: {url}", file=sys.stderr)
    raise SystemExit(1)

if tool in ("psql", "pg_dump", "pg_restore"):
    st = load_state()
    expected_pw = st.get("expected_pg_password", "")
    if not os.environ.get("PGPASSWORD") or (expected_pw and os.environ.get("PGPASSWORD") != expected_pw):
        print("psql authentication failed: invalid or missing PGPASSWORD", file=sys.stderr)
        raise SystemExit(1)

    db_name = "pantheon_staging"
    for i, a in enumerate(args):
        if a == "-d" and i + 1 < len(args):
            db_name = args[i + 1]

    if tool == "pg_dump":
        if os.environ.get("FAKE_PG_DUMP_FAIL") == "true":
            print("simulated pg_dump failure", file=sys.stderr)
            raise SystemExit(1)
        out_f = ""
        for i, a in enumerate(args):
            if a == "-f" and i + 1 < len(args):
                out_f = args[i + 1]
        db_data = st.get("databases", {}).get(db_name, {})
        Path(out_f).write_text(json.dumps(db_data, sort_keys=True), encoding="utf-8")
        raise SystemExit(0)

    if tool == "pg_restore":
        if os.environ.get("FAKE_PG_RESTORE_FAIL") == "true":
            print("simulated pg_restore failure", file=sys.stderr)
            raise SystemExit(1)
        dump_file = Path(args[-1])
        if not dump_file.exists():
            print("dump file missing", file=sys.stderr)
            raise SystemExit(1)
        if db_name not in st.get("databases", {}):
            print(f'pg_restore: error: connection to database "{db_name}" failed: FATAL:  database "{db_name}" does not exist', file=sys.stderr)
            raise SystemExit(1)
        st.setdefault("databases", {})[db_name] = json.loads(dump_file.read_text(encoding="utf-8"))
        save_state(st)
        raise SystemExit(0)

    # tool == psql
    sql = ""
    for i, a in enumerate(args):
        if a == "-c" and i + 1 < len(args):
            sql = args[i + 1]

    # Enforce PostgreSQL rule: DROP DATABASE and CREATE DATABASE cannot run inside a multi-statement -c transaction block
    stmts_in_c = [s.strip() for s in sql.split(";") if s.strip()]
    if len(stmts_in_c) > 1 and any(kw in sql.upper() for kw in ("DROP DATABASE", "CREATE DATABASE")):
        print("ERROR:  DROP DATABASE cannot run inside a transaction block", file=sys.stderr)
        raise SystemExit(1)

    if sql.upper().startswith("DROP DATABASE"):
        target_db = sql.split('"', 1)[1].split('"', 1)[0] if '"' in sql else stmts_in_c[0].split()[-1]
        st.setdefault("databases", {}).pop(target_db, None)
        save_state(st)
        print("DROP DATABASE")
        raise SystemExit(0)

    if sql.upper().startswith("CREATE DATABASE"):
        new_db = sql.split('CREATE DATABASE "', 1)[1].split('"', 1)[0]
        st.setdefault("databases", {})[new_db] = {"tables": {}, "schema": {}}
        save_state(st)
        print("CREATE DATABASE")
        raise SystemExit(0)

    db_entry = st.setdefault("databases", {}).setdefault(db_name, {})
    db_tables = db_entry.setdefault("tables", {})

    def _cols(*names):
        return {n: {"type": "text", "nullable": True, "has_default": False} for n in names}

    default_catalog = {
        "public.telemetry_events": _cols("event_id", "event_type", "payload", "created_at", "ingested_at", "ingested_seq"),
        "public.loop_controller_records": _cols("controller_id", "loop_id", "payload", "evidence_refs", "tenant_id", "updated_at"),
        "public.trade_journey_action_ledger": _cols("idempotency_key", "request_hash", "receipt", "state"),
        "agora.agora_dataset_records": _cols("evidence_id", "tenant_id", "user_id", "content", "lease_token"),
        "governance.governance_records": _cols("record_id", "payload"),
    }
    db_schema = db_entry.setdefault("schema", default_catalog)

    sql_file = ""
    for i, a in enumerate(args):
        if a == "-f" and i + 1 < len(args):
            sql_file = args[i + 1]
    if sql_file:
        if os.environ.get("FAKE_FAIL_MIGRATION_SQL") == "true":
            print("ERROR: syntax or lock error executing migration SQL", file=sys.stderr)
            raise SystemExit(1)
        if os.environ.get("FAKE_SKIP_SCHEMA_MUTATION") != "true":
            applied = Path(sql_file).read_text(encoding="utf-8")
            if "trade_journey_projection.controller" in applied:
                db_schema["trade_journey_projection.controller"] = _cols("controller_id", "tenant_scope", "environment_scope")
        st.setdefault("applied_migration_files", []).append(Path(sql_file).name)
        st["migration_applied"] = True
        save_state(st)
        print("CREATE TABLE")
        raise SystemExit(0)

    if "FROM information_schema.columns" in sql:
        print(json.dumps(db_schema, sort_keys=True))
        raise SystemExit(0)

    if "json_object_agg(k, v)" in sql:
        # One entry per owned relation of every schema in the live catalog (not just public).
        if "count(*)" in sql:
            print(json.dumps({t: len(db_tables.get(t) or []) for t in db_schema}, sort_keys=True))
        else:
            print(json.dumps({t: db_tables.get(t) or [] for t in db_schema}, sort_keys=True))
        raise SystemExit(0)
    if sql.strip().upper().startswith("SELECT COUNT(*) FROM PUBLIC.TELEMETRY_EVENTS"):
        print(len(db_tables.get("public.telemetry_events") or []))
        raise SystemExit(0)

    print("OK")
    raise SystemExit(0)
"""


FAKE_GCLOUD_SCRIPT = r"""#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

state_file = Path(os.environ["GCLOUD_STATE_FILE"])
log_file = Path(os.environ["GCLOUD_LOG_FILE"])

raw_args = sys.argv[1:]
args = [a for a in raw_args if a != "--format=json" and a != "--quiet"]
log_file.parent.mkdir(parents=True, exist_ok=True)
with log_file.open("a", encoding="utf-8") as lf:
    lf.write(" ".join(raw_args) + "\n")

fail_on = os.environ.get("GCLOUD_FAIL_ON", "").strip()
if fail_on and fail_on in " ".join(raw_args):
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
        "vm_mkdir_ready": {},
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
    print("WARNING: The following filter keys were not present in any resource : zone", file=sys.stderr)
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
    print("WARNING: The following filter keys were not present in any resource : zone", file=sys.stderr)
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
    sa_email = parse_flag("--service-account")
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
        "serviceAccounts": [{"email": sa_email, "scopes": ["https://www.googleapis.com/auth/cloud-platform"]}],
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
    print("WARNING: The following filter keys were not present in any resource : zone", file=sys.stderr)
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
    if "--tunnel-through-iap" not in raw_args:
        print("IAP-only firewall: direct public SCP is forbidden (--tunnel-through-iap required)", file=sys.stderr)
        raise SystemExit(1)
    target = args[-1]
    vm_name, remote_dest = target.split(":", 1)
    remote_dir = Path(remote_dest.rstrip("/"))
    if not state.get("vm_mkdir_ready", {}).get(vm_name) or not remote_dir.is_dir():
        print(f"Remote directory {remote_dir} was not created over IAP SSH before SCP on {vm_name}", file=sys.stderr)
        raise SystemExit(1)
    files = [a for a in args[2:-1] if not a.startswith("--")]
    for src in files:
        shutil.copy2(src, remote_dir / Path(src).name)
    print(json.dumps({"scp": "ok", "count": len(files)}))
    raise SystemExit(0)

if args[:2] == ["compute", "ssh"]:
    if "--tunnel-through-iap" not in raw_args:
        print("IAP-only firewall: direct public SSH is forbidden (--tunnel-through-iap required)", file=sys.stderr)
        raise SystemExit(1)
    vm_name = args[2]
    cmd = parse_flag("--command")
    if not cmd:
        print("compute ssh missing --command", file=sys.stderr)
        raise SystemExit(1)
    if "mkdir -p '/tmp/pantheon-staging-bundle'" in cmd or "mkdir -p /tmp/pantheon-staging-bundle" in cmd:
        state.setdefault("vm_mkdir_ready", {})[vm_name] = True
        save_state()
    child_env = os.environ.copy()
    host_bin_dir = os.environ.get("FAKE_HOST_BIN_DIR", "")
    if host_bin_dir:
        child_env["PATH"] = f"{host_bin_dir}:{child_env.get('PATH', '')}"
    proc = subprocess.run(["bash", "-c", cmd], env=child_env, text=True, capture_output=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    raise SystemExit(proc.returncode)

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
    prev_fe_sha: str | None = None,
    mig_sha: str = "sha256:" + ("4" * 64),
) -> dict:
    fe_commit = "a" * 40
    bff_commit = "b" * 40
    fe_sha_val = fe_sha or ("sha256:" + ("1" * 64))
    reg = "asia-east1-docker.pkg.dev/pantheon-stg-20260902/pantheon-releases"
    bff_img = f"{reg}/operator-bff@sha256:" + ("2" * 64)
    compat_sha = "sha256:" + ("3" * 64)
    prev_fe_sha_val = prev_fe_sha or ("sha256:" + ("5" * 64))
    prev_bff_img = f"{reg}/operator-bff@sha256:" + ("6" * 64)
    return release_manifest.build_manifest(
        release_id=release_id,
        release_state=state,
        frontend_commit=fe_commit,
        frontend_artifact_sha256=fe_sha_val,
        backend_commit=bff_commit,
        operator_bff_image=bff_img,
        additional_images={
            "runtime_manager": f"{reg}/runtime-manager@sha256:" + ("a" * 64),
            "governance": f"{reg}/governance@sha256:" + ("b" * 64),
            "deployment": f"{reg}/deployment@sha256:" + ("c" * 64),
            "lifecycle_projector": f"{reg}/lifecycle-projector@sha256:" + ("d" * 64),
            **{
                key: f"{reg}/{key.replace('_', '-')}@sha256:" + (f"{i:02x}" * 32)
                for i, key in enumerate(staging_sanitize.PRODUCT_OWNER_IMAGE_KEYS, start=0xA0)
            },
        },
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
            "frontend_artifact_sha256": prev_fe_sha_val,
            "backend_commit": "d" * 40,
            "backend_operator_bff_image": prev_bff_img,
            "backend_images": {
                "operator_bff": prev_bff_img,
                "runtime_manager": f"{reg}/runtime-manager@sha256:" + ("61" * 32),
                "governance": f"{reg}/governance@sha256:" + ("62" * 32),
                "deployment": f"{reg}/deployment@sha256:" + ("63" * 32),
                "lifecycle_projector": f"{reg}/lifecycle-projector@sha256:" + ("64" * 32),
                **{
                    key: f"{reg}/{key.replace('_', '-')}@sha256:" + (f"{i:02x}" * 32)
                    for i, key in enumerate(staging_sanitize.PRODUCT_OWNER_IMAGE_KEYS, start=0xC0)
                },
            },
            "config_schema_version": "pantheon.env-config.v1",
        },
    )


MIGRATIONS_SRC_DIR = REPO_ROOT / "services" / "trade_journey" / "migrations"


def _real_migration_set() -> tuple[list[dict], str]:
    """Actual versioned SQL files and their set digest (sorted `<sha256>  <name>\\n` lines)."""
    files = []
    lines = []
    for path in sorted(MIGRATIONS_SRC_DIR.glob("*.sql")):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        files.append({"name": path.name, "sha256": "sha256:" + digest})
        lines.append(f"{digest}  {path.name}\n")
    return files, "sha256:" + hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _sample_catalog() -> dict:
    def cols(*names: str) -> dict:
        return {n: {"type": "text", "nullable": True, "has_default": False} for n in names}

    return {
        "public.telemetry_events": cols("event_id", "event_type", "payload", "created_at", "ingested_at", "ingested_seq"),
        "public.loop_controller_records": cols("controller_id", "loop_id", "payload", "evidence_refs", "tenant_id", "updated_at"),
        "public.trade_journey_action_ledger": cols("idempotency_key", "request_hash", "receipt", "state"),
        "agora.agora_dataset_records": cols("evidence_id", "tenant_id", "user_id", "content", "lease_token"),
        "governance.governance_records": cols("record_id", "payload"),
    }


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
        "catalog": _sample_catalog(),
        "tables": {
            "agora.agora_dataset_records": [
                {
                    "evidence_id": "ev-1",
                    "tenant_id": "tenant-alpha",
                    "user_id": "real.person@example.com",
                    "content": {"note": "contact real.person@example.com", "api_key": "drop-me"},
                    "lease_token": "lease-secret-value",
                }
            ],
            "governance.governance_records": [
                {"record_id": "gov-1", "payload": {"ok": True, "refresh_token": "drop-me"}}
            ],
            "public.telemetry_events": [
                {
                    "event_id": "evt-1",
                    "event_type": "loop_tick",
                    "payload": {
                        "correlation_id": "corr-001",
                        "token": "secret-jwt-token",
                        "auth": "Bearer secret",
                        "nested": {"broker_secret": "drop-me", "latency_ms": 42},
                    },
                    "created_at": "2026-10-24T07:00:00Z",
                    "ingested_at": "2026-10-24T07:00:01Z",
                    "ingested_seq": 1,
                }
            ],
            "public.loop_controller_records": [
                {
                    "controller_id": "tj-projector",
                    "loop_id": "loop-1",
                    "tenant_id": "tenant-alpha",
                    "payload": {"state": "ok", "api_key": "drop-me"},
                    "evidence_refs": [{"ref": "e1", "access_token": "drop-me"}],
                }
            ],
            "public.trade_journey_action_ledger": [
                {
                    "idempotency_key": "idem-1",
                    "request_hash": "h1",
                    "state": "completed",
                    "receipt": {"ok": True, "client_secret": "drop-me"},
                }
            ],
        },
    }


def _sample_backup_record(owner_sha: str = "sha256:" + ("9" * 64)) -> dict:
    counts = {name: 1 for name in _sample_catalog()}
    return {
        "backup_id": "stg-backup-20261024-001",
        "backup_sha256": "sha256:" + ("8" * 64),
        "expected_catalog_sha256": "sha256:" + ("5" * 64),
        "restored_catalog_sha256": "sha256:" + ("5" * 64),
        "restore_completed": True,
        "read_query_verified": True,
        "live_row_counts": dict(counts),
        "restored_row_counts": dict(counts),
        "owner_store_backup_sha256": "sha256:" + ("7" * 64),
        "owner_store_manifest_sha256": owner_sha,
        "restored_owner_store_manifest_sha256": owner_sha,
        "owner_store_dirs": ["bff", "runtime", "governance", "product"],
        "catalog_tables": sorted(counts),
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
                observed_bff_digest="asia-east1-docker.pkg.dev/pantheon-stg-20260902/pantheon-releases/operator-bff@sha256:" + ("e" * 64),
            )

        bad_tag = copy.deepcopy(manifest)
        bad_tag["backend"]["images"]["operator_bff"] = "asia-east1-docker.pkg.dev/pantheon-stg-20260902/pantheon-releases/operator-bff:latest"
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "immutable digest reference"):
            release_manifest.validate_manifest(bad_tag)

    def test_rejects_unverified_release_and_unaccepted_all12_chain_relabeling(self) -> None:
        unverified = _sample_manifest(state="candidate_built")
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "only 'dev_verified' releases"):
            release_manifest.verify_promotion_eligibility(unverified)

        relabeled = _sample_manifest()
        relabeled["dev_verification"]["all12_chain_id"] = "h12-01a11-20261023-9aeb"
        relabeled["dev_verification"].pop("all12_status", None)
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "requires all12_status 'passed'"):
            release_manifest.validate_manifest(relabeled)
        relabeled["dev_verification"]["all12_status"] = "failed"
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "all12_status 'passed'"):
            release_manifest.validate_manifest(relabeled)
        relabeled["dev_verification"]["all12_status"] = "passed"
        release_manifest.validate_manifest(relabeled)

        failed_all12 = _sample_manifest()
        failed_all12["dev_verification"]["all12_status"] = "failed"
        with self.assertRaisesRegex(release_manifest.ReleaseManifestError, "all12_status 'passed'"):
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
    def test_sanitize_dataset_strips_secrets_and_verifies_actual_product_tables(self) -> None:
        raw = _sample_raw_dataset()
        sanitized = staging_sanitize.sanitize_dataset(raw)
        result = staging_sanitize.verify_sanitized_dataset(sanitized)
        self.assertTrue(result["sanitized_verified"])
        self.assertEqual(
            sorted(sanitized["tables"]),
            [
                "agora.agora_dataset_records",
                "governance.governance_records",
                "public.loop_controller_records",
                "public.telemetry_events",
                "public.trade_journey_action_ledger",
            ],
        )
        self.assertEqual(result["schemas"], ["agora", "governance", "public"])
        agora = sanitized["tables"]["agora.agora_dataset_records"][0]
        self.assertEqual(agora["lease_token"], "[REDACTED]")
        self.assertRegex(agora["user_id"], r"^staging-user-[0-9a-f]{12}@pantheon\.internal$")
        self.assertNotIn("real.person@example.com", json.dumps(agora))
        self.assertNotIn("api_key", agora["content"])
        self.assertNotIn("refresh_token", sanitized["tables"]["governance.governance_records"][0]["payload"])
        payload = sanitized["tables"]["public.telemetry_events"][0]["payload"]
        self.assertNotIn("token", payload)
        self.assertNotIn("auth", payload)
        self.assertNotIn("broker_secret", payload["nested"])
        self.assertEqual(payload["nested"]["latency_ms"], 42)
        loop = sanitized["tables"]["public.loop_controller_records"][0]
        self.assertNotIn("api_key", loop["payload"])
        self.assertNotIn("access_token", loop["evidence_refs"][0])
        self.assertNotIn("client_secret", sanitized["tables"]["public.trade_journey_action_ledger"][0]["receipt"])

    def test_compose_mounts_durable_owner_stores_like_accepted_dev_wiring(self) -> None:
        compose = yaml.safe_load((REPO_ROOT / "deploy" / "staging" / "docker-compose.staging.yml").read_text())
        mounts = {
            svc: {v.split("}/", 1)[1] for v in spec.get("volumes", [])}
            for svc, spec in compose["services"].items()
        }
        self.assertIn("bff:/data/bff", mounts["operator-bff"])
        self.assertIn("bff:/data/bff", mounts["lifecycle-projector"])
        self.assertIn("runtime:/data/runtime", mounts["runtime-manager"])
        self.assertIn("governance:/data/governance", mounts["governance"])
        self.assertIn("governance:/data/governance", mounts["deployment"])
        self.assertIn("runtime:/data/runtime:ro", mounts["deployment"])
        self.assertIn("governance:/data/governance:ro", mounts["operator-bff"])

    def test_compose_packages_every_required_owner_from_immutable_images_only(self) -> None:
        text = COMPOSE_STAGING.read_text(encoding="utf-8")
        compose = yaml.safe_load(text)
        services = compose["services"]
        for key in staging_sanitize.REQUIRED_OWNER_IMAGE_KEYS:
            svc = key.replace("_", "-")
            self.assertIn(svc, services, key)
            spec = services[svc]
            self.assertNotIn("build", spec, svc)
            self.assertEqual(spec["pull_policy"], "never", svc)
            self.assertTrue(spec["image"].startswith(f"${{PANTHEON_{key.upper()}_IMAGE:?"), svc)
        # Product owners are in the default `core` profile so up/rollback manage the whole closure.
        for key in staging_sanitize.PRODUCT_OWNER_IMAGE_KEYS:
            self.assertIn("core", services[key.replace("_", "-")]["profiles"])
        # Original request-driven routes are served by the BFF's configured owner targets.
        bff_env = services["operator-bff"]["environment"]
        for var, host in (
            ("PERSONA_URL", "persona"),
            ("PANTHEON_RESEARCH_ORCHESTRATOR_API_URL", "research-orchestrator-svc"),
            ("PANTHEON_REGISTRY_API_URL", "registry"),
            ("PANTHEON_EVOLUTION_API_URL", "evolution"),
            ("SOURCE_MANAGEMENT_API_URL", "source-ingest"),
            ("PANTHEON_MEMORY_API_URL", "memory"),
            ("PANTHEON_CAPITAL_API_URL", "capital"),
            ("PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL", "openclaw-gateway-adapter"),
        ):
            self.assertIn(f"http://{host}:", bff_env[var], var)
        for dep in ("memory", "capital", "openclaw-gateway-adapter"):
            self.assertIn(dep, services["operator-bff"]["depends_on"], dep)
        adapter_env = services["openclaw-gateway-adapter"]["environment"]
        self.assertNotIn("OPENCLAW_GATEWAY_URL", adapter_env)
        for flag in ("PRODUCTION_BROKER", "PAPER_ADAPTER", "LIVE_ADAPTER", "CANARY_ADAPTER", "CAPITAL_BINDING"):
            self.assertEqual(adapter_env[f"OPENCLAW_{flag}_ENABLED"], "false", flag)
        self.assertEqual(
            services["agora-interaction-worker"]["command"], ["python", "scripts/run_agora_interaction_worker.py"]
        )
        # Dev-only principal issuer / periodic producers / well-known Dev credentials are not shipped.
        rendered = yaml.safe_dump(compose)
        for forbidden in ("dev-paper", "pantheon-local", "scheduler", "producer", "pantheon_app", "-internal"):
            self.assertNotIn(forbidden, rendered, forbidden)
        for svc, spec in services.items():
            env = spec.get("environment") or {}
            for flag in ("PANTHEON_PROVIDER_ENABLED", "PANTHEON_LIVE_BROKER_ENABLED", "PANTHEON_REAL_WRITES_ENABLED"):
                if flag in env:
                    self.assertEqual(str(env[flag]).lower(), "false", f"{svc}.{flag}")

    def test_compose_binds_native_store_paths_to_mounted_owner_dirs(self) -> None:
        compose = yaml.safe_load((REPO_ROOT / "deploy" / "staging" / "docker-compose.staging.yml").read_text())
        env = {svc: spec.get("environment", {}) for svc, spec in compose["services"].items()}
        self.assertEqual(env["runtime-manager"]["PANTHEON_RUNTIME_BINDING_STORE_PATH"], "/data/runtime/runtime_bindings.json")
        self.assertEqual(env["runtime-manager"]["PANTHEON_COMMAND_STATE_FILE"], "/data/runtime/internal_api_commands.json")
        self.assertEqual(env["governance"]["GOVERNANCE_DATA_DIR"], "/data/governance")
        self.assertEqual(env["deployment"]["DEPLOYMENT_DATA_DIR"], "/data/governance")
        self.assertEqual(env["deployment"]["PANTHEON_GOVERNANCE_DATA_DIR"], "/data/governance")
        self.assertEqual(env["deployment"]["PANTHEON_RUNTIME_BINDING_STORE_PATH"], "/data/runtime/runtime_bindings.json")
        self.assertEqual(env["operator-bff"]["BFF_DATA_DIR"], "/data/bff")
        self.assertEqual(env["lifecycle-projector"]["BFF_DATA_DIR"], "/data/bff")

    def test_telemetry_service_token_is_scoped_to_exactly_the_staging_tenant(self) -> None:
        from unittest import mock

        import flask

        from services.telemetry import auth as telemetry_auth

        compose = yaml.safe_load(COMPOSE_STAGING.read_text(encoding="utf-8"))
        raw_env = compose["services"]["telemetry"]["environment"]
        tenant = "tenant-staging-a"
        env = {
            k: re.sub(r"\$\{STAGING_TENANT_ID:\?[^}]*\}", tenant, str(v)) for k, v in raw_env.items()
        }
        self.assertEqual(env["PANTHEON_TELEMETRY_SERVICE_TENANTS"], tenant)
        self.assertNotIn("*", env["PANTHEON_TELEMETRY_SERVICE_TENANTS"])
        env["PANTHEON_TELEMETRY_SERVICE_TOKEN"] = "fixture-service-token"  # from the secrets env_file at runtime

        def resolve(request_tenant: str):
            app = flask.Flask(__name__)
            headers = {"Authorization": "Bearer fixture-service-token", "X-Tenant-Id": request_tenant}
            with mock.patch.dict(os.environ, env, clear=False), app.test_request_context(headers=headers):
                return telemetry_auth.resolve_telemetry_authority(required_roles=["service"])

        self.assertEqual(resolve(tenant).tenant_id, tenant)
        with self.assertRaises(telemetry_auth.TelemetryAuthorityError) as ctx:
            resolve("tenant-other")
        self.assertEqual(ctx.exception.code, "TENANT_FORBIDDEN")

    def test_structured_pii_in_json_and_direct_columns_is_masked_and_verified(self) -> None:
        raw = _sample_raw_dataset()
        row = raw["tables"]["agora.agora_dataset_records"][0]
        row["content"] = {
            "profile": {"full_name": "Jane Roe", "phone": "+886912345678", "national_id": "A123456789"},
            "contacts": [{"mobile": "0912345678", "kind": "primary"}],
        }
        sanitized = staging_sanitize.sanitize_dataset(raw)
        dumped = json.dumps(sanitized["tables"]["agora.agora_dataset_records"][0]["content"])
        for leaked in ("Jane Roe", "+886912345678", "A123456789", "0912345678"):
            self.assertNotIn(leaked, dumped)
        self.assertIn("primary", dumped)
        staging_sanitize.verify_sanitized_dataset(sanitized)
        sanitized["tables"]["agora.agora_dataset_records"][0]["content"]["profile"]["phone"] = "+886912345678"
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "PII key"):
            staging_sanitize.verify_sanitized_dataset(sanitized)

        direct = _sample_raw_dataset()
        direct["catalog"]["agora.agora_dataset_records"]["national_id"] = {"type": "text"}
        direct["tables"]["agora.agora_dataset_records"][0]["national_id"] = "A123456789"
        out = staging_sanitize.sanitize_dataset(direct)
        self.assertEqual(out["tables"]["agora.agora_dataset_records"][0]["national_id"], "[REDACTED]")
        out["tables"]["agora.agora_dataset_records"][0]["national_id"] = "A123456789"
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "PII column"):
            staging_sanitize.verify_sanitized_dataset(out)

    def test_dataset_must_cover_every_catalog_relation_including_empty_ones(self) -> None:
        raw = _sample_raw_dataset()
        for rel in ("agora.agora_dataset_records", "governance.governance_records"):
            del raw["tables"][rel]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "missing from sanitization input"):
            staging_sanitize.sanitize_dataset(raw)
        raw = _sample_raw_dataset()
        raw["tables"]["governance.governance_records"] = []
        sanitized = staging_sanitize.sanitize_dataset(raw)
        self.assertEqual(staging_sanitize.verify_sanitized_dataset(sanitized)["row_counts"]["governance.governance_records"], 0)
        del sanitized["tables"]["governance.governance_records"]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "missing from sanitization input"):
            staging_sanitize.verify_sanitized_dataset(sanitized)

    def test_rejects_production_data_and_unknown_schema_inputs_fail_closed(self) -> None:
        prod_input = _sample_raw_dataset()
        prod_input["metadata"]["source_environment"] = "production"
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "production data is strictly forbidden"):
            staging_sanitize.sanitize_dataset(prod_input)

        unknown_table = _sample_raw_dataset()
        unknown_table["tables"]["agora.not_in_catalog"] = [{"id": "1"}]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "unknown table"):
            staging_sanitize.sanitize_dataset(unknown_table)

        phantom_table = _sample_raw_dataset()
        phantom_table["tables"]["public.users"] = [{"id": "usr-1"}]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "unknown table"):
            staging_sanitize.sanitize_dataset(phantom_table)

        credential_table = _sample_raw_dataset()
        credential_table["catalog"]["public.broker_credentials"] = {"id": {"type": "text"}}
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "credential table"):
            staging_sanitize.sanitize_dataset(credential_table)

        unknown_secret_table = _sample_raw_dataset()
        unknown_secret_table["tables"]["secret_live_orders"] = [{"id": "1"}]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "credential table"):
            staging_sanitize.sanitize_dataset(unknown_secret_table)

        unredacted = staging_sanitize.sanitize_dataset(_sample_raw_dataset())
        unredacted["tables"]["agora.agora_dataset_records"][0]["lease_token"] = "still-live"
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "unredacted"):
            staging_sanitize.verify_sanitized_dataset(unredacted)

        unknown_col = _sample_raw_dataset()
        unknown_col["tables"]["public.telemetry_events"][0]["raw_payload"] = {"x": 1}
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "unknown column"):
            staging_sanitize.sanitize_dataset(unknown_col)

        residual = staging_sanitize.sanitize_dataset(_sample_raw_dataset())
        residual["tables"]["public.telemetry_events"][0]["payload"]["api_key"] = "x"
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "sensitive key"):
            staging_sanitize.verify_sanitized_dataset(residual)

    def test_migration_backup_rollback_and_security_readback_verifiers(self) -> None:
        mig_sha = "sha256:" + ("4" * 64)
        valid_plan = {
            "expected_migration_set_sha256": mig_sha,
            "observed_migration_set_sha256": mig_sha,
            "phase": "expand",
            "statements": ["ALTER TABLE telemetry_events ADD COLUMN locale TEXT NULL;"],
            "old_app_schema": {
                "public.telemetry_events": {"event_id": {"type": "text"}, "event_type": {"type": "text"}},
            },
            "new_schema": {
                "public.telemetry_events": {
                    "event_id": {"type": "text"},
                    "event_type": {"type": "text"},
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
        destructive_plan["statements"] = ["ALTER TABLE telemetry_events DROP COLUMN event_type;"]
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "destructive SQL forbidden"):
            staging_sanitize.verify_migration_and_compatibility(destructive_plan)

        incompatible_plan = copy.deepcopy(valid_plan)
        incompatible_plan["new_schema"]["public.telemetry_events"]["locale"] = {
            "type": "text",
            "nullable": False,
            "has_default": False,
        }
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "neither nullable nor defaulted"):
            staging_sanitize.verify_migration_and_compatibility(incompatible_plan)

        owner_sha = "sha256:" + ("9" * 64)
        backup_record = _sample_backup_record(owner_sha)
        self.assertTrue(
            staging_sanitize.verify_backup_restore_readability(backup_record)["backup_restore_verified"]
        )
        for mutate, pattern in (
            (lambda r: r.update(restored_catalog_sha256="sha256:" + "0" * 64), "restored catalog hash"),
            (lambda r: r["restored_row_counts"].update({"public.telemetry_events": 7}), "row counts differ"),
            (lambda r: r["restored_row_counts"].update(users=1), "unknown table"),
            (lambda r: r.update(restored_owner_store_manifest_sha256="sha256:" + "0" * 64), "owner-store file manifest"),
            (lambda r: r.update(owner_store_dirs=["bff"]), "owner store backup must cover"),
        ):
            bad = copy.deepcopy(backup_record)
            mutate(bad)
            with self.assertRaisesRegex(staging_sanitize.SanitizationError, pattern):
                staging_sanitize.verify_backup_restore_readability(bad)

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

        served = dict(baseline["backend_images"])
        rollback_readback["served_backend_images"] = served
        self.assertTrue(
            staging_sanitize.verify_exact_pair_rollback(baseline, rollback_readback)["exact_pair_rollback_verified"]
        )
        for dropped in ("agora_interaction_worker", "registry"):
            partial = {k: v for k, v in served.items() if k != dropped}
            with self.assertRaisesRegex(staging_sanitize.SanitizationError, "every required owner"):
                staging_sanitize.verify_exact_pair_rollback(
                    baseline, dict(rollback_readback, served_backend_images=partial)
                )
        wrong = dict(served, persona=baseline["backend_images"]["registry"])
        with self.assertRaisesRegex(staging_sanitize.SanitizationError, "does not match baseline image"):
            staging_sanitize.verify_exact_pair_rollback(baseline, dict(rollback_readback, served_backend_images=wrong))

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
    def _unlock_vm_env_dir(self) -> None:
        vm_env = getattr(self, "vm_env_dir", None)
        if vm_env and Path(vm_env).exists():
            for p in [Path(vm_env), *Path(vm_env).rglob("*")]:
                if p.is_symlink():
                    continue
                try:
                    if p.is_dir():
                        p.chmod(0o755)
                    elif (p.stat().st_mode & 0o777) == 0o000:
                        p.chmod(0o600)
                except OSError:
                    pass

    def _lock_root_owned_paths(self) -> None:
        if self.secrets_env_file.exists():
            self.secrets_env_file.chmod(0o000)
        if self.pg_pw_file.exists():
            self.pg_pw_file.chmod(0o000)
        for base in (self.staging_root, self.fe_root, self.bin_dir, self.data_mount, self.caddyfile_path.parent):
            if base.exists():
                for p in sorted([base, *base.rglob("*")], key=lambda x: len(str(x)), reverse=True):
                    if p.is_symlink():
                        continue
                    if p.is_dir():
                        p.chmod(0o555)

    def setUp(self) -> None:
        self._unlock_vm_env_dir()
        if getattr(self, "tmp_dir", None) is not None:
            self.tmp_dir.cleanup()
        shutil.rmtree("/tmp/pantheon-staging-bundle", ignore_errors=True)
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self.tmp_dir.name)
        self.state_dir = self.tmp_path / "state"
        self.fe_tarball = self.tmp_path / "fe-bundle.tar.gz"
        self.fe_sha = _build_fe_tarball(self.fe_tarball)
        self.fe_baseline_tarball = self.tmp_path / "fe-baseline-bundle.tar.gz"
        self.fe_baseline_sha = _build_fe_tarball(
            self.fe_baseline_tarball,
            content="<!doctype html><title>Pantheon Baseline Release</title>",
        )
        self.migration_files, self.migration_set_sha = _real_migration_set()
        self.manifest = _sample_manifest(
            fe_sha=self.fe_sha, prev_fe_sha=self.fe_baseline_sha, mig_sha=self.migration_set_sha
        )
        self.manifest_path = self.tmp_path / "release-manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.manifest_sha = release_manifest.compute_manifest_sha256(self.manifest)

        # Constrained host binaries used when FAKE_GCLOUD_SCRIPT executes remote SSH commands
        self.host_bin_dir = self.tmp_path / "host_bin"
        self.host_bin_dir.mkdir(parents=True, exist_ok=True)
        for tool in ("docker", "curl", "caddy", "mountpoint", "psql", "pg_dump", "pg_restore", "sudo"):
            tpath = self.host_bin_dir / tool
            tpath.write_text(FAKE_HOST_BIN_SCRIPT, encoding="utf-8")
            tpath.chmod(0o755)

        self.vm_env_dir = self.tmp_path / "vm_env"
        self.staging_root = self.vm_env_dir / "root"
        self.data_mount = self.vm_env_dir / "data"
        self.fe_root = self.vm_env_dir / "fe"
        self.bin_dir = self.vm_env_dir / "bin"
        self.caddyfile_path = self.vm_env_dir / "etc" / "caddy" / "Caddyfile"
        self.secrets_dir = self.staging_root / "secrets"
        for d in (
            self.staging_root,
            self.data_mount / "sanitized" / "migrations",
            self.data_mount / "bff",
            self.data_mount / "runtime",
            self.data_mount / "governance",
            self.data_mount / "product",
            self.fe_root,
            self.bin_dir,
            self.secrets_dir,
            self.caddyfile_path.parent,
        ):
            d.mkdir(parents=True, exist_ok=True)

        self.secrets_env_file = self.secrets_dir / "staging.secrets.env"
        self.secrets_env_file.write_text(
            "PANTHEON_BFF_JWT_SECRET=stg-jwt-secret-value-001\n"
            "PANTHEON_RUNTIME_JWT_SECRET=stg-runtime-jwt-secret-001\n"
            "PANTHEON_DATABASE_URL=postgresql://pantheon_stg:stg-pg-pass@127.0.0.1:15432/pantheon_staging\n"
            "TELEMETRY_DB_DSN=postgresql://pantheon_stg:stg-pg-pass@postgres:5432/pantheon_staging\n"
            "LIFECYCLE_PROJECTOR_PROJECTION_DSN=postgresql://pantheon_stg:stg-pg-pass@postgres:5432/pantheon_staging\n"
            + "".join(
                f"{k}=postgresql://pantheon_stg:stg-pg-pass@postgres:5432/pantheon_staging\n"
                for k in PRODUCT_DSN_KEYS
            )
            + "".join(f"{k}=stg-fixture-{k.lower()}-value\n" for k in PRODUCT_SECRET_KEYS),
            encoding="utf-8",
        )
        self.secrets_env_file.chmod(0o600)

        self.pg_pw_file = self.secrets_dir / "postgres_password"
        self.pg_pw_file.write_text("stg-isolated-pg-password-20261025\n", encoding="utf-8")
        self.pg_pw_file.chmod(0o600)

        sanitized = staging_sanitize.sanitize_dataset(_sample_raw_dataset(self.manifest["migration_set_sha256"]))
        (self.data_mount / "sanitized" / "snapshot-metadata.json").write_text(
            json.dumps(sanitized["metadata"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.data_mount / "sanitized" / "migration-spec.json").write_text(
            json.dumps({
                "phase": "expand",
                "migration_set_sha256": self.manifest["migration_set_sha256"],
                "files": self.migration_files,
            }, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        for mig in self.migration_files:
            shutil.copyfile(MIGRATIONS_SRC_DIR / mig["name"], self.data_mount / "sanitized" / "migrations" / mig["name"])

        # Lock root-owned directories (0555) and secret files (0000) so non-root OS Login commands
        # fail with EACCES unless elevated via sudo.
        self._lock_root_owned_paths()

        self.vm_runtime_state_file = self.vm_env_dir / "vm_runtime_state.json"
        self.vm_bin_log_file = self.vm_env_dir / "vm_bin.log"
        self.vm_runtime_state_file.write_text(
            json.dumps({
                "pulled_images": [],
                "logged_in_registries": [],
                "containers": {},
                "expected_pg_password": "stg-isolated-pg-password-20261025",
                "migration_set_sha256": self.manifest["migration_set_sha256"],
                "compatibility_manifest_sha256": self.manifest["compatibility_manifest_sha256"],
                "databases": {
                    "pantheon_staging": {"tables": sanitized["tables"], "schema": _sample_catalog()},
                },
            }, indent=2, sort_keys=True),
            encoding="utf-8",
        )

        self.gcloud_bin = self.tmp_path / "fake-gcloud.py"
        self.gcloud_bin.write_text(FAKE_GCLOUD_SCRIPT, encoding="utf-8")
        self.gcloud_bin.chmod(0o755)
        self.gcloud_state_file = self.tmp_path / "gcloud-state.json"
        self.gcloud_log_file = self.tmp_path / "gcloud-commands.log"

        self.gcloud_state_file.write_text(
            json.dumps({
                "addresses": [],
                "disks": [],
                "instances": [],
                "iam_bindings": [],
                "images": {},
                "gcs_blobs": {
                    "gs://pantheon-stg-artifacts/fe-bundle.tar.gz": str(self.fe_tarball),
                    "gs://pantheon-stg-artifacts/fe-baseline-bundle.tar.gz": str(self.fe_baseline_tarball),
                    "gs://pantheon-stg-20260902-release-artifacts/fe-bundle.tar.gz": str(self.fe_tarball),
                    "gs://pantheon-stg-20260902-release-artifacts/fe-baseline-bundle.tar.gz": str(self.fe_baseline_tarball),
                },
                "vm_mkdir_ready": {},
            }),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self._unlock_vm_env_dir()
        shutil.rmtree("/tmp/pantheon-staging-bundle", ignore_errors=True)
        self.tmp_dir.cleanup()

    def _base_env(self, **overrides: str) -> dict[str, str]:
        env = {
            **os.environ,
            "STAGING_GCP_PROJECT_ID": "pantheon-stg-20260902",
            "STAGING_GCP_ZONE": "asia-east1-b",
            "STAGING_INSTANCE_TEMPLATE": "pantheon-stg-template-20261024",
            "STAGING_DEPLOY_SERVICE_ACCOUNT": "pantheon-stg-github-deploy@pantheon-stg-20260902.iam.gserviceaccount.com",
            "STAGING_RUNTIME_SERVICE_ACCOUNT": "pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com",
            "STAGING_SERVICE_ACCOUNT": "pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com",
            "STAGING_SANITIZED_SNAPSHOT": "pantheon-nonprod-sanitized-20261024",
            "RELEASE_MANIFEST_PATH": str(self.manifest_path),
            "EXPECTED_MANIFEST_SHA256": self.manifest_sha,
            "STAGING_FE_ARTIFACT_PATH": str(self.fe_tarball),
            "STAGING_FE_BASELINE_ARTIFACT_PATH": str(self.fe_baseline_tarball),
            "WORKFLOW_RUN_ID": "500001",
            "STAGING_STATE_DIR": str(self.state_dir),
            "STAGING_DRY_RUN": "false",
            "STAGING_NOW_EPOCH": "1792828800",
            "STAGING_PUBLIC_GCP_IDENTITY_API_KEY": "AIza" + "a" * 35,  # local-only fixture, not a real key
            "STAGING_PUBLIC_GCP_IDENTITY_PROJECT_ID": "pantheon-stg-20260902",
            "STAGING_PUBLIC_GCP_IDENTITY_AUTH_DOMAIN": "pantheon-stg-20260902.firebaseapp.com",
            "STAGING_TENANT_ID": "tenant-stg-fixture",
            "STAGING_ROOT": str(self.staging_root),
            "STAGING_DATA_MOUNT": str(self.data_mount),
            "STAGING_FE_ROOT": str(self.fe_root),
            "STAGING_BIN_DIR": str(self.bin_dir),
            "STAGING_CADDYFILE_PATH": str(self.caddyfile_path),
            "STAGING_SECRETS_ENV_FILE": str(self.secrets_env_file),
            "STAGING_POSTGRES_PASSWORD_FILE": str(self.pg_pw_file),
            "STAGING_ALLOW_APT_INSTALL": "false",
            "STAGING_READY_TRIES": "3",
            "STAGING_READY_INTERVAL": "0",
            "DOCKER_BIN": str(self.host_bin_dir / "docker"),
            "CURL_BIN": str(self.host_bin_dir / "curl"),
            "CADDY_BIN": str(self.host_bin_dir / "caddy"),
            "MOUNTPOINT_BIN": str(self.host_bin_dir / "mountpoint"),
            "PSQL_BIN": str(self.host_bin_dir / "psql"),
            "PG_DUMP_BIN": str(self.host_bin_dir / "pg_dump"),
            "PG_RESTORE_BIN": str(self.host_bin_dir / "pg_restore"),
            "SUDO_BIN": str(self.host_bin_dir / "sudo"),
            "FAKE_OSLOGIN_NONROOT": "true",
            "FAKE_REPO_ROOT": str(REPO_ROOT),
            "FAKE_PG_SIM_DIR": str(self.vm_env_dir / "pg-sim"),
            "FAKE_HOST_BIN_DIR": str(self.host_bin_dir),
            "VM_RUNTIME_STATE_FILE": str(self.vm_runtime_state_file),
            "VM_BIN_LOG_FILE": str(self.vm_bin_log_file),
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

    def test_deployer_service_account_rejected_on_vm_and_zero_project_iam_mutation(self) -> None:
        # 1. Attaching the WIF deployer SA to the staging VM is rejected fail-closed
        for forbidden_sa in (
            "pantheon-stg-github-deploy@pantheon-stg-20260902.iam.gserviceaccount.com",
            "pantheon-stg-deployer@pantheon-stg-20260902.iam.gserviceaccount.com",
        ):
            res = subprocess.run(
                [str(PROVISION_SCRIPT)],
                env=self._base_env(
                    STAGING_RUNTIME_SERVICE_ACCOUNT=forbidden_sa,
                    STAGING_SERVICE_ACCOUNT=forbidden_sa,
                ),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res.returncode, 0)
            self.assertIn("DEPLOYER_SA_FORBIDDEN_ON_VM", res.stderr)

        # 2. Normal provisioning uses runtime SA on VM and never mutates project IAM
        prov = subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        lease = json.loads(prov.stdout)
        self.assertEqual(
            lease["service_account"],
            "pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com",
        )
        cmds = self.gcloud_log_file.read_text(encoding="utf-8").splitlines()
        self.assertFalse(any("add-iam-policy-binding" in c for c in cmds))
        self.assertFalse(any("remove-iam-policy-binding" in c for c in cmds))

    def test_iap_only_ssh_scp_and_ordinary_compose_stdout_counterexamples(self) -> None:
        # Counterexample 1: strict IAP-only firewall wrapper around gcloud
        strict_iap_wrapper = self.tmp_path / "strict-iap-gcloud.py"
        strict_iap_wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] in (['compute', 'scp'], ['compute', 'ssh']) and '--tunnel-through-iap' not in args:\n"
            "    print('IAP-only firewall: direct public SSH is not permitted', file=sys.stderr)\n"
            "    raise SystemExit(1)\n"
            f"os.execv({str(self.gcloud_bin)!r}, [{str(self.gcloud_bin)!r}] + args)\n",
            encoding="utf-8",
        )
        strict_iap_wrapper.chmod(0o755)

        prov = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(GCLOUD_BIN=str(strict_iap_wrapper)),
            capture_output=True,
            text=True,
            check=True,
        )
        lease = json.loads(prov.stdout)
        self.assertEqual(lease["status"], "active")

        ver = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", GCLOUD_BIN=str(strict_iap_wrapper)),
            capture_output=True,
            text=True,
            check=True,
        )
        ver_json = json.loads(ver.stdout)
        self.assertEqual(ver_json["status"], "staging_verified")

        # Verify that SSH mkdir ran BEFORE SCP and all SSH/SCP commands included --tunnel-through-iap
        cmds = self.gcloud_log_file.read_text(encoding="utf-8").splitlines()
        ssh_scp_cmds = [c for c in cmds if c.startswith("compute ssh") or c.startswith("compute scp")]
        self.assertGreaterEqual(len(ssh_scp_cmds), 3)
        for c in ssh_scp_cmds:
            self.assertIn("--tunnel-through-iap", c)
        self.assertTrue(ssh_scp_cmds[0].startswith("compute ssh"))
        self.assertIn("mkdir -p", ssh_scp_cmds[0])
        self.assertTrue(ssh_scp_cmds[1].startswith("compute scp"))

        # Counterexample 2: docker compose up prints ordinary stdout ("Container pantheon-staging-operator-bff-1 Started")
        # and bootstrap.sh queries on-host identity afterward. Verify vm_bin.log recorded real docker pull,
        # compose up --no-build, docker inspect, curl /bff/readyz, and curl /bff/version.
        vm_logs = self.vm_bin_log_file.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(l.startswith("docker pull ") and "operator-bff@sha256:" in l for l in vm_logs))
        self.assertTrue(any(l.startswith("docker pull ") and "runtime-manager@sha256:" in l for l in vm_logs))
        self.assertTrue(any("compose" in l and "up -d --no-build" in l for l in vm_logs))
        self.assertTrue(any(l.startswith("docker inspect -f {{.Config.Image}}") for l in vm_logs))
        self.assertTrue(any("/bff/readyz" in l for l in vm_logs))
        self.assertTrue(any("/bff/version" in l for l in vm_logs))

        # Also verify that if a broken SSH transport ONLY emits ordinary compose stdout without
        # the on-host identity JSON readback, staging_provision.sh fails closed and compensates.
        self.setUp()
        no_json_ssh_wrapper = self.tmp_path / "no-json-ssh-gcloud.py"
        no_json_ssh_wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['compute', 'ssh'] and any('bootstrap.sh' in a for a in args):\n"
            "    print('Container pantheon-staging-operator-bff-1 Started')\n"
            "    raise SystemExit(0)\n"
            f"os.execv({str(self.gcloud_bin)!r}, [{str(self.gcloud_bin)!r}] + args)\n",
            encoding="utf-8",
        )
        no_json_ssh_wrapper.chmod(0o755)
        bad_ssh_prov = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(GCLOUD_BIN=str(no_json_ssh_wrapper)),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(bad_ssh_prov.returncode, 0)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", bad_ssh_prov.stderr)
        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        self.assertEqual(cloud_state["instances"], [])
        self.assertEqual(cloud_state["disks"], [])
        self.assertEqual(cloud_state["addresses"], [])

    def test_runtime_config_emits_only_accepted_public_schema_fields(self) -> None:
        env = self._base_env(RELEASE_ID="pantheon-20261024-001")
        subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        text = (self.staging_root / "config" / "runtime-config.js").read_text(encoding="utf-8")
        prefix = "window.__PANTHEON_DEPLOYMENT_CONFIG__ = "
        line = next(l for l in text.splitlines() if l.startswith(prefix))
        cfg = json.loads(line[len(prefix):].rstrip(";"))
        self.assertEqual(
            set(cfg),
            {"schemaVersion", "environment", "configRef", "configSha256", "bffBaseUrl", "gcpIdentity"},
        )
        self.assertEqual(cfg["schemaVersion"], "pantheon.fe.public-runtime-config.v1")
        self.assertEqual(cfg["environment"], "staging")
        self.assertEqual(cfg["configRef"], "pantheon-20261024-001")
        self.assertEqual(set(cfg["gcpIdentity"]), {"apiKey", "projectId", "authDomain", "issuer", "audience"})
        self.assertNotIn("control_vm_broker_credentials_present", text)
        digest_src = {k: v for k, v in cfg.items() if k != "configSha256"}
        canonical = json.dumps(digest_src, sort_keys=True, separators=(",", ":"))
        self.assertEqual(cfg["configSha256"], "sha256:" + hashlib.sha256(canonical.encode()).hexdigest())

    def test_runtime_config_requires_public_identity_inputs(self) -> None:
        env = self._base_env(RELEASE_ID="pantheon-20261024-001", STAGING_PUBLIC_GCP_IDENTITY_API_KEY="")
        res = subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PUBLIC_IDENTITY_CONFIG_MISSING", res.stderr + res.stdout)

    def test_product_owner_inputs_fail_closed(self) -> None:
        res = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", STAGING_TENANT_ID=""),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("TENANT_ID_MISSING", res.stderr + res.stdout)

        self.setUp()
        self._unlock_vm_env_dir()
        original = self.secrets_env_file.read_text(encoding="utf-8")
        for key, bad in (
            ("EVOLUTION_AUTH_TOKEN", "pantheon-local-evolution-service"),
            ("PANTHEON_TELEMETRY_SERVICE_TOKEN", "telemetry-service-internal"),
            ("PERSONA_STORE_DSN", ""),
            ("AGORA_RESEARCH_STORE_DSN", "postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon"),
        ):
            with self.subTest(key=key):
                lines = [l for l in original.splitlines() if not l.startswith(f"{key}=")]
                if bad:
                    lines.append(f"{key}={bad}")
                self.secrets_env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                res = subprocess.run(
                    [str(PROVISION_SCRIPT)],
                    env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("SECRET_CONTENT_INVALID", res.stderr + res.stdout)

    def test_dsn_query_routing_override_rejected(self) -> None:
        self._unlock_vm_env_dir()
        original = self.secrets_env_file.read_text(encoding="utf-8")
        for override in ("dbname=pantheon_prod", "host=prod-db.internal", "port=6543"):
            with self.subTest(override=override):
                self.secrets_env_file.write_text(
                    original.replace(
                        "TELEMETRY_DB_DSN=postgresql://pantheon_stg:stg-pg-pass@postgres:5432/pantheon_staging",
                        f"TELEMETRY_DB_DSN=postgresql://pantheon_stg:stg-pg-pass@postgres:5432/pantheon_staging?{override}",
                    ),
                    encoding="utf-8",
                )
                res = subprocess.run(
                    [str(PROVISION_SCRIPT)],
                    env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("routing override", res.stderr + res.stdout)

    def test_bare_debian_bootstrap_secrets_and_real_collector_negative_gates(self) -> None:
        # 1. Missing runtime binary when apt install is disabled fails closed with RUNTIME_BINARY_MISSING
        bad_bin_env = self._base_env(CADDY_BIN="/nonexistent/caddy-binary", STAGING_ALLOW_APT_INSTALL="false")
        res_bin = subprocess.run([str(PROVISION_SCRIPT)], env=bad_bin_env, capture_output=True, text=True)
        self.assertNotEqual(res_bin.returncode, 0)
        self.assertIn("RUNTIME_BINARY_MISSING", res_bin.stderr)

        # 2. Insecure secret file permissions (0644) fail closed with SECRET_MODE
        self.setUp()
        self._unlock_vm_env_dir()
        self.secrets_env_file.chmod(0o644)
        self.pg_pw_file.chmod(0o000)
        for base in (self.staging_root, self.fe_root, self.bin_dir, self.data_mount, self.caddyfile_path.parent):
            for p in sorted([base, *base.rglob("*")], key=lambda x: len(str(x)), reverse=True):
                if p.is_dir():
                    p.chmod(0o555)
        res_mode = subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True)
        self.assertNotEqual(res_mode.returncode, 0)
        self.assertIn("SECRET_MODE", res_mode.stderr)

        # 3. Missing owner-closure service (e.g. governance not running) fails closed
        self.setUp()
        res_owner = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(FAKE_OMIT_SERVICE="governance"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_owner.returncode, 0)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", res_owner.stderr)

        # 3b. A missing product owner / request worker is as fatal as a missing core owner
        for omitted in ("registry", "agora-interaction-worker"):
            self.setUp()
            res_product = subprocess.run(
                [str(PROVISION_SCRIPT)],
                env=self._base_env(FAKE_OMIT_SERVICE=omitted),
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(res_product.returncode, 0, omitted)
            self.assertIn(f"Required service {omitted} is not running", res_product.stderr)

        # 4. Real collector gate failures (migration SQL error, pg_restore failure, cross-tenant HTTP 200 leak, live broker env leak)
        self.setUp()
        base_prov_env = self._base_env(RELEASE_ID="pantheon-20261024-001")
        subprocess.run([str(PROVISION_SCRIPT)], env=base_prov_env, capture_output=True, text=True, check=True)
        for gate_case, overrides, expected_err in (
            ("migration_sql_fail", {"FAKE_FAIL_MIGRATION_SQL": "true"}, "MIGRATION_SQL_FAILED"),
            ("pg_restore_fail", {"FAKE_PG_RESTORE_FAIL": "true"}, "PG_RESTORE_FAILED"),
            ("cross_tenant_leak", {"FAKE_CROSS_TENANT_STATUS": "200"}, "SECURITY_READBACK_FAILED"),
            ("live_broker_leak", {"FAKE_CONTAINER_LIVE_BROKER": "true"}, "SECURITY_READBACK_FAILED"),
            ("cacheable_runtime_cfg", {"FAKE_RUNTIME_CONFIG_CACHE_CONTROL": "public, max-age=3600"}, "RUNTIME_CONFIG_CACHEABLE"),
        ):
            with self.subTest(gate_case=gate_case):
                env = self._base_env(**overrides, RELEASE_ID="pantheon-20261024-001")
                ver = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True)
                self.assertNotEqual(ver.returncode, 0)
                self.assertIn("LIVE_GATE_COLLECTION_FAILED", ver.stderr)
                self.assertIn(expected_err, ver.stderr)

    def test_collector_rejects_tampered_migration_files_and_missing_owner_store(self) -> None:
        subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
            check=True,
        )
        mig_file = self.data_mount / "sanitized" / "migrations" / self.migration_files[0]["name"]
        original = mig_file.read_bytes()
        self._unlock_vm_env_dir()
        mig_file.write_bytes(original + b"\n-- tampered\n")
        ver = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(ver.returncode, 0)
        self.assertIn("MIGRATION_FILE_SHA_MISMATCH", ver.stderr)

        self._unlock_vm_env_dir()
        mig_file.write_bytes(original)
        shutil.rmtree(self.data_mount / "governance")
        ver = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(ver.returncode, 0)
        self.assertIn("OWNER_STORE_MISSING", ver.stderr)

    def _provision_and_verify(self, **env: str) -> subprocess.CompletedProcess:
        subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", **env),
            capture_output=True,
            text=True,
            check=True,
        )
        self._unlock_vm_env_dir()
        (self.vm_env_dir / "pg-sim").mkdir(exist_ok=True)
        return subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", **env),
            capture_output=True,
            text=True,
        )

    def test_file_backed_native_owner_state_survives_recreation_and_restore(self) -> None:
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr)
        ack = "stg-ack-pantheon-20261024-001"
        gov = json.loads((self.data_mount / "governance" / "approval_decisions.json").read_text())
        self.assertIn(ack, gov)
        bff = json.loads((self.data_mount / "bff" / "session_lifecycle.json").read_text())
        self.assertEqual(bff["sessions"][ack]["state"], "acknowledged")

    def test_pg_backed_governance_needs_no_json_files_and_acks_through_native_store(self) -> None:
        ver = self._provision_and_verify(FAKE_GOVERNANCE_BACKEND="postgres")
        self.assertEqual(ver.returncode, 0, ver.stderr)
        ack = "stg-ack-pantheon-20261024-001"
        self.assertFalse(list((self.data_mount / "governance").glob("*.json")))
        sim = json.loads((self.vm_env_dir / "pg-sim" / "approval_decisions.json").read_text())
        self.assertIn(ack, sim)

    def test_collector_rejects_ack_lost_on_owner_recreation(self) -> None:
        ver = self._provision_and_verify(FAKE_WIPE_OWNER_ON_RECREATE="true")
        self.assertNotEqual(ver.returncode, 0)
        self.assertIn("OWNER_ACK_LOST", ver.stderr)

    def test_runtime_manager_container_id_resolved_after_dependency_recreation(self) -> None:
        # The fake compose recreates runtime-manager (new container ID) whenever operator-bff is
        # reconciled; inspecting the ID frozen at collection start would fail with "No such container".
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr)
        self.assertNotIn("DOCKER_INSPECT_ENV_FAILED", ver.stderr)

    def test_collector_waits_for_owner_health_starting_to_healthy(self) -> None:
        ver = self._provision_and_verify(FAKE_OWNER_HEALTH="starting_then_healthy", STAGING_READY_INTERVAL="0")
        self.assertEqual(ver.returncode, 0, ver.stderr)

    def test_collector_rejects_persistently_unhealthy_owner(self) -> None:
        ver = self._provision_and_verify(
            FAKE_OWNER_HEALTH="unhealthy", STAGING_READY_INTERVAL="0.05", STAGING_OWNER_READY_TIMEOUT="0.3"
        )
        self.assertNotEqual(ver.returncode, 0)
        self.assertIn("PRODUCT_OWNER_UNSERVED", ver.stderr)

    def test_collector_waits_for_baseline_owner_health_starting_to_healthy(self) -> None:
        ver = self._provision_and_verify(FAKE_BASELINE_OWNER_HEALTH="starting_then_healthy", STAGING_READY_INTERVAL="0")
        self.assertEqual(ver.returncode, 0, ver.stderr)

    def test_collector_rejects_persistently_unhealthy_baseline_owner(self) -> None:
        ver = self._provision_and_verify(
            FAKE_BASELINE_OWNER_HEALTH="unhealthy", STAGING_READY_INTERVAL="0.05", STAGING_OWNER_READY_TIMEOUT="0.3"
        )
        self.assertNotEqual(ver.returncode, 0)
        self.assertIn("ROLLBACK_BASELINE_UNHEALTHY", ver.stderr)

    def test_bff_and_worker_share_durable_agora_stores(self) -> None:
        compose = yaml.safe_load(COMPOSE_STAGING.read_text(encoding="utf-8"))["services"]
        bff = compose["operator-bff"]["environment"]
        worker = compose["agora-interaction-worker"]["environment"]
        for kind in ("WORKSHOP", "DATASET", "GOVERNANCE", "RESEARCH"):
            for suffix in ("BACKEND", "SCHEMA"):
                key = f"AGORA_{kind}_STORE_{suffix}"
                self.assertIn(key, bff, key)
                self.assertEqual(bff[key], worker[key], key)
            self.assertEqual(bff[f"AGORA_{kind}_STORE_BACKEND"], "postgres")

    def test_collector_rejects_corrupt_runtime_owner_state(self) -> None:
        subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
            check=True,
        )
        self._unlock_vm_env_dir()
        (self.data_mount / "runtime" / "runtime_bindings.json").write_text("{not json", encoding="utf-8")
        ver = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(ver.returncode, 0)
        self.assertIn("OWNER_STORE_CORRUPT", ver.stderr)

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
        self.assertIn("--service-account=pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com", inst_create_cmd)
        self.assertIn("name=pantheon-stg-pantheon-20261024-001-boot,device-name=pantheon-stg-pantheon-20261024-001-boot,boot=yes,mode=rw,auto-delete=no", inst_create_cmd)
        self.assertIn("name=pantheon-stg-data-pantheon-20261024-001,device-name=staging-data,mode=rw,auto-delete=no", inst_create_cmd)
        self.assertTrue(any("compute scp" in c and "--tunnel-through-iap" in c for c in cmds))
        self.assertTrue(any("compute ssh pantheon-stg-pantheon-20261024-001" in c and "--tunnel-through-iap" in c for c in cmds))

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
        self.assertEqual(first_json["resource_count"], 4)

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
        sa_member = "serviceAccount:pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com"
        cloud_state["iam_bindings"] = [
            {"role": "roles/logging.logWriter", "members": [sa_member]},
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
            {"role": "roles/logging.logWriter", "members": [sa_member]},
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
                "statements": ["ALTER TABLE telemetry_events ADD COLUMN staging_note TEXT NULL;"],
                "old_app_schema": {"public.telemetry_events": {"event_id": {"type": "text"}}},
                "new_schema": {
                    "public.telemetry_events": {
                        "event_id": {"type": "text"},
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
            json.dumps(_sample_backup_record()),
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
                "served_backend_images": rb["backend_images"],
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
            manifest = _sample_manifest(release_id=rel_id, fe_sha=self.fe_sha, prev_fe_sha=self.fe_baseline_sha, mig_sha=self.migration_set_sha)
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
            ver = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True)
            self.assertEqual(ver.returncode, 0, ver.stderr)
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

    def test_pr6798_five_blocking_source_findings_regressions(self) -> None:
        # Finding 1: Missing or tampered rollback_baseline FE tarball fails closed before/during bootstrap;
        # real unpacked baseline archive and full 5-service owner closure are verified on rollback.
        missing_base_fe = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(
                STAGING_FE_BASELINE_ARTIFACT_PATH=str(self.tmp_path / "missing-baseline.tar.gz"),
                STAGING_FE_BASELINE_ARTIFACT_URI="",
            ),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(missing_base_fe.returncode, 0)
        self.assertIn("BASELINE_FE_ARTIFACT_MISSING", missing_base_fe.stderr)

        tampered_base_tar = self.tmp_path / "tampered-baseline.tar.gz"
        _build_fe_tarball(tampered_base_tar, content="tampered-baseline-fe")
        mismatch_base_fe = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(STAGING_FE_BASELINE_ARTIFACT_PATH=str(tampered_base_tar)),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(mismatch_base_fe.returncode, 0)
        self.assertIn("immutable baseline FE artifact checksum mismatch", mismatch_base_fe.stderr)

        # Finding 5: Non-root OS Login without sudo elevation or without metadata token fails closed
        self.setUp()
        no_sudo_prov = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(SUDO_BIN=""),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(no_sudo_prov.returncode, 0)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", no_sudo_prov.stderr)

        self.setUp()
        no_meta_tok_prov = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(FAKE_METADATA_TOKEN_FAIL="true"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(no_meta_tok_prov.returncode, 0)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", no_meta_tok_prov.stderr)

        # Provision cleanly via GCS URIs (including gs://pantheon-stg-20260902-release-artifacts/...)
        self.setUp()
        prov_env = self._base_env(
            RELEASE_ID="pantheon-20261024-001",
            STAGING_FE_ARTIFACT_PATH="",
            STAGING_FE_ARTIFACT_URI="gs://pantheon-stg-20260902-release-artifacts/fe-bundle.tar.gz",
            STAGING_FE_BASELINE_ARTIFACT_PATH="",
            STAGING_FE_BASELINE_ARTIFACT_URI="gs://pantheon-stg-20260902-release-artifacts/fe-baseline-bundle.tar.gz",
        )
        subprocess.run([str(PROVISION_SCRIPT)], env=prov_env, capture_output=True, text=True, check=True)

        # Finding 3: Live catalog observation (no fallback to mig_spec) and old-app execution against new schema
        res_no_catalog_change = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env={**prov_env, "FAKE_SKIP_SCHEMA_MUTATION": "true"},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_no_catalog_change.returncode, 0)
        self.assertIn("MIGRATION_NOT_APPLIED", res_no_catalog_change.stderr)

        res_old_app_broken = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env={**prov_env, "FAKE_OLD_APP_INCOMPATIBLE": "true"},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_old_app_broken.returncode, 0)
        self.assertIn("MIGRATION_VERIFICATION_FAILED", res_old_app_broken.stderr)

        # Finding 1 on-host tamper check: if baseline index.html is replaced with a fabricated placeholder,
        # pantheon-staging-collect-gates rejects it with BASELINE_FE_CONTENT_TAMPERED.
        self._unlock_vm_env_dir()
        baseline_index = self.fe_root / "releases" / "pantheon-20261023-001" / "index.html"
        orig_baseline_bytes = baseline_index.read_bytes()
        baseline_index.write_text("<!doctype html><title>Pantheon Rollback Baseline</title>", encoding="utf-8")
        self._lock_root_owned_paths()
        tampered_ver = subprocess.run([str(VERIFY_SCRIPT)], env=prov_env, capture_output=True, text=True)
        self.assertNotEqual(tampered_ver.returncode, 0)
        self.assertIn("BASELINE_FE_CONTENT_TAMPERED", tampered_ver.stderr)
        self._unlock_vm_env_dir()
        baseline_index.write_bytes(orig_baseline_bytes)
        self._lock_root_owned_paths()

        # Finding 2: Returning 401 or 405 instead of 403 FORBIDDEN on cross-tenant or viewer mutation
        # is rejected fail-closed (never treated as a passing rejection).
        for bad_case, bad_overrides in (
            ("cross_tenant_returns_401", {"FAKE_CROSS_TENANT_STATUS": "401"}),
            ("cross_tenant_returns_405", {"FAKE_CROSS_TENANT_STATUS": "405"}),
            ("viewer_mutation_returns_401", {"FAKE_VIEWER_MUTATION_STATUS": "401"}),
            ("viewer_mutation_returns_405", {"FAKE_VIEWER_MUTATION_STATUS": "405"}),
        ):
            with self.subTest(bad_case=bad_case):
                res_sec = subprocess.run(
                    [str(VERIFY_SCRIPT)],
                    env={**prov_env, **bad_overrides},
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res_sec.returncode, 0)
                self.assertIn("SECURITY_READBACK_FAILED", res_sec.stderr)

        # Finding 4: Verify full gate pass and inspect vm_bin.log for separate DROP/CREATE DATABASE calls,
        # docker login via metadata token, live information_schema query, and real /bff/me, /bff/switch-tenant,
        # and /bff/command-confirmations probes.
        ver_ok = subprocess.run([str(VERIFY_SCRIPT)], env=prov_env, capture_output=True, text=True, check=True)
        ver_payload = json.loads(ver_ok.stdout)
        self.assertEqual(ver_payload["status"], "staging_verified")
        vm_logs = self.vm_bin_log_file.read_text(encoding="utf-8").splitlines()
        self.assertTrue(any(l.startswith("docker login ") and "oauth2accesstoken" in l for l in vm_logs))
        self.assertTrue(any("FROM information_schema.columns" in l for l in vm_logs))
        self.assertFalse(any("PRE_MIGRATION_SCHEMA" in l or "POST_MIGRATION_SCHEMA" in l for l in vm_logs))
        self.assertTrue(any('DROP DATABASE IF EXISTS "pantheon_staging_restore_verify";' in l and "CREATE DATABASE" not in l for l in vm_logs))
        self.assertTrue(any('CREATE DATABASE "pantheon_staging_restore_verify";' in l and "DROP DATABASE" not in l for l in vm_logs))
        self.assertTrue(any(l.endswith("/bff/me") for l in vm_logs))
        self.assertTrue(any(l.endswith("/bff/switch-tenant") for l in vm_logs))
        self.assertTrue(any(l.endswith("/bff/command-confirmations") for l in vm_logs))


class WorkflowAndTemplateContractTests(unittest.TestCase):
    def test_ephemeral_workflow_wires_public_identity_vars_to_host(self) -> None:
        raw = EPHEMERAL_WORKFLOW.read_text(encoding="utf-8")
        ops = (REPO_ROOT / "scripts" / "deploy" / "staging_ops.sh").read_text(encoding="utf-8")
        for name in ("API_KEY", "PROJECT_ID", "AUTH_DOMAIN", "ISSUER", "AUDIENCE"):
            var = f"STAGING_PUBLIC_GCP_IDENTITY_{name}"
            self.assertIn(f"{var}: ${{{{ vars.{var}", raw)
            self.assertIn(f'"{var}"', ops)
        gate = raw.split("Gate staging authority", 1)[1].split("Authenticate to staging", 1)[0]
        for name in ("API_KEY", "PROJECT_ID", "AUTH_DOMAIN"):
            self.assertIn(f"STAGING_PUBLIC_GCP_IDENTITY_{name}", gate)

    def test_ephemeral_workflow_forwards_bootstrap_inputs_to_host(self) -> None:
        raw = EPHEMERAL_WORKFLOW.read_text(encoding="utf-8")
        ops = (REPO_ROOT / "scripts" / "deploy" / "staging_ops.sh").read_text(encoding="utf-8")
        for var in (
            "STAGING_SECRETS_GSM_NAME",
            "STAGING_POSTGRES_PASSWORD_GSM_NAME",
            "STAGING_IDP_ISSUER",
            "STAGING_POSTGRES_IMAGE",
            "STAGING_NATS_IMAGE",
        ):
            self.assertIn(f"{var}: ${{{{ vars.{var} || '' }}}}", raw)
            self.assertIn(f'"{var}"', ops)

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
                for k, v in (job.get("env") or {}).items():
                    self.assertIsNone(
                        disallowed_job_env_ctx.search(str(v)),
                        f"{wf_path.name} jobs.{job_id}.env.{k} uses forbidden context: {v}",
                    )
                for step in job.get("steps", []):
                    uses = str(step.get("uses") or "")
                    if uses.startswith("google-github-actions/auth@"):
                        has_wif_auth = True
                        sa_expr = str((step.get("with") or {}).get("service_account") or "")
                        self.assertIn("STAGING_GCP_DEPLOY_SERVICE_ACCOUNT", sa_expr)
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
        self.assertEqual(tpl["name"], "pantheon-stg-template-20261024")
        self.assertEqual(tpl["properties"]["machineType"], "e2-standard-2")
        self.assertFalse(tpl["properties"]["scheduling"]["preemptible"])
        self.assertEqual(tpl["properties"]["scheduling"]["provisioningModel"], "STANDARD")
        self.assertEqual(
            tpl["properties"]["serviceAccounts"][0]["email"],
            "pantheon-stg-runtime@pantheon-stg-20260902.iam.gserviceaccount.com",
        )
        self.assertIn("pantheon-iap", tpl["properties"]["tags"]["items"])
        self.assertIn("pantheon-https", tpl["properties"]["tags"]["items"])

        compose = yaml.safe_load(COMPOSE_STAGING.read_text(encoding="utf-8"))
        for svc_name in ("postgres", "nats", "runtime-manager", "governance", "deployment", "lifecycle-projector", "operator-bff"):
            svc = compose["services"][svc_name]
            self.assertEqual(svc["pull_policy"], "never")
            self.assertNotIn("build", svc)

        pg = compose["services"]["postgres"]
        self.assertEqual(pg["environment"]["POSTGRES_PASSWORD_FILE"], "/run/secrets/postgres_password")
        self.assertIn("postgres_password", pg["secrets"])

        bff = compose["services"]["operator-bff"]
        self.assertEqual(bff["environment"]["PANTHEON_BFF_AUTH_MODE"], "strict")
        self.assertEqual(bff["environment"]["PANTHEON_BFF_AUTH_STUB"], "false")

        self.assertEqual(bff["ports"], ["127.0.0.1:${STAGING_BFF_PORT:-18001}:8001"])
        self.assertEqual(bff["environment"]["PANTHEON_RUNTIME_MANAGER_URL"], "http://runtime-manager:8080")
        for key in ("PANTHEON_GOVERNANCE_API_URL", "PANTHEON_GOVERNANCE_SERVICE_URL", "PANTHEON_GOVERNANCE_APPROVAL_API_URL"):
            self.assertEqual(bff["environment"][key], "http://governance:8082")
        for key in ("PANTHEON_DEPLOYMENT_API_URL", "PANTHEON_DEPLOYMENT_SERVICE_URL"):
            self.assertEqual(bff["environment"][key], "http://deployment:8095")
        lp = compose["services"]["lifecycle-projector"]
        self.assertEqual(lp["command"], ["python", "-m", "services.trade_journey.lifecycle_projector", "run"])
        self.assertEqual(lp["environment"]["GIT_SHA"], "${PANTHEON_BACKEND_COMMIT:?backend commit required}")

        caddy_raw = CADDY_TEMPLATE.read_text(encoding="utf-8")
        self.assertIn("handle /runtime-config.js", caddy_raw)
        self.assertIn("root * __STAGING_CONFIG_DIR__", caddy_raw)
        self.assertTrue(BOOTSTRAP_SCRIPT.exists() and os.access(BOOTSTRAP_SCRIPT, os.X_OK))
        self.assertTrue(COLLECTOR_SCRIPT.exists() and os.access(COLLECTOR_SCRIPT, os.X_OK))

    def test_collector_does_not_require_custom_image_labels(self) -> None:
        # Actual retained BFF image carries no org.pantheon.* migration/compat labels; the
        # contract is manifest + migration-spec + live catalog/checksum observations instead.
        raw = COLLECTOR_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("org.pantheon.migration_set_sha256", raw)
        self.assertNotIn("org.pantheon.compatibility_manifest_sha256", raw)
        self.assertIn("MIGRATION_SPEC_SHA_MISMATCH", raw)


if __name__ == "__main__":
    unittest.main()

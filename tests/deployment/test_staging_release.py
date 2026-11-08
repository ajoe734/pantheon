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
import time
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
    "RECONCILER_LOOP_CONTROLLER_DSN",
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
from datetime import datetime, timedelta, timezone
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
                    for child in sd.rglob("*"):
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
    while cmd_args and cmd_args[0] in ("-E", "--preserve-env", "-n", "--non-interactive"):
        cmd_args.pop(0)
    if not cmd_args:
        raise SystemExit(0)
    if (
        Path(cmd_args[0]).parent == Path(sys.argv[0]).parent
        and Path(cmd_args[0]).name == "docker"
        and (
            cmd_args[1:2] in (["pull"], ["login"], ["inspect"], ["image"], ["logs"])
            or (cmd_args[1:2] == ["compose"] and ("ps" in cmd_args or "version" in cmd_args))
        )
    ):
        os.environ["PANTHEON_PRIVILEGED"] = "1"
        tool = "docker"
        args = cmd_args[1:]
        with vm_log_file.open("a", encoding="utf-8") as lf:
            lf.write(f"{tool} {' '.join(args)}\n")
    else:
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
        idx = 1
        extra_env = {}
        while idx < len(args) and args[idx].startswith("-"):
            flag = args[idx]
            if flag in ("-e", "--env") and idx + 1 < len(args):
                env_spec = args[idx + 1]
                if "=" in env_spec:
                    ek, ev = env_spec.split("=", 1)
                    extra_env[ek] = ev
                elif env_spec in os.environ:
                    extra_env[env_spec] = os.environ[env_spec]
                idx += 2
            elif flag.startswith("--env="):
                env_spec = flag.split("=", 1)[1]
                if "=" in env_spec:
                    ek, ev = env_spec.split("=", 1)
                    extra_env[ek] = ev
                elif env_spec in os.environ:
                    extra_env[env_spec] = os.environ[env_spec]
                idx += 1
            else:
                idx += 1
        cid = args[idx]
        exec_cmd = args[idx + 1:]
        cinfo = next((i for i in st.get("containers", {}).values() if i.get("cid") == cid), None)
        if not cinfo or not cinfo.get("running"):
            print(f"Error response from daemon: container {cid} is not running", file=sys.stderr)
            raise SystemExit(1)
        if exec_cmd and Path(exec_cmd[0]).name in ("psql", "pg_dump", "pg_restore"):
            tool_basename = Path(exec_cmd[0]).name
            if not str(cinfo.get("image", "")).startswith("postgres:16"):
                print(
                    f"Refusing to run {tool_basename} in non-PG16 container ({cinfo.get('image')!r})",
                    file=sys.stderr,
                )
                raise SystemExit(1)
            pg_exec_env = os.environ.copy()
            pg_exec_env.update(extra_env)
            pg_exec_env["PANTHEON_PG_CLIENT_MAJOR"] = "16"
            host_tool = Path(os.environ["FAKE_HOST_BIN_DIR"]) / tool_basename
            proc = subprocess.run([str(host_tool), *exec_cmd[1:]], env=pg_exec_env, capture_output=True, text=True)
            sys.stdout.write(proc.stdout)
            sys.stderr.write(proc.stderr)
            raise SystemExit(proc.returncode)
        # Runs the collector's native-store snippet against the real repo store classes, with the
        # container's own env and its /data/<owner> mounts mapped onto the fake data disk.
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

    if args[:1] == ["logs"]:
        secret = Path(os.environ["STAGING_POSTGRES_PASSWORD_FILE"]).read_text().strip()
        print("business line strategy=alpha-77 result_id=res-123 password=" + secret)
        print("Traceback (most recent call last):")
        print('  File "/workspace/services/control-plane/bff/main.py", line 42, in main')
        print("    connect(password='" + secret + "')")
        print('  File "/etc/private/secret.py", line 7, in leak')
        print('  File "/workspace/runtime/paper-performance/strategy_alpha77.py", line 9, in result_res123')
        print("ResearchResult_res123_Strategy_alpha77Error: nope")
        print("RuntimeError: boom strategy=alpha-77 " + secret)
        raise SystemExit(0)

    if args[:1] == ["inspect"]:
        cid = args[-1]
        fmt = ""
        for i, a in enumerate(args):
            if a == "-f" and i + 1 < len(args):
                fmt = args[i + 1]
        cinfo = None
        target_svc = ""
        for svc, info in st.get("containers", {}).items():
            if info.get("cid") == cid:
                cinfo = info
                target_svc = svc
                break
        if not cinfo:
            print(f"No such container: {cid}", file=sys.stderr)
            raise SystemExit(1)
        if fmt == "{{.Config.Image}}":
            print(cinfo["image"])
        elif fmt == "{{.State.StartedAt}}":
            override_started = os.environ.get("FAKE_OWNER_STARTED_AT", "").strip()
            print(override_started or cinfo.get("started_at") or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ"))
        elif "State.Running" in fmt:
            running = "true" if cinfo.get("running", True) else "false"
            mode = os.environ.get(
                "FAKE_BASELINE_OWNER_HEALTH" if cinfo.get("baseline") else "FAKE_OWNER_HEALTH", ""
            )
            no_healthcheck_svcs = {
                "runtime-manager",
                "operator-bff",
                "lifecycle-projector",
                "consultation-svc",
                "agora-interaction-worker",
            }
            starting_health_svc = os.environ.get("FAKE_STARTING_HEALTH_OWNER", "").strip()
            health = ""
            if starting_health_svc and target_svc == starting_health_svc:
                health = "starting"
            elif mode and target_svc not in no_healthcheck_svcs | {"governance", "deployment"}:
                counter = vm_state_file.with_name("health-" + cid + ".count")
                n = int(counter.read_text()) if counter.exists() else 0
                counter.write_text(str(n + 1))
                if mode == "unhealthy":
                    health = "unhealthy"
                elif mode == "starting":
                    health = "starting"
                else:
                    health = "healthy" if n >= 2 else "starting"
            elif target_svc not in no_healthcheck_svcs:
                health = "healthy"
            if "RestartCount" in fmt:
                if cinfo.get("baseline"):
                    crashloop_svc = os.environ.get("FAKE_BASELINE_CRASHLOOP_OWNER", "").strip()
                elif st.get("rollback_rehearsal_done"):
                    crashloop_svc = (
                        os.environ.get("FAKE_RESTORE_CRASHLOOP_OWNER", "").strip()
                        or os.environ.get("FAKE_CRASHLOOP_OWNER", "").strip()
                    )
                else:
                    crashloop_svc = os.environ.get("FAKE_CRASHLOOP_OWNER", "").strip()
                short_uptime_svc = os.environ.get("FAKE_SHORT_UPTIME_OWNER", "").strip()
                early_restart_svc = os.environ.get("FAKE_EARLY_RESTART_OWNER", "").strip()
                uptime_sec = float(os.environ.get("FAKE_DEFAULT_OWNER_UPTIME_SECONDS", "60"))
                if crashloop_svc and target_svc == crashloop_svc:
                    rc_file = vm_state_file.with_name("restart-" + cid + ".count")
                    rc = int(rc_file.read_text()) if rc_file.exists() else 0
                    rc_file.write_text(str(rc + 1))
                    uptime_sec = 3.0
                elif short_uptime_svc and target_svc == short_uptime_svc:
                    sb_file = vm_state_file.with_name("short-uptime-" + cid + ".count")
                    sb_n = int(sb_file.read_text()) if sb_file.exists() else 0
                    sb_file.write_text(str(sb_n + 1))
                    rc = int(cinfo.get("restart_count", 0))
                    uptime_sec = float(os.environ.get("FAKE_SHORT_UPTIME_SECONDS", "3"))
                elif early_restart_svc and target_svc == early_restart_svc:
                    er_file = vm_state_file.with_name("early-restart-" + cid + ".count")
                    er_n = int(er_file.read_text()) if er_file.exists() else 0
                    er_file.write_text(str(er_n + 1))
                    if er_n == 0:
                        rc = 0
                        uptime_sec = 2.0
                    elif er_n == 1:
                        rc = 1
                        uptime_sec = 3.0
                    elif er_n == 2:
                        rc = 1
                        uptime_sec = 8.0
                    else:
                        rc = 1
                        uptime_sec = 20.0
                else:
                    rc = int(cinfo.get("restart_count", 0))
                if "State.StartedAt" in fmt:
                    inspect_started_at = (
                        datetime.now(timezone.utc) - timedelta(seconds=uptime_sec)
                    ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                    print(f"{running} {rc} {inspect_started_at}" + (f" {health}" if health else ""))
                else:
                    print(f"{running} {rc}" + (f" {health}" if health else ""))
            else:
                print(running + (" " + health if health else ""))
        elif "State.Status" in fmt:
            print("exited 1 false 2 true")
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
        for k in list(c_env):
            if k in os.environ:
                c_env[k] = os.environ[k]

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
            profiles = [args[i + 1] for i, a in enumerate(args) if a == "--profile" and i + 1 < len(args)]
            if svc not in ("postgres", "nats") and "core" not in profiles and "stateful" not in profiles:
                print(
                    f'service "{svc}" depends on undefined service "nats": invalid compose project',
                    file=sys.stderr,
                )
                raise SystemExit(1)
            if svc == "lifecycle-projector" and "singleton" not in profiles and "core" not in profiles:
                print(f'no such service: {svc}', file=sys.stderr)
                raise SystemExit(1)
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
                "signal-store": "PANTHEON_SIGNAL_STORE_IMAGE",
                "runtime-manager": "PANTHEON_RUNTIME_MANAGER_IMAGE",
                "governance": "PANTHEON_GOVERNANCE_IMAGE",
                "deployment": "PANTHEON_DEPLOYMENT_IMAGE",
                "lifecycle-projector": "PANTHEON_LIFECYCLE_PROJECTOR_IMAGE",
                "operator-bff": "PANTHEON_OPERATOR_BFF_IMAGE",
            }
            for product_svc in (
                "registry telemetry incidents postmortems evolution lineage-read reconciliation-drift-svc persona "
                "consultation-svc source-ingest training-session-svc policy-learning-svc "
                "research-orchestrator-svc agora-interaction-worker paper-fleet-reconciler paper-signal-producer "
                "memory capital openclaw-gateway-adapter"
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
                started_at_val = (
                    os.environ.get("FAKE_OWNER_STARTED_AT", "").strip()
                    if svc not in ("postgres", "nats", "signal-store")
                    else ""
                ) or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                st.setdefault("containers", {})[svc] = {
                    "cid": f"cid-{svc}-{st.setdefault('cid_gen', 0) + 1}",
                    "image": img_ref,
                    "running": True,
                    "started_at": started_at_val,
                    "baseline": bool(env_file and env_file.name == "compose.baseline.env"),
                    "env": merged_svc_env,
                }
                if svc == "paper-fleet-reconciler" and os.environ.get("FAKE_SKIP_RUNTIME_LEASE_WRITE") != "true":
                    db_tables = (
                        st.setdefault("databases", {})
                        .setdefault("pantheon_staging", {})
                        .setdefault("tables", {})
                    )
                    loop_rows = list(db_tables.get("public.loop_controller_records") or [])
                    loop_rows = [
                        r for r in loop_rows if r.get("controller_id") != "runtime-paper-fleet-reconciler"
                    ]
                    loop_rows.append(
                        {
                            "controller_id": "runtime-paper-fleet-reconciler",
                            "loop_id": "paper-fleet-reconciler-loop",
                            "tenant_id": c_env.get("STAGING_TENANT_ID", "tenant-alpha"),
                            "payload": {
                                "_loop_controller_record": {
                                    "lease_token": "runtime-lease-token-after-owner-start",
                                    "state": "active",
                                }
                            },
                            "evidence_refs": [],
                            "updated_at": started_at_val,
                        }
                    )
                    db_tables["public.loop_controller_records"] = loop_rows
            st["cid_gen"] = st.get("cid_gen", 0) + 1
            if env_file and env_file.name == "compose.baseline.env" and "governance" in target_svcs:
                st["rollback_rehearsal_done"] = True
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
        if all_up and os.environ.get("FAKE_BFF_RESTART_WARMUP") == "true":
            bff_cid = st.get("containers", {}).get("operator-bff", {}).get("cid", "")
            if bff_cid:
                warmup_file = vm_state_file.with_name("readyz-warmup-" + bff_cid + ".count")
                wn = int(warmup_file.read_text()) if warmup_file.exists() else 0
                warmup_file.write_text(str(wn + 1))
                if wn < 1:
                    all_up = False
        if (
            all_up
            and st.get("rollback_rehearsal_done")
            and os.environ.get("FAKE_READYZ_REQUIRES_OWNERS_HEALTHY") == "true"
            and os.environ.get("FAKE_OWNER_HEALTH") == "starting_then_healthy"
        ):
            bff_info = st.get("containers", {}).get("operator-bff", {})
            if not bff_info.get("baseline"):
                reg_cid = st.get("containers", {}).get("registry", {}).get("cid", "")
                if reg_cid:
                    reg_counter = vm_state_file.with_name("health-" + reg_cid + ".count")
                    rn = int(reg_counter.read_text()) if reg_counter.exists() else 0
                    if rn < 2:
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
        exp = int(claims.get("exp") or 0)
        iat = int(claims.get("iat") or 0)
        if exp - iat != 300:
            return None, "AUTH_TTL_INVALID"
        now_epoch = int(datetime.now(timezone.utc).timestamp()) + int(st.get("simulated_elapsed_seconds", 0))
        if exp <= now_epoch:
            return None, "AUTH_EXPIRED"
        if st.get("rollback_rehearsal_done"):
            if token in st.get("pre_rollback_tokens", []):
                return None, "AUTH_EXPIRED"
        else:
            pre_toks = st.setdefault("pre_rollback_tokens", [])
            if token not in pre_toks:
                pre_toks.append(token)
                save_state(st)
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
    if st.get("containers", {}).get("postgres", {}).get("running") is not True:
        print(
            'psql: error: connection to server at "127.0.0.1", port 15432 failed: Connection refused',
            file=sys.stderr,
        )
        raise SystemExit(2)
    pg_img = str(st.get("containers", {}).get("postgres", {}).get("image", ""))
    client_major = os.environ.get("PANTHEON_PG_CLIENT_MAJOR", "15")
    if pg_img.startswith("postgres:16") and client_major != "16":
        print(
            f"{tool}: error: server version: 16.15; {tool} version: 15.19 (Debian 15.19-0+deb12u1)\n"
            f"{tool}: error: aborting because of server version mismatch",
            file=sys.stderr,
        )
        raise SystemExit(1)
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
        if os.environ.get("FAKE_CONCURRENT_OWNER_STORE_WRITE_DURING_PG_DUMP") == "true":
            bg_file = Path(os.environ["STAGING_DATA_MOUNT"]) / "bff" / "concurrent_bg_write.json"
            bg_file.write_text(json.dumps({"bg_tick": datetime.now(timezone.utc).isoformat()}), encoding="utf-8")
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
            print("ERROR: syntax or lock error executing migration SQL order_id=ORD-SYNTH-7788 buyer@synthetic.example", file=sys.stderr)
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
    if fail_on == "compute ssh":
        for hint in (
            "",
            "Recommendation: To check for possible causes of SSH connectivity issues and get",
            "recommendations, rerun the ssh command with the --troubleshoot option.",
            "gcloud compute ssh x --troubleshoot --tunnel-through-iap",
            "Or, to investigate an IAP tunneling issue:",
            "https://cloud.google.com/iap/docs/using-tcp-forwarding#tunneling_with_ssh",
        ):
            print(hint, file=sys.stderr)
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
    if any(t == "--labels" or t.startswith("--labels=") for t in args):
        print("ERROR: (gcloud.compute.addresses.create) unrecognized arguments: --labels", file=sys.stderr)
        raise SystemExit(2)
    labels = {}
    ip_val = f"203.0.113.{10 + len(state['addresses'])}"
    obj = {
        "id": str(1000 + len(state["addresses"])),
        "name": name,
        "project_id": project,
        "region": region,
        "address": ip_val,
        "selfLink": f"https://www.googleapis.com/compute/v1/projects/{project}/regions/{region}/addresses/{name}",
        "labels": labels,
        "description": parse_flag("--description"),
        "status": "RESERVED",
    }
    state["addresses"] = [a for a in state["addresses"] if a["name"] != name] + [obj]
    save_state()
    print(json.dumps(obj))
    raise SystemExit(0)

if args[:3] == ["compute", "addresses", "update"]:
    name = args[3]
    match = [a for a in state["addresses"] if a["name"] == name]
    if not match:
        print(f"address {name} not found", file=sys.stderr)
        raise SystemExit(1)
    match[0]["labels"] = {**match[0]["labels"], **parse_labels(parse_flag("--update-labels"))}
    save_state()
    print(json.dumps(match[0]))
    raise SystemExit(0)

if args[:3] == ["compute", "addresses", "describe"]:
    match = [a for a in state["addresses"] if a["name"] == args[3]]
    if not match:
        print(f"address {args[3]} not found", file=sys.stderr)
        raise SystemExit(1)
    print(json.dumps(match[0]))
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
        in_use = [
            i["name"]
            for i in state.get("instances", [])
            if any(d.get("name") == name for d in (i.get("disks") or []))
        ]
        if in_use:
            print(f"The disk resource '{name}' is already being used by '{in_use[0]}'", file=sys.stderr)
            raise SystemExit(1)
        state["disks"] = [d for d in state["disks"] if d["name"] != name]
        save_state()
    print(json.dumps({"deleted": name}))
    raise SystemExit(0)

if args[:3] == ["compute", "instance-templates", "describe"]:
    if os.environ.get("GCLOUD_TEMPLATE_NO_SUBNET") == "true":
        nic = {"network": "https://www.googleapis.com/compute/v1/projects/pantheon-stg-20260902/global/networks/pantheon-staging"}
    else:
        nic = {
            "network": "https://www.googleapis.com/compute/v1/projects/pantheon-stg-20260902/global/networks/pantheon-staging",
            "subnetwork": "https://www.googleapis.com/compute/v1/projects/pantheon-stg-20260902/regions/asia-east1/subnetworks/pantheon-staging-asia-east1",
        }
    print(json.dumps({"name": args[3], "properties": {"networkInterfaces": [nic], "serviceAccounts": [{"email": "template-sa@example.invalid", "scopes": ["https://www.googleapis.com/auth/cloud-platform"]}]}}))
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
        # Real gcloud fills the 7 default scopes (no cloud-platform) unless --scopes is given.
        "serviceAccounts": [{"email": sa_email, "scopes": (parse_flag("--scopes").split(",") if parse_flag("--scopes") and os.environ.get("GCLOUD_IGNORE_SCOPES") != "true" else ["https://www.googleapis.com/auth/devstorage.read_only", "https://www.googleapis.com/auth/logging.write"])}],
        "networkInterfaces": [
            {
                # Real gcloud falls back to the default network/subnet when the NIC is not restated.
                "network": (None if os.environ.get("GCLOUD_IGNORE_NIC_FLAGS") == "true" else parse_flag("--network")) or f"https://www.googleapis.com/compute/v1/projects/{project}/global/networks/default",
                "subnetwork": (None if os.environ.get("GCLOUD_IGNORE_NIC_FLAGS") == "true" else parse_flag("--subnet")) or f"https://www.googleapis.com/compute/v1/projects/{project}/regions/asia-east1/subnetworks/default",
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
    if state.get("ssh_4047_remaining", 0) > 0:
        state["ssh_4047_remaining"] -= 1
        save_state()
        print("ERROR: (gcloud.compute.start-iap-tunnel) Error while connecting [4047: 'Failed to lookup instance'].", file=sys.stderr)
        raise SystemExit(255)
    if state.get("ssh_4003_remaining", 0) > 0:
        state["ssh_4003_remaining"] -= 1
        save_state()
        print("ERROR: (gcloud.compute.start-iap-tunnel) Error while connecting [4003: 'failed to connect to backend'].", file=sys.stderr)
        raise SystemExit(255)
    if state.get("ssh_publickey_remaining", 0) > 0:
        state["ssh_publickey_remaining"] -= 1
        save_state()
        print("deploy@10.0.0.1: Permission denied (publickey).", file=sys.stderr)
        raise SystemExit(255)
    if state.get("ssh_nonretryable"):
        print("ERROR: [4033: 'permission denied']", file=sys.stderr)
        raise SystemExit(255)
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
        # Build tarballs from execute-plans carry a build-time deployment.json (e.g. 6041 bytes)
        # that bootstrap.sh write_deployment_json overwrites with the staging release identity.
        build_dep = json.dumps(
            {"build_time_manifest": True, "source": "execute-plans-ci", "padding": "x" * 256},
            sort_keys=True,
        ).encode("utf-8")
        dep_info = tarfile.TarInfo("deployment.json")
        dep_info.size = len(build_dep)
        dep_info.mtime = 0
        tar.addfile(dep_info, io.BytesIO(build_dep))
    data = buf.getvalue()
    path.write_bytes(data)
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _repo_migration_head_commit() -> str:
    proc = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    sha = proc.stdout.strip()
    return sha if proc.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", sha) else ("b" * 40)


DEFAULT_SAMPLE_BFF_COMMIT = _repo_migration_head_commit()


def _sample_manifest(
    *,
    release_id: str = "pantheon-20261024-001",
    state: str = "dev_verified",
    fe_sha: str | None = None,
    prev_fe_sha: str | None = None,
    mig_sha: str = "sha256:" + ("4" * 64),
    bff_commit: str | None = None,
) -> dict:
    fe_commit = "a" * 40
    bff_commit_val = bff_commit or DEFAULT_SAMPLE_BFF_COMMIT
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
        backend_commit=bff_commit_val,
        operator_bff_image=bff_img,
        additional_images={
            "runtime_manager": f"{reg}/runtime-manager@sha256:" + ("a" * 64),
            "governance": f"{reg}/governance@sha256:" + ("b" * 64),
            "deployment": f"{reg}/deployment@sha256:" + ("c" * 64),
            "lifecycle_projector": f"{reg}/lifecycle-projector@sha256:" + ("d" * 64),
            **_owner_images(reg, 0xA0),
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
            "bff_version_commit": bff_commit_val,
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
                **_owner_images(reg, 0xC0),
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
                        "nested": {"broker_secret": "drop-me", "latency_ms": 42, "legit_null": None},
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



def _owner_images(reg, start):
    """Distinct per-owner digests; the Paper owners reuse the training-session image (no new image)."""
    images = {
        key: f"{reg}/{key.replace('_', '-')}@sha256:" + (f"{i:02x}" * 32)
        for i, key in enumerate(staging_sanitize.PRODUCT_OWNER_IMAGE_KEYS, start=start)
    }
    for key in ("paper_fleet_reconciler", "paper_signal_producer"):
        images[key] = images["training_session_svc"]
    return images


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
        self.assertIn("legit_null", payload["nested"])
        self.assertIsNone(payload["nested"]["legit_null"])
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
        # The only sanctioned producer is the existing Source-artifact Paper signal producer (training-session image).
        non_paper = {k: v for k, v in services.items() if k not in ("paper-signal-producer", "paper-fleet-reconciler")}
        rendered = yaml.safe_dump({**compose, "services": non_paper} if "services" in compose else non_paper)
        for forbidden in ("dev-paper", "pantheon-local", "scheduler", "producer", "pantheon_app", "-internal"):
            self.assertNotIn(forbidden, rendered, forbidden)
        paper_rendered = yaml.safe_dump({k: services[k] for k in ("paper-signal-producer", "paper-fleet-reconciler")})
        for forbidden in ("dev-paper", "pantheon-local", "pantheon_app", "-internal", "build"):
            self.assertNotIn(forbidden, paper_rendered, forbidden)
        self.assertEqual(services["paper-signal-producer"]["environment"]["PAPER_SIGNAL_STRATEGY"], "artifact")
        self.assertEqual(services["paper-fleet-reconciler"]["stop_signal"], "SIGINT")
        for paper in ("paper-fleet-reconciler", "paper-signal-producer"):
            self.assertEqual(services[paper]["environment"]["PANTHEON_LIVE_BROKER_ENABLED"], "false", paper)
            self.assertEqual(services[paper]["environment"]["PANTHEON_REAL_WRITES_ENABLED"], "false", paper)
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

    def test_staging_compose_owners_pass_persistence_posture_validation(self) -> None:
        from services.foundation import persistence_posture

        compose = yaml.safe_load(COMPOSE_STAGING.read_text(encoding="utf-8"))
        services = compose["services"]

        # Every staging compose owner that calls require_persistence_posture or
        # validate_persistence_posture (including all SERVICE_BACKEND_DEFAULTS owners present in
        # docker-compose.staging.yml plus registry and evolution custom backend_env_vars callers).
        owner_posture_specs: tuple[tuple[str, str, dict[str, str] | None, bool], ...] = (
            ("governance", "governance", None, True),
            ("operator-bff", "operator-bff", None, True),
            ("capital", "capital", None, True),
            ("consultation-svc", "consultation", None, True),
            ("evolution", "evolution", {"EVOLUTION_STORE_BACKEND": "json"}, False),
            ("incidents", "incidents", None, True),
            ("memory", "memory", None, True),
            ("policy-learning-svc", "policy-learning", None, True),
            ("postmortems", "postmortems", None, True),
            ("reconciliation-drift-svc", "reconciliation-drift", None, True),
            ("registry", "registry", {"REGISTRY_STORE_BACKEND": "memory"}, False),
            ("research-orchestrator-svc", "research-orchestrator", None, True),
            ("training-session-svc", "training-session", None, True),
        )

        # Ensure no staging compose service in SERVICE_BACKEND_DEFAULTS is omitted from the table.
        covered_compose_svcs = {svc for svc, _, _, _ in owner_posture_specs}
        for svc_name in services:
            if (
                svc_name in persistence_posture.SERVICE_BACKEND_DEFAULTS
                or svc_name.removesuffix("-svc") in persistence_posture.SERVICE_BACKEND_DEFAULTS
            ):
                self.assertIn(svc_name, covered_compose_svcs, svc_name)

        interpolation_fixtures = {
            "PANTHEON_RELEASE_ID": "pantheon-20261024-001",
            "PANTHEON_BACKEND_COMMIT": "b" * 40,
            "STAGING_TENANT_ID": "tenant-stg-fixture",
            "STAGING_JWT_ISSUER": "https://auth.stg.pantheon.internal",
            "STAGING_JWT_AUDIENCE": "pantheon-staging-api",
            "STAGING_IDP_ISSUER": "https://auth.stg.pantheon.internal",
            "STAGING_BFF_CORS_ORIGINS": "https://stg-fe.pantheon.internal",
        }

        def render_compose_env(svc_name: str) -> dict[str, str]:
            spec = services[svc_name]
            raw_env = spec.get("environment") or {}
            rendered: dict[str, str] = {}
            if spec.get("env_file"):
                # staging.secrets.env provides isolated staging Postgres DATABASE_URL at runtime;
                # it deliberately carries no *BACKEND or *POSTURE keys.
                rendered["DATABASE_URL"] = "postgresql://pantheon_stg:stg-pg-pass@postgres:5432/pantheon_staging"
            for k, v in raw_env.items():
                val = str(v)
                val = re.sub(
                    r"\$\{([A-Z0-9_]+):-([^}]*)\}",
                    lambda m: interpolation_fixtures.get(m.group(1), m.group(2)),
                    val,
                )
                val = re.sub(
                    r"\$\{([A-Z0-9_]+):\?[^}]*\}",
                    lambda m: interpolation_fixtures.get(m.group(1), f"fixture-{m.group(1).lower()}"),
                    val,
                )
                rendered[str(k)] = val
            return rendered

        for compose_svc, posture_key, backend_vars, req_obj in owner_posture_specs:
            with self.subTest(compose_service=compose_svc, posture_service=posture_key):
                owner_env = render_compose_env(compose_svc)
                self.assertEqual(owner_env.get("PANTHEON_ENV"), "staging", compose_svc)
                check = persistence_posture.validate_persistence_posture(
                    posture_key,
                    env=owner_env,
                    backend_env_vars=backend_vars,
                    require_object_store=req_obj,
                )
                self.assertEqual(check.errors, (), f"{compose_svc} ({posture_key}) errors: {check.errors}")
                self.assertEqual(check.status, "ok", compose_svc)
                persistence_posture.require_persistence_posture(
                    posture_key,
                    env=owner_env,
                    backend_env_vars=backend_vars,
                    require_object_store=req_obj,
                )

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

    def test_gate_failure_redaction_strips_secrets_long_tokens_and_caps_at_600(self) -> None:
        jwt_val = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJvcGVyYXRvciIsInRlbmFudCI6InN0ZyJ9.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        sk_val = "sk-1234567890abcdefghijklmnop"
        ghp_val = "ghp_1234567890abcdefghijklmnopqrstuv"
        aiza_val = "AIzaSyA1234567890abcdefghijk_LMNOPQRST"
        long_41 = "A" * 41
        exact_40_sha = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4"
        raw_message = (
            f"rollback served_backend_commit {exact_40_sha} mismatch; "
            f"jwt={jwt_val} key={sk_val} gh={ghp_val} gcp={aiza_val} "
            f"Authorization: Bearer {jwt_val} "
            f"dsn=postgresql://pantheon_stg:superSecretPgPass99@postgres:5432/pantheon_staging "
            f"contact=ops.secret@example.com long_id={long_41} "
            + ("tail-diagnostic-padding " * 35)
        )
        redacted = staging_sanitize.redact_gate_failure_message(
            raw_message,
            error_code="ROLLBACK_VERIFICATION_FAILED",
            max_length=600,
        )
        self.assertLessEqual(len(redacted), 600)
        self.assertIn(exact_40_sha, redacted)
        self.assertIn("rollback served_backend_commit", redacted)
        for forbidden in (
            jwt_val,
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            sk_val,
            ghp_val,
            aiza_val,
            "superSecretPgPass99",
            "ops.secret@example.com",
            long_41,
        ):
            self.assertNotIn(forbidden, redacted)


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

    def _staging_cfg(self, name: str) -> dict:
        text = (self.staging_root / "config" / name).read_text(encoding="utf-8")
        prefix = "window.__PANTHEON_DEPLOYMENT_CONFIG__ = "
        line = next(l for l in text.splitlines() if l.startswith(prefix))
        return json.loads(line[len(prefix):].rstrip(";"))

    @staticmethod
    def _cfg_digest(cfg: dict) -> str:
        body = {k: v for k, v in cfg.items() if k != "configSha256"}
        return "sha256:" + hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    def test_public_real_writes_default_and_false_keep_legacy_key_absent(self) -> None:
        digests = []
        for extra in ({}, {"VITE_BFF_REAL_WRITES": "false"}):
            self.setUp()
            env = self._base_env(RELEASE_ID="pantheon-20261024-001", **extra)
            subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
            for name in ("runtime-config.js", "runtime-config.candidate.js", "runtime-config.baseline.js"):
                cfg = self._staging_cfg(name)
                self.assertNotIn("VITE_BFF_REAL_WRITES", cfg)
                self.assertEqual(cfg["configSha256"], self._cfg_digest(cfg))
            digests.append(self._staging_cfg("runtime-config.js")["configSha256"])
        self.assertEqual(len(set(digests)), 1)

    def test_public_real_writes_true_is_candidate_only_and_baseline_noninheriting(self) -> None:
        env = self._base_env(RELEASE_ID="pantheon-20261024-001")
        subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        legacy_digest = self._staging_cfg("runtime-config.js")["configSha256"]
        self.setUp()
        env = self._base_env(RELEASE_ID="pantheon-20261024-001", VITE_BFF_REAL_WRITES="true")
        subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        for name in ("runtime-config.js", "runtime-config.candidate.js"):
            cfg = self._staging_cfg(name)
            self.assertEqual(cfg["VITE_BFF_REAL_WRITES"], "true")
            self.assertEqual(cfg["configSha256"], self._cfg_digest(cfg))
            self.assertNotEqual(cfg["configSha256"], legacy_digest)
        base = self._staging_cfg("runtime-config.baseline.js")
        self.assertNotIn("VITE_BFF_REAL_WRITES", base)
        self.assertEqual(base["configSha256"], self._cfg_digest(base))

    def test_public_real_writes_malformed_value_rejected(self) -> None:
        for bad in ("TRUE", "1", "yes", "on", " true ", "true\n", " false ", "false\n"):
            with self.subTest(bad=bad):
                self.setUp()
                env = self._base_env(RELEASE_ID="pantheon-20261024-001", VITE_BFF_REAL_WRITES=bad)
                res = subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True)
                self.assertNotEqual(res.returncode, 0)
                self.assertIn("PUBLIC_REAL_WRITES_INVALID", res.stderr + res.stdout)

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

    def _mint_runtime_manager_token(self, ttl: str = "7200", secret: str = "stg-runtime-signing-key") -> tuple[str, str]:
        from services import runtime_auth_inbound as inbound

        text = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")
        match = re.search(r"<<'PY'\n(import base64.*?)\nPY\n", text, re.S)
        self.assertIsNotNone(match, "mint_runtime_manager_token heredoc missing")
        with tempfile.TemporaryDirectory() as tmp:
            env_file = Path(tmp) / "staging.secrets.env"
            env_file.write_text(
                f"PANTHEON_RUNTIME_JWT_SECRET={secret}\nPANTHEON_RUNTIME_MANAGER_TOKEN=opaque-placeholder\n",
                encoding="utf-8",
            )
            env_file.chmod(0o600)
            script = Path(tmp) / "mint.py"
            script.write_text(match.group(1) + "\n", encoding="utf-8")
            res = subprocess.run(
                ["python3", str(script), str(env_file), "https://auth.stg.pantheon.internal",
                 "pantheon-staging-api", "tenant-stg", ttl],
                capture_output=True, text=True,
            )
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(env_file.stat().st_mode & 0o777, 0o600)
            kv = dict(l.split("=", 1) for l in env_file.read_text(encoding="utf-8").splitlines())
            self.assertEqual(sum(1 for l in env_file.read_text(encoding="utf-8").splitlines()
                                 if l.startswith("PANTHEON_RUNTIME_MANAGER_TOKEN=")), 1)
            return kv["PANTHEON_RUNTIME_MANAGER_TOKEN"], inbound

    def test_bootstrap_renders_runtime_manager_token_accepted_by_native_verifier(self) -> None:
        token, inbound = self._mint_runtime_manager_token()
        env = {
            "PANTHEON_RUNTIME_AUTH_MODE": "strict",
            "PANTHEON_RUNTIME_JWT_SECRET": "stg-runtime-signing-key",
            "PANTHEON_RUNTIME_JWT_ISSUER": "https://auth.stg.pantheon.internal",
            "PANTHEON_RUNTIME_JWT_AUDIENCE": "pantheon-staging-api",
        }
        ctx = inbound.validate_request_auth(
            authorization=f"Bearer {token}", required_roles=("operator",), env=env
        )
        self.assertEqual(ctx.actor_id, "svc:staging-paper-runtime-caller")
        self.assertIn("operator", ctx.roles)
        self.assertEqual(ctx.claims["tenant_id"], "tenant-stg")
        self.assertLessEqual(ctx.claims["exp"] - ctx.claims["iat"], 7200)
        for key, bad in (
            ("PANTHEON_RUNTIME_JWT_SECRET", "other-key"),
            ("PANTHEON_RUNTIME_JWT_ISSUER", "https://evil.example"),
            ("PANTHEON_RUNTIME_JWT_AUDIENCE", "other-aud"),
        ):
            with self.subTest(key=key):
                with self.assertRaises(inbound.AuthError):
                    inbound.validate_request_auth(
                        authorization=f"Bearer {token}", required_roles=("operator",), env={**env, key: bad}
                    )
        with self.assertRaises(inbound.AuthError):
            inbound.validate_request_auth(
                authorization=f"Bearer {token}", required_roles=("risk_owner",), env=env
            )
        with self.assertRaises(inbound.AuthError):
            inbound._verify_jwt_hs256(
                token, secret=env["PANTHEON_RUNTIME_JWT_SECRET"], issuer=env["PANTHEON_RUNTIME_JWT_ISSUER"],
                audience=env["PANTHEON_RUNTIME_JWT_AUDIENCE"], now=time.time() + 7300,
            )

    def test_sanitizer_preserves_exact_public_builtin_provenance_only(self) -> None:
        ref = staging_sanitize.PUBLIC_BUILTIN_HISTORICAL_PROVENANCE_REFS[0]
        registration_text = (
            REPO_ROOT / "services/registry/strategy-artifacts/tw-session-momentum-v1.registration.json"
        ).read_text(encoding="utf-8")
        self.assertIn(json.dumps(ref)[1:-1], registration_text)
        staging_sanitize._check_no_retired_identifiers(json.loads(registration_text))
        staging_sanitize._check_no_retired_identifiers({"provenance_refs": [ref]})
        for bad in (
            {"provenance_refs": [ref.replace("b9f439", "000000")]},
            {"provenance_refs": [ref], "runtime_path": "/home/lupin/paper-loop"},
            {"host": "35.201.204.12"},
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(staging_sanitize.SanitizationError):
                    staging_sanitize._check_no_retired_identifiers(bad)

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

    def test_bff_not_ready_public_diagnostics_are_structural_only_and_keep_original_error(self) -> None:
        res = subprocess.run(
            [str(PROVISION_SCRIPT)], env=self._base_env(FAKE_BFF_UNHEALTHY="true"), capture_output=True, text=True
        )
        out = res.stderr + res.stdout
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("BFF_NOT_READY", out)
        self.assertIn("BFF_DIAG status=exited exit_code=1 oom_killed=false restart_count=2 error_present=true", out)
        self.assertIn("BFF_DIAG frame=/workspace/services/control-plane/bff/main.py:42", out)
        self.assertIn("BFF_DIAG exception_type=RuntimeError", out)
        self.assertIn("BFF_DIAG exception_type=OtherException", out)
        for leaked in ("stg-isolated-pg-password-20261025", "alpha-77", "res-123", "connect(", "/etc/private", "strategy_alpha77", "result_res123", "ResearchResult", "boom", "Traceback"):
            self.assertNotIn(leaked, out)

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
            self.assertIn("OWNER_CONTAINER_MISSING", res_product.stderr)

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
                self.assertNotIn("ORD-SYNTH-7788", ver.stderr)
                self.assertNotIn("buyer@synthetic.example", ver.stderr)

    def test_collect_gates_preserves_only_validated_unserved_owner_names(self) -> None:
        subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
            check=True,
        )
        cases = (
            ("registry, agora-interaction-worker", "unserved_product_owner_services: agora-interaction-worker,registry"),
            ("registry, not-a-real-owner", None),
            ("registry; echo SECRET-TOKEN-1", None),
        )
        for idx, (names, expected) in enumerate(cases):
            with self.subTest(names=names):
                payload = json.dumps(
                    {
                        "status": "error",
                        "step": "collect-verify-gates",
                        "error_code": "PRODUCT_OWNER_UNSERVED",
                        "message": f"Product owners not running/healthy: {names}",
                    }
                )
                wrapper = self.tmp_path / f"unserved-gcloud-{idx}.py"
                wrapper.write_text(
                    "#!/usr/bin/env python3\n"
                    "import os, sys\n"
                    "args = sys.argv[1:]\n"
                    "if args[:2] == ['compute', 'ssh'] and any('collect-gates' in a for a in args):\n"
                    f"    print({payload!r})\n"
                    "    raise SystemExit(1)\n"
                    f"os.execv({str(self.gcloud_bin)!r}, [{str(self.gcloud_bin)!r}] + args)\n",
                    encoding="utf-8",
                )
                wrapper.chmod(0o755)
                ver = subprocess.run(
                    [str(VERIFY_SCRIPT)],
                    env=self._base_env(RELEASE_ID="pantheon-20261024-001", GCLOUD_BIN=str(wrapper)),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(ver.returncode, 0)
                self.assertIn("LIVE_GATE_COLLECTION_FAILED", ver.stderr)
                self.assertIn("PRODUCT_OWNER_UNSERVED", ver.stderr)
                self.assertNotIn("SECRET-TOKEN-1", ver.stderr)
                self.assertNotIn("not-a-real-owner", ver.stderr)
                if expected:
                    self.assertIn(expected, ver.stderr)
                else:
                    self.assertNotIn("unserved_product_owner_services", ver.stderr)

    def test_collector_rejects_tampered_migration_files_and_missing_owner_store(self) -> None:
        subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
            check=True,
        )
        mig_file = (
            self.staging_root
            / "state"
            / "migration-material"
            / "migrations"
            / self.migration_files[0]["name"]
        )
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

    def test_pre_paper_baseline_rolls_back_without_inventing_absent_workers(self) -> None:
        # A baseline predating the Paper workers is valid: retained owners are restored exactly, the absent
        # workers are removed for the rollback window and never started from the interpolation sentinel.
        for key in staging_sanitize.BASELINE_OPTIONAL_OWNER_IMAGE_KEYS:
            del self.manifest["rollback_baseline"]["backend_images"][key]
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.manifest_sha = release_manifest.compute_manifest_sha256(self.manifest)
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        baseline_env = (self.staging_root / "state" / "compose.baseline.env").read_text()
        self.assertEqual(baseline_env.count("baseline-absent.invalid"), 2)
        self.assertIn("baseline-absent.invalid", baseline_env)
        for key in staging_sanitize.BASELINE_OPTIONAL_OWNER_IMAGE_KEYS:
            self.assertNotIn(
                self.manifest["backend"]["images"][key],
                [l.split("=", 1)[1] for l in baseline_env.splitlines() if l.startswith(f"PANTHEON_{key.upper()}_IMAGE=")],
            )

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
        self.assertIn("--machine-type=e2-standard-8", inst_create_cmd)
        self.assertIn("--address=203.0.113.10", inst_create_cmd)
        self.assertIn("--network=https://www.googleapis.com/compute/v1/projects/pantheon-stg-20260902/global/networks/pantheon-staging", inst_create_cmd)
        self.assertIn("--subnet=https://www.googleapis.com/compute/v1/projects/pantheon-stg-20260902/regions/asia-east1/subnetworks/pantheon-staging-asia-east1", inst_create_cmd)
        live_inst = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))["instances"][0]
        self.assertEqual(live_inst["machineType"], "e2-standard-8")
        live_nic = live_inst["networkInterfaces"][0]
        self.assertTrue(live_nic["network"].endswith("/networks/pantheon-staging"))
        self.assertTrue(live_nic["subnetwork"].endswith("/subnetworks/pantheon-staging-asia-east1"))
        self.assertEqual(live_nic["accessConfigs"][0]["natIP"], "203.0.113.10")
        self.assertIn("--scopes=https://www.googleapis.com/auth/cloud-platform", inst_create_cmd)
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
                if fail_token == "compute ssh":
                    self.assertIn("HOST_SSH_MKDIR_FAILED", fail_res.stderr)
                    self.assertIn("rc=1", fail_res.stderr)
                    self.assertIn("simulated gcloud failure on compute ssh", fail_res.stderr)
                    self.assertFalse(any("compute scp" in c for c in self.gcloud_log_file.read_text(encoding="utf-8").splitlines()))
                cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
                self.assertEqual(cloud_state["instances"], [])
                self.assertEqual(cloud_state["disks"], [])
                self.assertEqual(cloud_state["addresses"], [])

        # Template without an explicit subnetwork fails closed before any VM is created
        self.setUp()
        no_nic = subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(GCLOUD_TEMPLATE_NO_SUBNET="true"), capture_output=True, text=True)
        self.assertNotEqual(no_nic.returncode, 0)
        self.assertIn("explicit network and subnetwork", no_nic.stderr)
        self.assertEqual(json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))["instances"], [])

        # Returned NIC differing from the template (default VPC) is rejected and compensated before any lease
        self.setUp()
        bad_nic = subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(GCLOUD_IGNORE_NIC_FLAGS="true"), capture_output=True, text=True)
        self.assertNotEqual(bad_nic.returncode, 0)
        self.assertIn("does not match template network/subnetwork", bad_nic.stderr)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", bad_nic.stderr)
        self.assertFalse(any("compute ssh" in c or "compute scp" in c for c in self.gcloud_log_file.read_text(encoding="utf-8").splitlines()))
        bad_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        self.assertEqual(bad_state["instances"], [])
        self.assertEqual(bad_state["disks"], [])
        self.assertEqual(bad_state["addresses"], [])

        # Returned SA scopes differing from the template (gcloud defaults) are rejected and compensated
        self.setUp()
        bad_scopes = subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(GCLOUD_IGNORE_SCOPES="true"), capture_output=True, text=True)
        self.assertNotEqual(bad_scopes.returncode, 0)
        self.assertIn("do not match template scopes", bad_scopes.stderr)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", bad_scopes.stderr)
        self.assertEqual(json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))["instances"], [])

        # First IAP SSH: only exact transient 4047/4003 is retried (fake sleep keeps wall time at zero)
        for label, state_patch, expect_ok, expect_mkdirs in (
            ("transient", {"ssh_4047_remaining": 2}, True, 3),
            ("transient_4003_then_4047", {"ssh_4003_remaining": 2, "ssh_4047_remaining": 1}, True, 4),
            ("exhausted", {"ssh_4047_remaining": 99}, False, 6),
            ("exhausted_4003", {"ssh_4003_remaining": 99}, False, 6),
            ("transient_publickey", {"ssh_4047_remaining": 1, "ssh_publickey_remaining": 1}, True, 3),
            ("exhausted_publickey", {"ssh_publickey_remaining": 99}, False, 6),
            ("nonretryable", {"ssh_nonretryable": True}, False, 1),
        ):
            with self.subTest(ssh_retry=label):
                self.setUp()
                fake_sleep_dir = self.tmp_path / "fake-sleep-bin"
                fake_sleep_dir.mkdir()
                (fake_sleep_dir / "sleep").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                (fake_sleep_dir / "sleep").chmod(0o755)
                sleep_env = {"PATH": f"{fake_sleep_dir}:{os.environ['PATH']}"}
                st = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
                st.update(state_patch)
                self.gcloud_state_file.write_text(json.dumps(st), encoding="utf-8")
                res = subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(**sleep_env), capture_output=True, text=True)
                mkdir_calls = [c for c in self.gcloud_log_file.read_text(encoding="utf-8").splitlines() if "compute ssh" in c and "rm -rf" in c]
                self.assertEqual(len(mkdir_calls), expect_mkdirs)
                self.assertEqual(len(set(mkdir_calls)), 1)
                if expect_ok:
                    self.assertEqual(res.returncode, 0, res.stderr)
                    self.assertEqual(res.stderr.count("HOST_SSH_MKDIR_RETRY"), expect_mkdirs - 1)
                else:
                    self.assertNotEqual(res.returncode, 0)
                    self.assertIn("HOST_SSH_MKDIR_FAILED", res.stderr)
                    self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", res.stderr)
                    self.assertIn("rc=255", res.stderr)
                    self.assertEqual(json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))["instances"], [])
                    self.assertEqual(res.stderr.count("HOST_SSH_MKDIR_RETRY"), 0 if label == "nonretryable" else 5)

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

    def test_address_labels_applied_by_update_after_unlabelled_create(self) -> None:
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        cmds = self.gcloud_log_file.read_text(encoding="utf-8").splitlines()
        create_cmd = next(c for c in cmds if "compute addresses create" in c)
        self.assertNotIn("--labels", create_cmd)
        self.assertTrue(any("compute addresses update pantheon-stg-ip-pantheon-20261024-001" in c and "--update-labels=" in c for c in cmds))
        inv = json.loads((self.state_dir / "inventory" / "pantheon-20261024-001.json").read_text(encoding="utf-8"))
        addr = next(r for r in inv["resources"] if r["type"] == "address")
        self.assertEqual(addr["state"], "provisioned")
        self.assertEqual(addr["resource_id"], "1000")
        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        self.assertEqual(cloud_state["addresses"][0]["labels"]["release_id"], "pantheon-20261024-001")

    def test_address_label_update_failure_compensates_only_exact_created_address(self) -> None:
        env = self._base_env(GCLOUD_FAIL_ON="compute addresses update")
        self.gcloud_state_file.write_text(
            json.dumps({
                "addresses": [{
                    "id": "9",
                    "name": "foreign-unlabelled",
                    "project_id": "pantheon-stg-20260902",
                    "region": "us-central1",
                    "address": "203.0.113.99",
                    "selfLink": "https://example.invalid/foreign",
                    "labels": {},
                    "status": "RESERVED",
                }],
                "disks": [], "instances": [], "iam_bindings": [], "images": {}, "gcs_blobs": {}, "vm_mkdir_ready": {},
            }),
            encoding="utf-8",
        )
        res = subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", res.stderr)
        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        self.assertEqual([a["name"] for a in cloud_state["addresses"]], ["foreign-unlabelled"])

    def test_orphan_sweep_reclaims_expired_pending_address_without_local_journal(self) -> None:
        # Provision, then simulate a runner killed between create and label update: no labels, no local journal.
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        addr = cloud_state["addresses"][0]
        self.assertTrue(addr["description"].startswith("pantheon-pending-v1:"))
        addr["labels"] = {}
        foreign = {**addr, "id": "77", "name": "foreign-pending", "description": "pantheon-pending-v1:{}"}
        mismatch = {**addr, "id": "78", "name": "pantheon-stg-ip-other-release"}
        cloud_state["addresses"] += [foreign, mismatch]
        cloud_state["instances"] = []
        cloud_state["disks"] = []
        self.gcloud_state_file.write_text(json.dumps(cloud_state), encoding="utf-8")
        shutil.rmtree(self.state_dir)
        res = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "orphan-sweep"],
            env=self._base_env(
                STAGING_STATE_DIR=str(self.tmp_path / "fresh-runner-state"),
                STAGING_ORPHAN_CLEANUP_ENABLED="true",
                STAGING_NOW_EPOCH="1792836001",
            ),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertEqual([d["name"] for d in out["deleted_resources"]], ["pantheon-stg-ip-pantheon-20261024-001"])
        final = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        self.assertEqual(sorted(a["name"] for a in final["addresses"]), ["foreign-pending", "pantheon-stg-ip-other-release"])

    def test_orphan_sweep_protects_foreign_replacement_of_journaled_pending_address(self) -> None:
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        inv_path = self.state_dir / "inventory" / "pantheon-20261024-001.json"
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        for r in inv["resources"]:
            if r["type"] == "address":
                r["state"] = "label_pending"
                r["resource_id"] = "original-created-id"
        inv_path.write_text(json.dumps(inv), encoding="utf-8")
        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        for a in cloud_state["addresses"]:
            a["id"] = "different-foreign-id"
            a["labels"] = {}
            a["description"] = "unrelated"
        cloud_state["instances"] = []
        cloud_state["disks"] = []
        self.gcloud_state_file.write_text(json.dumps(cloud_state), encoding="utf-8")
        res = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "orphan-sweep"],
            env=self._base_env(STAGING_ORPHAN_CLEANUP_ENABLED="true", STAGING_NOW_EPOCH="1792836001"),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertFalse(any(d["type"] == "address" for d in out["deleted_resources"]))
        final = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        self.assertEqual([a["id"] for a in final["addresses"]], ["different-foreign-id"])

    def test_fresh_runner_rejects_unexpired_pending_address_of_other_release(self) -> None:
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        cloud_state = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
        for a in cloud_state["addresses"]:
            a["labels"] = {}
        cloud_state["instances"] = []
        cloud_state["disks"] = []
        self.gcloud_state_file.write_text(json.dumps(cloud_state), encoding="utf-8")
        res = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(
                RELEASE_ID="pantheon-20261024-002",
                WORKFLOW_RUN_ID="other-run",
                STAGING_STATE_DIR=str(self.tmp_path / "fresh-runner-state"),
            ),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("already_provisioned", res.stdout)

    def test_teardown_protects_unlabelled_address_with_different_id(self) -> None:
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        inv_path = self.state_dir / "inventory" / "pantheon-20261024-001.json"
        inv = json.loads(inv_path.read_text(encoding="utf-8"))
        for r in inv["resources"]:
            if r["type"] == "address":
                r["state"] = "label_pending"
                r["resource_id"] = "stale-other-id"
        inv_path.write_text(json.dumps(inv), encoding="utf-8")
        res = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "teardown", "--result", "success", "--reason", "completed"],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        out = json.loads(res.stdout)
        self.assertTrue(any(s["type"] == "address" for s in out["skipped_unowned_resources"]))

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

    def test_teardown_rebuilt_inventory_deletes_vm_disks_then_address(self) -> None:
        subprocess.run([str(PROVISION_SCRIPT)], env=self._base_env(), capture_output=True, text=True, check=True)
        shutil.rmtree(self.state_dir)  # no journal: inventory is rebuilt from the cloud
        self.gcloud_log_file.write_text("", encoding="utf-8")
        env = self._base_env(RELEASE_ID="pantheon-20261024-001")
        order = ["instance", "disk", "disk", "address"]

        no_gcloud_dry = subprocess.run(
            [str(DESTROY_SCRIPT), "--mode", "teardown", "--dry-run", "--result", "success"],
            env={**env, "GCLOUD_BIN": ""}, capture_output=True, text=True, check=True,
        )
        self.assertEqual(json.loads(no_gcloud_dry.stdout)["status"], "already_absent")

        with self.subTest("dry_run"):
            dry = subprocess.run(
                [str(DESTROY_SCRIPT), "--mode", "teardown", "--dry-run", "--result", "success"],
                env=env, capture_output=True, text=True, check=True,
            )
            dry_json = json.loads(dry.stdout)
            self.assertEqual(dry_json["status"], "destroyed")
            self.assertTrue(dry_json["dry_run"])
            self.assertEqual([d["type"] for d in dry_json["deleted_resources"]], order)
            self.assertNotIn(" delete ", self.gcloud_log_file.read_text(encoding="utf-8"))
            cloud = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
            self.assertEqual((len(cloud["instances"]), len(cloud["disks"]), len(cloud["addresses"])), (1, 2, 1))

        with self.subTest("live"):
            live = subprocess.run(
                [str(DESTROY_SCRIPT), "--mode", "teardown", "--result", "success"],
                env=env, capture_output=True, text=True,
            )
            self.assertEqual(live.returncode, 0, live.stderr)
            live_json = json.loads(live.stdout)
            self.assertEqual(live_json["status"], "destroyed")
            self.assertTrue(live_json["post_delete_readback_verified"])
            self.assertEqual([d["type"] for d in live_json["deleted_resources"]], order)
            deletes = [c.split()[1] for c in self.gcloud_log_file.read_text(encoding="utf-8").splitlines() if " delete " in c]
            self.assertEqual(deletes, ["instances", "disks", "disks", "addresses"])
            cloud = json.loads(self.gcloud_state_file.read_text(encoding="utf-8"))
            self.assertEqual((cloud["instances"], cloud["disks"], cloud["addresses"]), ([], [], []))

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

    def test_bootstrap_orders_postgres_pre_start_seed_verification_before_full_compose_up(self) -> None:
        env = self._base_env(RELEASE_ID="pantheon-20261024-001")
        subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        vm_logs = self.vm_bin_log_file.read_text(encoding="utf-8").splitlines()

        pg_up_idx = next(
            (
                i
                for i, line in enumerate(vm_logs)
                if line.startswith("docker compose ")
                and "--profile core up -d --no-build postgres" in line
            ),
            None,
        )
        self.assertIsNotNone(pg_up_idx, "bootstrap.sh must start postgres alone before full compose up")
        pg_ready_idx = next(
            (i for i, line in enumerate(vm_logs) if i > pg_up_idx and line.startswith("psql ") and "SELECT 1;" in line),
            None,
        )
        self.assertIsNotNone(pg_ready_idx, "bootstrap.sh must wait for postgres readiness before pre-start seed check")
        pre_start_catalog_idx = next(
            (
                i
                for i, line in enumerate(vm_logs)
                if i > pg_ready_idx and line.startswith("psql ") and "FROM information_schema.columns" in line
            ),
            None,
        )
        self.assertIsNotNone(pre_start_catalog_idx, "pre-start collector must query live information_schema catalog")
        pre_start_tables_idx = next(
            (
                i
                for i, line in enumerate(vm_logs)
                if i > pre_start_catalog_idx and line.startswith("psql ") and "row_to_json" in line
            ),
            None,
        )
        self.assertIsNotNone(pre_start_tables_idx, "pre-start collector must read per-table rows via row_to_json")
        full_up_idx = next(
            (
                i
                for i, line in enumerate(vm_logs)
                if line.startswith("docker compose ") and line.endswith("--profile core up -d --no-build")
            ),
            None,
        )
        self.assertIsNotNone(full_up_idx, "bootstrap.sh must run full --profile core up -d --no-build after pre-start check")
        self.assertLess(pg_up_idx, pg_ready_idx)
        self.assertLess(pg_ready_idx, pre_start_catalog_idx)
        self.assertLess(pre_start_catalog_idx, pre_start_tables_idx)
        self.assertLess(pre_start_tables_idx, full_up_idx)

        self._unlock_vm_env_dir()
        record_path = self.staging_root / "state" / "pre-start-sanitized-dataset.json"
        self.assertTrue(record_path.is_file())
        self.assertEqual(record_path.stat().st_mode & 0o777, 0o600)
        record = json.loads(record_path.read_text(encoding="utf-8"))
        self.assertTrue(record["verified"])
        self.assertTrue(record["sanitized_verified"])
        self.assertEqual(record["observed_phase"], "pre_owner_start")
        self.assertEqual(record["snapshot_name"], "pantheon-nonprod-sanitized-20261024")
        self.assertEqual(record["table_count"], 5)
        self.assertEqual(record["row_counts"]["public.loop_controller_records"], 1)
        self.assertRegex(record["live_dataset_sha256"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(record["metadata_dataset_sha256"], r"^sha256:[0-9a-f]{64}$")

        # Confirm that paper-fleet-reconciler wrote a runtime row containing lease_token AFTER owner start,
        # and that live gate verification passes by consuming the pre-owner-start record.
        vm_state = json.loads(self.vm_runtime_state_file.read_text(encoding="utf-8"))
        live_loop_rows = vm_state["databases"]["pantheon_staging"]["tables"]["public.loop_controller_records"]
        self.assertEqual(len(live_loop_rows), 2)
        self.assertTrue(
            any(
                r.get("payload", {}).get("_loop_controller_record", {}).get("lease_token")
                == "runtime-lease-token-after-owner-start"
                for r in live_loop_rows
            )
        )
        self._lock_root_owned_paths()

        (self.vm_env_dir / "pg-sim").mkdir(exist_ok=True)
        ver = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertEqual(ver.returncode, 0, ver.stderr)
        ver_json = json.loads(ver.stdout)
        self.assertEqual(ver_json["status"], "staging_verified")
        san_gate = ver_json["gates"]["sanitized_data"]
        self.assertTrue(san_gate["sanitized_verified"])
        self.assertTrue(san_gate["verified"])
        self.assertEqual(san_gate["observed_phase"], "pre_owner_start")
        self.assertEqual(san_gate["table_count"], 5)
        self.assertEqual(san_gate["row_counts"]["public.loop_controller_records"], 1)
        self.assertIn("paper-fleet-reconciler", san_gate["owner_started_at"])
        prod_preflight.check_staging_evidence(ver_json, self.manifest, self.manifest_sha)

    def test_seed_row_with_lease_token_fails_closed_before_product_owners_start(self) -> None:
        vm_state = json.loads(self.vm_runtime_state_file.read_text(encoding="utf-8"))
        vm_state["databases"]["pantheon_staging"]["tables"]["public.loop_controller_records"].append(
            {
                "controller_id": "leaked-seed-controller",
                "loop_id": "loop-seed-leak",
                "tenant_id": "tenant-alpha",
                "payload": {"_loop_controller_record": {"lease_token": "seed-leaked-lease-token-value"}},
                "evidence_refs": [],
            }
        )
        self.vm_runtime_state_file.write_text(json.dumps(vm_state, indent=2, sort_keys=True), encoding="utf-8")

        res = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("PARTIAL_CREATE_FAILURE_COMPENSATED", res.stderr)

        self._unlock_vm_env_dir()
        self.assertFalse((self.staging_root / "state" / "pre-start-sanitized-dataset.json").exists())
        post_state = json.loads(self.vm_runtime_state_file.read_text(encoding="utf-8"))
        self.assertEqual(set(post_state.get("containers", {}).keys()), {"postgres"})

    def test_collector_rejects_missing_unverified_mismatched_insecure_or_late_pre_start_record(self) -> None:
        env = self._base_env(RELEASE_ID="pantheon-20261024-001")
        subprocess.run([str(PROVISION_SCRIPT)], env=env, capture_output=True, text=True, check=True)
        record_path = self.staging_root / "state" / "pre-start-sanitized-dataset.json"
        self._unlock_vm_env_dir()
        original_record_bytes = record_path.read_bytes()
        original_record = json.loads(original_record_bytes.decode("utf-8"))

        # 1. Missing pre-start record
        record_path.unlink()
        self._lock_root_owned_paths()
        res_missing = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res_missing.returncode, 0)
        self.assertIn("LIVE_GATE_COLLECTION_FAILED", res_missing.stderr)
        self.assertIn("SANITIZED_DATASET_INVALID", res_missing.stderr)

        # 2. Unverified / failed pre-start record (verified=false)
        self._unlock_vm_env_dir()
        record_path.write_text(
            json.dumps({**original_record, "verified": False}, sort_keys=True),
            encoding="utf-8",
        )
        record_path.chmod(0o600)
        self._lock_root_owned_paths()
        res_unverified = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res_unverified.returncode, 0)
        self.assertIn("SANITIZED_DATASET_INVALID", res_unverified.stderr)

        # 3. Mismatched snapshot_name vs on-disk metadata
        self._unlock_vm_env_dir()
        record_path.write_text(
            json.dumps({**original_record, "snapshot_name": "pantheon-nonprod-sanitized-99999999"}, sort_keys=True),
            encoding="utf-8",
        )
        record_path.chmod(0o600)
        self._lock_root_owned_paths()
        res_snap = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res_snap.returncode, 0)
        self.assertIn("SANITIZED_DATASET_INVALID", res_snap.stderr)

        # 4. Insecure file mode (0644 instead of 0600)
        self._unlock_vm_env_dir()
        record_path.write_bytes(original_record_bytes)
        record_path.chmod(0o644)
        self._lock_root_owned_paths()
        res_mode = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res_mode.returncode, 0)
        self.assertIn("SANITIZED_DATASET_INVALID", res_mode.stderr)

        # 5. Late observed_at (not strictly earlier than product owner container start)
        self._unlock_vm_env_dir()
        record_path.write_text(
            json.dumps({**original_record, "observed_at": "2099-12-31T23:59:59.999999Z"}, sort_keys=True),
            encoding="utf-8",
        )
        record_path.chmod(0o600)
        self._lock_root_owned_paths()
        res_late = subprocess.run([str(VERIFY_SCRIPT)], env=env, capture_output=True, text=True)
        self.assertNotEqual(res_late.returncode, 0)
        self.assertIn("SANITIZED_DATASET_INVALID", res_late.stderr)

    def test_collector_verifies_rewritten_baseline_deployment_json_and_rejects_identity_tampering(self) -> None:
        # 1. Baseline FE archive carries a build-time deployment.json that bootstrap.sh rewrites with
        # the baseline release identity; valid rewritten identity succeeds, while tampering any
        # baseline identity/safety field in deployment.json fails with BASELINE_FE_CONTENT_TAMPERED.
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)

        prev_rel = self.manifest["rollback_baseline"]["previous_release_id"]
        baseline_dir = self.fe_root / "releases" / prev_rel
        baseline_dep = baseline_dir / "deployment.json"
        self._unlock_vm_env_dir()
        original_dep = json.loads(baseline_dep.read_text(encoding="utf-8"))

        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("collector_mod_dep", str(COLLECTOR_SCRIPT))
        spec = importlib.util.spec_from_loader("collector_mod_dep", loader)
        assert spec is not None
        collector_mod = importlib.util.module_from_spec(spec)
        loader.exec_module(collector_mod)

        for field_name, bad_val in (
            ("release_id", "pantheon-tampered-release"),
            ("frontend_commit", "f" * 40),
            ("frontend_artifact_sha256", "sha256:" + ("9" * 64)),
            ("backend_commit", "e" * 40),
            ("operator_bff_image", "asia-east1-docker.pkg.dev/pantheon-stg-20260902/pantheon-releases/operator-bff@sha256:" + ("8" * 64)),
            ("config_schema_version", "pantheon.env-config.tampered"),
            ("live_broker_enabled", True),
            ("provider_enabled", 1),
        ):
            with self.subTest(field=field_name):
                self._unlock_vm_env_dir()
                tampered_doc = {**original_dep, field_name: bad_val}
                baseline_dep.write_text(json.dumps(tampered_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
                with self.assertRaises(SystemExit):
                    collector_mod._verify_unpacked_tarball_matches_dir(
                        self.fe_baseline_tarball,
                        baseline_dir,
                        self.manifest["rollback_baseline"],
                    )

        # End-to-end VERIFY_SCRIPT check with tampered deployment.json fails closed with BASELINE_FE_CONTENT_TAMPERED.
        self._unlock_vm_env_dir()
        baseline_dep.write_text(
            json.dumps({**original_dep, "release_id": "pantheon-tampered-release"}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        res = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        self.assertIn("BASELINE_FE_CONTENT_TAMPERED", res.stderr)

        self._unlock_vm_env_dir()
        baseline_dep.write_text(json.dumps(original_dep, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def test_baseline_fe_verify_keeps_dotfile_member_names(self) -> None:
        # "./.prepared-receipt.json" must map to ".prepared-receipt.json", not "prepared-receipt.json"
        # (str.lstrip("./") strips a character set rather than a prefix).
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("collector_mod_dotfile", str(COLLECTOR_SCRIPT))
        spec = importlib.util.spec_from_loader("collector_mod_dotfile", loader)
        assert spec is not None
        collector_mod = importlib.util.module_from_spec(spec)
        loader.exec_module(collector_mod)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            target = tmp_path / "release"
            target.mkdir()
            files = {"./.prepared-receipt.json": b"{}\n", "./index.html": b"<html></html>\n"}
            tarball = tmp_path / "fe.tar.gz"
            with tarfile.open(tarball, "w:gz") as tf:
                for name, data in files.items():
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    tf.addfile(info, io.BytesIO(data))
                    (target / name[2:]).write_bytes(data)
            collector_mod._verify_unpacked_tarball_matches_dir(tarball, target)
            (target / ".prepared-receipt.json").unlink()
            with self.assertRaises(SystemExit):
                collector_mod._verify_unpacked_tarball_matches_dir(tarball, target)

    def test_deploy_host_renders_and_ships_migration_material_and_fails_closed_on_manifest_mismatch(self) -> None:
        # 2. staging_ops.sh renders migration-spec.json + migrations/*.sql via prod_preflight.py
        # migration render into the bundle and bootstrap.sh installs them under
        # STAGING_ROOT/state/migration-material without requiring data_mount/sanitized/migration-spec.json.
        self.assertFalse((self.data_mount / "sanitized" / "migration-spec.json").exists())
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        self._unlock_vm_env_dir()
        installed_spec = self.staging_root / "state" / "migration-material" / "migration-spec.json"
        installed_dir = self.staging_root / "state" / "migration-material" / "migrations"
        self.assertTrue(installed_spec.is_file())
        spec_doc = json.loads(installed_spec.read_text(encoding="utf-8"))
        self.assertEqual(spec_doc["migration_set_sha256"], self.migration_set_sha)
        for entry in self.migration_files:
            self.assertTrue((installed_dir / entry["name"]).is_file())

        # A manifest whose migration_set_sha256 does not match the repo SQL files fails closed in deploy-host.
        self.setUp()
        bad_manifest = _sample_manifest(
            release_id="pantheon-20261024-002",
            fe_sha=self.fe_sha,
            prev_fe_sha=self.fe_baseline_sha,
            mig_sha="sha256:" + ("e" * 64),
        )
        bad_manifest_path = self.tmp_path / "bad-mig-manifest.json"
        bad_manifest_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
        bad_manifest_sha = release_manifest.compute_manifest_sha256(bad_manifest)
        res_bad = subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(
                RELEASE_ID="pantheon-20261024-002",
                RELEASE_MANIFEST_PATH=str(bad_manifest_path),
                EXPECTED_MANIFEST_SHA256=bad_manifest_sha,
            ),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res_bad.returncode, 0)
        self.assertIn("MIGRATION_RENDER_FAILED", res_bad.stderr + res_bad.stdout)

    def test_compose_env_file_is_not_overridden_by_collector_process_environ(self) -> None:
        # 3. Real Docker Compose gives shell environment precedence over --env-file; collector must
        # strip keys loaded from state/compose.env whenever --env-file is on the command line so
        # --env-file compose.baseline.env starts the baseline BFF image.
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        gate_dir = self.state_dir / "observed-gates" / "pantheon-20261024-001"
        mig_plan = json.loads((gate_dir / "migration-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(
            mig_plan["old_app_image_observed"],
            self.manifest["rollback_baseline"]["backend_operator_bff_image"],
        )

    def test_collector_waits_for_bff_readyz_across_all_four_owner_restart_points_and_restore_leg_order(self) -> None:
        # 4. Verifies bounded /bff/readyz polling after (a) old-app start, (b) post-migration
        # candidate restore, (c) rollback leg, and (d) restore leg (where _wait_product_owners()
        # runs before /bff/readyz, matching the rollback leg).
        ver = self._provision_and_verify(
            FAKE_BFF_RESTART_WARMUP="true",
            FAKE_OWNER_HEALTH="starting_then_healthy",
            FAKE_READYZ_REQUIRES_OWNERS_HEALTHY="true",
            STAGING_READY_INTERVAL="0",
        )
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        gate_dir = self.state_dir / "observed-gates" / "pantheon-20261024-001"
        rb_readback = json.loads((gate_dir / "rollback-readback.json").read_text(encoding="utf-8"))
        self.assertTrue(rb_readback["bff_healthy"])
        self.assertLessEqual(rb_readback["rollback_duration_seconds"], 120.0)

    def test_postgres_client_tools_run_inside_pinned_pg16_container_and_reject_pg15_host_client(self) -> None:
        # 5. psql, pg_dump, and pg_restore must run inside the pinned postgres:16 container so
        # Debian 12's host postgresql-client (15.19) never aborts against server 16.15.
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        vm_logs = self.vm_bin_log_file.read_text(encoding="utf-8").splitlines()
        self.assertTrue(
            any(l.startswith("docker exec -e PGPASSWORD cid-postgres-") and " pg_dump " in l for l in vm_logs),
            "pg_dump must run inside the pinned postgres container via docker exec",
        )
        self.assertTrue(
            any(l.startswith("docker exec -e PGPASSWORD cid-postgres-") and " pg_restore " in l for l in vm_logs),
            "pg_restore must run inside the pinned postgres container via docker exec",
        )
        self.assertTrue(
            any(l.startswith("docker exec -e PGPASSWORD cid-postgres-") and " psql " in l for l in vm_logs),
            "psql must run inside the pinned postgres container via docker exec",
        )
        # Direct invocation of the host PG 15.19 client against the running PG 16.15 server fails closed.
        direct_dump = subprocess.run(
            [str(self.host_bin_dir / "pg_dump"), "-h", "127.0.0.1", "-p", "15432", "-U", "pantheon_stg", "-d", "pantheon_staging", "-Fc", "-f", str(self.tmp_path / "out.dump")],
            env={**self._base_env(), "PGPASSWORD": "stg-isolated-pg-password-20261025"},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(direct_dump.returncode, 0)
        self.assertIn("server version: 16.15; pg_dump version: 15.19", direct_dump.stderr)

    def test_safe_extract_tar_falls_back_on_python_3112_without_filter_kwarg_and_rejects_unsafe_members(self) -> None:
        # 6. Python 3.11.2 TarFile.extractall lacks the filter= keyword argument; _safe_extract_tar
        # must validate members first, fall back cleanly on TypeError, and reject unsafe members.
        import importlib.machinery
        import importlib.util

        loader = importlib.machinery.SourceFileLoader("collector_mod", str(COLLECTOR_SCRIPT))
        spec = importlib.util.spec_from_loader("collector_mod", loader)
        assert spec is not None
        collector_mod = importlib.util.module_from_spec(spec)
        loader.exec_module(collector_mod)

        class _Py3112TarFile(tarfile.TarFile):
            filter_called = False

            def extractall(self, path=".", members=None, *, numeric_owner=False, **kwargs):  # type: ignore[override]
                if "filter" in kwargs:
                    _Py3112TarFile.filter_called = True
                    raise TypeError("TarFile.extractall() got an unexpected keyword argument 'filter'")
                return super().extractall(path=path, members=members, numeric_owner=numeric_owner)

        valid_tar = self.tmp_path / "valid-owner.tar.gz"
        with tarfile.open(valid_tar, "w:gz") as tf:
            payload = b'{"sessions": {"ack-1": {"state": "acknowledged"}}}'
            ti = tarfile.TarInfo("bff/session_lifecycle.json")
            ti.size = len(payload)
            tf.addfile(ti, io.BytesIO(payload))

        dest_ok = self.tmp_path / "extract-ok"
        dest_ok.mkdir(parents=True, exist_ok=True)
        with _Py3112TarFile.open(valid_tar, "r:gz") as tf:
            collector_mod._safe_extract_tar(tf, dest_ok)
        self.assertTrue(_Py3112TarFile.filter_called)
        self.assertTrue((dest_ok / "bff" / "session_lifecycle.json").is_file())

        # Unsafe tar members (path traversal, absolute path, symlink, hardlink, FIFO) must fail closed.
        for label, build_info in (
            ("dotdot", lambda: tarfile.TarInfo("../escape.txt")),
            ("abspath", lambda: tarfile.TarInfo("/etc/passwd")),
            ("symlink", lambda: self._make_tarinfo("bff/link", tarfile.SYMTYPE, linkname="/etc/shadow")),
            ("hardlink", lambda: self._make_tarinfo("bff/hlink", tarfile.LNKTYPE, linkname="/etc/shadow")),
            ("fifo", lambda: self._make_tarinfo("bff/pipe", tarfile.FIFOTYPE)),
        ):
            with self.subTest(case=label):
                bad_tar = self.tmp_path / f"bad-{label}.tar.gz"
                with tarfile.open(bad_tar, "w:gz") as tf:
                    ti = build_info()
                    if ti.isfile():
                        raw = b"bad"
                        ti.size = len(raw)
                        tf.addfile(ti, io.BytesIO(raw))
                    else:
                        tf.addfile(ti)
                dest_bad = self.tmp_path / f"extract-bad-{label}"
                dest_bad.mkdir(parents=True, exist_ok=True)
                with _Py3112TarFile.open(bad_tar, "r:gz") as tf:
                    with self.assertRaises(SystemExit):
                        collector_mod._safe_extract_tar(tf, dest_bad)

    @staticmethod
    def _make_tarinfo(name: str, ttype: bytes, linkname: str = "") -> tarfile.TarInfo:
        ti = tarfile.TarInfo(name)
        ti.type = ttype
        ti.linkname = linkname
        return ti

    def test_rollback_and_restore_drains_pass_core_and_singleton_profiles_to_compose_rm(self) -> None:
        # 7. lifecycle-projector depends on nats (profiles: [core, stateful]); both drain calls
        # must pass --profile core --profile singleton and absent-owner rm must pass --profile core.
        for key in staging_sanitize.BASELINE_OPTIONAL_OWNER_IMAGE_KEYS:
            del self.manifest["rollback_baseline"]["backend_images"][key]
        self.manifest_path.write_text(json.dumps(self.manifest), encoding="utf-8")
        self.manifest_sha = release_manifest.compute_manifest_sha256(self.manifest)

        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        vm_logs = self.vm_bin_log_file.read_text(encoding="utf-8").splitlines()
        lp_rm_cmds = [l for l in vm_logs if l.startswith("docker compose ") and " rm -fsv lifecycle-projector" in l]
        self.assertEqual(len(lp_rm_cmds), 2)
        for cmd in lp_rm_cmds:
            self.assertIn("--profile core --profile singleton rm -fsv lifecycle-projector", cmd)
        absent_rm_cmds = [
            l for l in vm_logs if l.startswith("docker compose ") and " rm -fsv paper-fleet-reconciler" in l
        ]
        self.assertEqual(len(absent_rm_cmds), 1)
        self.assertIn("--profile core rm -fsv paper-fleet-reconciler", absent_rm_cmds[0])

    def test_owner_store_backup_and_manifest_use_consistent_frozen_snapshot_under_concurrent_writes(self) -> None:
        # 8. Concurrent owner writes to data_mount/bff during pg_dump must not cause
        # owner_store_manifest_sha256 != restored_owner_store_manifest_sha256.
        ver = self._provision_and_verify(FAKE_CONCURRENT_OWNER_STORE_WRITE_DURING_PG_DUMP="true")
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        gate_dir = self.state_dir / "observed-gates" / "pantheon-20261024-001"
        br_rec = json.loads((gate_dir / "backup-restore-record.json").read_text(encoding="utf-8"))
        self.assertEqual(
            br_rec["owner_store_manifest_sha256"],
            br_rec["restored_owner_store_manifest_sha256"],
        )

    def test_section6_mints_fresh_300s_tokens_after_sections_3_to_5_expire_initial_tokens(self) -> None:
        # 9. Section 3 tokens have ttl_seconds=300 and expire while Sections 3, 4, and 5 execute;
        # Section 6 must mint fresh 300s tokens right before its probes.
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        gate_dir = self.state_dir / "observed-gates" / "pantheon-20261024-001"
        sec_rec = json.loads((gate_dir / "security-readback.json").read_text(encoding="utf-8"))
        self.assertTrue(sec_rec["tenant_isolation_enforced"])
        self.assertTrue(sec_rec["viewer_mutation_rejected"])

    def test_large_ssh_bundle_over_256kib_flows_through_deploy_and_collect_verify_gates(self) -> None:
        # 10. Drill 4 regression: collector bundle > 256 KiB (well above Linux MAX_ARG_STRLEN 128 KiB)
        # must never be passed via environment variables or argv to python3 (which died with E2BIG / exit 126
        # at staging_ops.sh:661, :622, and :499). Both deploy-host and collect-verify-gates (plus
        # staging_verify.sh) must read 0600 files under STATE_DIR and preserve the full bundle.
        self._unlock_vm_env_dir()
        raw_ds = _sample_raw_dataset(self.manifest["migration_set_sha256"])
        for i in range(80):
            tname = f"public.domain_table_{i:03d}"
            raw_ds["catalog"][tname] = {
                f"col_{j:02d}": {"type": "character varying(255)", "nullable": True, "has_default": False}
                for j in range(25)
            }
            raw_ds["tables"][tname] = []
        sanitized = staging_sanitize.sanitize_dataset(raw_ds)
        (self.data_mount / "sanitized" / "snapshot-metadata.json").write_text(
            json.dumps(sanitized["metadata"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
        vm_state = json.loads(self.vm_runtime_state_file.read_text(encoding="utf-8"))
        vm_state["databases"]["pantheon_staging"] = {
            "tables": sanitized["tables"],
            "schema": raw_ds["catalog"],
        }
        self.vm_runtime_state_file.write_text(json.dumps(vm_state, indent=2, sort_keys=True), encoding="utf-8")
        self._lock_root_owned_paths()

        # Wrap fake gcloud so SSH deploy-host also emits > 256 KiB of bootstrap log lines before its JSON readback.
        big_ssh_wrapper = self.tmp_path / "big-ssh-gcloud.py"
        big_ssh_wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, subprocess, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['compute', 'ssh'] and any('bootstrap.sh' in a for a in args):\n"
            "    sys.stdout.write(('bootstrap-progress-line-' + ('x' * 100) + '\\n') * 2400)\n"
            "    sys.stdout.flush()\n"
            f"proc = subprocess.run([{str(self.gcloud_bin)!r}] + args)\n"
            "raise SystemExit(proc.returncode)\n",
            encoding="utf-8",
        )
        big_ssh_wrapper.chmod(0o755)

        ver = self._provision_and_verify(GCLOUD_BIN=str(big_ssh_wrapper))
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        ver_json = json.loads(ver.stdout)
        self.assertEqual(ver_json["status"], "staging_verified")

        deploy_out_file = self.state_dir / "artifacts" / "pantheon-20261024-001-ssh-deploy.out"
        verify_out_file = self.state_dir / "artifacts" / "pantheon-20261024-001-ssh-verify.out"
        self.assertTrue(deploy_out_file.is_file())
        self.assertTrue(verify_out_file.is_file())
        self.assertEqual(deploy_out_file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(verify_out_file.stat().st_mode & 0o777, 0o600)
        self.assertGreater(deploy_out_file.stat().st_size, 262144)
        self.assertGreater(verify_out_file.stat().st_size, 262144)

        gate_dir = self.state_dir / "observed-gates" / "pantheon-20261024-001"
        for fname in (
            "observed-deploy.json",
            "sanitized-dataset.json",
            "migration-plan.json",
            "backup-restore-record.json",
            "rollback-readback.json",
            "security-readback.json",
        ):
            gfile = gate_dir / fname
            self.assertTrue(gfile.is_file(), fname)
            gdoc = json.loads(gfile.read_text(encoding="utf-8"))
            self.assertTrue(gdoc["observed_from_live_vm"], fname)
            self.assertFalse(gdoc["dry_run"], fname)

        mig_plan = json.loads((gate_dir / "migration-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(len(mig_plan["old_app_schema"]), 85)
        self.assertEqual(len(mig_plan["new_schema"]), 86)
        self.assertIn("public.domain_table_079", mig_plan["old_app_schema"])
        self.assertEqual(len(mig_plan["old_app_schema"]["public.domain_table_079"]), 25)

        # Also verify the collect-verify-gates failure branch (:622) handles > 256 KiB output without E2BIG.
        fail_payload = json.dumps(
            {
                "status": "error",
                "step": "collect-verify-gates",
                "error_code": "PRODUCT_OWNER_UNSERVED",
                "message": "Product owners not running/healthy: registry, agora-interaction-worker",
            }
        )
        big_fail_wrapper = self.tmp_path / "big-fail-gcloud.py"
        big_fail_wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['compute', 'ssh'] and any('collect-gates' in a for a in args):\n"
            "    sys.stdout.write(('collector-progress-line-' + ('y' * 100) + '\\n') * 2400)\n"
            f"    print({fail_payload!r})\n"
            "    raise SystemExit(1)\n"
            f"os.execv({str(self.gcloud_bin)!r}, [{str(self.gcloud_bin)!r}] + args)\n",
            encoding="utf-8",
        )
        big_fail_wrapper.chmod(0o755)
        res_fail = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", GCLOUD_BIN=str(big_fail_wrapper)),
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_fail.returncode, 1)
        self.assertNotIn("Argument list too long", res_fail.stderr)
        self.assertIn("LIVE_GATE_COLLECTION_FAILED", res_fail.stderr)
        self.assertIn("PRODUCT_OWNER_UNSERVED", res_fail.stderr)
        self.assertIn("unserved_product_owner_services: agora-interaction-worker,registry", res_fail.stderr)

    def test_collect_verify_gates_failure_reports_redacted_message_and_uploads_gate_failure_summary(self) -> None:
        subprocess.run(
            [str(PROVISION_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001"),
            capture_output=True,
            text=True,
            check=True,
        )
        jwt_secret = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJzdGctb3AiLCJ0ZW5hbnRfaWQiOiJ0ZW5hbnQtYWxwaGEifQ.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c"
        sk_secret = "sk-9876543210abcdefghijklmnop"
        ghp_secret = "ghp_9876543210abcdefghijklmnopqrstuv"
        aiza_secret = "AIzaSyB9876543210abcdefghijk_LMNOPQRST"
        long_token = "tok_" + ("Z" * 42)
        fail_payload = json.dumps(
            {
                "status": "error",
                "step": "collect-verify-gates",
                "error_code": "ROLLBACK_VERIFICATION_FAILED",
                "message": (
                    f"rollback duration must be <= 120 seconds (got 124.512); "
                    f"auth=Bearer {jwt_secret} key={sk_secret} gh={ghp_secret} gcp={aiza_secret} "
                    f"long={long_token} email=leaked.operator@example.com "
                    + ("extra-context-segment " * 35)
                ),
                "rollback_duration_seconds": 124.512,
                "bff_healthy": True,
                "phase_timings_seconds": {
                    "drain": 4.12,
                    "baseline_up": 41.3,
                    "owners_healthy": 68.4,
                    "readyz_200": 6.2,
                    "readback": 4.492,
                },
            }
        )
        fail_wrapper = self.tmp_path / "rollback-fail-gcloud.py"
        fail_wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import os, sys\n"
            "args = sys.argv[1:]\n"
            "if args[:2] == ['compute', 'ssh'] and any('collect-gates' in a for a in args):\n"
            "    print('raw-ssh-noise-line token=sk-rawsshsecret1234567890abcd')\n"
            f"    print({fail_payload!r})\n"
            "    raise SystemExit(1)\n"
            f"os.execv({str(self.gcloud_bin)!r}, [{str(self.gcloud_bin)!r}] + args)\n",
            encoding="utf-8",
        )
        fail_wrapper.chmod(0o755)
        ver = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", GCLOUD_BIN=str(fail_wrapper)),
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(ver.returncode, 0)
        die_doc = json.loads(ver.stderr.strip().splitlines()[-1])
        self.assertEqual(die_doc["error_code"], "LIVE_GATE_COLLECTION_FAILED")
        self.assertIn("ROLLBACK_VERIFICATION_FAILED", die_doc["message"])
        self.assertIn("rollback duration must be <= 120 seconds (got 124.512)", die_doc["message"])
        self.assertIn("rollback_duration_seconds=124.512", die_doc["message"])
        self.assertIn("bff_healthy=true", die_doc["message"])

        summary_file = self.state_dir / "evidence" / "pantheon-20261024-001-gate-failure-summary.json"
        self.assertTrue(summary_file.is_file())
        summary = json.loads(summary_file.read_text(encoding="utf-8"))
        self.assertEqual(summary["status"], "error")
        self.assertEqual(summary["step"], "collect-verify-gates")
        self.assertEqual(summary["error_code"], "ROLLBACK_VERIFICATION_FAILED")
        self.assertEqual(summary["rollback_duration_seconds"], 124.512)
        self.assertIs(summary["bff_healthy"], True)
        self.assertEqual(
            set(summary["phase_timings_seconds"].keys()),
            {"drain", "baseline_up", "owners_healthy", "readyz_200", "readback"},
        )
        self.assertLessEqual(len(summary["message"]), 600)
        self.assertIn("rollback duration must be <= 120 seconds (got 124.512)", summary["message"])

        # Raw SSH output must never be placed in the uploaded evidence directory.
        evidence_files = [p.name for p in (self.state_dir / "evidence").iterdir()]
        self.assertFalse(any("ssh" in name or name.endswith(".out") for name in evidence_files), evidence_files)

        combined_public = ver.stderr + "\n" + summary_file.read_text(encoding="utf-8")
        for secret_item in (
            jwt_secret,
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            sk_secret,
            "sk-rawsshsecret1234567890abcd",
            ghp_secret,
            aiza_secret,
            long_token,
            "leaked.operator@example.com",
            "raw-ssh-noise-line",
        ):
            self.assertNotIn(secret_item, combined_public)

    def test_rollback_readback_records_per_phase_timings_summing_to_at_most_rollback_duration(self) -> None:
        ver = self._provision_and_verify()
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        gate_dir = self.state_dir / "observed-gates" / "pantheon-20261024-001"
        rb_readback = json.loads((gate_dir / "rollback-readback.json").read_text(encoding="utf-8"))
        self.assertIn("phase_timings_seconds", rb_readback)
        timings = rb_readback["phase_timings_seconds"]
        expected_phases = ("drain", "baseline_up", "owners_healthy", "readyz_200", "readback")
        self.assertEqual(set(timings.keys()), set(expected_phases))
        for phase_name in expected_phases:
            val = timings[phase_name]
            self.assertIsInstance(val, (int, float))
            self.assertNotIsInstance(val, bool)
            self.assertGreaterEqual(val, 0.0, phase_name)
        self.assertLessEqual(sum(timings.values()), rb_readback["rollback_duration_seconds"])
        self.assertLessEqual(rb_readback["rollback_duration_seconds"], 120.0)

        ver_summary = json.loads(ver.stdout)
        self.assertEqual(
            ver_summary["gates"]["exact_pair_rollback"]["phase_timings_seconds"],
            timings,
        )

    def test_core_owner_restart_count_increase_while_running_is_reported_unserved_and_stable_passes(self) -> None:
        # 1. A fake core owner (deployment or governance) whose RestartCount increases between
        # samples while State.Running=true must be reported unserved across initial, rollback, and
        # restore legs with structure-only diagnostics and no leaked secrets.
        base_prov_env = self._base_env(RELEASE_ID="pantheon-20261024-001", STAGING_READY_INTERVAL="0")
        subprocess.run([str(PROVISION_SCRIPT)], env=base_prov_env, capture_output=True, text=True, check=True)
        self._unlock_vm_env_dir()
        (self.vm_env_dir / "pg-sim").mkdir(exist_ok=True)
        initial_vm_state = self.vm_runtime_state_file.read_bytes()
        self._lock_root_owned_paths()

        for leg, env_overrides, expected_code in (
            ("initial_deployment", {"FAKE_CRASHLOOP_OWNER": "deployment"}, "CORE_OWNER_UNSERVED"),
            ("initial_governance", {"FAKE_CRASHLOOP_OWNER": "governance"}, "CORE_OWNER_UNSERVED"),
            ("rollback_deployment", {"FAKE_BASELINE_CRASHLOOP_OWNER": "deployment"}, "ROLLBACK_BASELINE_UNHEALTHY"),
            ("restore_deployment", {"FAKE_RESTORE_CRASHLOOP_OWNER": "deployment"}, "RESTORE_CANDIDATE_UNHEALTHY"),
        ):
            with self.subTest(leg=leg):
                self._unlock_vm_env_dir()
                self.vm_runtime_state_file.write_bytes(initial_vm_state)
                for cnt_file in self.vm_env_dir.glob("*.count"):
                    cnt_file.unlink(missing_ok=True)
                self._lock_root_owned_paths()
                ver = subprocess.run(
                    [str(VERIFY_SCRIPT)],
                    env=self._base_env(RELEASE_ID="pantheon-20261024-001", STAGING_READY_INTERVAL="0", **env_overrides),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(ver.returncode, 0, f"{leg} unexpectedly passed")
                out = ver.stderr + ver.stdout
                self.assertIn("LIVE_GATE_COLLECTION_FAILED", out)
                self.assertIn(expected_code, out)
                owner_name = next(iter(env_overrides.values()))
                self.assertIn(f"owner={owner_name}", out)
                self.assertIn("status=exited exit_code=1 oom_killed=false restart_count=2 error_present=true", out)
                self.assertIn("frame=/workspace/services/control-plane/bff/main.py:42", out)
                self.assertIn("exception_type=RuntimeError", out)
                self.assertIn("exception_type=OtherException", out)
                summary_file = self.state_dir / "evidence" / "pantheon-20261024-001-gate-failure-summary.json"
                self.assertTrue(summary_file.is_file())
                summary = json.loads(summary_file.read_text(encoding="utf-8"))
                self.assertEqual(summary["error_code"], expected_code)
                self.assertIn(f"owner={owner_name}", summary["message"])
                if leg in ("rollback_deployment", "restore_deployment"):
                    self.assertIn("rollback_duration_seconds", summary)
                    self.assertIsInstance(summary["rollback_duration_seconds"], (int, float))
                    self.assertIn("phase_timings_seconds", summary)
                for leaked in (
                    "stg-isolated-pg-password-20261025",
                    "alpha-77",
                    "res-123",
                    "connect(",
                    "/etc/private",
                    "strategy_alpha77",
                    "result_res123",
                    "ResearchResult",
                    "boom",
                    "Traceback",
                ):
                    self.assertNotIn(leaked, out + summary_file.read_text(encoding="utf-8"))

        # 2. A stable core owner (RestartCount unchanged across samples while State.Running=true) passes.
        self._unlock_vm_env_dir()
        self.vm_runtime_state_file.write_bytes(initial_vm_state)
        for cnt_file in self.vm_env_dir.glob("*.count"):
            cnt_file.unlink(missing_ok=True)
        self._lock_root_owned_paths()
        ver_ok = subprocess.run(
            [str(VERIFY_SCRIPT)],
            env=self._base_env(RELEASE_ID="pantheon-20261024-001", STAGING_READY_INTERVAL="0"),
            capture_output=True,
            text=True,
        )
        self.assertEqual(ver_ok.returncode, 0, ver_ok.stderr + ver_ok.stdout)
        self.assertEqual(json.loads(ver_ok.stdout)["status"], "staging_verified")

    def test_short_uptime_early_restart_and_starting_healthcheck_owner_readiness(self) -> None:
        base_prov_env = self._base_env(RELEASE_ID="pantheon-20261024-001", STAGING_READY_INTERVAL="0")
        subprocess.run([str(PROVISION_SCRIPT)], env=base_prov_env, capture_output=True, text=True, check=True)
        self._unlock_vm_env_dir()
        (self.vm_env_dir / "pg-sim").mkdir(exist_ok=True)
        initial_vm_state = self.vm_runtime_state_file.read_bytes()
        self._lock_root_owned_paths()

        def reset_vm_state() -> None:
            self._unlock_vm_env_dir()
            self.vm_runtime_state_file.write_bytes(initial_vm_state)
            for cnt_file in self.vm_env_dir.glob("*.count"):
                cnt_file.unlink(missing_ok=True)
            self._lock_root_owned_paths()

        # 1. Crash-looping fake with Running=true and RestartCount unchanged on two consecutive samples,
        #    but StartedAt only 3s old (< STAGING_OWNER_MIN_UPTIME_SECONDS=15), is unserved.
        for svc_name, expected_code in (
            ("governance", "CORE_OWNER_UNSERVED"),
            ("consultation-svc", "PRODUCT_OWNER_UNSERVED"),
        ):
            with self.subTest(case=f"short_uptime_{svc_name}"):
                reset_vm_state()
                ver_short = subprocess.run(
                    [str(VERIFY_SCRIPT)],
                    env=self._base_env(
                        RELEASE_ID="pantheon-20261024-001",
                        FAKE_SHORT_UPTIME_OWNER=svc_name,
                        FAKE_SHORT_UPTIME_SECONDS="3",
                        STAGING_READY_INTERVAL="0.05",
                        STAGING_OWNER_READY_TIMEOUT="0.2",
                    ),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(ver_short.returncode, 0, f"{svc_name} with 3s StartedAt unexpectedly passed")
                self.assertIn(expected_code, ver_short.stderr + ver_short.stdout)
                self.assertIn(svc_name, ver_short.stderr + ver_short.stdout)

        # 2. A fake that restarts once early (RestartCount 0 -> 1) and is then stable with sufficient
        #    uptime (>= 15s) is served.
        for svc_name in ("governance",):
            with self.subTest(case=f"early_single_restart_{svc_name}"):
                reset_vm_state()
                ver_early = subprocess.run(
                    [str(VERIFY_SCRIPT)],
                    env=self._base_env(
                        RELEASE_ID="pantheon-20261024-001",
                        FAKE_EARLY_RESTART_OWNER=svc_name,
                        STAGING_READY_INTERVAL="0",
                    ),
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(ver_early.returncode, 0, ver_early.stderr + ver_early.stdout)
                self.assertEqual(json.loads(ver_early.stdout)["status"], "staging_verified")

        # 3. A healthcheck owner reporting "starting" is unserved.
        for svc_name, expected_code in (
            ("governance", "CORE_OWNER_UNSERVED"),
            ("registry", "PRODUCT_OWNER_UNSERVED"),
        ):
            with self.subTest(case=f"starting_health_{svc_name}"):
                reset_vm_state()
                ver_starting = subprocess.run(
                    [str(VERIFY_SCRIPT)],
                    env=self._base_env(
                        RELEASE_ID="pantheon-20261024-001",
                        FAKE_STARTING_HEALTH_OWNER=svc_name,
                        STAGING_READY_INTERVAL="0.05",
                        STAGING_OWNER_READY_TIMEOUT="0.2",
                    ),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(ver_starting.returncode, 0, f"{svc_name} with starting health unexpectedly passed")
                self.assertIn(expected_code, ver_starting.stderr + ver_starting.stdout)
                self.assertIn(svc_name, ver_starting.stderr + ver_starting.stdout)

    def test_deploy_host_renders_migrations_from_manifest_backend_commit_not_checkout_head(self) -> None:
        origin_repo = self.tmp_path / "fake-origin-repo"
        origin_repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "-C", str(origin_repo), "init", "-b", "dev"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(origin_repo), "config", "user.email", "test@pantheon.internal"], check=True)
        subprocess.run(["git", "-C", str(origin_repo), "config", "user.name", "Pantheon Test"], check=True)

        # Commit 0: commit lacking services/trade_journey/migrations
        (origin_repo / "README.md").write_text("fake repo\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(origin_repo), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(origin_repo), "commit", "-m", "init without migrations"], check=True, capture_output=True)
        missing_path_commit = subprocess.run(
            ["git", "-C", str(origin_repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Commit 1 (manifest backend.commit): base migration set (2 SQL files)
        origin_mig_dir = origin_repo / "services" / "trade_journey" / "migrations"
        origin_mig_dir.mkdir(parents=True, exist_ok=True)
        base_sql_names = (
            "001_create_trade_journey_projection_schema.sql",
            "002_add_trade_journey_search_indexes.sql",
        )
        base_lines: list[str] = []
        for sql_name in base_sql_names:
            raw_bytes = (MIGRATIONS_SRC_DIR / sql_name).read_bytes()
            (origin_mig_dir / sql_name).write_bytes(raw_bytes)
            base_lines.append(f"{hashlib.sha256(raw_bytes).hexdigest()}  {sql_name}\n")
        backend_mig_sha = "sha256:" + hashlib.sha256("".join(base_lines).encode("utf-8")).hexdigest()

        subprocess.run(["git", "-C", str(origin_repo), "add", "services/trade_journey/migrations"], check=True)
        subprocess.run(["git", "-C", str(origin_repo), "commit", "-m", "release backend commit migrations"], check=True, capture_output=True)
        backend_commit = subprocess.run(
            ["git", "-C", str(origin_repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # Commit 2 (checkout HEAD): adds an extra migration after the release's backend commit
        extra_sql_name = "003_reclassify_stale_open_journeys.sql"
        extra_bytes = (
            (MIGRATIONS_SRC_DIR / extra_sql_name).read_bytes()
            if (MIGRATIONS_SRC_DIR / extra_sql_name).is_file()
            else b"ALTER TABLE public.telemetry_events ADD COLUMN IF NOT EXISTS extra_post_release_col TEXT NULL;\n"
        )
        (origin_mig_dir / extra_sql_name).write_bytes(extra_bytes)
        head_lines = [*base_lines, f"{hashlib.sha256(extra_bytes).hexdigest()}  {extra_sql_name}\n"]
        head_mig_sha = "sha256:" + hashlib.sha256("".join(head_lines).encode("utf-8")).hexdigest()
        self.assertNotEqual(backend_mig_sha, head_mig_sha)

        subprocess.run(["git", "-C", str(origin_repo), "add", f"services/trade_journey/migrations/{extra_sql_name}"], check=True)
        subprocess.run(["git", "-C", str(origin_repo), "commit", "-m", "later dev migration"], check=True, capture_output=True)
        checkout_head_commit = subprocess.run(
            ["git", "-C", str(origin_repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertNotEqual(backend_commit, checkout_head_commit)

        # Shallow checkout (depth=1, matching actions/checkout default) at checkout_head_commit
        checkout_repo = self.tmp_path / "fake-checkout-repo"
        subprocess.run(
            ["git", "clone", "--no-local", "--depth=1", f"file://{origin_repo}", str(checkout_repo)],
            check=True,
            capture_output=True,
        )
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(checkout_repo), "rev-parse", "--is-shallow-repository"],
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip(),
            "true",
        )
        self.assertTrue((checkout_repo / "services" / "trade_journey" / "migrations" / extra_sql_name).is_file())
        # Before deploy-host runs, backend_commit is not present in the depth=1 shallow checkout
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(checkout_repo), "cat-file", "-e", f"{backend_commit}^{{commit}}"],
                capture_output=True,
            ).returncode,
            0,
        )

        # Populate controller scripts, staging assets, and schema into the fake checkout
        shutil.copytree(REPO_ROOT / "scripts" / "deploy", checkout_repo / "scripts" / "deploy", dirs_exist_ok=True)
        shutil.copytree(REPO_ROOT / "deploy" / "staging", checkout_repo / "deploy" / "staging", dirs_exist_ok=True)
        shutil.copytree(REPO_ROOT / "schemas", checkout_repo / "schemas", dirs_exist_ok=True)

        fake_provision = checkout_repo / "scripts" / "deploy" / "staging_provision.sh"
        fake_verify = checkout_repo / "scripts" / "deploy" / "staging_verify.sh"

        # Configure the VM fixture and manifest for backend_commit + backend_mig_sha
        self._unlock_vm_env_dir()
        sanitized = staging_sanitize.sanitize_dataset(_sample_raw_dataset(backend_mig_sha))
        (self.data_mount / "sanitized" / "snapshot-metadata.json").write_text(
            json.dumps(sanitized["metadata"], indent=2, sort_keys=True),
            encoding="utf-8",
        )
        vm_st = json.loads(self.vm_runtime_state_file.read_text(encoding="utf-8"))
        vm_st["migration_set_sha256"] = backend_mig_sha
        vm_st["databases"]["pantheon_staging"]["tables"] = sanitized["tables"]
        self.vm_runtime_state_file.write_text(json.dumps(vm_st, indent=2, sort_keys=True), encoding="utf-8")
        self._lock_root_owned_paths()

        manifest = _sample_manifest(
            release_id="pantheon-20261028-102",
            fe_sha=self.fe_sha,
            prev_fe_sha=self.fe_baseline_sha,
            mig_sha=backend_mig_sha,
            bff_commit=backend_commit,
        )
        manifest_path = self.tmp_path / "release-backend-commit-manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        manifest_sha = release_manifest.compute_manifest_sha256(manifest)

        env = self._base_env(
            RELEASE_ID="pantheon-20261028-102",
            RELEASE_MANIFEST_PATH=str(manifest_path),
            EXPECTED_MANIFEST_SHA256=manifest_sha,
        )

        # 0. On base (0b12194f4), staging_ops.sh rendered from ${REPO_ROOT}/services/trade_journey/migrations
        # (checkout HEAD with extra migration) and failed with MIGRATION_RENDER_FAILED.
        base_ref = "0b12194f4d628dfad789dd77574c0ec9a7ee20fc"
        base_show = subprocess.run(
            ["git", "-C", str(REPO_ROOT), "show", f"{base_ref}:scripts/deploy/staging_ops.sh"],
            capture_output=True,
            text=True,
        )
        if base_show.returncode == 0 and base_show.stdout:
            base_ops_text = base_show.stdout
        else:
            base_ops_text = (REPO_ROOT / "scripts" / "deploy" / "staging_ops.sh").read_text(encoding="utf-8").replace(
                '--src "${MIGRATION_COMMIT_SRC_DIR}"',
                '--src "${REPO_ROOT}/services/trade_journey/migrations"',
            )
        fake_ops = checkout_repo / "scripts" / "deploy" / "staging_ops.sh"
        fake_ops.write_text(base_ops_text, encoding="utf-8")
        fake_ops.chmod(0o755)
        base_res = subprocess.run(
            [str(fake_ops), "deploy-host"],
            env={
                **env,
                "VM_NAME": "pantheon-stg-pantheon-20261028-102",
                "RESERVED_IP": "203.0.113.10",
            },
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(base_res.returncode, 0)
        self.assertIn("MIGRATION_RENDER_FAILED", base_res.stderr + base_res.stdout)
        # Base 0b12194f4 never fetched backend_commit into the shallow checkout
        self.assertNotEqual(
            subprocess.run(
                ["git", "-C", str(checkout_repo), "cat-file", "-e", f"{backend_commit}^{{commit}}"],
                capture_output=True,
            ).returncode,
            0,
        )

        # 1. Restore updated staging_ops.sh: deploy-host fetches backend_commit, archives only
        # services/trade_journey/migrations from backend_commit, and succeeds.
        shutil.copy2(REPO_ROOT / "scripts" / "deploy" / "staging_ops.sh", fake_ops)
        prov = subprocess.run([str(fake_provision)], env=env, capture_output=True, text=True)
        self.assertEqual(prov.returncode, 0, prov.stderr + prov.stdout)

        # Verify backend_commit was fetched into the shallow checkout and only its migrations were shipped
        self.assertEqual(
            subprocess.run(
                ["git", "-C", str(checkout_repo), "cat-file", "-e", f"{backend_commit}^{{commit}}"],
                capture_output=True,
            ).returncode,
            0,
        )
        self.assertEqual(list((self.state_dir / "artifacts").glob("migration-src-*")), [])

        self._unlock_vm_env_dir()
        installed_spec = self.staging_root / "state" / "migration-material" / "migration-spec.json"
        installed_dir = self.staging_root / "state" / "migration-material" / "migrations"
        self.assertTrue(installed_spec.is_file())
        spec_doc = json.loads(installed_spec.read_text(encoding="utf-8"))
        self.assertEqual(spec_doc["migration_set_sha256"], backend_mig_sha)
        self.assertEqual([f["name"] for f in spec_doc["files"]], list(base_sql_names))
        self.assertEqual(sorted(p.name for p in installed_dir.glob("*.sql")), list(base_sql_names))
        self.assertFalse((installed_dir / extra_sql_name).exists())
        self._lock_root_owned_paths()

        ver = subprocess.run([str(fake_verify)], env=env, capture_output=True, text=True)
        self.assertEqual(ver.returncode, 0, ver.stderr + ver.stdout)
        self.assertEqual(json.loads(ver.stdout)["status"], "staging_verified")

        # 2. A manifest digest that matches neither backend.commit nor checkout HEAD (or matches checkout HEAD
        #    while backend.commit is at the earlier migration set) must still fail closed with MIGRATION_RENDER_FAILED.
        for case_name, bad_mig_sha, bad_bff_commit, expected_reason in (
            ("digest_matches_neither", "sha256:" + ("9" * 64), backend_commit, "reason=migration_digest_or_spec_mismatch"),
            ("digest_matches_checkout_head_not_backend_commit", head_mig_sha, backend_commit, "reason=migration_digest_or_spec_mismatch"),
            ("unfetchable_backend_commit", backend_mig_sha, "f" * 40, "reason=backend_commit_fetch_failed"),
            ("missing_migration_path_in_commit", backend_mig_sha, missing_path_commit, "reason=migration_path_missing"),
        ):
            with self.subTest(case=case_name):
                bad_manifest = _sample_manifest(
                    release_id="pantheon-20261028-103",
                    fe_sha=self.fe_sha,
                    prev_fe_sha=self.fe_baseline_sha,
                    mig_sha=bad_mig_sha,
                    bff_commit=bad_bff_commit,
                )
                bad_path = self.tmp_path / f"bad-{case_name}.json"
                bad_path.write_text(json.dumps(bad_manifest), encoding="utf-8")
                bad_sha = release_manifest.compute_manifest_sha256(bad_manifest)
                res_bad = subprocess.run(
                    [str(checkout_repo / "scripts" / "deploy" / "staging_ops.sh"), "deploy-host"],
                    env=self._base_env(
                        RELEASE_ID="pantheon-20261028-103",
                        RELEASE_MANIFEST_PATH=str(bad_path),
                        EXPECTED_MANIFEST_SHA256=bad_sha,
                        VM_NAME="pantheon-stg-pantheon-20261028-102",
                        RESERVED_IP="203.0.113.10",
                    ),
                    capture_output=True,
                    text=True,
                )
                self.assertNotEqual(res_bad.returncode, 0, case_name)
                self.assertIn("MIGRATION_RENDER_FAILED", res_bad.stderr + res_bad.stdout)
                self.assertIn(expected_reason, res_bad.stderr + res_bad.stdout)


class WorkflowAndTemplateContractTests(unittest.TestCase):
    def test_rendered_compose_has_governance_and_deployment_healthchecks_on_health(self) -> None:
        compose = yaml.safe_load(COMPOSE_STAGING.read_text(encoding="utf-8"))
        services = compose["services"]
        for svc_name, port in (("governance", 8082), ("deployment", 8095)):
            with self.subTest(service=svc_name):
                hc = services[svc_name].get("healthcheck")
                self.assertIsInstance(hc, dict, f"{svc_name} missing healthcheck")
                self.assertEqual(
                    hc["test"],
                    [
                        "CMD",
                        "python",
                        "-c",
                        f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{port}/health', timeout=5).read()",
                    ],
                )
                self.assertEqual(hc["interval"], "10s")
                self.assertEqual(hc["timeout"], "5s")
                self.assertEqual(hc["retries"], 10)
                self.assertEqual(hc["start_period"], "15s")

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
        defaults_raw = (REPO_ROOT / "deploy" / "staging" / "staging-env.defaults").read_text(encoding="utf-8")
        self.assertIn("STAGING_MACHINE_TYPE=e2-standard-8", defaults_raw)
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
        # Private `.internal` hosts need explicit internal TLS on Debian Caddy 2.6, and the
        # collector reads FE/runtime-config back over loopback HTTP through the same Caddy.
        tls_directives = [ln for ln in caddy_raw.splitlines() if ln.strip() == "tls internal"]
        self.assertEqual(len(tls_directives), 2)
        self.assertIn("http://127.0.0.1:__STAGING_FE_HTTP_PORT__", caddy_raw)
        self.assertIn("bind 127.0.0.1", caddy_raw)
        self.assertEqual(caddy_raw.count("import staging_fe_routes"), 2)
        self.assertTrue(BOOTSTRAP_SCRIPT.exists() and os.access(BOOTSTRAP_SCRIPT, os.X_OK))
        self.assertTrue(COLLECTOR_SCRIPT.exists() and os.access(COLLECTOR_SCRIPT, os.X_OK))

    def test_collector_does_not_require_custom_image_labels(self) -> None:
        # Actual retained BFF image carries no org.pantheon.* migration/compat labels; the
        # contract is manifest + migration-spec + live catalog/checksum observations instead.
        raw = COLLECTOR_SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("org.pantheon.migration_set_sha256", raw)
        self.assertNotIn("org.pantheon.compatibility_manifest_sha256", raw)
        self.assertIn("MIGRATION_SPEC_SHA_MISMATCH", raw)

    def test_stage_persona_provisioning_store_and_governance_approval_auth_contract(self) -> None:
        import base64
        import importlib.util
        import sys
        import time
        from unittest import mock
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa
        from fastapi import HTTPException
        from services.governance import main as gov_main
        from services.runtime_auth_inbound import encode_jwt_hs256

        compose = yaml.safe_load(COMPOSE_STAGING.read_text(encoding="utf-8"))
        bff_env = compose["services"]["operator-bff"]["environment"]
        self.assertEqual(bff_env["PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND"], "postgres")
        self.assertEqual(bff_env["PANTHEON_PERSONA_PROVISIONING_STORE_SCHEMA"], "bff")
        self.assertNotIn("PANTHEON_PERSONA_PROVISIONING_STORE_DSN", bff_env)

        gov_env = compose["services"]["governance"]["environment"]
        self.assertEqual(gov_env["PANTHEON_GOVERNANCE_AUTH_MODE"], "strict")
        self.assertEqual(
            gov_env["PANTHEON_GOVERNANCE_JWT_ISSUER"],
            "${STAGING_JWT_ISSUER:?staging jwt issuer required}",
        )
        self.assertEqual(
            gov_env["PANTHEON_GOVERNANCE_JWT_AUDIENCE"],
            "${STAGING_JWT_AUDIENCE:?staging jwt audience required}",
        )
        self.assertEqual(
            gov_env["PANTHEON_GOVERNANCE_OIDC_ISSUER"],
            "${STAGING_IDP_ISSUER:?staging idp issuer required}",
        )
        self.assertEqual(
            gov_env["PANTHEON_GOVERNANCE_OIDC_DISCOVERY_URL"],
            "${STAGING_IDP_ISSUER:?staging idp issuer required}/.well-known/openid-configuration",
        )

        boot_raw = BOOTSTRAP_SCRIPT.read_text(encoding="utf-8")
        mint_py = boot_raw.split("cat > \"${LOCAL_WORK_DIR}/mint_runtime_manager_token.py\" <<'PY'\n", 1)[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as td:
            td_path = Path(td)
            priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            pub_nums = priv.public_key().public_numbers()
            b64u = lambda b: base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")
            jwk = {
                "kty": "RSA",
                "kid": "stg-gov-kid-1",
                "use": "sig",
                "alg": "RS256",
                "n": b64u(pub_nums.n.to_bytes((pub_nums.n.bit_length() + 7) // 8, "big")),
                "e": b64u(pub_nums.e.to_bytes((pub_nums.e.bit_length() + 7) // 8, "big")),
            }
            jwks_path = td_path / "jwks.json"
            jwks_path.write_text(json.dumps({"keys": [jwk]}), encoding="utf-8")
            jwks_uri = f"file://{jwks_path}"

            mint_script = td_path / "mint_runtime_manager_token.py"
            mint_script.write_text(mint_py, encoding="utf-8")
            secrets_env = td_path / "staging-secrets.env"
            isolated_dsn = "postgresql://pantheon_stg_u:stg-secret@postgres:5432/pantheon_stg_iso"
            secrets_env.write_text(
                "\n".join(
                    [
                        "PANTHEON_BFF_JWT_SECRET=stg-bff-jwt-secret-0123456789abcdef",
                        "PANTHEON_RUNTIME_JWT_SECRET=stg-runtime-jwt-secret-0123456789abcdef",
                        f"DATABASE_URL={isolated_dsn}",
                        f"PANTHEON_DATABASE_URL={isolated_dsn}",
                        f"PANTHEON_BFF_JWKS_URI={jwks_uri}",
                        "PANTHEON_BFF_ROLE_CLAIMS=roles,groups",
                        "PANTHEON_BFF_ROLE_MAP=pantheon-operator=operator;pantheon-viewer=viewer",
                        "PANTHEON_BFF_ROLE_MAP_MODE=strict",
                        "PANTHEON_BFF_REQUIRE_EMAIL_VERIFIED=true",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            res = subprocess.run(
                [
                    sys.executable,
                    str(mint_script),
                    str(secrets_env),
                    "https://auth.pantheon-stg.internal",
                    "pantheon-runtime",
                    "tenant-a",
                    "3600",
                    "pantheon-stg-oidc-aud",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(res.returncode, 0)
            rendered_kv = dict(
                line.split("=", 1)
                for line in secrets_env.read_text(encoding="utf-8").splitlines()
                if "=" in line and not line.startswith("#")
            )
            self.assertEqual(rendered_kv["PANTHEON_PERSONA_PROVISIONING_STORE_DSN"], isolated_dsn)
            self.assertEqual(
                rendered_kv["PANTHEON_GOVERNANCE_JWT_SECRET"],
                "stg-runtime-jwt-secret-0123456789abcdef",
            )
            self.assertEqual(rendered_kv["PANTHEON_GOVERNANCE_OIDC_AUDIENCE"], "pantheon-stg-oidc-aud")
            self.assertEqual(
                rendered_kv["PANTHEON_GOVERNANCE_JWKS_URI"],
                jwks_uri,
            )
            self.assertEqual(rendered_kv["PANTHEON_GOVERNANCE_ROLE_CLAIMS"], "roles,groups")
            self.assertEqual(
                rendered_kv["PANTHEON_GOVERNANCE_ROLE_MAP"],
                "pantheon-operator=operator;pantheon-viewer=viewer",
            )
            self.assertEqual(rendered_kv["PANTHEON_GOVERNANCE_ROLE_MAP_MODE"], "strict")

            spec = importlib.util.spec_from_file_location(
                "bff_persona_provisioning_stg_test",
                REPO_ROOT / "services" / "control-plane" / "bff" / "persona_provisioning.py",
            )
            assert spec and spec.loader
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod
            spec.loader.exec_module(mod)
            merged_bff_env = {
                **rendered_kv,
                "PANTHEON_ENV": "staging",
                "PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND": bff_env["PANTHEON_PERSONA_PROVISIONING_STORE_BACKEND"],
                "PANTHEON_PERSONA_PROVISIONING_STORE_SCHEMA": bff_env["PANTHEON_PERSONA_PROVISIONING_STORE_SCHEMA"],
            }
            with mock.patch.object(mod.PostgresPersonaProvisioningStore, "_bootstrap", return_value=None):
                store = mod.make_persona_provisioning_store(merged_bff_env)
            self.assertIsInstance(store, mod.PostgresPersonaProvisioningStore)
            self.assertEqual(store.dsn, isolated_dsn)
            self.assertEqual(store.schema, "bff")

            def sign_rs256(claims):
                hdr = b64u(json.dumps({"alg": "RS256", "typ": "JWT", "kid": "stg-gov-kid-1"}, separators=(",", ":")).encode())
                pld = b64u(json.dumps(claims, separators=(",", ":")).encode())
                sig = priv.sign(f"{hdr}.{pld}".encode("ascii"), padding.PKCS1v15(), hashes.SHA256())
                return f"{hdr}.{pld}.{b64u(sig)}"

            now = int(time.time())
            stage_gov_runtime_env = {
                **rendered_kv,
                "PANTHEON_GOVERNANCE_AUTH_MODE": "strict",
                "PANTHEON_GOVERNANCE_JWT_ISSUER": "https://auth.pantheon-stg.internal",
                "PANTHEON_GOVERNANCE_JWT_AUDIENCE": "pantheon-runtime",
                "PANTHEON_GOVERNANCE_OIDC_ISSUER": "https://idp.pantheon-stg.internal",
                "PANTHEON_GOVERNANCE_OIDC_DISCOVERY_URL": "https://idp.pantheon-stg.internal/.well-known/openid-configuration",
            }
            idp_op_token = sign_rs256(
                {
                    "iss": "https://idp.pantheon-stg.internal",
                    "aud": "pantheon-stg-oidc-aud",
                    "sub": "user-stg-operator-1",
                    "tenant_id": "tenant-a",
                    "groups": ["pantheon-operator"],
                    "email_verified": True,
                    "iat": now,
                    "exp": now + 3600,
                }
            )
            idp_unmapped_token = sign_rs256(
                {
                    "iss": "https://idp.pantheon-stg.internal",
                    "aud": "pantheon-stg-oidc-aud",
                    "sub": "user-stg-unmapped-1",
                    "tenant_id": "tenant-a",
                    "groups": ["unmapped-group"],
                    "email_verified": True,
                    "iat": now,
                    "exp": now + 3600,
                }
            )
            hs256_token = encode_jwt_hs256(
                {
                    "iss": "https://auth.pantheon-stg.internal",
                    "aud": "pantheon-runtime",
                    "sub": "svc:governance-internal-reader",
                    "tenant_id": "tenant-a",
                    "roles": ["approval_reader"],
                    "iat": now,
                    "exp": now + 3600,
                },
                secret=rendered_kv["PANTHEON_GOVERNANCE_JWT_SECRET"],
            )
            with mock.patch.dict(os.environ, stage_gov_runtime_env, clear=False):
                ctx = gov_main._approval_reader(f"Bearer {idp_op_token}")
                self.assertEqual(ctx.actor_id, "user-stg-operator-1")
                self.assertEqual(ctx.claims["tenant_id"], "tenant-a")
                self.assertIn("operator", ctx.roles)
                with self.assertRaises(HTTPException) as unmapped_exc:
                    gov_main._approval_reader(f"Bearer {idp_unmapped_token}")
                self.assertEqual(unmapped_exc.exception.status_code, 401)
                hs_ctx = gov_main._approval_reader(f"Bearer {hs256_token}")
                self.assertEqual(hs_ctx.actor_id, "svc:governance-internal-reader")
                self.assertEqual(hs_ctx.claims["tenant_id"], "tenant-a")
                self.assertIn("approval_reader", hs_ctx.roles)


if __name__ == "__main__":
    unittest.main()

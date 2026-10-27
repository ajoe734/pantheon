"""Local controller and contract tests for production blue/green delivery (`ENV-PROD-CONTROL-IMPL-001`).

NOTE: These tests use constrained host binaries (`docker`, `curl`, `mountpoint`, `sudo`) and a
temporary state tree to exercise both the controller (`prod_atomic_switch.sh`, `prod_rollback.sh`)
and the real VM adapter (`prod_ops.sh`, `bootstrap.sh`). They prove script and controller behavior
(preflight, bootstrap, digest-pinned image pre-acquisition, atomic switch, rollback, failure
injection) only. They do NOT constitute hosted production drill evidence, and no cloud or GitHub
resource is touched.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import sys
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

import yaml

from scripts.deploy import prod_preflight, release_manifest
from tests.deployment.test_staging_release import (
    MIGRATIONS_SRC_DIR,
    _real_migration_set,
    _sample_manifest as _staging_sample_manifest,
)

# Staging-produced manifests are used as Stage builds them. The product has no schema_migrations table: the
# migration set identity is Stage's recipe over the real versioned SQL bytes (_real_migration_set), and the
# fake production postgres serves the live information_schema catalog Stage queries.
MIGRATION_FILES, MIGRATION_SET_SHA = _real_migration_set()


def _sample_manifest(**kw):
    return _staging_sample_manifest(mig_sha=MIGRATION_SET_SHA, **kw)


def _col(typ: str, default: bool = False) -> dict:
    return {"type": typ, "nullable": False, "has_default": default}


# Independent of prod_preflight's parser: literal substrate columns (scripts/db_migrate.sh) and a line-oriented
# reading of the versioned SQL (one column per indented "name TYPE" line), as a real information_schema would show.
_SQL_TO_PG = {"TEXT": "text", "BIGINT": "bigint", "INT": "integer", "BOOLEAN": "boolean", "JSONB": "jsonb",
              "TIMESTAMPTZ": "timestamp with time zone"}


def _live_catalog() -> dict:
    cat = {
        "public.telemetry_events": {
            "event_id": _col("text"), "event_type": _col("text"), "created_at": _col("timestamp with time zone"),
            "payload": _col("jsonb"), "ingested_seq": _col("bigint", True), "ingested_at": _col("timestamp with time zone", True),
        },
        "public.loop_controller_records": {
            "loop_id": _col("text"), "tenant_id": _col("text"), "environment": _col("text"),
            "controller_id": _col("text"), "controller_name": _col("text"), "deployment_sha": _col("text"),
            "evidence_refs": _col("jsonb", True), "truth_level": _col("text"), "payload": _col("jsonb", True),
            "updated_at": _col("timestamp with time zone", True),
        },
    }
    for f in MIGRATION_FILES:
        rel = None
        for line in (MIGRATIONS_SRC_DIR / f["name"]).read_text().splitlines():
            m = re.match(r"CREATE TABLE IF NOT EXISTS (\S+) \(", line)
            if m:
                rel = m.group(1); cat[rel] = {}; continue
            if line.startswith(");"):
                rel = None
            c = re.match(r"^    ([a-z_]+) ([A-Z]+)\b", line)
            if rel and c and c.group(1) not in ("primary", "unique", "check"):
                cat[rel][c.group(1)] = _col(_SQL_TO_PG[c.group(2)])
    return cat

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

# Fake docker/curl/mountpoint/sudo binaries exercise the REAL prod_ops.sh/bootstrap.sh code paths.
FAKE_BIN = r"""#!/usr/bin/env bash
tool="$(basename "$0")"
echo "$tool $*" >> "$BIN_LOG"
case "$tool" in
  sudo)
    if [[ "$1" == "-E" ]]; then shift; fi
    exec "$@"
    ;;
  mountpoint) [[ "${FAKE_MOUNTED:-yes}" == "yes" ]]; exit $? ;;
  docker)
    args="$*"
    if [[ "$args" == "login "* ]]; then
      [[ "${FAKE_DOCKER_FAIL:-}" == *"login"* ]] && exit 1
      cat >/dev/null
      echo "$args" >> "$FAKE_STATE/docker_logins"
      exit 0
    fi
    if [[ "$args" == "pull "* ]]; then
      [[ "${FAKE_DOCKER_FAIL:-}" == *"pull"* ]] && exit 1
      img="${2:-}"
      echo "$img" >> "$FAKE_STATE/pulled_images"
      exit 0
    fi
    if [[ "$args" == *"compose"*"--profile stateful up"* ]]; then
      env | grep -E '^(PANTHEON_(RELEASE_ID|BACKEND_COMMIT|OPERATOR_BFF_IMAGE|OPERATOR_BFF_DIGEST|RUNTIME_MANAGER_IMAGE|GOVERNANCE_IMAGE|DEPLOYMENT_IMAGE|LIFECYCLE_PROJECTOR_IMAGE))=' > "$FAKE_STATE/stateful_env"
    fi
    if [[ "$args" == *"compose"*" exec -T postgres "* ]]; then
      if [[ "$args" == *"-f -"* ]]; then cat >> "$FAKE_STATE/applied_sql"; echo "--" >> "$FAKE_STATE/applied_sql"; exit 0; fi
      cat >/dev/null
      [[ -f "$FAKE_STATE/catalog" ]] || exit 1
      cat "$FAKE_STATE/catalog"; exit 0
    fi
    if [[ "$args" == *"compose"*" ps -q "* ]]; then
      svc="${args##* ps -q }"
      if [[ "$svc" == lifecycle-projector ]]; then
        if [[ -f "$FAKE_STATE/singleton_up" ]]; then for _ in $(seq 1 "${FAKE_SINGLETON_COUNT:-1}"); do echo "cid-lifecycle-projector-$_"; done; fi
      elif [[ "$svc" == operator-bff-blue || "$svc" == operator-bff-green ]]; then
        if [[ -f "$FAKE_STATE/stopped_$svc" ]]; then exit 0; fi
        echo "cid-$svc"
      else echo "cid-$svc"; fi
      exit 0
    fi
    if [[ "$args" == *"compose"*"up -d"*lifecycle-projector* ]]; then
      [[ "${FAKE_DOCKER_FAIL:-}" == *"compose"* ]] && exit 1
      touch "$FAKE_STATE/singleton_up"
      if [[ -n "${FAKE_SINGLETON_COMMIT_OVERRIDE:-}" && "${PANTHEON_RELEASE_ID:-}" == "${FAKE_SINGLETON_COMMIT_OVERRIDE_RELEASE:-}" ]]; then
        printf '%s' "$FAKE_SINGLETON_COMMIT_OVERRIDE" > "$FAKE_STATE/singleton_commit"
      else
        printf '%s' "${PANTHEON_BACKEND_COMMIT:-}" > "$FAKE_STATE/singleton_commit"
      fi
      if [[ -n "${PANTHEON_LIFECYCLE_PROJECTOR_IMAGE:-}" ]]; then
        printf '%s' "${FAKE_SINGLETON_IMAGE_OVERRIDE:-$PANTHEON_LIFECYCLE_PROJECTOR_IMAGE}" > "$FAKE_STATE/image_cid-lifecycle-projector-1"
      fi
      if [[ -n "${PANTHEON_RELEASE_ID:-}" ]]; then
        printf '%s' "$PANTHEON_RELEASE_ID" > "$FAKE_STATE/singleton_release_id"
      fi
    fi
    if [[ "$args" == *"compose"*"up -d"*operator-bff-blue* ]]; then
      [[ "${FAKE_DOCKER_FAIL:-}" == *"compose"* ]] && exit 1
      rm -f "$FAKE_STATE/stopped_operator-bff-blue"
      if [[ -n "${PANTHEON_OPERATOR_BFF_IMAGE:-}" ]]; then
        printf '%s' "$PANTHEON_OPERATOR_BFF_IMAGE" > "$FAKE_STATE/image_cid-operator-bff-blue"
        python3 - "${PANTHEON_RELEASE_ID:-}" "${PANTHEON_BACKEND_COMMIT:-}" "${PANTHEON_OPERATOR_BFF_IMAGE}" "${PANTHEON_OPERATOR_BFF_DIGEST:-}" > "$FAKE_STATE/bff_version_${PROD_BLUE_PORT:-8001}" <<'PY'
import json, sys
rel_id, commit, img, digest = sys.argv[1:5]
if not digest and "@sha256:" in img:
    digest = "sha256:" + img.split("@sha256:", 1)[1]
print(json.dumps({"environment": "production", "release_id": rel_id, "source_commit_sha": commit, "image_digest": digest, "operator_bff_image": img}))
PY
      fi
    fi
    if [[ "$args" == *"compose"*"up -d"*operator-bff-green* ]]; then
      [[ "${FAKE_DOCKER_FAIL:-}" == *"compose"* ]] && exit 1
      rm -f "$FAKE_STATE/stopped_operator-bff-green"
      if [[ -n "${PANTHEON_OPERATOR_BFF_IMAGE:-}" ]]; then
        printf '%s' "$PANTHEON_OPERATOR_BFF_IMAGE" > "$FAKE_STATE/image_cid-operator-bff-green"
        python3 - "${PANTHEON_RELEASE_ID:-}" "${PANTHEON_BACKEND_COMMIT:-}" "${PANTHEON_OPERATOR_BFF_IMAGE}" "${PANTHEON_OPERATOR_BFF_DIGEST:-}" > "$FAKE_STATE/bff_version_${PROD_GREEN_PORT:-8002}" <<'PY'
import json, sys
rel_id, commit, img, digest = sys.argv[1:5]
if not digest and "@sha256:" in img:
    digest = "sha256:" + img.split("@sha256:", 1)[1]
print(json.dumps({"environment": "production", "release_id": rel_id, "source_commit_sha": commit, "image_digest": digest, "operator_bff_image": img}))
PY
      fi
    fi
    if [[ "$args" == *"compose"*"rm -fsv lifecycle-projector"* ]]; then
      rm -f "$FAKE_STATE/singleton_up" "$FAKE_STATE/image_cid-lifecycle-projector-1"
    fi
    if [[ "$args" == *"compose"*"rm -fsv operator-bff-blue"* ]]; then
      touch "$FAKE_STATE/stopped_operator-bff-blue"
    fi
    if [[ "$args" == *"compose"*"rm -fsv operator-bff-green"* ]]; then
      touch "$FAKE_STATE/stopped_operator-bff-green"
    fi
    if [[ "$args" == "inspect -f {{range .Config.Env}}"* ]]; then cat "$FAKE_STATE/container_env"; exit 0; fi
    if [[ "$args" == "inspect -f {{.Config.Image}}"* ]]; then
      cid="${@: -1}"
      if [[ -n "${FAKE_CONTAINER_IMAGE_OVERRIDE:-}" ]]; then
        printf '%s' "$FAKE_CONTAINER_IMAGE_OVERRIDE"; exit 0
      fi
      if [[ -f "$FAKE_STATE/image_${cid}" ]]; then
        cat "$FAKE_STATE/image_${cid}"; exit 0
      fi
      cat "$FAKE_STATE/container_image"; exit 0
    fi
    if [[ "$args" == "image inspect -f"*migration* ]]; then printf '%s' "${FAKE_MIGRATION_LABEL:-}"; exit 0; fi
    if [[ "$args" == "image inspect -f"*compat* ]]; then printf '%s' "${FAKE_COMPAT_LABEL:-}"; exit 0; fi
    if [[ "$args" == "image inspect "* ]]; then
      [[ "${FAKE_DOCKER_FAIL:-}" == *"image_inspect"* ]] && exit 1
      exit 0
    fi
    [[ "${FAKE_DOCKER_FAIL:-}" == *"$(echo "$args" | awk '{print $1}')"* ]] && exit 1
    exit 0 ;;
  curl)
    url="${@: -1}"; all="$*"; port="$(sed -n 's#.*127\.0\.0\.1:\([0-9]*\)/.*#\1#p' <<<"$url")"
    out=""; prev=""; for a in "$@"; do [[ "$prev" == "-o" ]] && out="$a"; prev="$a"; done
    if [[ "$url" == *computeMetadata/v1/instance/service-accounts* ]]; then
      [[ "${FAKE_METADATA_FAIL:-}" == "true" ]] && exit 22
      printf '{"access_token":"ya29.fake-prod-runtime-token"}'
      exit 0
    fi
    if [[ "$url" == */runtime-config.js ]]; then
      [[ "${FAKE_RC_FETCH_FAIL:-}" == "true" ]] && exit 22
      if [[ -n "$out" && "$out" != /dev/null ]]; then
        if [[ -f "$FAKE_STATE/served_runtime_config_override" ]]; then
          cat "$FAKE_STATE/served_runtime_config_override" > "$out"
        elif [[ -f "$PROD_ROOT/config/runtime-config.js" ]]; then
          cat "$PROD_ROOT/config/runtime-config.js" > "$out"
        else
          cat "$FAKE_STATE/served_runtime_config" > "$out"
        fi
      fi
      printf 'HTTP/2 200\r\ncache-control: %s\r\n\r\n' "${FAKE_RC_CACHE:-no-store, no-cache}"; exit 0
    fi
    if [[ "$url" == */deployment.json ]]; then
      [[ "${FAKE_FE_DEPLOYMENT_FAIL:-}" == "true" ]] && exit 22
      dep_src="${FAKE_FE_DEPLOYMENT_FILE:-}"
      if [[ -z "$dep_src" ]]; then
        if [[ -f "$FAKE_STATE/served_deployment_json" ]]; then
          dep_src="$FAKE_STATE/served_deployment_json"
        elif [[ -f "$PROD_ROOT/fe/current/deployment.json" ]]; then
          dep_src="$PROD_ROOT/fe/current/deployment.json"
        fi
      fi
      [[ -n "$dep_src" && -f "$dep_src" ]] || exit 22
      [[ -n "$out" && "$out" != /dev/null ]] && cat "$dep_src" > "$out"
      printf 'HTTP/2 200\r\ncache-control: %s\r\n\r\n' "${FAKE_FE_DEPLOYMENT_CACHE:-no-store, no-cache}"; exit 0
    fi
    if [[ "$url" == */bff/readyz ]]; then
      if [[ -z "$port" ]]; then
        if [[ -n "${FAKE_PUBLIC_BFF_READYZ_CODE:-}" ]]; then
          printf '%s' "$FAKE_PUBLIC_BFF_READYZ_CODE"; exit 0
        fi
        if [[ -f "$PROD_ROOT/proxy/bff_upstream.caddy" ]]; then
          if grep -q '503' "$PROD_ROOT/proxy/bff_upstream.caddy"; then printf 503; exit 0; fi
          port="$(sed -n 's#.*127\.0\.0\.1:\([0-9]*\).*#\1#p' "$PROD_ROOT/proxy/bff_upstream.caddy" | head -n1)"
        else
          port="${PROD_TARGET_PORT:-8002}"
        fi
      fi
      if [[ "${FAKE_REAL_READYZ:-}" == "1" ]]; then
        # Mirrors services/control-plane/bff/main.py::_lifecycle_projector_dependency: ready only when a
        # projector controller exists and its deployment_sha equals the BFF's own BFF_COMMIT.
        bff_commit="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("source_commit_sha",""))' "$FAKE_STATE/bff_version_${port}" 2>/dev/null)"
        if [[ -f "$FAKE_STATE/singleton_up" && -n "$bff_commit" && "$(cat "$FAKE_STATE/singleton_commit" 2>/dev/null)" == "$bff_commit" ]]; then printf 200; else printf 503; fi
        exit 0
      fi
      [[ " ${FAKE_READY_PORTS:-} " == *" $port "* ]] && printf 200 || printf 503
      exit 0
    fi
    if [[ "$url" == */livez ]]; then
      if [[ "${FAKE_REAL_READYZ:-}" == "1" ]]; then
        [[ -f "$FAKE_STATE/bff_version_${port}" ]] && printf 200 || printf 503
      else
        [[ " ${FAKE_READY_PORTS:-} " == *" $port "* ]] && printf 200 || printf 503
      fi
      exit 0
    fi
    if [[ "$url" == */bff/version ]]; then
      [[ "${FAKE_PUBLIC_BFF_VERSION_FAIL:-}" == "true" ]] && exit 22
      if [[ -n "${FAKE_PUBLIC_BFF_VERSION_JSON:-}" ]]; then
        printf '%s' "$FAKE_PUBLIC_BFF_VERSION_JSON"; exit 0
      fi
      uport="$port"
      if [[ -z "$uport" ]]; then
        if [[ -f "$PROD_ROOT/proxy/bff_upstream.caddy" ]]; then
          if grep -q '503' "$PROD_ROOT/proxy/bff_upstream.caddy"; then exit 22; fi
          uport="$(sed -n 's#.*127\.0\.0\.1:\([0-9]*\).*#\1#p' "$PROD_ROOT/proxy/bff_upstream.caddy" | head -n1)"
        else
          uport="${PROD_TARGET_PORT:-8002}"
        fi
      fi
      if [[ -f "$FAKE_STATE/bff_version_${uport}" ]]; then
        cat "$FAKE_STATE/bff_version_${uport}"; exit 0
      fi
      cat "$FAKE_STATE/container_version"; exit 0
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


def _public_runtime_config(config_ref: str, *, sha: bool = True, **override) -> str:
    cfg = {
        "schemaVersion": "pantheon.fe.public-runtime-config.v1",
        "environment": "production",
        "configRef": config_ref,
        "bffBaseUrl": "https://api.trading.acme-prod.net",
        "gcpIdentity": {"apiKey": "public-web-key-fixture", "projectId": "acme-prod-identity",
                        "authDomain": "acme-prod-identity.firebaseapp.com",
                        "issuer": "https://securetoken.google.com/acme-prod-identity", "audience": "acme-prod-identity"},
    }
    cfg.update(override)
    if sha:
        canon = json.dumps(cfg, sort_keys=True, separators=(",", ":"))
        cfg["configSha256"] = "sha256:" + hashlib.sha256(canon.encode()).hexdigest()
    return "window.__PANTHEON_DEPLOYMENT_CONFIG__ = " + json.dumps(cfg, sort_keys=True) + ";\n"


class OpsHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        for tool in ("docker", "curl", "mountpoint", "sudo"):
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
        self.migration_dir = self.root / "migration" / "rel-1"
        (self.migration_dir / "migrations").mkdir(parents=True)
        for f in MIGRATION_FILES:
            (self.migration_dir / "migrations" / f["name"]).write_bytes((MIGRATIONS_SRC_DIR / f["name"]).read_bytes())
        shutil.copy(REPO_ROOT / "scripts" / "db_migrate.sh", self.migration_dir / "db_migrate.sh")
        (self.migration_dir / "migration-spec.json").write_text(json.dumps(
            {"phase": "expand", "migration_set_sha256": MIGRATION_SET_SHA, "files": MIGRATION_FILES}))
        self.mpath = self.tmp / "manifest.json"
        self.mpath.write_text(json.dumps(self.manifest))
        self.epath = self.tmp / "staging_evidence.json"
        self.epath.write_text(json.dumps(_evidence(self.manifest, "0" * 64)))
        self.log = self.tmp / "bin.log"
        (self.state / "catalog").write_text(json.dumps(_live_catalog()))
        self.rc = self.root / "config" / "runtime-config.js"
        self.rc.write_text(_public_runtime_config("rel-1"))
        (self.state / "served_runtime_config").write_text(self.rc.read_text())
        (self.state / "container_env").write_text("\n".join([
            "PANTHEON_LIVE_BROKER_ENABLED=false", "PANTHEON_MANAGEMENT_NL_ASSISTANT_PROVIDER_ENABLED=false",
            "PANTHEON_BFF_AUTH_STUB=false", "PANTHEON_BFF_AUTH_MODE=strict"]) + "\n")
        bff_img = self.manifest["backend"]["images"]["operator_bff"]
        (self.state / "container_image").write_text(bff_img)
        (self.state / "container_version").write_text(json.dumps({
            "environment": "production",
            "release_id": self.manifest["release_id"],
            "source_commit_sha": self.manifest["backend"]["commit"],
            "image_digest": "sha256:" + bff_img.split("@sha256:", 1)[1],
            "operator_bff_image": bff_img,
        }))
        self.token = self.tmp / "token"
        self.token.write_text("synthetic-readonly-token")

    def env(self, **extra):
        env = {**os.environ, **PROD_ENV, "PATH": f"{self.bin}:{os.environ['PATH']}", "BIN_LOG": str(self.log),
               "FAKE_STATE": str(self.state), "PROD_ROOT": str(self.root), "PROD_DATA_MOUNT": str(self.tmp / "data"),
               "PROD_BLUE_PORT": "8001", "PROD_GREEN_PORT": "8002", "PROD_ACTIVE_PORT": "8001",
               "PROD_TARGET_PORT": "8002", "RELEASE_MANIFEST_PATH": str(self.mpath),
               "PROD_COMPOSE_FILE": str(COMPOSE), "PROD_COMPOSE_ENV_FILE": str(self.tmp / "compose.env"),
               "PROD_GATE_TOKEN_FILE": str(self.token), "PROD_MIGRATION_DIR": str(self.migration_dir),
               "FAKE_READY_PORTS": "8001 8002",
               "STAGING_EVIDENCE_PATH": str(self.epath),
               "PROD_BASELINE_READY_TRIES": "1", "PROD_BASELINE_READY_INTERVAL": "0",
               "PROD_CANDIDATE_READY_TRIES": "1", "PROD_CANDIDATE_READY_INTERVAL": "0"}
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
        }
        for gate, extra in cases.items():
            r = self.ops("gate", "rel-1", gate, "8002", **extra)
            self.assertNotEqual(r.returncode, 0, gate)
        # schema/compat are bound to genuine staging evidence, not image labels (accepted BFF has none).
        ev = json.loads(self.epath.read_text())
        mutations = {
            "mig_hash": lambda d: d["gates"]["migration_and_compatibility"].update(migration_set_sha256="sha256:" + "0" * 64),
            "not_compat": lambda d: d["gates"]["migration_and_compatibility"].update(old_app_new_schema_compatible=False),
            "dry_run": lambda d: d.update(dry_run=True),
            "other_image": lambda d: d["manifest"].update(operator_bff_image="r/x@sha256:" + "9" * 64),
        }
        for name, mut in mutations.items():
            bad = json.loads(json.dumps(ev)); mut(bad)
            bp = self.tmp / f"bad_{name}.json"; bp.write_text(json.dumps(bad))
            for gate in ("schema", "old_new_compat"):
                self.assertNotEqual(self.ops("gate", "rel-1", gate, "8002", STAGING_EVIDENCE_PATH=str(bp)).returncode, 0, f"{gate}/{name}")
        self.assertNotEqual(self.ops("gate", "rel-1", "old_new_compat", "8002", FAKE_READY_PORTS="8002").returncode, 0)
        self.assertNotEqual(self.ops("gate", "rel-1", "old_new_compat", "8002", FAKE_READY_PORTS="8002").returncode, 0)
        self.assertIn("UNKNOWN_GATE", self.ops("gate", "rel-1", "bogus", "8002").stderr)

    def test_schema_gate_consumes_stage_contract_without_schema_migrations_table(self):
        # The product has no schema_migrations table: identity = Stage recipe over versioned SQL bytes,
        # live state = information_schema catalog. Nothing in the controller may query a phantom table.
        for gate in ("schema", "old_new_compat"):
            self.assertEqual(self.ops("gate", "rel-1", gate, "8002").returncode, 0, gate)
        code = [l for l in OPS.read_text().splitlines() if not l.lstrip().startswith("#")]
        self.assertFalse([l for l in code if "schema_migrations" in l])
        self.assertNotIn("schema_migrations", self.log.read_text())

    def test_schema_gates_fail_on_unmigrated_catalog_and_tampered_or_mismatched_sql(self):
        cat = self.state / "catalog"
        spec = self.migration_dir / "migration-spec.json"
        first = self.migration_dir / "migrations" / MIGRATION_FILES[0]["name"]
        good_sql = first.read_bytes()
        for gate in ("schema", "old_new_compat"):
            cat.write_text("{}")  # empty database: migrations never applied
            self.assertNotEqual(self.ops("gate", "rel-1", gate, "8002").returncode, 0, gate)
            partial = _live_catalog(); partial.pop("trade_journey_projection.controller")
            cat.write_text(json.dumps(partial))
            r = self.ops("gate", "rel-1", gate, "8002")
            self.assertNotEqual(r.returncode, 0, gate); self.assertIn("lacks relations", r.stderr)
            cat.unlink()  # database unreachable
            self.assertNotEqual(self.ops("gate", "rel-1", gate, "8002").returncode, 0, gate)
            cat.write_text(json.dumps(_live_catalog()))
            first.write_bytes(good_sql + b"\n-- tampered\n")  # bytes differ from qualified spec
            self.assertNotEqual(self.ops("gate", "rel-1", gate, "8002").returncode, 0, gate)
            first.write_bytes(good_sql)
            saved = spec.read_text()
            spec.write_text(json.dumps({**json.loads(saved), "migration_set_sha256": "sha256:" + "0" * 64}))
            self.assertNotEqual(self.ops("gate", "rel-1", gate, "8002").returncode, 0, gate)
            spec.write_text(saved)
            self.assertEqual(self.ops("gate", "rel-1", gate, "8002").returncode, 0, gate)
        # Missing migration material fails closed.
        spec.unlink()
        self.assertNotEqual(self.ops("gate", "rel-1", "schema", "8002").returncode, 0)

    def test_schema_gate_rejects_unusable_application_catalog_columns(self):
        # Counterexample: every relation exists but with only an id column. Names alone are not the contract.
        cat = self.state / "catalog"
        good = _live_catalog()
        bare = {rel: {"id": _col("text")} for rel in good}
        mutations = {"id_only": bare}
        no_cursor = json.loads(json.dumps(good)); no_cursor["public.telemetry_events"].pop("ingested_seq")
        mutations["missing_ingested_seq"] = no_cursor
        wrong_type = json.loads(json.dumps(good)); wrong_type["public.telemetry_events"]["ingested_at"]["type"] = "text"
        mutations["wrong_type"] = wrong_type
        no_default = json.loads(json.dumps(good)); no_default["public.telemetry_events"]["ingested_seq"]["has_default"] = False
        mutations["no_default"] = no_default
        proj = json.loads(json.dumps(good)); proj["trade_journey_projection.controller"].pop("deployment_sha")
        mutations["projection_column"] = proj
        for name, bad in mutations.items():
            cat.write_text(json.dumps(bad))
            for gate in ("schema", "old_new_compat"):
                r = self.ops("gate", "rel-1", gate, "8002")
                self.assertNotEqual(r.returncode, 0, (name, gate))
                self.assertIn("schema contract", r.stderr, (name, gate))
        cat.write_text(json.dumps(good))
        self.assertEqual(self.ops("gate", "rel-1", "schema", "8002").returncode, 0)

    def test_render_builds_manifest_equal_material_and_fails_closed_on_other_checkout(self):
        out = self.tmp / "rendered"
        r = subprocess.run([sys.executable, "-I", str(REPO_ROOT / "scripts/deploy/prod_preflight.py"), "migration", "render",
                            str(self.mpath), str(out), "--src", str(MIGRATIONS_SRC_DIR)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        spec = json.loads((out / "migration-spec.json").read_text())
        self.assertEqual(spec["migration_set_sha256"], MIGRATION_SET_SHA)
        other = self.tmp / "other-src"; other.mkdir()
        (other / "001_x.sql").write_text("CREATE TABLE IF NOT EXISTS x (id TEXT);")
        r = subprocess.run([sys.executable, "-I", str(REPO_ROOT / "scripts/deploy/prod_preflight.py"), "migration", "render",
                            str(self.mpath), str(self.tmp / "rendered2"), "--src", str(other)], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)

    def test_workflow_delivers_migration_material_and_runs_migrate_before_switch(self):
        wf = WORKFLOW.read_text()
        for needle in ("prod_preflight.py migration render", "scripts/db_migrate.sh", "migration-spec.json"):
            self.assertIn(needle, wf)
        self.assertLess(wf.index("prod_ops.sh\\\" migrate"), wf.index("prod_atomic_switch.sh\\\""))

    def test_migrate_applies_only_verified_versioned_sql_in_order(self):
        r = self.ops("migrate", "rel-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        applied = (self.state / "applied_sql").read_text()
        substrate = subprocess.run(["bash", str(REPO_ROOT / "scripts" / "db_migrate.sh"), "--print-sql"],
                                   capture_output=True, text=True, check=True).stdout
        self.assertIn("CREATE TABLE IF NOT EXISTS telemetry_events", substrate)
        want = (substrate + "\n--\n" if not substrate.endswith("\n") else substrate + "--\n")
        want += "--\n".join((MIGRATIONS_SRC_DIR / f["name"]).read_text() for f in MIGRATION_FILES) + "--\n"
        self.assertEqual(applied, want)
        self.assertTrue((self.root / "state" / "substrate-rel-1.sha256").is_file())
        # Missing base-schema initializer fails closed before any SQL is applied.
        (self.state / "applied_sql").unlink()
        (self.migration_dir / "db_migrate.sh").unlink()
        self.assertNotEqual(self.ops("migrate", "rel-1").returncode, 0)
        self.assertFalse((self.state / "applied_sql").exists())
        shutil.copy(REPO_ROOT / "scripts" / "db_migrate.sh", self.migration_dir / "db_migrate.sh")
        (self.migration_dir / "migrations" / MIGRATION_FILES[0]["name"]).write_bytes(b"SELECT 1;")
        (self.state / "applied_sql").unlink(missing_ok=True)
        self.assertNotEqual(self.ops("migrate", "rel-1").returncode, 0)
        self.assertFalse((self.state / "applied_sql").exists())

    def test_bootstrap_first_release_old_new_compat_passes_without_prior_app(self):
        # Counterexample regression: PROD_BOOTSTRAP_FIRST_RELEASE=true with only candidate 8002 ready
        # and no prior baseline must pass old_new_compat without inventing a healthy old app on 8001.
        r = self.ops("gate", "rel-1", "old_new_compat", "8002",
                     PROD_BOOTSTRAP_FIRST_RELEASE="true", FAKE_READY_PORTS="8002")
        self.assertEqual(r.returncode, 0, r.stderr)
        # If candidate 8002 itself is not ready, bootstrap old_new_compat still fails closed.
        r_not_ready = self.ops("gate", "rel-1", "old_new_compat", "8002",
                               PROD_BOOTSTRAP_FIRST_RELEASE="true", FAKE_READY_PORTS="")
        self.assertNotEqual(r_not_ready.returncode, 0)
        # If a retained baseline.json exists on disk, claiming bootstrap fails closed.
        (self.root / "state" / "baseline.json").write_text('{"healthy":true}')
        r_has_base = self.ops("gate", "rel-1", "old_new_compat", "8002",
                              PROD_BOOTSTRAP_FIRST_RELEASE="true", FAKE_READY_PORTS="8002")
        self.assertNotEqual(r_has_base.returncode, 0)
        self.assertIn("GATE_COMPAT", r_has_base.stderr)

    def test_tenant_gate_is_never_skipped_without_identity(self):
        self.token.unlink()
        self.assertNotEqual(self.ops("gate", "rel-1", "tenant", "8002").returncode, 0)

    def test_read_only_gate_rejects_unsafe_runtime_flags(self):
        (self.state / "container_env").write_text("PANTHEON_LIVE_BROKER_ENABLED=true\n")
        self.assertIn("GATE_READ_ONLY", self.ops("gate", "rel-1", "read_only", "8002").stderr)

    def test_singleton_handoff_keeps_exactly_one_owner_and_restores_baseline_image(self):
        cand_proj = self.manifest["backend"]["images"]["lifecycle_projector"]
        base_proj = self.manifest["rollback_baseline"]["backend_images"]["lifecycle_projector"]
        self.assertEqual(self.ops("activate_singletons", "rel-1").returncode, 0)
        self.assertEqual((self.state / "image_cid-lifecycle-projector-1").read_text(), cand_proj)
        self.assertEqual(self.ops("drain_singletons", "rel-1").returncode, 0)
        self.assertFalse((self.state / "singleton_up").exists())
        # resume_baseline_singletons must use retained baseline image, NEVER candidate release_env!
        self.assertEqual(self.ops("resume_baseline_singletons", "rel-1").returncode, 0)
        restored_proj = (self.state / "image_cid-lifecycle-projector-1").read_text()
        self.assertNotEqual(restored_proj, cand_proj)
        self.assertEqual(restored_proj, base_proj)
        dup = self.ops("activate_singletons", "rel-1", FAKE_SINGLETON_COUNT="2")
        self.assertNotEqual(dup.returncode, 0)
        self.assertIn("expected exactly 1", dup.stderr)

    def test_runtime_config_is_external_no_store_and_matches(self):
        (self.root / "fe" / "releases" / "r1").mkdir()
        os.symlink(self.root / "fe" / "releases" / "r1", self.root / "fe" / "current")
        self.assertEqual(self.ops("verify_runtime_config", "rel-1").returncode, 0)
        self.assertIn("RUNTIME_CONFIG_CACHEABLE", self.ops("verify_runtime_config", "rel-1", FAKE_RC_CACHE="public, max-age=60").stderr)
        (self.state / "served_runtime_config_override").write_text("tampered")
        self.assertIn("RUNTIME_CONFIG_DRIFT", self.ops("verify_runtime_config", "rel-1").stderr)
        (self.state / "served_runtime_config_override").unlink()
        self.rc.unlink()
        self.assertIn("RUNTIME_CONFIG_MISSING", self.ops("verify_runtime_config", "rel-1").stderr)

    def test_runtime_config_contract_consumes_config_ref_and_truthful_sha(self):
        (self.root / "fe" / "releases" / "r1").mkdir()
        os.symlink(self.root / "fe" / "releases" / "r1", self.root / "fe" / "current")

        def verify(text, want="rel-1"):
            self.rc.write_text(text)
            (self.state / "served_runtime_config").write_text(text)
            return self.ops("verify_runtime_config", want)

        self.assertEqual(verify(_public_runtime_config("rel-1")).returncode, 0)
        self.assertEqual(verify(_public_runtime_config("rel-1", sha=False)).returncode, 0)  # sha is optional
        for bad in (
            _public_runtime_config("rel-0"),                                   # configRef mismatch
            _public_runtime_config("rel-1", schemaVersion="legacy"),           # unsupported schema
            _public_runtime_config("rel-1", gcpIdentity={"apiKey": "k"}),      # incomplete identity
            _public_runtime_config("rel-1").replace("sha256:", "sha256:0"),    # untruthful sha
            'window.__PANTHEON_DEPLOYMENT_CONFIG__ = {"release_id":"rel-1","oidcIssuer":"https://x",'
            '"bffBaseUrl":"https://api.trading.acme-prod.net"};\n',            # legacy shape
        ):
            self.assertIn("RUNTIME_CONFIG_CONTRACT", verify(bad).stderr)

    def test_post_identity_reads_back_real_public_and_local_values_and_fails_closed(self):
        cur = self.root / "fe" / "releases" / "r1"
        cur.mkdir()
        fe_sha = "sha256:" + "1" * 64
        bff_img = self.manifest["backend"]["images"]["operator_bff"]
        (cur / ".artifact_sha256").write_text(fe_sha)
        (cur / "deployment.json").write_text(json.dumps({
            "release_id": self.manifest["release_id"],
            "frontend_commit": self.manifest["frontend"]["commit"],
            "frontend_artifact_sha256": fe_sha,
            "backend_commit": self.manifest["backend"]["commit"],
            "operator_bff_image": bff_img,
        }))
        os.symlink(cur, self.root / "fe" / "current")
        r = self.ops("post_identity", "rel-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual(got["frontend_artifact_sha256"], fe_sha)
        self.assertEqual(got["operator_bff_image"], bff_img)
        self.assertEqual(got["frontend_commit"], self.manifest["frontend"]["commit"])
        self.assertEqual(got["backend_commit"], self.manifest["backend"]["commit"])
        self.assertTrue(got["public_fe_deployment_verified"])
        self.assertTrue(got["public_bff_version_verified"])
        # Public FE /deployment.json cacheable or mismatched fails closed
        self.assertNotEqual(self.ops("post_identity", "rel-1", FAKE_FE_DEPLOYMENT_CACHE="public, max-age=60").returncode, 0)
        self.assertNotEqual(self.ops("post_identity", "rel-1", FAKE_FE_DEPLOYMENT_FAIL="true").returncode, 0)
        # Public BFF /bff/version mismatch or /bff/readyz failure fails closed
        wrong_ver = json.dumps({"source_commit_sha": "0" * 40, "image_digest": "sha256:" + "9" * 64})
        self.assertNotEqual(self.ops("post_identity", "rel-1", FAKE_PUBLIC_BFF_VERSION_JSON=wrong_ver).returncode, 0)
        self.assertNotEqual(self.ops("post_identity", "rel-1", FAKE_PUBLIC_BFF_READYZ_CODE="503").returncode, 0)
        (cur / ".artifact_sha256").unlink()
        self.assertNotEqual(self.ops("post_identity", "rel-1").returncode, 0)

    def test_prepull_images_pulls_and_inspects_only_digest_pinned_images(self):
        r = self.ops("prepull_images", "rel-1")
        self.assertEqual(r.returncode, 0, r.stderr)
        pulled = (self.state / "pulled_images").read_text().splitlines()
        self.assertIn(self.manifest["backend"]["images"]["operator_bff"], pulled)
        self.assertIn(self.manifest["backend"]["images"]["lifecycle_projector"], pulled)
        self.assertNotIn("docker build", self.log.read_text())
        self.assertIn("IMAGE_PULL_FAILED", self.ops("prepull_images", "rel-1", FAKE_DOCKER_FAIL="pull").stderr)

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
            f.write_text("PANTHEON_BFF_JWT_SECRET=s1\nPANTHEON_RUNTIME_JWT_SECRET=s2\nPANTHEON_DATABASE_URL=postgresql://u:p@127.0.0.1:5432/db\n")
            f.chmod(0o600)
        (self.tmp / "data").mkdir(exist_ok=True)
        return self.env(PROD_SECRETS_ENV_FILE=str(secrets), PROD_POSTGRES_PASSWORD_FILE=str(pw), **extra)

    def _run(self, **extra):
        return subprocess.run([str(BOOTSTRAP)], env=self._env(**extra), capture_output=True, text=True, timeout=60)

    def test_bootstrap_installs_503_placeholder_prepulls_digests_and_no_invented_release(self):
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(json.loads(r.stdout)["rollback_target"])
        self.assertIn("503", (self.root / "proxy" / "bff_upstream.caddy").read_text())
        self.assertFalse((self.root / "fe" / "current").exists())
        rc = (self.root / "config" / "runtime-config.js").read_text()
        self.assertIn("https://api.trading.acme-prod.net", rc)
        self.assertIn('"schemaVersion": "pantheon.fe.public-runtime-config.v1"', rc)
        self.assertIn('"configRef": "pending-first-release"', rc)
        self.assertNotIn("oidcIssuer", rc)
        self.assertIn("--profile stateful", self.log.read_text())
        pulled = (self.state / "pulled_images").read_text().splitlines()
        self.assertIn(self.manifest["backend"]["images"]["operator_bff"], pulled)
        self.assertIn(self.manifest["backend"]["images"]["lifecycle_projector"], pulled)

    def test_bootstrap_cold_cache_prepulls_effective_stateful_images_from_defaults_and_existing_compose_env(self):
        env_file = self.tmp / "compose.env"
        pg = "postgres:16-alpine@sha256:" + "1" * 64
        nats = "nats:2.10-alpine@sha256:" + "2" * 64
        # Fresh host, no shell variables: the compose.env defaults it writes must be the refs that get pulled.
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        written = env_file.read_text()
        pulled = (self.state / "pulled_images").read_text().splitlines()
        for line in written.splitlines():
            if line.startswith(("PANTHEON_POSTGRES_IMAGE=", "PANTHEON_NATS_IMAGE=")):
                self.assertIn(line.split("=", 1)[1], pulled)
        self.assertTrue(any(i.startswith("postgres:") for i in pulled) and any(i.startswith("nats:") for i in pulled))
        # Existing compose.env with operator-pinned refs and no shell variables: those exact refs are pulled.
        (self.state / "pulled_images").unlink()
        env_file.write_text(env_file.read_text() + f"PANTHEON_POSTGRES_IMAGE={pg}\nPANTHEON_NATS_IMAGE={nats}\n")
        r = self._run()
        self.assertEqual(r.returncode, 0, r.stderr)
        pulled = (self.state / "pulled_images").read_text().splitlines()
        self.assertIn(pg, pulled); self.assertIn(nats, pulled)
        # Existing compose.env lacking a stateful ref and no shell value fails closed before stateful up.
        env_file.write_text("".join(l + "\n" for l in env_file.read_text().splitlines() if not l.startswith("PANTHEON_NATS_IMAGE=")))
        r = self._run()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("COMPOSE_ENV", r.stderr)

    def test_bootstrap_rejects_unpinned_images_and_supports_oslogin_sudo(self):
        r_unpinned = self._run(PANTHEON_POSTGRES_IMAGE="postgres:16-alpine")
        self.assertNotEqual(r_unpinned.returncode, 0)
        self.assertIn("IMAGE_NOT_DIGEST_PINNED", r_unpinned.stderr)
        (self.tmp / "compose.env").unlink()  # the rejected run persisted the unpinned ref; a pinned host starts clean
        r_sudo = self._run(FAKE_OSLOGIN_NONROOT="true", PROD_REGISTRY_AUTH="true")
        self.assertEqual(r_sudo.returncode, 0, r_sudo.stderr)
        self.assertIn("sudo ", self.log.read_text())
        self.assertTrue((self.state / "docker_logins").exists())

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
        pw.chmod(0o600)
        pw.write_text("")
        self.assertIn("SECRET_MISSING", subprocess.run([str(BOOTSTRAP)], env=self.env(
            PROD_SECRETS_ENV_FILE=str(self.tmp / "secrets.env"), PROD_POSTGRES_PASSWORD_FILE=str(pw)),
            capture_output=True, text=True).stderr)
        # Forbidden live broker secret on control VM is rejected
        sec = self.tmp / "secrets.env"
        sec.write_text("BROKER_API_KEY=live-secret\n")
        pw.write_text("validpw\n")
        self.assertIn("SECRET_CONTENT_INVALID", subprocess.run([str(BOOTSTRAP)], env=self.env(
            PROD_SECRETS_ENV_FILE=str(sec), PROD_POSTGRES_PASSWORD_FILE=str(pw)),
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
    "PROD_PUBLIC_GCP_IDENTITY_API_KEY": "public-web-key-fixture",
    "PROD_PUBLIC_GCP_IDENTITY_PROJECT_ID": "acme-prod-identity",
    "PROD_PUBLIC_GCP_IDENTITY_AUTH_DOMAIN": "acme-prod-identity.firebaseapp.com",
    "PROD_RPO_SECONDS": "300",
    "PROD_RTO_SECONDS": "1800",
    "PROD_RETENTION_DAYS": "30",
    "PROD_ONCALL_OWNER": "oncall-team",
    "PROD_ROLLBACK_OWNER": "release-owner",
}


def _evidence(manifest: dict, sha: str) -> dict:
    return {
        "status": "staging_verified",
        "dry_run": False,
        "observed_from_live_vm": True,
        "release_id": manifest["release_id"],
        "workflow_run_id": "123456",
        "manifest": {
            "manifest_sha256": sha,
            "operator_bff_image": manifest["backend"]["images"]["operator_bff"],
            "frontend_artifact_sha256": manifest["frontend"]["artifact_sha256"],
        },
        "gates": {**{k: {} for k in (
            "sanitized_data", "backup_restore_readability",
            "exact_pair_rollback", "security_and_safety_readbacks", "cleanup_dry_run")},
            "migration_and_compatibility": {"migration_verified": True, "old_app_new_schema_compatible": True,
                                            "migration_set_sha256": manifest["migration_set_sha256"]}},
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
        for key, val in (("dry_run", True), ("observed_from_live_vm", False)):
            bad = _evidence(manifest, sha)
            bad[key] = val
            with self.assertRaises(prod_preflight.PreflightError, msg=key):
                prod_preflight.check_staging_evidence(bad, manifest, sha)
        bad = _evidence(manifest, sha)
        del bad["gates"]["exact_pair_rollback"]
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_staging_evidence(bad, manifest, sha)
        bad = _evidence(manifest, sha)
        bad["gates"]["migration_and_compatibility"]["migration_set_sha256"] = "sha256:" + "0" * 64
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
        # Unmodified staging manifest (carries rollback_baseline artifact metadata) is consumable by
        # first-release bootstrap; the reference is never treated as an incumbent.
        self.assertEqual(prod_preflight.check_baseline(manifest, bootstrap=True, baseline_state=None), "bootstrap")
        with self.assertRaises(prod_preflight.PreflightError):
            prod_preflight.check_baseline({**manifest, "rollback_baseline": "x"}, bootstrap=True, baseline_state=None)


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

    def _prepare(self, *, bootstrap=False, mutate_manifest=None, keep_rollback_artifact=False):
        manifest = _sample_manifest(state="staging_verified")
        if bootstrap and not keep_rollback_artifact:
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
            "release_id": manifest["release_id"],
            "frontend_commit": manifest["frontend"]["commit"],
            "frontend_artifact_sha256": fe_sha,
            "backend_commit": manifest["backend"]["commit"],
            "operator_bff_image": manifest["backend"]["images"]["operator_bff"],
            "public_fe_deployment_verified": True,
            "public_bff_version_verified": True,
        }))
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
                         < steps.index("activate_singletons") < steps.index("reload_proxy") < steps.index("post_identity"), True)
        self.assertEqual(steps.count("gate"), 8)
        state = json.loads((self.root / "state" / f"{rid}.json").read_text())
        self.assertEqual(state["release_state"], "prod_active")
        self.assertNotIn("release_complete", json.dumps(state))
        # Immutable FE archive and manifest are retained on disk
        self.assertTrue((self.root / "fe" / "artifacts" / f"{rid}.tar.gz").is_file())
        self.assertTrue((self.root / "fe" / "artifacts" / f"{rid}.tar.gz.sha256").is_file())
        self.assertTrue((self.root / "manifests" / f"{rid}.json").is_file())

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
        self.assertIn("drain_singletons", self._steps())
        self.assertIn("stop_green", self._steps())
        self.assertIn("reload_proxy", self._steps())
        self.assertFalse((self.root / "fe" / "current").exists())
        self.assertIn("503", self._upstream())
        state = json.loads((self.root / "state" / f"{self.manifest['release_id']}.json").read_text())
        self.assertEqual(state["release_state"], "prod_aborted")

    def test_bootstrap_consumes_unmodified_staging_manifest_without_switching_to_artifact_baseline(self):
        # Staging requires rollback_baseline for promotion; first-release must accept it as artifact
        # metadata only, never as a healthy incumbent or rollback target.
        manifest = self._prepare(bootstrap=True, keep_rollback_artifact=True)
        self.assertIn("rollback_baseline", manifest)
        r = self._run(bootstrap=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("baseline_health", self._steps())
        self.assertNotIn("resume_baseline_singletons", self._steps())
        self.setUp()
        self._prepare(bootstrap=True, keep_rollback_artifact=True)
        r = self._run(bootstrap=True, extra_env={"IDENTITY_OVERRIDE": "{}"})
        self.assertEqual(r.returncode, 4)
        self.assertNotIn("resume_baseline_singletons", self._steps())
        self.assertFalse((self.root / "fe" / "current").exists())
        self.assertIn("503", self._upstream())
        # Without bootstrap declaration or with a real incumbent the same manifest is still governed.
        self.setUp()
        self._prepare(bootstrap=True, keep_rollback_artifact=True)
        self.assertEqual(self._run(bootstrap=False).returncode, 1)

    def test_first_release_without_bootstrap_declaration_refused(self):
        self._prepare(bootstrap=True)
        r = self._run(bootstrap=False)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(self._steps(), [])

    def test_port_collision_refused(self):
        self._prepare()
        r = self._run(extra_env={"PROD_GREEN_PORT": "8001"})
        self.assertIn("PORT_COLLISION", r.stderr)


class RealControllerAndOpsIntegrationTests(OpsHarness):
    """Executes the REAL bootstrap.sh, prod_atomic_switch.sh, prod_rollback.sh, and prod_ops.sh
    together under constrained host binaries (no FAKE_OPS shortcut)."""

    def _prepare_release(self, *, release_id: str, fe_marker: str, fe_commit: str, bff_commit: str,
                         digest_char: str, bootstrap: bool = False, rollback_baseline: dict | None = None):
        manifest = _sample_manifest(release_id=release_id, state="staging_verified")
        artifact = self.tmp / f"fe-{release_id}.tar.gz"
        fe_sha = _fe_tarball(artifact, fe_marker)
        manifest["frontend"]["commit"] = fe_commit
        manifest["frontend"]["artifact_sha256"] = fe_sha
        manifest["backend"]["commit"] = bff_commit
        manifest["dev_verification"]["frontend_commit"] = fe_commit
        manifest["dev_verification"]["bff_version_commit"] = bff_commit
        for owner in ("operator_bff", "runtime_manager", "governance", "deployment", "lifecycle_projector"):
            manifest["backend"]["images"][owner] = (
                f"asia-east1-docker.pkg.dev/pantheon-prod-20260902/pantheon/{owner}@sha256:" + digest_char * 64
            )
        if bootstrap:
            manifest.pop("rollback_baseline", None)
        elif rollback_baseline is not None:
            manifest["rollback_baseline"] = rollback_baseline
        sha = release_manifest.compute_manifest_sha256(manifest)
        mpath = self.tmp / f"manifest-{release_id}.json"
        mpath.write_text(json.dumps(manifest))
        epath = self.tmp / f"evidence-{release_id}.json"
        epath.write_text(json.dumps(_evidence(manifest, sha)))
        apath = self.tmp / "approvals.json"
        apath.write_text(json.dumps(APPROVALS))
        self.snapshot(release_id=release_id)
        (self.root / "state" / "snapshots" / f"{release_id}.json").write_text(json.dumps({
            "release_id": release_id,
            "status": "READY",
            "disk": PROD_ENV["PROD_DATA_DISK"],
            "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }))
        return manifest, sha, artifact, mpath, epath, apath

    def _run_bootstrap(self, mpath: Path):
        secrets = self.tmp / "secrets.env"
        pw = self.tmp / "pgpw"
        secrets.write_text("PANTHEON_BFF_JWT_SECRET=s1\nPANTHEON_RUNTIME_JWT_SECRET=s2\nPANTHEON_DATABASE_URL=postgresql://u:p@127.0.0.1:5432/db\n")
        pw.write_text("prod-db-pw\n")
        secrets.chmod(0o600)
        pw.chmod(0o600)
        (self.tmp / "data").mkdir(exist_ok=True)
        env = self.env(
            PROD_SECRETS_ENV_FILE=str(secrets),
            PROD_POSTGRES_PASSWORD_FILE=str(pw),
            RELEASE_MANIFEST_PATH=str(mpath),
        )
        return subprocess.run([str(BOOTSTRAP)], env=env, capture_output=True, text=True, timeout=60)

    def _run_switch(self, manifest: dict, sha: str, artifact: Path, mpath: Path, epath: Path, apath: Path,
                    *, bootstrap: bool = False, **extra_env):
        env = self.env(
            PROD_OPS_CMD=str(OPS),
            RELEASE_ID=manifest["release_id"],
            RELEASE_MANIFEST_PATH=str(mpath),
            EXPECTED_MANIFEST_SHA256=sha,
            STAGING_EVIDENCE_PATH=str(epath),
            APPROVALS_PATH=str(apath),
            WORKFLOW_INITIATOR="initiator-a",
            FE_ARTIFACT_PATH=str(artifact),
            PROD_BOOTSTRAP_FIRST_RELEASE="true" if bootstrap else "false",
            **extra_env,
        )
        return subprocess.run([str(SWITCH)], env=env, capture_output=True, text=True, timeout=60)

    def test_real_first_release_bootstrap_and_switch_with_only_green_ready(self):
        m1, sha1, art1, mpath1, epath1, apath1 = self._prepare_release(
            release_id="pantheon-20261025-001",
            fe_marker="fe-v1",
            fe_commit="1" * 40,
            bff_commit="2" * 40,
            digest_char="1",
            bootstrap=True,
        )
        boot = self._run_bootstrap(mpath1)
        self.assertEqual(boot.returncode, 0, boot.stderr)
        self.assertIn("503", (self.root / "proxy" / "bff_upstream.caddy").read_text())
        self.assertFalse((self.root / "fe" / "current").exists())

        # Only candidate port 8002 is ready; no old 8001 exists on first release!
        sw = self._run_switch(m1, sha1, art1, mpath1, epath1, apath1, bootstrap=True, FAKE_READY_PORTS="8002")
        self.assertEqual(sw.returncode, 0, sw.stderr)
        self.assertIn("127.0.0.1:8002", (self.root / "proxy" / "bff_upstream.caddy").read_text())
        self.assertTrue(os.readlink(self.root / "fe" / "current").endswith("/pantheon-20261025-001"))
        self.assertTrue((self.root / " state").exists() or (self.root / "state" / "baseline.json").is_file())
        baseline = json.loads((self.root / "state" / "baseline.json").read_text())
        self.assertEqual(baseline["release_id"], "pantheon-20261025-001")
        self.assertEqual(baseline["active_port"], "8002")
        self.assertEqual((self.state / "image_cid-lifecycle-projector-1").read_text(),
                         m1["backend"]["images"]["lifecycle_projector"])

    def test_real_bootstrap_abort_after_partial_switch_stops_candidate_and_restores_503(self):
        m1, sha1, art1, mpath1, epath1, apath1 = self._prepare_release(
            release_id="pantheon-20261025-001",
            fe_marker="fe-v1",
            fe_commit="1" * 40,
            bff_commit="2" * 40,
            digest_char="1",
            bootstrap=True,
        )
        self.assertEqual(self._run_bootstrap(mpath1).returncode, 0)
        # Inject failure at post_identity (after singletons_drained, bff_switched, activate_singletons, fe_switched)
        sw = self._run_switch(
            m1, sha1, art1, mpath1, epath1, apath1,
            bootstrap=True,
            FAKE_READY_PORTS="8002",
            FAKE_PUBLIC_BFF_VERSION_JSON=json.dumps({"source_commit_sha": "f" * 40, "image_digest": "sha256:" + "f" * 64}),
        )
        self.assertEqual(sw.returncode, 4, sw.stderr)
        self.assertIn("no_rollback_target", sw.stdout)
        # Candidate and singleton must be stopped; FE symlink removed; proxy restored to 503
        self.assertFalse((self.state / "singleton_up").exists())
        self.assertTrue((self.state / "stopped_operator-bff-green").exists())
        self.assertFalse((self.root / "fe" / "current").exists())
        self.assertIn("503", (self.root / "proxy" / "bff_upstream.caddy").read_text())
        state = json.loads((self.root / "state" / "pantheon-20261025-001.json").read_text())
        self.assertEqual(state["release_state"], "prod_aborted")

    def test_real_two_release_rollback_restores_exact_baseline_config_and_singleton_owner(self):
        # 1. Establish release 1 on port 8002 via bootstrap
        m1, sha1, art1, mpath1, epath1, apath1 = self._prepare_release(
            release_id="pantheon-20261025-001",
            fe_marker="fe-v1",
            fe_commit="1" * 40,
            bff_commit="2" * 40,
            digest_char="1",
            bootstrap=True,
        )
        self.assertEqual(self._run_bootstrap(mpath1).returncode, 0)
        sw1 = self._run_switch(m1, sha1, art1, mpath1, epath1, apath1, bootstrap=True, FAKE_READY_PORTS="8002")
        self.assertEqual(sw1.returncode, 0, sw1.stderr)
        r1_rc = (self.root / "config" / "runtime-config.js").read_text()

        # 2. Prepare release 2 (targets alternate port 8001) with rollback_baseline pointing to release 1
        rb = {
            "previous_release_id": "pantheon-20261025-001",
            "frontend_commit": m1["frontend"]["commit"],
            "frontend_artifact_sha256": m1["frontend"]["artifact_sha256"],
            "backend_commit": m1["backend"]["commit"],
            "backend_operator_bff_image": m1["backend"]["images"]["operator_bff"],
            "backend_images": m1["backend"]["images"],
            "config_schema_version": m1["config_schema_version"],
        }
        m2, sha2, art2, mpath2, epath2, apath2 = self._prepare_release(
            release_id="pantheon-20261025-002",
            fe_marker="fe-v2",
            fe_commit="3" * 40,
            bff_commit="4" * 40,
            digest_char="2",
            bootstrap=False,
            rollback_baseline=rb,
        )
        # Corrupt only port 8001's public /bff/version after start_green by overriding served_deployment_json
        # so switch fails at post_identity AFTER activating candidate singletons and switching FE/BFF.
        (self.state / "served_deployment_json").write_text(json.dumps({
            "release_id": "pantheon-20261025-002",
            "frontend_commit": "f" * 40,  # mismatch triggers post_identity failure -> rollback!
            "frontend_artifact_sha256": m2["frontend"]["artifact_sha256"],
            "backend_commit": m2["backend"]["commit"],
            "operator_bff_image": m2["backend"]["images"]["operator_bff"],
        }))
        # Wait: during rollback, baseline_health also checks public /deployment.json, so let's fail
        # post_identity via a one-shot curl failure on /bff/version instead, or let's test verify_runtime_config failure!
        (self.state / "served_deployment_json").unlink()
        (self.state / "served_runtime_config_override").write_text("tampered-during-switch")
        # Wait: if served_runtime_config_override is set, baseline_health during rollback would also see it unless
        # we inject failure in post_identity when port 8001 is active!
        (self.state / "served_runtime_config_override").unlink()
        # Let's make port 8001's bff_version report a mismatched commit by overriding bff_version_8001 after start_green?
        # Even simpler: in FAKE_BIN, if FAKE_SINGLETON_IMAGE_OVERRIDE is set during activate_singletons? No, let's check:
        # When bff_upstream.caddy routes to 8001 (during post_identity), curl /bff/version reads bff_version_8001!
        # When rollback restores bff_upstream.caddy to 8002, curl /bff/version reads bff_version_8002!
        # So if we corrupt bff_version_8001 (or pass wrong backend_commit in m2's container_env), post_identity on 8001 fails,
        # while rollback to 8002 reads bff_version_8002 (which has release 1's valid version) and succeeds!
        # Let's trigger that by setting FAKE_FE_DEPLOYMENT_FILE only while fe/current points to pantheon-20261025-002:
        # Actually, if we pre-create fe/releases/pantheon-20261025-002 with a tampered deployment.json (and matching .artifact_sha256),
        # Step 5 keeps the existing dir, Step 9 switches fe/current to it, Step 10 post_identity reads fe/current/deployment.json,
        # sees the wrong frontend_commit and fails! Then rollback switches fe/current back to pantheon-20261025-001 (whose deployment.json is valid)!
        rel2_dir = self.root / "fe" / "releases" / "pantheon-20261025-002"
        rel2_dir.mkdir(parents=True)
        (rel2_dir / ".artifact_sha256").write_text(m2["frontend"]["artifact_sha256"])
        (rel2_dir / "deployment.json").write_text(json.dumps({
            "release_id": "pantheon-20261025-002",
            "frontend_commit": "deadbeef" * 5,  # mismatch vs m2["frontend"]["commit"]
            "frontend_artifact_sha256": m2["frontend"]["artifact_sha256"],
            "backend_commit": m2["backend"]["commit"],
            "operator_bff_image": m2["backend"]["images"]["operator_bff"],
        }))

        sw2 = self._run_switch(m2, sha2, art2, mpath2, epath2, apath2, bootstrap=False, FAKE_READY_PORTS="8001 8002")
        self.assertEqual(sw2.returncode, 2, f"stdout={sw2.stdout}\nstderr={sw2.stderr}")
        # Verify FE symlink, BFF upstream, runtime-config, active_manifest, and singleton image all restored to release 1!
        self.assertTrue(os.readlink(self.root / "fe" / "current").endswith("/pantheon-20261025-001"))
        self.assertIn("127.0.0.1:8002", (self.root / "proxy" / "bff_upstream.caddy").read_text())
        self.assertEqual((self.root / "config" / "runtime-config.js").read_text(), r1_rc)
        active_m = json.loads((self.root / "state" / "active_manifest.json").read_text())
        self.assertEqual(active_m["release_id"], "pantheon-20261025-001")
        # Singleton must be running release 1's baseline projector image, NOT release 2's candidate image!
        self.assertTrue((self.state / "singleton_up").exists())
        self.assertEqual((self.state / "image_cid-lifecycle-projector-1").read_text(),
                         m1["backend"]["images"]["lifecycle_projector"])
        self.assertEqual((self.state / "singleton_release_id").read_text(), "pantheon-20261025-001")


    def test_real_readiness_contract_first_release_and_changed_backend_sha_handoff(self):
        # FAKE_REAL_READYZ models main.py::_lifecycle_projector_dependency: readyz is 200 only when the
        # singleton controller deployment_sha == the BFF's BFF_COMMIT. No unconditional 200s.
        m1, sha1, art1, mpath1, epath1, apath1 = self._prepare_release(
            release_id="pantheon-20261025-001", fe_marker="fe-v1", fe_commit="1" * 40,
            bff_commit="a" * 40, digest_char="1", bootstrap=True)
        boot = self._run_bootstrap(mpath1)
        self.assertEqual(boot.returncode, 0, boot.stderr)
        env_seen = dict(l.split("=", 1) for l in (self.state / "stateful_env").read_text().splitlines())
        self.assertEqual(env_seen["PANTHEON_RELEASE_ID"], "pantheon-20261025-001")
        self.assertEqual(env_seen["PANTHEON_BACKEND_COMMIT"], "a" * 40)
        self.assertEqual(env_seen["PANTHEON_LIFECYCLE_PROJECTOR_IMAGE"], m1["backend"]["images"]["lifecycle_projector"])
        sw1 = self._run_switch(m1, sha1, art1, mpath1, epath1, apath1, bootstrap=True, FAKE_REAL_READYZ="1")
        self.assertEqual(sw1.returncode, 0, sw1.stderr)
        self.assertEqual((self.state / "singleton_commit").read_text(), "a" * 40)

        rb = {"previous_release_id": "pantheon-20261025-001", "frontend_commit": m1["frontend"]["commit"],
              "frontend_artifact_sha256": m1["frontend"]["artifact_sha256"], "backend_commit": m1["backend"]["commit"],
              "backend_operator_bff_image": m1["backend"]["images"]["operator_bff"],
              "backend_images": m1["backend"]["images"], "config_schema_version": m1["config_schema_version"]}
        m2, sha2, art2, mpath2, epath2, apath2 = self._prepare_release(
            release_id="pantheon-20261025-002", fe_marker="fe-v2", fe_commit="3" * 40,
            bff_commit="b" * 40, digest_char="2", rollback_baseline=rb)
        sw2 = self._run_switch(m2, sha2, art2, mpath2, epath2, apath2, FAKE_REAL_READYZ="1")
        self.assertEqual(sw2.returncode, 0, f"stdout={sw2.stdout}\nstderr={sw2.stderr}")
        self.assertEqual((self.state / "singleton_commit").read_text(), "b" * 40)
        self.assertEqual((self.state / "singleton_release_id").read_text(), "pantheon-20261025-002")

    def test_candidate_never_ready_after_handoff_rolls_back_to_baseline_singleton(self):
        m1, sha1, art1, mpath1, epath1, apath1 = self._prepare_release(
            release_id="pantheon-20261025-001", fe_marker="fe-v1", fe_commit="1" * 40,
            bff_commit="a" * 40, digest_char="1", bootstrap=True)
        self.assertEqual(self._run_bootstrap(mpath1).returncode, 0)
        self.assertEqual(self._run_switch(m1, sha1, art1, mpath1, epath1, apath1, bootstrap=True,
                                          FAKE_REAL_READYZ="1").returncode, 0)
        rb = {"previous_release_id": "pantheon-20261025-001", "frontend_commit": m1["frontend"]["commit"],
              "frontend_artifact_sha256": m1["frontend"]["artifact_sha256"], "backend_commit": m1["backend"]["commit"],
              "backend_operator_bff_image": m1["backend"]["images"]["operator_bff"],
              "backend_images": m1["backend"]["images"], "config_schema_version": m1["config_schema_version"]}
        m2, sha2, art2, mpath2, epath2, apath2 = self._prepare_release(
            release_id="pantheon-20261025-002", fe_marker="fe-v2", fe_commit="3" * 40,
            bff_commit="b" * 40, digest_char="2", rollback_baseline=rb)
        # Projector fails to register the candidate sha (stale controller): candidate readyz stays 503.
        sw2 = self._run_switch(m2, sha2, art2, mpath2, epath2, apath2, FAKE_REAL_READYZ="1",
                               FAKE_SINGLETON_COMMIT_OVERRIDE="c" * 40,
                               FAKE_SINGLETON_COMMIT_OVERRIDE_RELEASE="pantheon-20261025-002",
                               PROD_CANDIDATE_READY_TRIES="1", PROD_CANDIDATE_READY_INTERVAL="0",
                               PROD_BASELINE_READY_TRIES="1", PROD_BASELINE_READY_INTERVAL="0")
        self.assertEqual(sw2.returncode, 2, f"stdout={sw2.stdout}\nstderr={sw2.stderr}")
        self.assertIn("127.0.0.1:8002", (self.root / "proxy" / "bff_upstream.caddy").read_text())
        self.assertTrue((self.state / "singleton_up").exists())
        self.assertEqual((self.state / "singleton_release_id").read_text(), "pantheon-20261025-001")
        self.assertEqual((self.state / "singleton_commit").read_text(), "a" * 40)


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
            self.assertEqual(s["ports"][0].rsplit(":", 1)[1], "8001")  # accepted native BFF port
            self.assertEqual(s["environment"]["PANTHEON_RUNTIME_MANAGER_URL"], "http://runtime-manager:8080")
            self.assertEqual(s["environment"]["PANTHEON_GOVERNANCE_API_URL"], "http://governance:8082")
            self.assertEqual(s["environment"]["PANTHEON_DEPLOYMENT_API_URL"], "http://deployment:8095")
            self.assertIn("BFF_COMMIT", s["environment"])
        proj = svc["lifecycle-projector"]
        self.assertEqual(proj["command"], ["python", "-m", "services.trade_journey.lifecycle_projector", "run"])
        self.assertIn("GIT_SHA", proj["environment"])
        self.assertEqual(proj["environment"]["LIFECYCLE_PROJECTOR_WRITER_BACKEND"], "postgres")
        self.assertNotEqual(svc["operator-bff-blue"]["ports"], svc["operator-bff-green"]["ports"])

    def test_workflow_forwards_public_identity_to_host_bootstrap_and_switch(self):
        wf = yaml.safe_load(WORKFLOW.read_text())
        text = WORKFLOW.read_text()
        for name in ("API_KEY", "PROJECT_ID", "AUTH_DOMAIN", "ISSUER", "AUDIENCE"):
            key = f"PROD_PUBLIC_GCP_IDENTITY_{name}"
            self.assertIn(key, wf["jobs"]["deploy"]["env"])
            self.assertIn(f"{key} ", text.split("for v in RELEASE_ID", 1)[1].split("; do", 1)[0] + " ")
        self.assertIn("PROD_REQUIRE_FULL_SECRET_KEYS=true", text)

    def test_bootstrap_renders_public_runtime_schema_and_validates_isolated_dsns(self):
        text = BOOTSTRAP.read_text()
        self.assertIn("pantheon.fe.public-runtime-config.v1", text)
        self.assertNotIn("oidcIssuer", text)
        self.assertIn("TELEMETRY_DB_DSN", text)
        self.assertIn("LIFECYCLE_PROJECTOR_PROJECTION_DSN", text)

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
                       "staging_evidence.json", "approvals.json", "env-policy.json", "prod_ops.sh",
                       "bootstrap.sh", "prepull_images"):
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

    def test_application_stores_are_durable_shared_across_colors_and_on_the_data_disk(self):
        # Acknowledged native-store state must live on the retained data disk (never container /tmp) and be
        # identical for blue, green, replacement and rollback; Stage's selected store paths are mirrored.
        svc = yaml.safe_load(COMPOSE.read_text())["services"]
        disk = "${PROD_DATA_MOUNT:?production data disk mount required}"
        want = {
            "runtime-manager": {"/data/runtime": "runtime"},
            "governance": {"/data/governance": "governance"},
            "deployment": {"/data/governance": "governance", "/data/runtime": "runtime"},
            "lifecycle-projector": {"/data/bff": "bff"},
            "operator-bff-blue": {"/data/bff": "bff", "/data/governance": "governance", "/data/runtime": "runtime"},
            "operator-bff-green": {"/data/bff": "bff", "/data/governance": "governance", "/data/runtime": "runtime"},
        }
        for name, mounts in want.items():
            got = {}
            for v in svc[name]["volumes"]:
                src, _, rest = v.partition(":/data/")
                got["/data/" + rest.split(":")[0]] = src
            for target, sub in mounts.items():
                self.assertEqual(got.get(target), f"{disk}/{sub}", f"{name} {target}")
        env = lambda n: svc[n]["environment"]
        self.assertEqual(env("runtime-manager")["PANTHEON_RUNTIME_BINDING_STORE_PATH"], "/data/runtime/runtime_bindings.json")
        self.assertEqual(env("runtime-manager")["PANTHEON_COMMAND_STATE_FILE"], "/data/runtime/internal_api_commands.json")
        self.assertEqual(env("governance")["GOVERNANCE_DATA_DIR"], "/data/governance")
        self.assertEqual(env("deployment")["DEPLOYMENT_DATA_DIR"], "/data/governance")
        for n in ("lifecycle-projector", "operator-bff-blue", "operator-bff-green"):
            self.assertEqual(env(n)["BFF_DATA_DIR"], "/data/bff")
        self.assertEqual(svc["operator-bff-blue"]["volumes"], svc["operator-bff-green"]["volumes"])
        self.assertEqual(env("operator-bff-blue"), env("operator-bff-green"))
        # Bootstrap creates the owner store dirs next to postgres/nats on the data disk.
        boot = (REPO_ROOT / "deploy" / "production" / "bootstrap.sh").read_text()
        for d in ("bff", "runtime", "governance"):
            self.assertIn(f'"${{PROD_DATA_MOUNT}}/{d}"', boot)

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

    def test_runbook_is_compact_and_honest_about_external_wait(self):
        rb = REPO_ROOT / "docs" / "deployment" / "production-control-runbook.md"
        text = rb.read_text()
        self.assertLessEqual(len(text.splitlines()), 180)
        self.assertIn("external_wait", text)
        self.assertIn("release_complete", text)
        self.assertIn("prod_ops.sh", text)
        self.assertIn("bootstrap.sh", text)
        self.assertIn("lifecycle-projector", text)


if __name__ == "__main__":
    unittest.main()

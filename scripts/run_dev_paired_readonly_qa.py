#!/usr/bin/env python3
"""Protected caller for the ORIGINAL paired Persona/strategy read-only QA.

Runs AFTER the existing dev Root has accepted the exact FE/BFF pair.  It is a
supplementary measurement, never a release gate: it records the raw CLI
outcome and exits non-zero for anything that is not a proven PASS, and the
workflow step that calls it must not rewrite that outcome.

Contract with the frontend CLI (``accept-persona-strategy-hosted-readonly.mjs``):
one JSON document on stdin, ``--out-dir`` as the only argument.  No other
schema is parsed and there is no compatibility fallback; a missing CLI or a
schema mismatch is reported as ``unproven``.

Secrets (the four dev-login client secrets; the caller itself performs no login) are read from the process
environment supplied by the protected workflow step, kept in RAM, and written
only to the CLI's stdin.  They never enter argv, evidence, or logs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "pantheon.dev-original-paired-readonly-qa-caller.v1"
CLI_RELATIVE = "scripts/accept-persona-strategy-hosted-readonly.mjs"
PROFILES = ("TEST_ADMIN", "OPERATOR_A", "OPERATOR_B", "VIEWER")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
HOST_SUFFIX = ".mvl-cap.tw"
BASELINE_PATH = re.compile(
    r"^/home/[A-Za-z0-9_.-]+/pantheon-acceptance-captures/[A-Za-z0-9_./-]+\.json$"
)
# Owner inventory is read from the source-ingest owner service on the dev VM
# over the existing pinned SSH transport with two FIXED read-only GETs.  The
# bearer (legitimate dev-login, RAM only) and tenant header travel on the SSH
# stdin as a curl header file (``-H @-``): never in local or remote argv.  It
# never derives expectations from the BFF projection under test.
_CURL = "curl -sS --max-time 30 -H @- -w '\\n%{http_code}' "
OWNER_READS = {
    "sources": _CURL + "http://127.0.0.1:18097/api/source-ingest/management/sources",
    "registry": _CURL + "http://127.0.0.1:18097/api/source-ingest/registry",
}
OWNER_PROFILE = "OPERATOR_A"
# Complete private witnesses are kept on the dev VM under the EXISTING private
# acceptance-capture tree through the same pinned SSH transport.
RETAIN_ROOT = "$HOME/pantheon-acceptance-captures/DEV-ORIGINAL-PAIRED-READONLY-QA"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
TRANSPORT_ENV_KEYS = (
    "PATH", "HOME", "RUNNER_TEMP", "GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "REMOTE_USER",
    "DEV_DEPLOY_SSH_HOST", "DEV_DEPLOY_SSH_USER", "DEV_DEPLOY_SSH_PORT", "DEV_DEPLOY_SSH_TIMEOUT",
    "DEV_DEPLOY_SSH_KEY_FILE", "DEV_DEPLOY_SSH_KNOWN_HOSTS_FILE",
)
READY_TIMEOUT_SECONDS = 120
RESULT_EMBED_MAX_BYTES = 2_000_000
DIFF_POINTER_CAP = 500
READY_SCRIPT = (
    "for (const p of ['playwright','@playwright/test']) {"
    " try { const m = await import(p);"
    " if (m.chromium && require_fs(m.chromium.executablePath())) process.exit(0); } catch {} }"
    " process.exit(3);"
).replace("require_fs(", "(await import('node:fs')).existsSync(")
CLI_TIMEOUT_SECONDS = 3600

Http = Callable[[str, str, Mapping[str, str], "bytes | None", float], "tuple[int, Any]"]
CliRunner = Callable[..., "tuple[int, str, str]"]  # (argv, stdin, timeout, cwd=None)
BaselineFetcher = Callable[[str, Path], None]
OwnerReader = Callable[[str, "str | None"], "tuple[int, Any]"]  # (key, header text|None) -> (status, JSON)
Retainer = Callable[[str, str, bytes], str]  # (run dir name, file name, bytes) -> remote sha256 readback


class Unproven(Exception):
    """The measurement could not be performed; never convertible to PASS."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check_origin(label: str, url: str, canonical: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or not parsed.hostname.endswith(HOST_SUFFIX)
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.port not in (None, 443)
    ):
        raise Unproven("wrong_origin", f"{label} is not a canonical https {HOST_SUFFIX} origin")
    origin = f"https://{parsed.hostname}"
    if canonical and origin != canonical.rstrip("/"):
        raise Unproven("wrong_origin", f"{label} differs from the canonical origin")
    return origin


def default_http(
    url: str, method: str, headers: Mapping[str, str], body: "bytes | None", timeout: float
) -> "tuple[int, Any]":
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        response = urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 (https checked)
        status, raw = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
    try:
        return status, json.loads(raw.decode("utf-8")) if raw else {}
    except ValueError:
        return status, {}


def default_cli_runner(
    argv: Sequence[str], stdin: bytes, timeout: float, cwd: "str | None" = None
) -> "tuple[int, str, str]":
    # Minimal environment: nothing from the workflow (secrets included) leaks.
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": os.environ.get("HOME", "/tmp")}
    try:
        proc = subprocess.run(  # noqa: S603
            list(argv), input=stdin, capture_output=True, timeout=timeout, env=env, check=False, cwd=cwd
        )
    except subprocess.TimeoutExpired:
        return 124, "", "cli timed out"
    except OSError as exc:
        return 127, "", f"cli could not be started: {type(exc).__name__}"
    return proc.returncode, proc.stdout.decode("utf-8", "replace"), proc.stderr.decode("utf-8", "replace")


def _transport_env() -> "dict[str, str]":
    return {k: os.environ[k] for k in TRANSPORT_ENV_KEYS if k in os.environ}


def default_baseline_fetcher(remote_path: str, local_path: Path) -> None:
    transport = Path(__file__).resolve().parent / "dev_vm_ssh.sh"
    try:
        proc = subprocess.run(  # noqa: S603
            [str(transport), "copy-from", remote_path, str(local_path)],
            capture_output=True, timeout=300, check=False, env=_transport_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        raise Unproven("missing_private_baseline", "private baseline transfer failed or timed out") from None
    if proc.returncode != 0:
        raise Unproven("missing_private_baseline", "private baseline could not be fetched")


def default_owner_reader(key: str, headers: "str | None") -> "tuple[int, Any]":
    transport = Path(__file__).resolve().parent / "dev_vm_ssh.sh"
    stdin = ("Accept: application/json\n" + (headers or "")).encode()
    try:
        proc = subprocess.run(  # noqa: S603
            [str(transport), "exec", OWNER_READS[key]],
            input=stdin, capture_output=True, timeout=90, check=False, env=_transport_env(),
        )
        if proc.returncode != 0:
            return 0, None
        body, _, status = proc.stdout.decode("utf-8").rpartition("\n")
        code = int(status.strip())
        try:
            return code, json.loads(body) if body.strip() else {}
        except ValueError:
            return code, None
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return 0, None


def default_retainer(run_dir: str, name: str, data: bytes) -> str:
    """Write one complete private witness to the dev VM and return the remote sha256 readback."""
    if not SAFE_NAME.match(run_dir) or not SAFE_NAME.match(name):
        raise Unproven("private_retention_failed", "unsafe private retention name")
    transport = Path(__file__).resolve().parent / "dev_vm_ssh.sh"
    target = f"{RETAIN_ROOT}/{run_dir}"
    write = f"umask 077 && mkdir -p {target} && cat > {target}/{name} && sha256sum {target}/{name}"
    try:
        proc = subprocess.run(  # noqa: S603
            [str(transport), "exec", write],
            input=data, capture_output=True, timeout=600, check=False, env=_transport_env(),
        )
    except (subprocess.TimeoutExpired, OSError):
        raise Unproven("private_retention_failed", "private witness transfer failed or timed out") from None
    out = proc.stdout.decode("utf-8", "replace").split()
    if proc.returncode != 0 or not out or not SHA64.match(out[0]):
        raise Unproven("private_retention_failed", "private witness could not be written")
    return out[0]


def _ident(row: Any) -> "tuple[str, str]":
    """(instance id, connector id) exactly as the BFF/FE contract names them."""
    if not isinstance(row, dict):
        raise Unproven("owner_inventory_invalid", "owner inventory row is not an object")
    inst = str(row.get("data_source_id") or row.get("source_instance_id") or "")
    conn = str(row.get("connector_id") or row.get("id") or inst)
    if not conn:
        raise Unproven("owner_inventory_invalid", "owner inventory row has no identifier")
    return inst, conn


class Caller:
    def __init__(
        self,
        args: argparse.Namespace,
        environ: Mapping[str, str],
        *,
        http: Http = default_http,
        cli_runner: CliRunner = default_cli_runner,
        baseline_fetcher: BaselineFetcher = default_baseline_fetcher,
        owner_reader: OwnerReader = default_owner_reader,
        retainer: Retainer = default_retainer,
    ) -> None:
        self.args = args
        self.environ = environ
        self.http = http
        self.cli_runner = cli_runner
        self.baseline_fetcher = baseline_fetcher
        self.owner_reader = owner_reader
        self.retainer = retainer
        self.secrets: list[str] = []

    # -- helpers ---------------------------------------------------------
    def _redact(self, text: str) -> str:
        for secret in self.secrets:
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text

    def _identity(self, fe: str, bff: str) -> dict:
        timeout = 30
        status, deployment = self.http(f"{fe}/deployment.json", "GET", {"Accept": "application/json"}, None, timeout)
        if status != 200 or not isinstance(deployment, dict):
            raise Unproven("identity_unreachable", "FE deployment identity unavailable")
        status, version = self.http(f"{bff}/bff/version", "GET", {"Accept": "application/json"}, None, timeout)
        if status != 200 or not isinstance(version, dict):
            raise Unproven("identity_unreachable", "BFF version identity unavailable")
        live_fe = str(deployment.get("commit") or deployment.get("frontendSha") or "")
        live_bff = str(version.get("source_commit_sha") or version.get("commit") or "")
        posture = version.get("config_posture") if isinstance(version.get("config_posture"), dict) else {}
        if live_fe != self.args.fe_sha or live_bff != self.args.bff_sha:
            raise Unproven("pair_mismatch", "served FE/BFF differs from the accepted pair")
        if posture.get("auth_mode") != "strict" or posture.get("auth_stub") is True:
            raise Unproven("auth_posture", "BFF auth posture is not strict")
        return {"fe": live_fe, "bff": live_bff, "auth_mode": "strict"}

    def _owner_headers(self, bff: str) -> str:
        """Real dev-login bearer (RAM only) bound to the tested tenant, as curl header lines."""
        prof = self.profiles[OWNER_PROFILE]
        status, login = self.http(
            f"{bff}/bff/auth/dev-login", "POST", {"Content-Type": "application/json", "Accept": "application/json"},
            json.dumps({"grant_type": "client_credentials", "client_id": prof["client_id"],
                        "client_secret": prof["client_secret"]}).encode(), 30)
        token = login.get("access_token") if isinstance(login, dict) else None
        if status != 200 or not isinstance(token, str) or not token:
            raise Unproven("owner_auth_unavailable", "dev-login for owner reads failed")
        self.secrets.append(token)
        status, me = self.http(f"{bff}/bff/me", "GET", {"Authorization": f"Bearer {token}",
                                                         "Accept": "application/json"}, None, 30)
        data = me.get("data") if isinstance(me, dict) else None
        if status != 200 or not isinstance(data, dict) or data.get("tenant_id") != self.args.tenant_id:
            raise Unproven("owner_tenant_mismatch", "authenticated principal is not in the tested tenant")
        return f"Authorization: Bearer {token}\nX-Tenant-Id: {self.args.tenant_id}\n"

    def _owner_get(self, key: str, headers: str) -> Any:
        # Anonymous access must be refused, otherwise the bearer proves nothing.
        anon_status, _ = self.owner_reader(key, None)
        if anon_status == 200:
            raise Unproven("owner_auth_not_enforced",
                           f"owner {key} API answers anonymous callers; authenticated owner evidence is impossible")
        status, doc = self.owner_reader(key, headers)
        if status in (401, 403):
            raise Unproven("owner_auth_unavailable", f"owner {key} API refused the authenticated principal")
        if status != 200 or doc is None:
            raise Unproven("owner_inventory_unavailable", f"owner {key} API is unavailable")
        return doc

    def _owner_inventory(self, bff: str) -> dict:
        """Independent, authenticated, tenant-scoped owner inventory (never the BFF projection).

        Governed owner instances win when present; otherwise the legacy
        connector registry is the owner evidence and the mode is
        ``legacy_projection``.  Unknown/failed/duplicate/incomplete/cross-tenant
        evidence is rejected, and raw ids/counts are preserved.
        """
        headers = self._owner_headers(bff)
        doc = self._owner_get("sources", headers)
        if not isinstance(doc, dict) or not isinstance(doc.get("sources"), list):
            raise Unproven("owner_inventory_unavailable", "owner instance listing is unavailable or malformed")
        rows = doc["sources"]
        if doc.get("count") != len(rows):
            raise Unproven("owner_inventory_invalid", "owner instance count does not match its rows")
        tenant = self.args.tenant_id
        for row in rows:
            owner_tenant = row.get("tenant_id") if isinstance(row, dict) else None
            if owner_tenant is None:
                raise Unproven("owner_tenant_unproven", "owner instance rows carry no tenant attribution")
            if owner_tenant != tenant:
                raise Unproven("owner_tenant_mismatch", "owner instance belongs to a different tenant")
        if rows:
            instances, ids = [], []
            for row in rows:
                inst, conn = _ident(row)
                if not inst:
                    raise Unproven("owner_inventory_invalid", "owner instance has no source_instance_id")
                ids.append(conn)
                instances.append({
                    "source_instance_id": inst, "connector_id": conn,
                    "lifecycle_state": row.get("lifecycle_state"),
                    "health": row.get("health") or row.get("health_state"),
                    "paused": row.get("paused", row.get("pause_reason")),
                })
            if len(set(ids)) != len(ids) or len({i["source_instance_id"] for i in instances}) != len(instances):
                raise Unproven("owner_inventory_invalid", "owner inventory contains duplicate identifiers")
            return {"source_mode": "governed_owner", "meta_source": "service_client",
                    "connector_ids": sorted(ids), "instance_count": len(rows), "instances": instances}
        reg = self._owner_get("registry", headers)
        if not isinstance(reg, dict) or not isinstance(reg.get("connectors"), list) or not reg["connectors"]:
            raise Unproven("owner_inventory_unavailable", "owner registry is unavailable, empty or malformed")
        ids = [_ident(row)[1] for row in reg["connectors"]]
        if len(set(ids)) != len(ids):
            raise Unproven("owner_inventory_invalid", "owner registry contains duplicate identifiers")
        return {"source_mode": "legacy_projection", "connector_ids": sorted(ids),
                "instance_count": 0, "registry_count": len(ids)}

    def _collect_cli_artifacts(self, out_dir: Path) -> "dict | None":
        """Retain COMPLETE private witnesses, then build the sanitized public summary.

        Every regular file of the CLI result directory is redacted of this run's
        credentials, written byte-complete to the private dev-VM acceptance
        tree over the existing SSH transport and hash-verified by readback.  The
        local directory is never deleted here.  The public record carries only
        hashes, counts and bounded pointers (no before/after/business values).
        """
        dirs = sorted(
            (d for d in out_dir.glob("paired-readonly-*") if d.is_dir() and not d.is_symlink()),
            key=lambda d: d.stat().st_mtime,
        )
        if not dirs:
            return None
        d, art = dirs[-1], {}
        run_dir = f"{self.args.root_run_id}-{self.args.run_attempt or '1'}"
        retained: dict[str, Any] = {}
        failure: "Unproven | None" = None
        for f in sorted(d.iterdir()):
            if not f.is_file() or f.is_symlink() or not SAFE_NAME.match(f.name):
                continue
            raw = f.read_bytes()
            data = self._redact(raw.decode("utf-8", "replace")).encode() if self.secrets else raw
            local_sha = hashlib.sha256(data).hexdigest()
            entry: dict[str, Any] = {"bytes": len(data), "sha256": local_sha, "retained": False}
            try:
                entry["remote_sha256"] = self.retainer(run_dir, f.name, data)
                entry["retained"] = entry["remote_sha256"] == local_sha
                if not entry["retained"]:
                    raise Unproven("private_retention_failed", f"private readback hash mismatch for {f.name}")
            except Unproven as exc:
                failure = failure or exc
            retained[f.name] = entry
        art["private_witness"] = {"remote_dir": f"{RETAIN_ROOT}/{run_dir}", "files": retained}
        if failure is not None:
            art["private_witness"]["failure"] = failure.code
        result = d / "result.json"
        if result.is_file() and not result.is_symlink():
            raw = result.read_bytes()
            art["result_sha256"] = hashlib.sha256(raw).hexdigest()
            art["result_bytes"] = len(raw)
            if len(raw) <= RESULT_EMBED_MAX_BYTES:
                try:
                    art["result"] = json.loads(self._redact(raw.decode("utf-8")))
                except ValueError:
                    art["result_parse_error"] = True
            else:
                art["result_truncated"] = True
        diff = d / "full-baseline-differences.json"
        if diff.is_file() and not diff.is_symlink():
            try:
                doc = json.loads(diff.read_text())
                art["differences"] = {
                    path: {"count": len(items), "pointers": [str(i.get("path")) for i in items[:DIFF_POINTER_CAP]],
                           "pointers_truncated": len(items) > DIFF_POINTER_CAP}
                    for path, items in doc.items() if isinstance(items, list)
                }
                art["differences_total"] = sum(v["count"] for v in art["differences"].values())
                art["differences_complete_private_sha256"] = retained.get(diff.name, {}).get("sha256")
            except (ValueError, AttributeError):
                art["differences_parse_error"] = True
        if failure is not None:
            art["retention_failure"] = failure
        return art

    # -- main ------------------------------------------------------------
    def run(self) -> dict:
        a = self.args
        evidence: dict[str, Any] = {
            "schema_version": SCHEMA,
            "started_at": _now(),
            "supplementary_not_a_release_gate": True,
            "run": {"root": a.root_run_id, "attempt": a.run_attempt, "pair": a.pair_id,
                    "fe": a.fe_sha, "bff": a.bff_sha},
            "baseline": {"expected_sha256": a.baseline_sha256, "remote_path": a.baseline_remote_path},
            "outcome": "unproven",
        }
        tmp = Path(tempfile.mkdtemp(prefix="pantheon-readonly-qa-"))
        os.chmod(tmp, 0o700)
        try:
            self._run(evidence, tmp)
        except Unproven as exc:
            evidence["outcome"] = "unproven"
            evidence["reason_code"] = exc.code
            evidence["reason"] = self._redact(str(exc))
        except Exception as exc:  # noqa: BLE001 - any runtime failure stays an honest unproven record
            evidence["outcome"] = "unproven"
            evidence["reason_code"] = "runtime_error"
            evidence["reason"] = self._redact(f"{type(exc).__name__}: {exc}")[:500]
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        evidence["finished_at"] = _now()
        return evidence

    def _run(self, evidence: dict, tmp: Path) -> None:
        a, env = self.args, self.environ
        for label, sha in (("fe_sha", a.fe_sha), ("bff_sha", a.bff_sha)):
            if not SHA40.match(a.__dict__[label] or ""):
                raise Unproven("bad_pair", f"{label} is not an exact 40-hex SHA")
        if not (a.pair_id and a.root_run_id and a.tenant_id and a.persona_id):
            raise Unproven("missing_input", "pair/root/tenant/persona identity is required")
        fe = _check_origin("DEV_FE_URL", a.fe_url, "https://app.dev.mvl-cap.tw")
        bff = _check_origin("DEV_BFF_URL", a.bff_url, "https://api.dev.mvl-cap.tw")
        try:
            roles = json.loads(a.expected_roles_json or "")
            if set(roles) != set(PROFILES) or not all(isinstance(v, list) and v for v in roles.values()):
                raise ValueError
        except ValueError:
            raise Unproven("missing_profile", "expected roles for all four profiles are required") from None

        profiles: dict[str, dict[str, str]] = {}
        for name in PROFILES:
            cid = env.get(f"DEV_BFF_DEV_LOGIN_{name}_CLIENT_ID", "")
            secret = env.get(f"DEV_BFF_DEV_LOGIN_{name}_CLIENT_SECRET", "")
            if not cid or not secret:
                raise Unproven("missing_profile", f"profile {name} credentials are not configured")
            self.secrets.append(secret)
            profiles[name] = {"client_id": cid, "client_secret": secret}
        self.profiles = profiles

        cli = Path(a.frontend_checkout) / CLI_RELATIVE
        if not cli.is_file() or cli.is_symlink():
            raise Unproven("missing_cli", "qualified frontend CLI is absent from the exact FE revision")
        evidence["cli"] = {"path": CLI_RELATIVE, "sha256": hashlib.sha256(cli.read_bytes()).hexdigest()}
        # Execution readiness (dependencies + browser) proven BEFORE any credential is used.
        ready_code, _, ready_err = self.cli_runner(
            ["node", "--input-type=module", "-e", READY_SCRIPT], b"", READY_TIMEOUT_SECONDS,
            cwd=a.frontend_checkout,
        )
        evidence["cli_ready"] = {"exit_code": ready_code}
        if ready_code != 0:
            raise Unproven("cli_not_ready", "frontend CLI dependencies/browser are not prepared: "
                           + self._redact(ready_err)[-300:])

        if not SHA64.match(a.baseline_sha256 or ""):
            raise Unproven("missing_private_baseline", "expected baseline sha256 is required")
        if not BASELINE_PATH.match(a.baseline_remote_path or "") or ".." in a.baseline_remote_path:
            raise Unproven("missing_private_baseline", "baseline path is not an allowed private acceptance path")
        local = tmp / "baseline.json"
        self.baseline_fetcher(a.baseline_remote_path, local)
        if not local.is_file():
            raise Unproven("missing_private_baseline", "private baseline was not retrieved")
        raw = local.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        evidence["baseline"].update({"sha256": digest, "bytes": len(raw)})  # metadata only
        if digest != a.baseline_sha256:
            raise Unproven("baseline_hash_mismatch", "private baseline does not match the expected hash")
        try:
            baseline_text = raw.decode("utf-8")
        except UnicodeDecodeError:
            raise Unproven("missing_private_baseline", "private baseline is not UTF-8 text") from None

        evidence["identity_before"] = self._identity(fe, bff)
        inventory = self._owner_inventory(bff)
        evidence["source_owner_registry"] = inventory

        payload = {
            "profiles": profiles,
            "identity": {"DEV_FE_URL": fe, "DEV_BFF_URL": bff},
            "expected": {
                "fe": a.fe_sha,
                "bff": a.bff_sha,
                "root": a.root_run_id,
                "pair": a.pair_id,
                "tenant_id": a.tenant_id,
                "roles": roles,
                "persona_id": a.persona_id,
                "source_owner_registry": {k: inventory[k] for k in ("connector_ids", "source_mode", "meta_source")
                                          if k in inventory},
            },
            "baseline_raw": baseline_text,
            "baseline_identity": {"sha256": digest},
        }
        out_dir = Path(a.out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(out_dir, 0o700)
        code, out, err = self.cli_runner(
            ["node", str(cli), "--out-dir", str(out_dir)],
            json.dumps(payload, separators=(",", ":")).encode(),
            CLI_TIMEOUT_SECONDS,
            cwd=a.frontend_checkout,
        )
        evidence["cli_result"] = {
            "exit_code": code,
            "stdout_tail": self._redact(out)[-4000:],
            "stderr_tail": self._redact(err)[-2000:],
        }
        artifacts = self._collect_cli_artifacts(out_dir)
        failure = artifacts.pop("retention_failure", None) if artifacts else None
        evidence["cli_artifacts"] = artifacts
        evidence["identity_after"] = self._identity(fe, bff)  # drift raises Unproven
        if artifacts is None:
            raise Unproven("private_retention_failed", "CLI produced no private result directory to retain")
        if failure is not None:
            raise failure
        evidence["outcome"] = "pass" if code == 0 else "failed"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    for name in (
        "fe-url", "bff-url", "fe-sha", "bff-sha", "root-run-id", "pair-id", "tenant-id",
        "persona-id", "run-attempt", "expected-roles-json", "frontend-checkout", "baseline-remote-path",
        "baseline-sha256", "out-dir", "evidence-out",
    ):
        p.add_argument(f"--{name}", default="")
    return p


def main(argv: "Sequence[str] | None" = None, environ: "Mapping[str, str] | None" = None, **kw: Any) -> int:
    args = build_parser().parse_args(argv)
    if not args.evidence_out or not args.out_dir:
        print("--evidence-out and --out-dir are required", file=sys.stderr)
        return 2
    caller = Caller(args, os.environ if environ is None else environ, **kw)
    evidence = caller.run()
    dest = Path(args.evidence_out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    os.chmod(dest, 0o600)
    print(json.dumps({"outcome": evidence["outcome"], "reason_code": evidence.get("reason_code")}))
    return 0 if evidence["outcome"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
